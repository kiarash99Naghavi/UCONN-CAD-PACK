def my_cad_function(args):
    import cadquery as cq
    import math

    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input

    # -------------------------
    # Helpers
    # -------------------------
    def near(a, b, tol=1e-3):
        return abs(a - b) <= tol

    def fmt_bb(bb):
        return f"[{bb.xmin:.3f}, {bb.ymin:.3f}, {bb.zmin:.3f}]..[{bb.xmax:.3f}, {bb.ymax:.3f}, {bb.zmax:.3f}]"

    def radial_xz(v):
        return math.hypot(v.x, v.z)

    def face_key(f):
        return f.wrapped.HashCode(1000003)

    def edge_key(e):
        return e.wrapped.HashCode(1000003)

    def inner_wires(face):
        ws = face.Wires()
        if not ws:
            return []
        ow = face.outerWire()
        ok = ow.wrapped.HashCode(1000003)
        return [w for w in ws if w.wrapped.HashCode(1000003) != ok]

    # Given hex (circumradius=10.5) at required angles
    R = 10.5
    verts_xz = [
        (9.093267, 5.25),
        (0.0, 10.5),
        (-9.093267, 5.25),
        (-9.093267, -5.25),
        (0.0, -10.5),
        (9.093267, -5.25),
    ]

    y_top = 3.175
    y_bot = -4.625

    across_corners = 2 * R
    across_flats = 2 * R * math.cos(math.radians(30))
    print("NAMED NUMBERS:")
    print(f"  y_top={y_top}  y_bot={y_bot}  circumR={R}")
    print(f"  hex verts_xz={verts_xz}")
    print(f"  vertex angles deg=[30, 90, 150, 210, 270, 330]")
    print(f"  across corners={across_corners:.6f}  across flats={across_flats:.6f}")

    def make_hex_wire_at_y(yval):
        pts = [cq.Vector(x, yval, z) for (x, z) in verts_xz]
        pts.append(cq.Vector(verts_xz[0][0], yval, verts_xz[0][1]))
        return cq.Wire.makePolygon(pts, close=True)

    def find_mouth_face(comp_solid, yval, ny_sign):
        cands = []
        for f in comp_solid.Faces():
            if f.geomType() != "PLANE":
                continue
            c = f.Center()
            if not near(c.y, yval, 1e-3):
                continue
            n = f.normalAt()
            if n.y * ny_sign <= 0.9:
                continue
            cands.append(f)
        print(f"SELECTED: {len(cands)} planar faces near Y={yval} with normal sign {ny_sign} (mouth candidates)")
        if not cands:
            return None
        # choose the one with the biggest inner-loop edge count (expected 192)
        def score(face):
            inn = inner_wires(face)
            if not inn:
                return 0
            return max(len(w.Edges()) for w in inn)
        cands.sort(key=score, reverse=True)
        top = cands[0]
        inn = inner_wires(top)
        best = max((len(w.Edges()) for w in inn), default=0)
        print(f"  MOUTH PICK: center={top.Center().toTuple()}  inner_max_edges={best}")
        return top

    # -------------------------
    # Snapshot & separate solids by invariants (no ordering reliance)
    # -------------------------
    base = shape.val() if hasattr(shape, "val") else shape
    sols = list(base.Solids())
    print(f"INPUT: solids={len(sols)}")

    compact_target_vol = 2162.777
    compact_target_bb = (-15.75, -4.625, -15.75, 15.75, 3.175, 15.75)
    protected_target_vol = 27615.571
    protected_target_bb = (-56.511, -6.35, -56.604, 56.511, 3.175, 56.234)

    def bb_tuple(bb):
        return (bb.xmin, bb.ymin, bb.zmin, bb.xmax, bb.ymax, bb.zmax)

    def bb_match(bb, tgt, tol=1e-3):
        t = bb_tuple(bb)
        return all(abs(t[i] - tgt[i]) <= tol for i in range(6))

    compact = None
    protected = None

    for i, s in enumerate(sols):
        v = s.Volume()
        bb = s.BoundingBox()
        print(f"  SOLID[{i}] vol={v:.3f}  bbox={fmt_bb(bb)}")

    # identify by BOTH volume and bbox
    for s in sols:
        v = s.Volume()
        bb = s.BoundingBox()
        if (abs(v - compact_target_vol) < 1e-2) and bb_match(bb, compact_target_bb, tol=1e-3):
            compact = s
        if (abs(v - protected_target_vol) < 1e-2) and bb_match(bb, protected_target_bb, tol=1e-3):
            protected = s

    print(f"SELECTED: {1 if compact else 0} solids for compact target (vol={compact_target_vol}, bbox={compact_target_bb})")
    print(f"SELECTED: {1 if protected else 0} solids for protected target (vol={protected_target_vol}, bbox={protected_target_bb})")

    if compact is None or protected is None:
        print("FAILED: could not uniquely identify both solids by invariants; returning input unchanged")
        return shape

    # Additional distinguishing check: compact has full exterior cylinder r=15.75 about Y
    cyl_ok = False
    for f in compact.Faces():
        if f.geomType() != "CYLINDER":
            continue
        try:
            cyl = f._geomAdaptor().Cylinder()
            r = cyl.Radius()
        except Exception:
            continue
        if abs(r - 15.75) < 1e-3:
            # heuristically treat as full wrap by having only 1 such face and big area
            if f.Area() > 100.0:
                cyl_ok = True
                print(f"CHECK: compact exterior cylinder candidate r={r:.6f} area={f.Area():.3f} center={f.Center().toTuple()}")
                break
    print(f"CHECK: compact has exterior full cylinder r=15.75 about Y -> {cyl_ok}")

    # Freeze protected solid as untouched original copy (no ops applied)
    protected_orig = protected

    comp_pre_v = compact.Volume()
    comp_pre_bb = compact.BoundingBox()
    prot_pre_v = protected_orig.Volume()
    prot_pre_bb = protected_orig.BoundingBox()

    # -------------------------
    # Identify the flower mouths (192-edge inner loops) on compact
    # -------------------------
    top_mouth = find_mouth_face(compact, y_top, +1)
    bot_mouth = find_mouth_face(compact, y_bot, -1)

    if top_mouth is None or bot_mouth is None:
        print("FAILED: could not find both mouth faces on compact; returning input unchanged")
        return shape

    def get_192_edge_inner_wire(face, tag):
        inn = inner_wires(face)
        sizes = [len(w.Edges()) for w in inn]
        print(f"CHECK: {tag} inner wire edge counts={sizes}")
        for w in inn:
            if len(w.Edges()) == 192:
                return w
        # fallback: largest
        if inn:
            return max(inn, key=lambda w: len(w.Edges()))
        return None

    top_wire = get_192_edge_inner_wire(top_mouth, "+Y mouth")
    bot_wire = get_192_edge_inner_wire(bot_mouth, "-Y mouth")

    if top_wire is None or bot_wire is None:
        print("FAILED: mouth face missing inner wire; returning input unchanged")
        return shape

    top_edges = top_wire.Edges()
    bot_edges = bot_wire.Edges()
    seed_edgekeys = set(edge_key(e) for e in top_edges + bot_edges)
    print(f"SELECTED: {len(seed_edgekeys)} unique edges as flower-mouth loop seeds (expect 192*2 with shared? got unique)")

    # -------------------------
    # Build face adjacency within compact, and find connected cavity faces near the center
    # -------------------------
    comp_faces = list(compact.Faces())
    fk_list = [face_key(f) for f in comp_faces]
    face_by_fk = {fk: f for fk, f in zip(fk_list, comp_faces)}

    edge_to_fks = {}
    for f in comp_faces:
        fk = face_key(f)
        for e in f.Edges():
            ek = edge_key(e)
            edge_to_fks.setdefault(ek, set()).add(fk)

    seed_fks = set()
    for ek in seed_edgekeys:
        seed_fks |= edge_to_fks.get(ek, set())
    print(f"SELECTED: {len(seed_fks)} faces incident to the mouth-loop edges (seed faces)")

    # BFS, but only traverse via edges whose radial center is within cavity region,
    # to avoid leaking to exterior faces.
    r_limit = 13.0
    visited = set()
    q = list(seed_fks)
    while q:
        fk = q.pop()
        if fk in visited:
            continue
        visited.add(fk)
        f = face_by_fk[fk]
        for e in f.Edges():
            c = e.Center()
            if radial_xz(c) > r_limit:
                continue
            ek = edge_key(e)
            for afk in edge_to_fks.get(ek, []):
                if afk not in visited:
                    q.append(afk)

    component_fks = visited
    print(f"SELECTED: {len(component_fks)} faces in connected cavity component (radial-limited BFS, r<{r_limit})")

    # Split into trim faces (mouth/partition planes) and removable cavity-wall faces
    trim_fks = set()
    remove_fks = set()
    for fk in component_fks:
        f = face_by_fk[fk]
        if f.geomType() == "PLANE":
            n = f.normalAt()
            if abs(n.y) > 0.9:
                trim_fks.add(fk)
                continue
        remove_fks.add(fk)

    print(f"SELECTED: {len(trim_fks)} faces for retrim-with-hex (horizontal mouth/partition faces)")
    print(f"SELECTED: {len(remove_fks)} faces to remove (cavity-wall connected face set)")

    # Report trim face levels
    trim_ys = sorted({round(face_by_fk[fk].Center().y, 6) for fk in trim_fks})
    print(f"CHECK: trim face Y-levels (seams) = {trim_ys}")

    # Ensure y span endpoints present for wall construction
    if not any(near(y, y_bot, 1e-6) for y in trim_ys):
        trim_ys = [y_bot] + trim_ys
    if not any(near(y, y_top, 1e-6) for y in trim_ys):
        trim_ys = trim_ys + [y_top]
    trim_ys = sorted({round(y, 6) for y in trim_ys})

    print(f"CHECK: final wall seam Y-levels used = {trim_ys}")

    # -------------------------
    # Build replacement (retrimmed) faces with the same outer wire but new hex inner wire
    # -------------------------
    replacement_faces = []
    for fk in trim_fks:
        f = face_by_fk[fk]
        yv = f.Center().y
        ow = f.outerWire()
        hexw = make_hex_wire_at_y(yv)
        try:
            nf = cq.Face.makeFromWires(ow, [hexw])
            replacement_faces.append(nf)
            inn_old = inner_wires(f)
            inn_old_max = max((len(w.Edges()) for w in inn_old), default=0)
            print(
                f"RETRIM: face_y={yv:.6f} geom=PLANE nY={f.normalAt().y:.3f} "
                f"old_inner_max_edges={inn_old_max} -> new_inner_edges={len(hexw.Edges())}"
            )
        except Exception as ex:
            print(f"FAILED: retrim face at y={yv} : {ex}")
            return shape

    print(f"SELECTED: {len(replacement_faces)} replacement planar faces created")

    # -------------------------
    # Build the six planar wall faces, split at seam Y-levels
    # -------------------------
    new_wall_faces = []
    for j in range(len(trim_ys) - 1):
        y0 = float(trim_ys[j])
        y1 = float(trim_ys[j + 1])
        if near(y0, y1, 1e-9):
            continue
        for i in range(6):
            (x0, z0) = verts_xz[i]
            (x1, z1) = verts_xz[(i + 1) % 6]
            pts = [
                cq.Vector(x0, y0, z0),
                cq.Vector(x1, y0, z1),
                cq.Vector(x1, y1, z1),
                cq.Vector(x0, y1, z0),
                cq.Vector(x0, y0, z0),
            ]
            w = cq.Wire.makePolygon(pts, close=True)
            try:
                wf = cq.Face.makeFromWires(w)
                new_wall_faces.append(wf)
            except Exception as ex:
                print(f"FAILED: wall face side={i} y={y0}..{y1} : {ex}")
                return shape

    print(f"SELECTED: {len(new_wall_faces)} new planar wall faces (expected 6*(nSeams-1)={6*(len(trim_ys)-1)})")

    # Placement self-check for walls: bbox and y span
    try:
        wall_comp = cq.Compound.makeCompound(new_wall_faces)
        wbb = wall_comp.BoundingBox()
        print(f"CHECK: new walls bbox={fmt_bb(wbb)}")
        print(f"CHECK: new walls y-span {wbb.ymin:.6f}..{wbb.ymax:.6f} (expected {y_bot}..{y_top})")
    except Exception as ex:
        print(f"WARN: wall bbox check failed: {ex}")

    # -------------------------
    # Keep untouched faces from compact (exclude removed cavity walls and trim faces)
    # -------------------------
    remove_all = set(remove_fks) | set(trim_fks)
    kept_faces = [face_by_fk[fk] for fk in fk_list if fk not in remove_all]
    print(f"SELECTED: {len(kept_faces)} untouched faces kept from compact solid")

    if len(kept_faces) == 0:
        print("FAILED: kept_faces=0 (would rebuild entire body); returning input unchanged")
        return shape

    # -------------------------
    # Sew into one shell and make a closed solid (NO booleans)
    # -------------------------
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Sewing, BRepBuilderAPI_MakeSolid
    from OCP.BRepLib import BRepLib

    sew = BRepBuilderAPI_Sewing(1e-6)
    for f in kept_faces:
        sew.Add(f.wrapped)
    for f in replacement_faces:
        sew.Add(f.wrapped)
    for f in new_wall_faces:
        sew.Add(f.wrapped)

    sew.Perform()
    sewed = sew.SewedShape()
    sewed_cq = cq.Shape.cast(sewed)

    shells = list(sewed_cq.Shells())
    print(f"CHECK: sewing produced shells={len(shells)}")
    if len(shells) != 1:
        print("FAILED: sewing did not yield exactly one shell; returning input unchanged")
        return shape

    mk = BRepBuilderAPI_MakeSolid(shells[0].wrapped)
    if not mk.IsDone():
        print("FAILED: MakeSolid not done; returning input unchanged")
        return shape

    new_s0_topo = mk.Solid()

    # Orient for correct outward normals
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
    print(f"  s0 pre  vol={comp_pre_v:.3f}  bbox={fmt_bb(comp_pre_bb)}")
    print(f"  s0 post vol={comp_post_v:.3f}  bbox={fmt_bb(comp_post_bb)}")
    print(f"  s1 pre  vol={prot_pre_v:.3f}  bbox={fmt_bb(prot_pre_bb)}")
    print(f"  s1 post vol={prot_post_v:.3f}  bbox={fmt_bb(prot_post_bb)}")

    # Check outer bbox of s0 unchanged
    dbb = (
        comp_post_bb.xmin - comp_pre_bb.xmin,
        comp_post_bb.ymin - comp_pre_bb.ymin,
        comp_post_bb.zmin - comp_pre_bb.zmin,
        comp_post_bb.xmax - comp_pre_bb.xmax,
        comp_post_bb.ymax - comp_pre_bb.ymax,
        comp_post_bb.zmax - comp_pre_bb.zmax,
    )
    print(f"CHECK: s0 bbox delta (xmin,ymin,zmin,xmax,ymax,zmax) = {[round(d, 6) for d in dbb]}")

    # Confirm both mouths contain a single sharp hex opening: find planar faces at Y=3.175 and -4.625 and inspect inner-wire edge count
    def find_plane_face_on_new_s0(yval, ny_sign):
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

    top_faces = find_plane_face_on_new_s0(y_top, +1)
    bot_faces = find_plane_face_on_new_s0(y_bot, -1)
    print(f"SELECTED: {len(top_faces)} planar faces at +Y mouth plane Y={y_top}")
    print(f"SELECTED: {len(bot_faces)} planar faces at -Y mouth plane Y={y_bot}")

    def report_hex_mouth(f, tag):
        inners = inner_wires(f)
        if not inners:
            print(f"CHECK: {tag} has 0 inner wires (ERROR: capped)")
            return
        # choose the biggest inner wire
        w = max(inners, key=lambda wi: len(wi.Edges()))
        print(f"CHECK: {tag} inner wire edges={len(w.Edges())} (expected 6 for sharp hex, not 192 flower)")

    if top_faces:
        report_hex_mouth(top_faces[0], "+Y mouth")
    if bot_faces:
        report_hex_mouth(bot_faces[0], "-Y mouth")

    print("OPENING center check: expected X=0,Z=0")
    cx = sum(x for (x, z) in verts_xz) / 6.0
    cz = sum(z for (x, z) in verts_xz) / 6.0
    print(f"  computed hex vertex-average center = ({cx:.6f}, {cz:.6f})")
    print(f"  Y span of walls expected {y_bot}..{y_top} (len {y_top - y_bot:.6f})")

    # -------------------------
    # Output: compound with edited compact first (s0), protected second (s1), no boolean
    # -------------------------
    out = cq.Compound.makeCompound([new_s0, protected_orig])
    return out