def my_cad_function(args):
    import cadquery as cq
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_Cylinder
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Sewing, BRepBuilderAPI_MakeSolid
    from OCP.BRepLib import BRepLib

    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input
    base = shape.val() if hasattr(shape, "val") else shape

    def fmt_bb(bb):
        return (
            f"[{bb.xmin:.3f}, {bb.ymin:.3f}, {bb.zmin:.3f}].."
            f"[{bb.xmax:.3f}, {bb.ymax:.3f}, {bb.zmax:.3f}]"
        )

    def bb_key(bb, nd=3):
        return (
            round(bb.xmin, nd), round(bb.ymin, nd), round(bb.zmin, nd),
            round(bb.xmax, nd), round(bb.ymax, nd), round(bb.zmax, nd)
        )

    def near(a, b, tol=1e-3):
        return abs(a - b) <= tol

    def inner_wires(face):
        ow = face.OuterWire()
        ow_h = ow.hashCode()
        inn = [w for w in face.Wires() if w.hashCode() != ow_h]
        return inn

    def wire_edge_count(w):
        return len(list(w.Edges()))

    # -------------------------
    # Snapshot & separate solids by invariants (no order reliance)
    # -------------------------
    solids = list(base.Solids())
    print(f"CHECK: imported solids={len(solids)}")
    for i, s in enumerate(solids):
        bb = s.BoundingBox()
        print(f"SNAPSHOT: solid[{i}] vol={s.Volume():.3f} bbox={fmt_bb(bb)} faces={len(s.Faces())} edges={len(s.Edges())}")

    compact_target_vol = 2162.777
    compact_target_bb = (-15.75, -4.625, -15.75, 15.75, 3.175, 15.75)
    protected_target_vol = 27615.571
    protected_target_bb = (-56.511, -6.35, -56.604, 56.511, 3.175, 56.234)

    compact = None
    protected = None

    for s in solids:
        v = s.Volume()
        bb = s.BoundingBox()
        k = bb_key(bb, 3)
        if abs(v - compact_target_vol) < 0.05 and k == tuple(round(x, 3) for x in compact_target_bb):
            compact = s
        if abs(v - protected_target_vol) < 0.05 and k == tuple(round(x, 3) for x in protected_target_bb):
            protected = s

    print(f"SELECTED: {1 if compact else 0} solids for compact target by vol+bbox")
    print(f"SELECTED: {1 if protected else 0} solids for protected target by vol+bbox")

    if compact is None or protected is None:
        print("FAILED: could not uniquely identify both solids by invariants; returning input")
        return shape

    # Additional invariant: compact has full cylindrical exterior face r=15.75 about world Y
    cyl_hits = []
    for f in compact.Faces():
        try:
            ad = BRepAdaptor_Surface(f.wrapped, True)
            if ad.GetType() == GeomAbs_Cylinder:
                cyl = ad.Cylinder()
                r = float(cyl.Radius())
                d = cyl.Axis().Direction()
                if abs(r - 15.75) < 1e-3 and abs(float(d.X())) < 1e-6 and abs(float(d.Z())) < 1e-6 and abs(abs(float(d.Y())) - 1.0) < 1e-6:
                    cyl_hits.append(f)
        except Exception:
            pass
    print(f"SELECTED: {len(cyl_hits)} cylindrical faces on compact with r=15.75 axis~Y (expected 1)")

    protected_orig = protected  # never used in modeling ops; preserved as-is

    comp_pre_v = compact.Volume()
    comp_pre_bb = compact.BoundingBox()
    prot_pre_v = protected_orig.Volume()
    prot_pre_bb = protected_orig.BoundingBox()

    print("INVARIANTS pre:")
    print(f"  s0 pre  vol={comp_pre_v:.3f} bbox={fmt_bb(comp_pre_bb)}")
    print(f"  s1 pre  vol={prot_pre_v:.3f} bbox={fmt_bb(prot_pre_bb)}")

    # -------------------------
    # Identify the +Y and -Y mouth faces by position/normal and the 192-edge inner loop
    # -------------------------
    y_top = 3.175
    y_bot = -4.625

    top_candidates = []
    bot_candidates = []
    for f in compact.Faces():
        if f.geomType() != "PLANE":
            continue
        c = f.Center()
        n = f.normalAt()  # no args
        inn = inner_wires(f)
        inn_max = max((wire_edge_count(w) for w in inn), default=0)
        if near(c.y, y_top, 1e-3) and n.y > 0.9 and inn_max >= 150:
            top_candidates.append((inn_max, f))
        if near(c.y, y_bot, 1e-3) and n.y < -0.9 and inn_max >= 150:
            bot_candidates.append((inn_max, f))

    top_candidates.sort(key=lambda t: t[0], reverse=True)
    bot_candidates.sort(key=lambda t: t[0], reverse=True)

    top_face = top_candidates[0][1] if top_candidates else None
    bot_face = bot_candidates[0][1] if bot_candidates else None

    print(f"SELECTED: {len(top_candidates)} planar +Y mouth candidates at Y={y_top} with inner loop edges>=150")
    print(f"SELECTED: {len(bot_candidates)} planar -Y mouth candidates at Y={y_bot} with inner loop edges>=150")

    if top_face is None or bot_face is None:
        print("FAILED: could not locate both mouth faces with 192-edge inner loops; returning input")
        return shape

    def max_inner_wire(face):
        inn = inner_wires(face)
        if not inn:
            return None
        return max(inn, key=lambda w: wire_edge_count(w))

    top_loop = max_inner_wire(top_face)
    bot_loop = max_inner_wire(bot_face)
    print(f"CHECK: top mouth face center={list(map(lambda x: round(x,6), top_face.Center().toTuple()))} normal={list(map(lambda x: round(x,6), top_face.normalAt().toTuple()))}")
    print(f"CHECK: bot mouth face center={list(map(lambda x: round(x,6), bot_face.Center().toTuple()))} normal={list(map(lambda x: round(x,6), bot_face.normalAt().toTuple()))}")
    print(f"CHECK: top inner loop edge-count={wire_edge_count(top_loop)} (expected 192)")
    print(f"CHECK: bot inner loop edge-count={wire_edge_count(bot_loop)} (expected 192)")

    # Opening center check (from loop edge midpoints average)
    def loop_center_xz(wire):
        pts = []
        for e in wire.Edges():
            pts.append(e.Center())
        if not pts:
            return (None, None)
        cx = sum(p.x for p in pts) / len(pts)
        cz = sum(p.z for p in pts) / len(pts)
        return (cx, cz)

    top_cx, top_cz = loop_center_xz(top_loop)
    bot_cx, bot_cz = loop_center_xz(bot_loop)
    print(f"CHECK: +Y mouth loop center approx (X,Z)=({top_cx:.6f},{top_cz:.6f}) expected (0,0)")
    print(f"CHECK: -Y mouth loop center approx (X,Z)=({bot_cx:.6f},{bot_cz:.6f}) expected (0,0)")
    print(f"CHECK: wall Y span required {y_bot}..{y_top} len={(y_top - y_bot):.6f}")

    # -------------------------
    # Remove the connected cavity-wall face set incident to those loops
    # -------------------------
    # Build edge->faces adjacency map within compact
    faces_comp = list(compact.Faces())
    edge_to_faces = {}
    for fi, f in enumerate(faces_comp):
        for e in f.Edges():
            eh = e.hashCode()
            edge_to_faces.setdefault(eh, []).append(fi)

    boundary_faces_h = {top_face.hashCode(), bot_face.hashCode()}
    start_edge_hashes = {e.hashCode() for e in top_loop.Edges()} | {e.hashCode() for e in bot_loop.Edges()}
    print(f"SELECTED: {len(start_edge_hashes)} unique loop edges (top+bottom) for cavity adjacency seed")

    seed_face_idxs = set()
    for eh in start_edge_hashes:
        for fi in edge_to_faces.get(eh, []):
            if faces_comp[fi].hashCode() not in boundary_faces_h:
                seed_face_idxs.add(fi)
    print(f"SELECTED: {len(seed_face_idxs)} seed faces adjacent to mouth loops (excluding mouth faces)")

    cavity_face_idxs = set()
    q = list(seed_face_idxs)
    while q:
        fi = q.pop()
        if fi in cavity_face_idxs:
            continue
        f = faces_comp[fi]
        if f.hashCode() in boundary_faces_h:
            continue
        cavity_face_idxs.add(fi)
        for e in f.Edges():
            eh = e.hashCode()
            for nfi in edge_to_faces.get(eh, []):
                if nfi in cavity_face_idxs:
                    continue
                nf = faces_comp[nfi]
                if nf.hashCode() in boundary_faces_h:
                    continue
                q.append(nfi)

    cavity_faces = [faces_comp[i] for i in sorted(cavity_face_idxs)]
    print(f"SELECTED: {len(cavity_faces)} faces for complete connected cavity-wall removal")

    # -------------------------
    # Build the replacement hex wires and new faces/walls (direct face replacement)
    # -------------------------
    R = 10.5
    verts_xz = [
        (9.093267, 5.25),
        (0.0, 10.5),
        (-9.093267, 5.25),
        (-9.093267, -5.25),
        (0.0, -10.5),
        (9.093267, -5.25),
    ]
    angles_deg = [30, 90, 150, 210, 270, 330]
    across_corners = 2 * R
    across_flats = 2 * R * 0.8660254037844386
    print("HEX spec:")
    print(f"  circumradius R={R:.6f} mm")
    print(f"  vertex angles deg={angles_deg}")
    print(f"  across-corners={across_corners:.6f} mm (expected 21.0)")
    print(f"  across-flats  ={across_flats:.6f} mm (expected 18.186533)")
    print(f"  vertices XZ={verts_xz}")

    def make_hex_wire_at_y(y, reverse=False):
        pts = [cq.Vector(x, y, z) for (x, z) in (list(reversed(verts_xz)) if reverse else verts_xz)]
        return cq.Wire.makePolygon(pts, close=True)

    # For holes: try both inner-wire orientations and pick one that yields a face with the same normal direction
    def make_retrimmed_mouth(orig_face, y):
        outer = orig_face.OuterWire()
        orig_n = orig_face.normalAt()
        tried = []
        for rev in (False, True):
            inner = make_hex_wire_at_y(y, reverse=rev)
            try:
                nf = cq.Face.makeFromWires(outer, [inner])
                nn = nf.normalAt()
                dot = nn.x * orig_n.x + nn.y * orig_n.y + nn.z * orig_n.z
                tried.append((rev, dot, nf))
            except Exception as ex:
                print(f"WARN: makeFromWires failed for mouth at y={y} reverse={rev}: {ex}")
        if not tried:
            raise ValueError("could not build retrimmed mouth face")
        # pick the one whose normal best matches the original
        tried.sort(key=lambda t: t[1], reverse=True)
        rev, dot, nf = tried[0]
        if dot < 0:
            # reverse orientation
            nf = cq.Shape.cast(nf.wrapped.Reversed())
        print(f"CHECK: retrim mouth y={y} used_inner_reverse={rev} normal_dot={dot:.6f}")
        return nf

    new_top = make_retrimmed_mouth(top_face, y_top)
    new_bot = make_retrimmed_mouth(bot_face, y_bot)

    # Build 6 planar walls parallel to world Y spanning Y=-4.625..3.175
    wall_faces = []
    for i in range(6):
        (x0, z0) = verts_xz[i]
        (x1, z1) = verts_xz[(i + 1) % 6]
        p0t = cq.Vector(x0, y_top, z0)
        p1t = cq.Vector(x1, y_top, z1)
        p1b = cq.Vector(x1, y_bot, z1)
        p0b = cq.Vector(x0, y_bot, z0)
        w = cq.Wire.makePolygon([p0t, p1t, p1b, p0b], close=True)
        f = cq.Face.makeFromWires(w)
        # ensure wall face normal points approximately toward the cavity (toward origin in XZ)
        n = f.normalAt()
        c = f.Center()
        to_origin = cq.Vector(-c.x, 0, -c.z)
        # if the normal points away from the origin, reverse
        if (n.x * to_origin.x + n.z * to_origin.z) < 0:
            f = cq.Shape.cast(f.wrapped.Reversed())
        wall_faces.append(f)
    print(f"SELECTED: {len(wall_faces)} new planar wall faces (expected 6)")

    # -------------------------
    # Sew: kept faces + retrimmed mouths + new walls
    # -------------------------
    cavity_h = {f.hashCode() for f in cavity_faces}
    kept_faces = [f for f in faces_comp if (f.hashCode() not in cavity_h and f.hashCode() not in boundary_faces_h)]
    print(f"SELECTED: {len(kept_faces)} untouched compact faces to keep")
    print(f"SELECTED: {2} replacement mouth faces")

    sew = BRepBuilderAPI_Sewing(1e-6)
    for f in kept_faces:
        sew.Add(f.wrapped)
    sew.Add(new_top.wrapped if hasattr(new_top, "wrapped") else new_top.val().wrapped)
    sew.Add(new_bot.wrapped if hasattr(new_bot, "wrapped") else new_bot.val().wrapped)
    for f in wall_faces:
        sew.Add(f.wrapped if hasattr(f, "wrapped") else f.val().wrapped)

    sew.Perform()
    sewed = sew.SewedShape()
    sewed_cq = cq.Shape.cast(sewed)

    shells = list(sewed_cq.Shells())
    solids_from_sew = list(sewed_cq.Solids())
    print(f"CHECK: sewing produced shells={len(shells)} solids={len(solids_from_sew)}")

    if len(solids_from_sew) == 1 and len(shells) == 0:
        # already a solid
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

    # Confirm both mouths contain the same single open sharp hexagon: inner wire edge count = 6
    def find_plane_faces_on_new_s0(yval, ny_sign):
        found = []
        for f in new_s0.Faces():
            if f.geomType() != "PLANE":
                continue
            c = f.Center()
            if not near(c.y, yval, 1e-3):
                continue
            n = f.normalAt()
            if n.y * ny_sign <= 0.9:
                continue
            found.append(f)
        return found

    top_faces = find_plane_faces_on_new_s0(y_top, +1)
    bot_faces = find_plane_faces_on_new_s0(y_bot, -1)
    print(f"SELECTED: {len(top_faces)} planar faces at +Y mouth plane Y={y_top}")
    print(f"SELECTED: {len(bot_faces)} planar faces at -Y mouth plane Y={y_bot}")

    def report_hex_mouth(f, tag):
        inners = inner_wires(f)
        if not inners:
            print(f"CHECK: {tag} has 0 inner wires (ERROR: capped)")
            return
        w = max(inners, key=lambda wi: len(list(wi.Edges())))
        print(f"CHECK: {tag} inner wire edges={len(list(w.Edges()))} (expected 6 for sharp hex)")

    if top_faces:
        report_hex_mouth(top_faces[0], "+Y mouth")
    if bot_faces:
        report_hex_mouth(bot_faces[0], "-Y mouth")

    print("OPENING center check (spec): expected X=0,Z=0")
    cx = sum(x for (x, z) in verts_xz) / 6.0
    cz = sum(z for (x, z) in verts_xz) / 6.0
    print(f"  computed hex vertex-average center = ({cx:.6f}, {cz:.6f})")
    print(f"  Y span of walls expected {y_bot}..{y_top} (len {y_top - y_bot:.6f})")

    # Check protected solid invariants exactly (volume and bbox) - must remain untouched
    if abs(prot_post_v - protected_target_vol) > 0.02 or bb_key(prot_post_bb) != tuple(round(x, 3) for x in protected_target_bb):
        print("FAILED: protected solid invariants changed (should be untouched); returning input unchanged")
        return shape

    # -------------------------
    # Output: compound with edited compact first (s0), protected second (s1), no boolean
    # -------------------------
    out = cq.Compound.makeCompound([new_s0, protected_orig])
    return out