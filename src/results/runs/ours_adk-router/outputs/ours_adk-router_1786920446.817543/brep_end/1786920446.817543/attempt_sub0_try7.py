def my_cad_function(args):
    import cadquery as cq
    import math

    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input
    base = shape.val() if hasattr(shape, "val") else shape

    # ---------- helpers ----------
    def vtuple(v):
        return (float(v.x), float(v.y), float(v.z))

    def ang_norm(a):
        # to [0, 2pi)
        tw = 2.0 * math.pi
        a = a % tw
        if a < 0:
            a += tw
        return a

    def unwrap_about(theta, theta0):
        # map theta to be within +-pi of theta0, return continuous around theta0
        tw = 2.0 * math.pi
        d = (theta - theta0 + math.pi) % tw - math.pi
        return theta0 + d

    def rotate_y(p, angle_rad, axis_xz):
        ax, az = axis_xz
        dx = p.x - ax
        dz = p.z - az
        ca = math.cos(angle_rad)
        sa = math.sin(angle_rad)
        x2 = ax + ca * dx - sa * dz
        z2 = az + sa * dx + ca * dz
        return cq.Vector(x2, p.y, z2)

    def line_intersect_2d(P, v, O, d):
        # Solve P + s*v = O + t*d in XZ plane; return intersection point (Vector)
        vx, vz = v
        dx, dz = d
        wx = O[0] - P[0]
        wz = O[1] - P[1]
        det = vx * (-dz) - vz * (-dx)
        if abs(det) < 1e-9:
            return None
        s = (wx * (-dz) - wz * (-dx)) / det
        ix = P[0] + s * vx
        iz = P[1] + s * vz
        return cq.Vector(ix, 0.0, iz)

    def dist_point_to_seg_2d(O, A, B):
        # distances in XZ plane
        ox, oz = O
        ax, az = A
        bx, bz = B
        abx = bx - ax
        abz = bz - az
        apx = ox - ax
        apz = oz - az
        ab2 = abx * abx + abz * abz
        if ab2 < 1e-12:
            return math.hypot(ox - ax, oz - az)
        t = (apx * abx + apz * abz) / ab2
        if t < 0.0:
            t = 0.0
        elif t > 1.0:
            t = 1.0
        cx = ax + t * abx
        cz = az + t * abz
        return math.hypot(ox - cx, oz - cz)

    # ---------- isolate solids; edit only s1 ----------
    solids = base.Solids()
    print(f"SELECTED: {len(solids)} solids in STEP")
    if len(solids) != 2:
        print("WARNING: expected 2 solids (s0,s1)")

    # pick s1 as the larger volume solid
    vols = [(i, float(s.Volume())) for i, s in enumerate(solids)]
    vols_sorted = sorted(vols, key=lambda t: t[1])
    s0_i, s1_i = vols_sorted[0][0], vols_sorted[-1][0]
    s0 = solids[s0_i]
    s1 = solids[s1_i]
    print(f"SELECTED: s1 as solid index {s1_i} vol={vols_sorted[-1][1]:.3f} (s0 index {s0_i} vol={vols_sorted[0][1]:.3f})")

    # ---------- resolve explicit reference faces by global face indices ----------
    faces = base.Faces()
    print(f"INFO: base has {len(faces)} faces")

    f_y0 = faces[778]
    f_ybot = faces[941]
    f_axis = faces[669]

    c_y0 = f_y0.Center()
    c_ybot = f_ybot.Center()
    c_axis = f_axis.Center()
    print(f"RESOLVED face#778 (s1 Y=0 rim plane) center={vtuple(c_y0)}")
    print(f"RESOLVED face#941 (s1 Y=-3.175 rim plane) center={vtuple(c_ybot)}")
    print(f"RESOLVED face#669 (s1 r=15.8256 full cylinder) center={vtuple(c_axis)}")

    # measured slab interval
    y_top = 0.0
    y_bot = -3.175
    slab_h = y_top - y_bot
    eps = 0.02
    print(f"NUMBERS: y_top={y_top}, y_bot={y_bot}, slab_h={slab_h}, eps={eps}, tooth_count=27, pitch_deg=13.333333")

    # Use axis from the cylinder face: center point and [0,1,0] direction.
    axis_xz = (float(c_axis.x), float(c_axis.z))
    print(f"AXIS: using Y-axis through xz={axis_xz} (from face#669 center)")

    # ---------- read outer boundary loops (no section op) ----------
    ow0 = f_y0.outerWire()
    owb = f_ybot.outerWire()
    e0 = ow0.Edges()
    eb = owb.Edges()
    print(f"SELECTED: {len(e0)} edges on outerWire of face#778 (Y=0) for perimeter sampling")
    print(f"SELECTED: {len(eb)} edges on outerWire of face#941 (Y=-3.175) for perimeter sampling")

    # ---------- discretize outer wire at Y=0 and build polar samples ----------
    pts = []
    for ed in e0:
        try:
            pts.extend(ed.discretize(40))
        except Exception:
            # fallback, sometimes OCC discretize can fail on degenerate edges
            pts.extend([v.toTuple() for v in []])
    if not pts:
        print("SELECTED: 0 sample points from Y=0 outer wire -> cannot proceed")
        return shape

    samples = []
    ax, az = axis_xz
    for p in pts:
        x = float(p.x)
        z = float(p.z)
        r = math.hypot(x - ax, z - az)
        th = ang_norm(math.atan2(z - az, x - ax))
        samples.append((th, r, cq.Vector(x, float(p.y), z)))

    # Reference tooth: find local radial maximum (global max among samples)
    th_tip, r_tip, p_tip = max(samples, key=lambda t: t[1])
    print(f"MEASURED: tip candidate (global max) r_tip={r_tip:.4f} at theta={math.degrees(th_tip):.4f}deg point={vtuple(p_tip)}")

    # Find closest outerwire edge to this tip point (by sampling distance)
    best = None
    best_d2 = 1e99
    for ed in e0:
        try:
            sp = ed.discretize(80)
        except Exception:
            continue
        for q in sp:
            dx = float(q.x - p_tip.x)
            dz = float(q.z - p_tip.z)
            d2 = dx * dx + dz * dz
            if d2 < best_d2:
                best_d2 = d2
                best = ed
    if best is None:
        print("SELECTED: 0 edges closest-to-tip -> cannot proceed")
        return shape

    vts = best.Vertices()
    if len(vts) < 2:
        print(f"ERROR: closest edge has {len(vts)} vertices")
        return shape

    pA = vts[0].Center()
    pB = vts[-1].Center()
    thA = ang_norm(math.atan2(float(pA.z) - az, float(pA.x) - ax))
    thB = ang_norm(math.atan2(float(pB.z) - az, float(pB.x) - ax))

    # unwrap endpoints around tip angle so thA_u < th_tip_u < thB_u
    th_tip_u = th_tip
    thA_u = unwrap_about(thA, th_tip_u)
    thB_u = unwrap_about(thB, th_tip_u)
    thL_u, thR_u = (thA_u, thB_u) if thA_u < thB_u else (thB_u, thA_u)
    print(f"MEASURED: tip-edge endpoints thetaL={math.degrees(thL_u):.4f}deg thetaR={math.degrees(thR_u):.4f}deg (from closest perimeter edge)")
    print(f"MEASURED: closest-edge endpoint points A={vtuple(pA)} B={vtuple(pB)} d_tip_to_edge_samp={math.sqrt(best_d2):.6f}mm")

    # Estimate neighboring root minima around the tip WITHOUT section:
    # search in windows offset from the tip (avoid the tip-edge itself)
    pitch = 2.0 * math.pi / 27.0
    left_w0 = th_tip_u - 0.85 * pitch
    left_w1 = th_tip_u - 0.25 * pitch
    right_w0 = th_tip_u + 0.25 * pitch
    right_w1 = th_tip_u + 0.85 * pitch

    # unwrap all sample thetas around tip
    samples_u = []
    for th, r, p in samples:
        thu = unwrap_about(th, th_tip_u)
        samples_u.append((thu, r, p))

    left_candidates = [t for t in samples_u if left_w0 <= t[0] <= left_w1]
    right_candidates = [t for t in samples_u if right_w0 <= t[0] <= right_w1]
    print(f"SELECTED: {len(left_candidates)} sample points in left-root window for root minimum")
    print(f"SELECTED: {len(right_candidates)} sample points in right-root window for root minimum")
    if not left_candidates or not right_candidates:
        print("ERROR: insufficient candidates to locate root minima; returning input unchanged")
        return shape

    th_rootL_u, r_rootL, p_rootL = min(left_candidates, key=lambda t: t[1])
    th_rootR_u, r_rootR, p_rootR = min(right_candidates, key=lambda t: t[1])
    print(f"MEASURED: rootL r={r_rootL:.4f} theta={math.degrees(th_rootL_u):.4f}deg p={vtuple(p_rootL)}")
    print(f"MEASURED: rootR r={r_rootR:.4f} theta={math.degrees(th_rootR_u):.4f}deg p={vtuple(p_rootR)}")

    # ---------- Construct tangent straight tip line at the max ----------
    # At a radial maximum, tangent is approximately circumferential (perp to radial vector)
    rx = float(p_tip.x) - ax
    rz = float(p_tip.z) - az
    rlen = math.hypot(rx, rz)
    if rlen < 1e-9:
        print("ERROR: tip radial vector too small")
        return shape
    tx = -rz / rlen
    tz = rx / rlen

    # Intersect tip tangent line with rays through the measured tooth-transition angles
    thL = thL_u
    thR = thR_u
    dL = (math.cos(thL), math.sin(thL))
    dR = (math.cos(thR), math.sin(thR))

    P2 = (float(p_tip.x), float(p_tip.z))
    v2 = (tx, tz)
    O2 = (ax, az)

    iL = line_intersect_2d(P2, v2, O2, dL)
    iR = line_intersect_2d(P2, v2, O2, dR)
    if iL is None or iR is None:
        print("ERROR: tip-line intersection failed (degenerate line system)")
        return shape

    # Put these 2D points back at Y=0 for reference
    p_tipL = cq.Vector(float(iL.x), 0.0, float(iL.z))
    p_tipR = cq.Vector(float(iR.x), 0.0, float(iR.z))
    print(f"CONSTRUCTED: tip line tangent dir=({tx:.6f},{tz:.6f})")
    print(f"CONSTRUCTED: tip endpoint L={vtuple(p_tipL)} at theta={math.degrees(thL):.4f}deg")
    print(f"CONSTRUCTED: tip endpoint R={vtuple(p_tipR)} at theta={math.degrees(thR):.4f}deg")

    # ---------- Build 27-tooth outer polygon (XZ), rotating reference geometry ----------
    pts_outer = []
    for i in range(27):
        a = i * pitch
        # rotate around the measured Y axis through axis_xz
        rL = rotate_y(p_rootL, a, axis_xz)
        tL = rotate_y(p_tipL, a, axis_xz)
        tR = rotate_y(p_tipR, a, axis_xz)
        rR = rotate_y(p_rootR, a, axis_xz)
        pts_outer.extend([rL, tL, tR, rR])

    # Close polygon by connecting last point to the first
    # Compute core radius as max circle inside ALL root-chord segments (between successive teeth roots)
    # Here root chords are between rR_i and rL_{i+1}
    root_chord_dists = []
    for i in range(27):
        rR_i = pts_outer[4 * i + 3]
        rL_next = pts_outer[4 * ((i + 1) % 27) + 0]
        dseg = dist_point_to_seg_2d(axis_xz, (float(rR_i.x), float(rR_i.z)), (float(rL_next.x), float(rL_next.z)))
        root_chord_dists.append(dseg)
    core_r_raw = min(root_chord_dists)
    core_margin = 0.20
    core_r = max(0.1, core_r_raw - core_margin)
    print(f"MEASURED: core_r_raw(min dist origin->rootChord)={core_r_raw:.4f} -> core_r={core_r:.4f} (margin {core_margin})")

    # ---------- Build replacement annulus in slab y=[-3.175,0] ----------
    y0 = y_bot - eps
    y1 = y_top + eps
    h = y1 - y0
    print(f"BUILD: slab replacement extent y0={y0} to y1={y1} (h={h})")

    # build outer wire polygon points at y=y0
    poly3d = []
    for p in pts_outer:
        poly3d.append(cq.Vector(float(p.x), y0, float(p.z)))
    # close
    poly3d.append(cq.Vector(float(pts_outer[0].x), y0, float(pts_outer[0].z)))

    try:
        outer_wire = cq.Wire.makePolygon(poly3d)
    except Exception as ex:
        print(f"ERROR: failed to make outer polygon wire: {ex}")
        return shape

    inner_wire = cq.Wire.makeCircle(core_r - 0.05, cq.Vector(ax, y0, az), cq.Vector(0, 1, 0))
    try:
        ann_face = cq.Face.makeFromWires(outer_wire, [inner_wire])
    except Exception as ex:
        print(f"ERROR: failed to make annulus face from wires: {ex}")
        return shape

    new_rim = cq.Solid.extrudeLinear(ann_face, cq.Vector(0, h, 0))

    # ---------- Remove existing s1 material outside core_r only within this slab ----------
    bigR = 80.0
    wp_cyl = cq.Workplane(cq.Plane(origin=(ax, y0, az), normal=(0, 1, 0), xDir=(1, 0, 0)))
    big_cyl = wp_cyl.circle(bigR).extrude(h).val()
    inner_cyl = wp_cyl.circle(core_r).extrude(h).val()
    cut_annulus = big_cyl.cut(inner_cyl)

    # apply cut then fuse
    s1_cut = s1.cut(cut_annulus)

    # self-check: removed / added
    removed = s1.intersect(cut_annulus)
    try:
        bb_rem = removed.BoundingBox()
        print(f"SELF-CHECK removed bbox x[{bb_rem.xmin:.3f},{bb_rem.xmax:.3f}] y[{bb_rem.ymin:.3f},{bb_rem.ymax:.3f}] z[{bb_rem.zmin:.3f},{bb_rem.zmax:.3f}]")
    except Exception:
        print("SELF-CHECK removed: (no bbox)")

    added = new_rim.cut(s1_cut)
    try:
        bb_add = added.BoundingBox()
        print(f"SELF-CHECK added bbox x[{bb_add.xmin:.3f},{bb_add.xmax:.3f}] y[{bb_add.ymin:.3f},{bb_add.ymax:.3f}] z[{bb_add.zmin:.3f},{bb_add.zmax:.3f}]")
        print(f"SELF-CHECK added center={vtuple(added.Center())}")
        # compare to named y-interval
        print(f"SELF-CHECK added y-extent vs target: ymin={bb_add.ymin:.4f} (target {y_bot}), ymax={bb_add.ymax:.4f} (target {y_top})")
    except Exception:
        print("SELF-CHECK added: (no bbox)")

    edited_s1 = s1_cut.fuse(new_rim)

    # Recompound, leaving the other solid byte-identical
    out = cq.Compound.makeCompound([s0, edited_s1])
    try:
        bb_out = out.BoundingBox()
        print(f"RESULT bbox x[{bb_out.xmin:.3f},{bb_out.xmax:.3f}] y[{bb_out.ymin:.3f},{bb_out.ymax:.3f}] z[{bb_out.zmin:.3f},{bb_out.zmax:.3f}]")
    except Exception:
        pass

    return out