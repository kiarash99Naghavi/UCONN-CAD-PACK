def my_cad_function(args):
    import cadquery as cq
    from math import sqrt

    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input

    # -------------------------
    # Helpers
    # -------------------------
    def near(a, b, tol=1e-6):
        return abs(a - b) <= tol

    def fmt_bb(bb):
        return f"[{bb.xmin:.3f}, {bb.ymin:.3f}, {bb.zmin:.3f}]..[{bb.xmax:.3f}, {bb.ymax:.3f}, {bb.zmax:.3f}]"

    def bb_key(bb, nd=3):
        return (round(bb.xmin, nd), round(bb.ymin, nd), round(bb.zmin, nd), round(bb.xmax, nd), round(bb.ymax, nd), round(bb.zmax, nd))

    def inner_wires(face: cq.Face):
        # CadQuery Face API is lower-case: outerWire()
        wires = list(face.Wires())
        if not wires:
            return []
        try:
            outer = face.outerWire()
            inners = [w for w in wires if not w.isSame(outer)]
            if inners:
                return inners
        except Exception:
            pass
        # Fallback: assume the longest wire is outer
        wl = [(w, w.Length()) for w in wires]
        wl.sort(key=lambda t: t[1], reverse=True)
        outer = wl[0][0]
        inners = [w for w in wires if not w.isSame(outer)]
        return inners

    def centered_inner_wire(face: cq.Face, ctr_tol=0.25):
        # pick inner wire whose bbox center is near X=0,Z=0
        inners = inner_wires(face)
        if not inners:
            return None
        best = None
        best_score = 1e9
        for w in inners:
            bb = w.BoundingBox()
            cx = 0.5 * (bb.xmin + bb.xmax)
            cz = 0.5 * (bb.zmin + bb.zmax)
            score = abs(cx) + abs(cz)
            if score < best_score:
                best_score = score
                best = w
        if best is None:
            return None
        bb = best.BoundingBox()
        cx = 0.5 * (bb.xmin + bb.xmax)
        cz = 0.5 * (bb.zmin + bb.zmax)
        if abs(cx) <= ctr_tol and abs(cz) <= ctr_tol:
            return best
        return None

    def reverse_wire(w: cq.Wire):
        return cq.Shape.cast(w.wrapped.Reversed())

    def reverse_face(f: cq.Face):
        return cq.Shape.cast(f.wrapped.Reversed())

    def make_hex_wire_at_y(yval: float):
        # vertices per spec, circumradius = 10.5
        verts_xz = [
            (9.093267, 5.25),
            (0.0, 10.5),
            (-9.093267, 5.25),
            (-9.093267, -5.25),
            (0.0, -10.5),
            (9.093267, -5.25),
        ]
        pts = [cq.Vector(x, yval, z) for (x, z) in verts_xz]
        w = cq.Wire.makePolygon(pts, close=True)
        return w

    def rebuild_planar_with_hex(face_old: cq.Face):
        # Keep outer boundary exactly; replace inner with sharp hex
        y = face_old.Center().y
        outer = face_old.outerWire()
        inner = make_hex_wire_at_y(y)
        n_old = face_old.normalAt()

        # Try build with given orientation; if it fails, reverse inner
        newf = None
        try:
            newf = cq.Face.makeFromWires(outer, [inner])
        except Exception:
            newf = cq.Face.makeFromWires(outer, [reverse_wire(inner)])

        # Ensure normal matches old
        try:
            n_new = newf.normalAt()
            dot = n_new.x * n_old.x + n_new.y * n_old.y + n_new.z * n_old.z
            if dot < 0:
                newf = reverse_face(newf)
        except Exception:
            pass

        # Print quick self-check
        bb_o = outer.BoundingBox()
        bb_i = inner.BoundingBox()
        print(
            f"RETRIM: planar face at y={y:.6f} old_n={n_old.toTuple()} outer_bb={fmt_bb(bb_o)} hex_bb={fmt_bb(bb_i)}"
        )
        return newf

    # -------------------------
    # Split solids by invariants (do NOT rely on list order)
    # -------------------------
    base = shape.val() if hasattr(shape, "val") else shape
    solids = list(base.Solids())
    print(f"CHECK: imported solids={len(solids)}")

    compact_target_vol = 2162.777
    protected_target_vol = 27615.571
    compact_target_bb = (-15.75, -4.625, -15.75, 15.75, 3.175, 15.75)
    protected_target_bb = (-56.511, -6.35, -56.604, 56.511, 3.175, 56.234)

    def is_match(s, vol, bb):
        v = s.Volume()
        b = s.BoundingBox()
        return (abs(v - vol) < 0.02) and (bb_key(b) == tuple(round(x, 3) for x in bb))

    for i, s in enumerate(solids):
        bb = s.BoundingBox()
        print(
            f"SNAPSHOT: solid[{i}] vol={s.Volume():.3f} bbox={fmt_bb(bb)} faces={len(s.Faces())} edges={len(s.Edges())}"
        )

    compact_candidates = [s for s in solids if is_match(s, compact_target_vol, compact_target_bb)]
    protected_candidates = [s for s in solids if is_match(s, protected_target_vol, protected_target_bb)]

    print(f"SELECTED: {len(compact_candidates)} solids for compact target by vol+bbox")
    print(f"SELECTED: {len(protected_candidates)} solids for protected target by vol+bbox")

    if len(compact_candidates) != 1 or len(protected_candidates) != 1:
        print("FAILED: could not uniquely identify compact/protected solids; returning input")
        return shape

    compact = compact_candidates[0]
    protected_orig = protected_candidates[0]  # freeze as untouched original copy

    comp_pre_v = compact.Volume()
    comp_pre_bb = compact.BoundingBox()
    prot_pre_v = protected_orig.Volume()
    prot_pre_bb = protected_orig.BoundingBox()

    print("INVARIANTS pre:")
    print(f"  s0 pre  vol={comp_pre_v:.3f} bbox={fmt_bb(comp_pre_bb)}")
    print(f"  s1 pre  vol={prot_pre_v:.3f} bbox={fmt_bb(prot_pre_bb)}")

    # Additional distinguishing invariant: full cylindrical exterior face r=15.75 about world Y
    cyl_r = 15.75
    cyl_faces = []
    for f in compact.Faces():
        if f.geomType() != "CYLINDER":
            continue
        g = f.geomAdaptor()
        try:
            r = g.Cylinder().Radius()
        except Exception:
            continue
        if abs(r - cyl_r) < 1e-4:
            cyl_faces.append(f)
    print(f"SELECTED: {len(cyl_faces)} cylindrical faces on compact with r=15.75 (expected 1)")

    # -------------------------
    # Identify the flower mouth faces using the provided face indices AND verify
    # -------------------------
    all_faces = list(base.Faces())
    top_face_idx = 1
    bot_face_idx = 233
    if top_face_idx >= len(all_faces) or bot_face_idx >= len(all_faces):
        print("FAILED: face indices out of range; returning input")
        return shape

    top_face_global = all_faces[top_face_idx]
    bot_face_global = all_faces[bot_face_idx]

    print(
        f"CHECK: face#{top_face_idx} center={top_face_global.Center().toTuple()} area={top_face_global.Area():.3f} n={top_face_global.normalAt().toTuple()}"
    )
    print(
        f"CHECK: face#{bot_face_idx} center={bot_face_global.Center().toTuple()} area={bot_face_global.Area():.3f} n={bot_face_global.normalAt().toTuple()}"
    )

    # Map these global faces into compact solid by hashCode
    comp_faces = list(compact.Faces())
    top_h = top_face_global.hashCode()
    bot_h = bot_face_global.hashCode()
    top_face = next((f for f in comp_faces if f.hashCode() == top_h), None)
    bot_face = next((f for f in comp_faces if f.hashCode() == bot_h), None)

    if top_face is None or bot_face is None:
        print("FAILED: could not locate top/bottom mouth faces in compact solid; returning input")
        return shape

    y_top = 3.175
    y_bot = -4.625

    if not near(top_face.Center().y, y_top, 1e-3) or not near(bot_face.Center().y, y_bot, 1e-3):
        print(
            f"FAILED: mouth face Y mismatch: top_y={top_face.Center().y:.6f} (exp {y_top}), bot_y={bot_face.Center().y:.6f} (exp {y_bot}); returning input"
        )
        return shape

    top_inner = centered_inner_wire(top_face, ctr_tol=0.25)
    bot_inner = centered_inner_wire(bot_face, ctr_tol=0.25)

    if top_inner is None or bot_inner is None:
        print("FAILED: could not find centered inner wires on mouth faces; returning input")
        return shape

    top_ec = len(list(top_inner.Edges()))
    bot_ec = len(list(bot_inner.Edges()))
    print(f"SELECTED: top mouth centered inner loop edges={top_ec} (expected 192)")
    print(f"SELECTED: bot mouth centered inner loop edges={bot_ec} (expected 192)")
    if top_ec != 192 or bot_ec != 192:
        print("FAILED: mouth inner loop edge count not 192; returning input")
        return shape

    # -------------------------
    # Build adjacency (face<->edge) on compact and flood-fill the cavity region
    # without leaking through the OUTER wires of planar partition faces.
    # -------------------------
    edge_to_faces = {}
    face_edges = []
    for fi, f in enumerate(comp_faces):
        ehs = []
        for e in f.Edges():
            eh = e.hashCode()
            ehs.append(eh)
            edge_to_faces.setdefault(eh, []).append(fi)
        face_edges.append(ehs)

    # Rim edges are the inner-loop edges at the two mouths
    rim_edge_hashes = set(e.hashCode() for e in top_inner.Edges()) | set(e.hashCode() for e in bot_inner.Edges())
    print(f"SELECTED: {len(rim_edge_hashes)} unique rim edges from two 192-edge mouth loops")

    # Seed faces = faces adjacent to rim edges, excluding the mouth faces themselves
    top_fi = next(i for i, f in enumerate(comp_faces) if f.hashCode() == top_face.hashCode())
    bot_fi = next(i for i, f in enumerate(comp_faces) if f.hashCode() == bot_face.hashCode())

    seeds = set()
    for eh in rim_edge_hashes:
        for afi in edge_to_faces.get(eh, []):
            if afi not in (top_fi, bot_fi):
                seeds.add(afi)

    print(f"SELECTED: {len(seeds)} seed faces adjacent to mouth rim edges (excluding mouths)")
    if len(seeds) == 0:
        print("FAILED: no seed faces found adjacent to rim; returning input")
        return shape

    # Detect planar boundary faces (mouths and partitions) and define "do not traverse outer wire" rule
    boundary_face_inner_edges = {}
    for fi, f in enumerate(comp_faces):
        if f.geomType() != "PLANE":
            continue
        n = f.normalAt()
        if abs(n.y) < 0.9:
            continue
        iw = centered_inner_wire(f, ctr_tol=0.25)
        if iw is None:
            continue
        boundary_face_inner_edges[fi] = set(e.hashCode() for e in iw.Edges())

    print(f"SELECTED: {len(boundary_face_inner_edges)} planar +/-Y faces with centered inner opening (mouths/partitions candidates)")

    # BFS across cavity region, but on boundary planar faces only traverse through inner edges
    region = set()
    q = list(seeds)
    while q:
        fi = q.pop()
        if fi in region:
            continue
        region.add(fi)

        allowed_edges = face_edges[fi]
        if fi in boundary_face_inner_edges:
            # prevent leaking into the rest of the part through the outer wire
            allowed_edges = [eh for eh in face_edges[fi] if eh in boundary_face_inner_edges[fi]]

        for eh in allowed_edges:
            for nfi in edge_to_faces.get(eh, []):
                if nfi not in region:
                    q.append(nfi)

    # Include the two mouth faces themselves for replacement (we won't keep originals)
    region.add(top_fi)
    region.add(bot_fi)

    print(f"SELECTED: cavity-connected region faces={len(region)} (includes mouths)")

    # Partition/boundary planar faces in region to retrim with hex
    boundary_fis_in_region = [fi for fi in region if fi in boundary_face_inner_edges]
    boundary_fis_in_region.sort(key=lambda i: comp_faces[i].Center().y)

    print(f"SELECTED: {len(boundary_fis_in_region)} planar boundary faces in region to retrim")
    for fi in boundary_fis_in_region:
        f = comp_faces[fi]
        n = f.normalAt()
        iw = centered_inner_wire(f, ctr_tol=0.25)
        ec = len(list(iw.Edges())) if iw else 0
        c = f.Center()
        print(f"  BOUNDARY_FACE fi={fi} y={c.y:.6f} n={n.toTuple()} inner_edges={ec}")

    # Cavity-wall faces to remove = region minus these planar boundary faces
    cavity_wall_fis = sorted([fi for fi in region if fi not in boundary_face_inner_edges])
    print(f"SELECTED: {len(cavity_wall_fis)} cavity-wall faces to remove/replace")

    # -------------------------
    # Rebuild replacement faces
    #   - retrim boundary planar faces with single sharp hex inner wire
    #   - add 6 planar Y-parallel walls, split at Y seam levels from boundary faces
    # -------------------------
    # Y seam levels from boundary faces (use actual face center y values)
    y_levels = sorted({round(comp_faces[fi].Center().y, 6) for fi in boundary_fis_in_region})
    if round(y_top, 6) not in y_levels:
        y_levels.append(round(y_top, 6))
    if round(y_bot, 6) not in y_levels:
        y_levels.append(round(y_bot, 6))
    y_levels = sorted(set(y_levels))

    print(f"CHECK: Y seam levels for wall splitting = {y_levels}")
    if y_levels[0] > y_bot + 1e-6 or y_levels[-1] < y_top - 1e-6:
        print("FAILED: seam levels do not cover required wall Y-span; returning input")
        return shape

    # Replace boundary planar faces
    new_boundary_faces = []
    for fi in boundary_fis_in_region:
        f_old = comp_faces[fi]
        try:
            nf = rebuild_planar_with_hex(f_old)
            new_boundary_faces.append(nf)
        except Exception as ex:
            print(f"FAILED: could not retrim boundary face fi={fi} at y={f_old.Center().y:.6f}: {ex}")
            return shape

    print(f"SELECTED: {len(new_boundary_faces)} retrimmed boundary faces (expected >=2)")

    # Build hex walls (split across seam levels)
    verts_xz = [
        (9.093267, 5.25),
        (0.0, 10.5),
        (-9.093267, 5.25),
        (-9.093267, -5.25),
        (0.0, -10.5),
        (9.093267, -5.25),
    ]

    wall_faces = []
    # segments between successive y levels
    for si in range(len(y_levels) - 1):
        y0 = y_levels[si]
        y1 = y_levels[si + 1]
        # ensure y0<y1
        y_low, y_high = (y0, y1) if y0 < y1 else (y1, y0)
        for i in range(6):
            (x0, z0) = verts_xz[i]
            (x1, z1) = verts_xz[(i + 1) % 6]
            p0t = cq.Vector(x0, y_high, z0)
            p1t = cq.Vector(x1, y_high, z1)
            p1b = cq.Vector(x1, y_low, z1)
            p0b = cq.Vector(x0, y_low, z0)
            w = cq.Wire.makePolygon([p0t, p1t, p1b, p0b], close=True)
            f = cq.Face.makeFromWires(w)
            # Ensure wall face normal points into cavity (toward origin in XZ)
            n = f.normalAt()
            c = f.Center()
            to_origin = cq.Vector(-c.x, 0.0, -c.z)
            if (n.x * to_origin.x + n.z * to_origin.z) < 0:
                f = reverse_face(f)
            wall_faces.append(f)

    print(f"SELECTED: {len(wall_faces)} new planar wall faces (6 * segments = {6*(len(y_levels)-1)})")

    # -------------------------
    # Sew kept + replacement faces into one closed solid (no booleans)
    # -------------------------
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Sewing, BRepBuilderAPI_MakeSolid
    from OCP.BRepLib import BRepLib

    region_hashes = set(comp_faces[fi].hashCode() for fi in region)
    kept_faces = [f for f in comp_faces if f.hashCode() not in region_hashes]

    print(f"SELECTED: {len(kept_faces)} untouched compact faces to keep")
    print(f"SELECTED: {len(new_boundary_faces)} replacement planar faces")

    sew = BRepBuilderAPI_Sewing(1e-6)
    for f in kept_faces:
        sew.Add(f.wrapped)
    for f in new_boundary_faces:
        sew.Add(f.wrapped)
    for f in wall_faces:
        sew.Add(f.wrapped)

    sew.Perform()
    sewed = cq.Shape.cast(sew.SewedShape())

    shells = list(sewed.Shells())
    solids_from_sew = list(sewed.Solids())
    print(f"CHECK: sewing produced shells={len(shells)} solids={len(solids_from_sew)}")

    if len(solids_from_sew) == 1:
        new_s0 = solids_from_sew[0]
    else:
        if len(shells) != 1:
            print("FAILED: sewing did not yield exactly one shell; returning input")
            return shape
        mk = BRepBuilderAPI_MakeSolid(shells[0].wrapped)
        try:
            if hasattr(mk, "IsDone") and not mk.IsDone():
                print("FAILED: MakeSolid not done; returning input")
                return shape
        except Exception:
            pass
        new_s0_topo = mk.Solid()
        try:
            BRepLib.OrientClosedSolid_s(new_s0_topo)
        except Exception as ex:
            print(f"WARN: OrientClosedSolid failed: {ex}")
        new_s0 = cq.Shape.cast(new_s0_topo)

    # -------------------------
    # Verification & reporting
    # -------------------------
    comp_post_v = new_s0.Volume()
    comp_post_bb = new_s0.BoundingBox()
    prot_post_v = protected_orig.Volume()
    prot_post_bb = protected_orig.BoundingBox()

    print("RESULT invariants:")
    print(f"  s0 pre  vol={comp_pre_v:.3f} bbox={fmt_bb(comp_pre_bb)}")
    print(f"  s0 post vol={comp_post_v:.3f} bbox={fmt_bb(comp_post_bb)}")
    print(f"  s1 pre  vol={prot_pre_v:.3f} bbox={fmt_bb(prot_pre_bb)}")
    print(f"  s1 post vol={prot_post_v:.3f} bbox={fmt_bb(prot_post_bb)}")

    dbb = (
        comp_post_bb.xmin - comp_pre_bb.xmin,
        comp_post_bb.ymin - comp_pre_bb.ymin,
        comp_post_bb.zmin - comp_pre_bb.zmin,
        comp_post_bb.xmax - comp_pre_bb.xmax,
        comp_post_bb.ymax - comp_pre_bb.ymax,
        comp_post_bb.zmax - comp_pre_bb.zmax,
    )
    print(f"CHECK: s0 bbox delta (xmin,ymin,zmin,xmax,ymax,zmax) = {[round(d, 6) for d in dbb]}")

    # Spec report
    print("SPEC report:")
    print("  Opening center expected at X=0, Z=0")
    print(f"  Wall Y span expected {y_bot}..{y_top} (len {y_top - y_bot:.6f})")
    print("  Hex circumradius = 10.5 mm")
    print("  Hex vertices XZ = [(9.093267,5.25),(0,10.5),(-9.093267,5.25),(-9.093267,-5.25),(0,-10.5),(9.093267,-5.25)]")
    print("  Vertex angles = [30, 90, 150, 210, 270, 330] deg from +X toward +Z")
    print("  Across corners = 21.0 mm")
    print("  Across flats = 18.186533 mm")

    # Confirm both mouths contain the same single open sharp hexagon: inner wire edge count = 6
    def find_mouth_face(solid, yval, ny_sign):
        found = []
        for f in solid.Faces():
            if f.geomType() != "PLANE":
                continue
            c = f.Center()
            if abs(c.y - yval) > 1e-3:
                continue
            n = f.normalAt()
            if n.y * ny_sign <= 0.9:
                continue
            iw = centered_inner_wire(f, ctr_tol=0.5)
            if iw is None:
                continue
            found.append((f, iw))
        return found

    top_m = find_mouth_face(new_s0, y_top, +1)
    bot_m = find_mouth_face(new_s0, y_bot, -1)
    print(f"SELECTED: {len(top_m)} planar faces at +Y mouth plane Y={y_top}")
    print(f"SELECTED: {len(bot_m)} planar faces at -Y mouth plane Y={y_bot}")

    def report_mouth(m, tag):
        if not m:
            print(f"CHECK: {tag} not found (ERROR)")
            return
        # choose the one with centered opening
        f, iw = m[0]
        ec = len(list(iw.Edges()))
        bb = iw.BoundingBox()
        cx = 0.5 * (bb.xmin + bb.xmax)
        cz = 0.5 * (bb.zmin + bb.zmax)
        print(f"CHECK: {tag} inner wire edges={ec} (expected 6 for sharp hex)")
        print(f"CHECK: {tag} inner opening bbox-center=({cx:.6f},{cz:.6f})")

    report_mouth(top_m, "+Y mouth")
    report_mouth(bot_m, "-Y mouth")

    # Protected solid must remain untouched (invariants)
    if abs(prot_post_v - protected_target_vol) > 0.02 or bb_key(prot_post_bb) != tuple(round(x, 3) for x in protected_target_bb):
        print("FAILED: protected solid invariants changed (should be untouched); returning input unchanged")
        return shape

    # -------------------------
    # Output: compound with edited compact first (s0), protected second (s1)
    # -------------------------
    out = cq.Compound.makeCompound([new_s0, protected_orig])
    return out