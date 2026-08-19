def my_cad_function(args):
    import cadquery as cq
    import math
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_Cylinder

    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input

    def vtuple(v):
        return (float(v.x), float(v.y), float(v.z))

    def ang_xz(p, ax, az):
        return math.atan2(float(p.z) - az, float(p.x) - ax)

    def r_xz(p, ax, az):
        dx = float(p.x) - ax
        dz = float(p.z) - az
        return math.hypot(dx, dz)

    def rot_y_vec(p, a, ax, az):
        dx = float(p.x) - ax
        dz = float(p.z) - az
        ca = math.cos(a)
        sa = math.sin(a)
        return cq.Vector(ax + dx * ca - dz * sa, float(p.y), az + dx * sa + dz * ca)

    def line_intersect_2d(P, v, O, d):
        # P + t v intersects O + s d in XZ
        vx, vz = v
        dx, dz = d
        px, pz = P
        ox, oz = O
        det = vx * (-dz) - vz * (-dx)
        if abs(det) < 1e-12:
            return None
        rhsx = ox - px
        rhsz = oz - pz
        t = (rhsx * (-dz) - rhsz * (-dx)) / det
        return cq.Vector(px + t * vx, 0.0, pz + t * vz)

    def dist_point_to_seg_2d(O, A, B):
        # O, A, B are (x,z)
        ox, oz = O
        ax, az = A
        bx, bz = B
        abx = bx - ax
        abz = bz - az
        aox = ox - ax
        aoz = oz - az
        denom = abx * abx + abz * abz
        if denom < 1e-18:
            return math.hypot(ox - ax, oz - az)
        t = (aox * abx + aoz * abz) / denom
        t = max(0.0, min(1.0, t))
        px = ax + t * abx
        pz = az + t * abz
        return math.hypot(ox - px, oz - pz)

    def cyl_radius_axis(face):
        ad = BRepAdaptor_Surface(face.wrapped)
        if ad.GetType() != GeomAbs_Cylinder:
            return None
        cyl = ad.Cylinder()
        r = float(cyl.Radius())
        axdir = cyl.Axis().Direction()
        axis = (float(axdir.X()), float(axdir.Y()), float(axdir.Z()))
        loc = cyl.Location()
        center = (float(loc.X()), float(loc.Y()), float(loc.Z()))
        return r, axis, center

    base = shape.val() if hasattr(shape, "val") else shape
    sols = base.Solids()
    print(f"SELECTED: {len(sols)} solids in STEP")
    if len(sols) != 2:
        print("ERROR: expected 2 solids; returning input")
        return shape

    s0 = sols[0]
    s1 = sols[1]
    print(f"SELECTED: s1 as solid index 1 vol={s1.Volume():.3f} (s0 index 0 vol={s0.Volume():.3f})")

    faces_all = base.Faces()
    print(f"INFO: base has {len(faces_all)} faces")

    # Resolve the named reference faces (must match index centers)
    f_y0 = faces_all[778]
    f_yb = faces_all[941]
    f_axis = faces_all[669]
    print(f"RESOLVED face#778 (s1 Y=0 rim plane) center={vtuple(f_y0.Center())}")
    print(f"RESOLVED face#941 (s1 Y=-3.175 rim plane) center={vtuple(f_yb.Center())}")
    print(f"RESOLVED face#669 (s1 r=15.8256 full cylinder) center={vtuple(f_axis.Center())}")

    # Use axis center and measured Y axis (from face#669 center; index says axis=[0,1,0])
    ax = float(f_axis.Center().x)
    az = float(f_axis.Center().z)
    axis_xz = (ax, az)
    print(f"AXIS: using Y-axis through xz={axis_xz} (from face#669 center)")

    # --- Reconfirm target cylindrical face families by adjacency to outer loop of rim planes ---
    # Outer loop edges of the planar rim faces at Y=0 and Y=-3.175
    w0 = f_y0.outerWire()
    wb = f_yb.outerWire()
    e0 = w0.Edges()
    eb = wb.Edges()
    e0_h = set([ed.hashCode() for ed in e0])
    eb_h = set([ed.hashCode() for ed in eb])
    print(f"SELECTED: {len(e0)} edges on outerWire of face#778 (Y=0)")
    print(f"SELECTED: {len(eb)} edges on outerWire of face#941 (Y=-3.175)")

    # Find all cylindrical faces in s1 with r≈1.9844 and r≈3.9942, axis ~Y, and adjacent to either outer loop
    s1_faces = s1.Faces()
    # map face hash to base-face index for reporting
    face_idx_by_hash = {faces_all[i].hashCode(): i for i in range(len(faces_all))}

    def is_adjacent_to_outerloop(face):
        for ed in face.Edges():
            h = ed.hashCode()
            if (h in e0_h) or (h in eb_h):
                return True
        return False

    r_small = 1.9844
    r_large = 3.9942
    tol_r = 0.01
    tol_axis = 1e-2

    cand_small = []
    cand_large = []
    for f in s1_faces:
        info = cyl_radius_axis(f)
        if info is None:
            continue
        r, axis_dir, _c = info
        if abs(abs(axis_dir[1]) - 1.0) > tol_axis:
            continue
        if abs(r - r_small) <= tol_r:
            cand_small.append(f)
        elif abs(r - r_large) <= tol_r:
            cand_large.append(f)

    print(f"SELECTED: {len(cand_small)} s1 cylindrical faces with r≈{r_small} (pre-adjacency)")
    print(f"SELECTED: {len(cand_large)} s1 cylindrical faces with r≈{r_large} (pre-adjacency)")

    adj_small = [f for f in cand_small if is_adjacent_to_outerloop(f)]
    adj_large = [f for f in cand_large if is_adjacent_to_outerloop(f)]

    adj_small_idx = sorted([face_idx_by_hash.get(f.hashCode(), -1) for f in adj_small])
    adj_large_idx = sorted([face_idx_by_hash.get(f.hashCode(), -1) for f in adj_large])
    print(f"SELECTED: {len(adj_small)} r≈{r_small} cylindrical faces adjacent to rim outer loops; idx(sample)={adj_small_idx[:8]}")
    print(f"SELECTED: {len(adj_large)} r≈{r_large} cylindrical faces adjacent to rim outer loops; idx(sample)={adj_large_idx[:8]}")

    # --- Read the reference tooth directly from Y=0 rim outer loop (no section op) ---
    # Sample points densely from the outer wire edges.
    samples = []
    max_per_edge = []
    for i, ed in enumerate(e0):
        try:
            pts = ed.discretize(80)
        except Exception:
            try:
                pts = ed.discretize(30)
            except Exception as ex:
                print(f"WARN: edge discretize failed idx={i} type={ed.geomType()} err={ex}")
                continue
        # take max radius point on this edge
        rmax = -1.0
        pmax = None
        for p in pts:
            rr = r_xz(p, ax, az)
            samples.append(p)
            if rr > rmax:
                rmax = rr
                pmax = p
        if pmax is not None:
            max_per_edge.append((rmax, i, ed, pmax))

    print(f"SELECTED: {len(samples)} sample points from Y=0 outer wire (raw, with duplicates)")
    if len(samples) < 100 or not max_per_edge:
        print("ERROR: insufficient samples to identify perimeter; returning input unchanged")
        return shape

    # Identify the edge containing the global radial maximum (a tooth tip arc)
    max_per_edge.sort(key=lambda t: (-t[0], t[1]))
    r_tip, tip_edge_i, tip_edge, p_tip = max_per_edge[0]
    th_tip = ang_xz(p_tip, ax, az)
    print(f"MEASURED: tip edge idx={tip_edge_i} type={tip_edge.geomType()} r_tip={r_tip:.4f} p_tip={vtuple(p_tip)} theta_tip={math.degrees(th_tip):.6f}deg")

    # Transition angles = endpoints of the tip edge (where rounded tip joins adjacent perimeter faces)
    sp = tip_edge.startPoint()
    ep = tip_edge.endPoint()
    th_s = ang_xz(sp, ax, az)
    th_e = ang_xz(ep, ax, az)
    # choose left/right by signed delta around th_tip
    def dtheta(th):
        return ((th - th_tip + math.pi) % (2 * math.pi)) - math.pi
    ds = dtheta(th_s)
    de = dtheta(th_e)
    if ds <= de:
        thL = th_tip + ds
        thR = th_tip + de
        p_trL = sp
        p_trR = ep
    else:
        thL = th_tip + de
        thR = th_tip + ds
        p_trL = ep
        p_trR = sp

    print(f"MEASURED: transition endpoints pL={vtuple(p_trL)} thetaL={math.degrees(thL):.6f}deg; pR={vtuple(p_trR)} thetaR={math.degrees(thR):.6f}deg")

    tooth_count = 27
    pitch = 2 * math.pi / tooth_count
    pitch_deg = 360.0 / tooth_count
    y_top = 0.0
    y_bot = -3.175
    eps = 0.02
    print(f"NUMBERS: tooth_count={tooth_count}, pitch_deg={pitch_deg}, y_top={y_top}, y_bot={y_bot}, eps={eps}")

    # Neighboring root minima: min radius in each half-pitch window around tip
    left = []
    right = []
    for p in samples:
        th = ang_xz(p, ax, az)
        dd = ((th - th_tip + math.pi) % (2 * math.pi)) - math.pi
        rr = r_xz(p, ax, az)
        if -0.5 * pitch <= dd < 0.0:
            left.append((rr, dd, p))
        elif 0.0 < dd <= 0.5 * pitch:
            right.append((rr, dd, p))

    print(f"SELECTED: {len(left)} sample points in left half-pitch window")
    print(f"SELECTED: {len(right)} sample points in right half-pitch window")
    if not left or not right:
        print("ERROR: cannot locate root minima; returning input unchanged")
        return shape

    r_rootL, ddL, p_rootL = min(left, key=lambda t: t[0])
    r_rootR, ddR, p_rootR = min(right, key=lambda t: t[0])
    th_rootL = th_tip + ddL
    th_rootR = th_tip + ddR
    print(f"MEASURED: rootL r={r_rootL:.4f} p={vtuple(p_rootL)} theta={math.degrees(th_rootL):.6f}deg")
    print(f"MEASURED: rootR r={r_rootR:.4f} p={vtuple(p_rootR)} theta={math.degrees(th_rootR):.6f}deg")

    # Construct tangent straight tip line at p_tip (tangent direction perpendicular to radial vector)
    rx = float(p_tip.x) - ax
    rz = float(p_tip.z) - az
    rlen = math.hypot(rx, rz)
    if rlen < 1e-9:
        print("ERROR: tip radial vector too small; returning input unchanged")
        return shape
    tx = -rz / rlen
    tz = rx / rlen

    # Intersect this tangent line with rays at transition angles thL, thR
    P2 = (float(p_tip.x), float(p_tip.z))
    v2 = (tx, tz)
    O2 = (ax, az)
    dL = (math.cos(thL), math.sin(thL))
    dR = (math.cos(thR), math.sin(thR))
    iL = line_intersect_2d(P2, v2, O2, dL)
    iR = line_intersect_2d(P2, v2, O2, dR)
    if iL is None or iR is None:
        print("ERROR: tip tangent/ray intersection failed; returning input unchanged")
        return shape

    p_tipL = cq.Vector(float(iL.x), y_top, float(iL.z))
    p_tipR = cq.Vector(float(iR.x), y_top, float(iR.z))
    print(f"CONSTRUCTED: tip tangent dir=({tx:.6f},{tz:.6f})")
    print(f"CONSTRUCTED: tip endpoints p_tipL={vtuple(p_tipL)} p_tipR={vtuple(p_tipR)}")

    # Build 27-tooth outer polygon (XZ), rotating reference geometry
    pts_outer = []
    for i in range(tooth_count):
        a = i * pitch
        rL = rot_y_vec(cq.Vector(float(p_rootL.x), y_top, float(p_rootL.z)), a, ax, az)
        tL = rot_y_vec(p_tipL, a, ax, az)
        tR = rot_y_vec(p_tipR, a, ax, az)
        rR = rot_y_vec(cq.Vector(float(p_rootR.x), y_top, float(p_rootR.z)), a, ax, az)
        pts_outer.extend([rL, tL, tR, rR])

    # Compute core radius from min distance origin->each root chord (rR_i to rL_{i+1})
    root_chord_dists = []
    for i in range(tooth_count):
        rR_i = pts_outer[4 * i + 3]
        rL_next = pts_outer[4 * ((i + 1) % tooth_count) + 0]
        dseg = dist_point_to_seg_2d(axis_xz, (float(rR_i.x), float(rR_i.z)), (float(rL_next.x), float(rL_next.z)))
        root_chord_dists.append(dseg)

    core_r_raw = min(root_chord_dists)
    core_margin = 0.20
    core_r = max(0.1, core_r_raw - core_margin)
    print(f"MEASURED: core_r_raw(min dist origin->rootChord)={core_r_raw:.4f} -> core_r={core_r:.4f} (margin {core_margin})")

    # --- Replace only within slab y=[-3.175, 0] ---
    y0 = y_bot - eps
    y1 = y_top + eps
    h = y1 - y0
    print(f"BUILD: slab replacement extent y0={y0} to y1={y1} (h={h})")

    # Build outer polygon wire at y=y0
    poly3d = [cq.Vector(float(p.x), y0, float(p.z)) for p in pts_outer]
    # close
    poly3d.append(cq.Vector(float(pts_outer[0].x), y0, float(pts_outer[0].z)))

    try:
        outer_wire = cq.Wire.makePolygon(poly3d)
    except Exception as ex:
        print(f"ERROR: failed to make outer polygon wire: {ex}; returning input unchanged")
        return shape

    try:
        inner_wire = cq.Wire.makeCircle(core_r, cq.Vector(ax, y0, az), cq.Vector(0, 1, 0))
    except Exception as ex:
        print(f"ERROR: failed to make inner core circle wire: {ex}; returning input unchanged")
        return shape

    try:
        ann_face = cq.Face.makeFromWires(outer_wire, [inner_wire])
    except Exception as ex:
        print(f"ERROR: failed to make annulus face from wires: {ex}; returning input unchanged")
        return shape

    new_rim = cq.Solid.extrudeLinear(ann_face, cq.Vector(0, h, 0))

    # Remove existing s1 material outside core_r only within the slab
    bigR = 120.0
    wp_cyl = cq.Workplane(cq.Plane(origin=(ax, y0, az), normal=(0, 1, 0), xDir=(1, 0, 0)))
    big_cyl = wp_cyl.circle(bigR).extrude(h).val()
    inner_cyl = wp_cyl.circle(core_r).extrude(h).val()
    cut_annulus = big_cyl.cut(inner_cyl)

    # Apply cut then fuse
    s1_cut = s1.cut(cut_annulus)

    # --- Placement self-check: removed/added ---
    removed = s1.intersect(cut_annulus)
    try:
        bb_rem = removed.BoundingBox()
        print(f"SELF-CHECK removed bbox x[{bb_rem.xmin:.3f},{bb_rem.xmax:.3f}] y[{bb_rem.ymin:.3f},{bb_rem.ymax:.3f}] z[{bb_rem.zmin:.3f},{bb_rem.zmax:.3f}]")
        print(f"SELF-CHECK removed y-extent vs target: ymin={bb_rem.ymin:.4f} (target {y_bot}), ymax={bb_rem.ymax:.4f} (target {y_top})")
    except Exception as ex:
        print(f"SELF-CHECK removed: no bbox ({ex})")

    added = new_rim.cut(s1_cut)
    try:
        bb_add = added.BoundingBox()
        print(f"SELF-CHECK added bbox x[{bb_add.xmin:.3f},{bb_add.xmax:.3f}] y[{bb_add.ymin:.3f},{bb_add.ymax:.3f}] z[{bb_add.zmin:.3f},{bb_add.zmax:.3f}]")
        print(f"SELF-CHECK added center={vtuple(added.Center())}")
        print(f"SELF-CHECK added y-extent vs target: ymin={bb_add.ymin:.4f} (target {y_bot}), ymax={bb_add.ymax:.4f} (target {y_top})")
    except Exception as ex:
        print(f"SELF-CHECK added: no bbox ({ex})")

    edited_s1 = s1_cut.fuse(new_rim)

    # Post-check: count remaining rounded perimeter cylindrical faces (r=1.9844 and r=3.9942) that overlap slab
    remaining_small = 0
    remaining_large = 0
    for f in edited_s1.Faces():
        info = cyl_radius_axis(f)
        if info is None:
            continue
        r, axis_dir, _c = info
        if abs(abs(axis_dir[1]) - 1.0) > tol_axis:
            continue
        bb = f.BoundingBox()
        overlaps_slab = (bb.ymax >= y_bot - 1e-6) and (bb.ymin <= y_top + 1e-6)
        if not overlaps_slab:
            continue
        if abs(r - r_small) <= tol_r:
            remaining_small += 1
        if abs(r - r_large) <= tol_r:
            remaining_large += 1
    print(f"CONFIRM: remaining cylindrical perimeter faces in slab: r=1.9844 -> {remaining_small}, r=3.9942 -> {remaining_large} (target 0,0)")

    # Post-check: count candidate straight tooth faces (planar, vertical, in outer rim region, slab-located)
    # Heuristic: planar faces with |normal.y|~0, center radius > core_r+1, and center y in slab
    straight_outer = 0
    for f in edited_s1.Faces():
        if f.geomType() != "PLANE":
            continue
        n = f.normalAt()
        if abs(float(n.y)) > 0.03:
            continue
        c = f.Center()
        rr = r_xz(c, ax, az)
        if rr < core_r + 1.0:
            continue
        if float(c.y) < y_bot - 0.5 or float(c.y) > y_top + 0.5:
            continue
        straight_outer += 1
    print(f"CONFIRM(heuristic): planar vertical outer faces in slab region counted={straight_outer} (expected 81 for 27 tips + 54 flanks; other nearby faces may inflate this count)")

    # Recompound, leaving the other solid byte-identical
    out = cq.Compound.makeCompound([s0, edited_s1])
    try:
        bb_out = out.BoundingBox()
        print(f"RESULT bbox x[{bb_out.xmin:.3f},{bb_out.xmax:.3f}] y[{bb_out.ymin:.3f},{bb_out.ymax:.3f}] z[{bb_out.zmin:.3f},{bb_out.zmax:.3f}]")
        print(f"RESULT volumes: s0={s0.Volume():.3f}, edited_s1={edited_s1.Volume():.3f}, total={out.Volume():.3f}")
    except Exception:
        pass

    return out