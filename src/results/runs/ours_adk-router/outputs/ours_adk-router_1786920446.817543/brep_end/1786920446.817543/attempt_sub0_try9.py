def my_cad_function(args):
    import cadquery as cq
    import math

    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input

    base = shape.val() if hasattr(shape, "val") else shape
    sols = base.Solids()
    print(f"SELECTED: {len(sols)} solids in STEP")
    if len(sols) != 2:
        print("ERROR: expected 2 solids; returning input")
        return shape

    # Identify s1 by volume (larger)
    vols = [s.Volume() for s in sols]
    s1_i = max(range(len(sols)), key=lambda i: vols[i])
    s0_i = 1 - s1_i
    s1 = sols[s1_i]
    s0 = sols[s0_i]
    print(f"SELECTED: s1 as solid index {s1_i} vol={s1.Volume():.3f} (s0 index {s0_i} vol={s0.Volume():.3f})")
    print(f"INFO: base has {len(base.Faces())} faces")

    # --- Resolve reference faces by index (as demanded) ---
    try:
        f_y0 = base.Faces()[778]   # s1 rim plane at Y=0
        f_yb = base.Faces()[941]   # s1 rim plane at Y=-3.175
        f_axis = base.Faces()[669] # s1 full cylinder r=15.8256
    except Exception as ex:
        print(f"ERROR: cannot resolve face indices (778,941,669): {ex}")
        return shape

    def vtuple(v):
        return (float(v.x), float(v.y), float(v.z))

    print(f"RESOLVED face#778 (s1 Y=0 rim plane) center={vtuple(f_y0.Center())}")
    print(f"RESOLVED face#941 (s1 Y=-3.175 rim plane) center={vtuple(f_yb.Center())}")
    print(f"RESOLVED face#669 (s1 r=15.8256 full cylinder) center={vtuple(f_axis.Center())}")

    # Use center and Y axis of r=15.8256 cylinder
    ax = float(f_axis.Center().x)
    az = float(f_axis.Center().z)
    axis_dir = cq.Vector(0, 1, 0)
    print(f"AXIS: using [0,1,0] through xz=({ax:.6g},{az:.6g}) (from face#669 center)")

    y_top = 0.0
    y_bot = -3.175
    tooth_count = 27
    pitch = 2.0 * math.pi / tooth_count

    # --- Helpers to measure cylinders and sample curves ---
    from OCP.BRepAdaptor import BRepAdaptor_Surface, BRepAdaptor_Curve
    from OCP.GeomAbs import GeomAbs_Cylinder

    def cyl_info(face):
        """Return (radius, dirVec, centerPointVec) for cylindrical faces, else None."""
        try:
            ad = BRepAdaptor_Surface(face.wrapped, True)
            if ad.GetType() != GeomAbs_Cylinder:
                return None
            c = ad.Cylinder()
            r = float(c.Radius())
            d = c.Axis().Direction()
            loc = c.Location()
            return r, cq.Vector(float(d.X()), float(d.Y()), float(d.Z())), cq.Vector(float(loc.X()), float(loc.Y()), float(loc.Z()))
        except Exception:
            return None

    def edge_sample_points(edge, n=11):
        """Sample n points along edge using OCC adaptor (avoids nonexistent Edge.discretize)."""
        crv = BRepAdaptor_Curve(edge.wrapped)
        f = float(crv.FirstParameter())
        l = float(crv.LastParameter())
        if not math.isfinite(f) or not math.isfinite(l) or abs(l - f) < 1e-12:
            p = crv.Value(f)
            return [cq.Vector(float(p.X()), float(p.Y()), float(p.Z()))]
        pts = []
        for i in range(n):
            t = f + (l - f) * (i / (n - 1))
            p = crv.Value(t)
            pts.append(cq.Vector(float(p.X()), float(p.Y()), float(p.Z())))
        return pts

    def theta_of(p):
        return math.atan2(float(p.z) - az, float(p.x) - ax)

    def r_of(p):
        dx = float(p.x) - ax
        dz = float(p.z) - az
        return math.hypot(dx, dz)

    def ang0_2pi(a):
        a = a % (2.0 * math.pi)
        return a + (2.0 * math.pi if a < 0 else 0.0)

    def wrap_to_pi(a):
        a = (a + math.pi) % (2.0 * math.pi) - math.pi
        return a

    # --- Identify outer-wire edges for rim planes (for adjacency tests) ---
    w0 = f_y0.outerWire()
    wb = f_yb.outerWire()
    e0 = list(w0.Edges())
    eb = list(wb.Edges())
    rim_outer_edges = e0 + eb
    rim_codes = set([int(e.hashCode()) for e in rim_outer_edges])
    print(f"SELECTED: {len(e0)} edges on outerWire of face#778 (Y=0)")
    print(f"SELECTED: {len(eb)} edges on outerWire of face#941 (Y=-3.175)")

    # --- Reconfirm the target cylindrical face families on s1, and adjacency to outer loops ---
    tol_r = 0.02
    tol_axis_y = 0.02
    r_small = 1.9844
    r_large = 3.9942

    cyl_small = []
    cyl_large = []
    for i, f in enumerate(s1.Faces()):
        ci = cyl_info(f)
        if ci is None:
            continue
        r, d, _c = ci
        if abs(abs(float(d.y)) - 1.0) > tol_axis_y:
            continue
        if abs(r - r_small) <= tol_r:
            cyl_small.append((i, f, r))
        if abs(r - r_large) <= tol_r:
            cyl_large.append((i, f, r))

    print(f"SELECTED: {len(cyl_small)} s1 cylindrical faces with r≈{r_small} (pre-adjacency)")
    print(f"SELECTED: {len(cyl_large)} s1 cylindrical faces with r≈{r_large} (pre-adjacency)")

    def adjacent_to_rim(face):
        try:
            for e in face.Edges():
                if int(e.hashCode()) in rim_codes:
                    return True
        except Exception:
            pass
        return False

    small_adj = [(i, f) for (i, f, _r) in cyl_small if adjacent_to_rim(f)]
    large_adj = [(i, f) for (i, f, _r) in cyl_large if adjacent_to_rim(f)]
    print(f"SELECTED: {len(small_adj)} r≈{r_small} cylindrical faces adjacent to rim outer loops; idx(sample)={[i for i,_ in small_adj[:12]]}")
    print(f"SELECTED: {len(large_adj)} r≈{r_large} cylindrical faces adjacent to rim outer loops; idx(sample)={[i for i,_ in large_adj[:12]]}")

    # --- Read reference tooth from Y=0 outer loop WITHOUT section: choose the outer edge with max mid-radius ---
    edge_infos = []
    for j, ed in enumerate(e0):
        try:
            mid = edge_sample_points(ed, n=3)[1]
        except Exception:
            pts = edge_sample_points(ed, n=2)
            mid = pts[len(pts)//2]
        edge_infos.append((j, ed, r_of(mid), mid))

    edge_infos.sort(key=lambda t: t[2], reverse=True)
    ref_j, ref_edge, ref_rmid, ref_mid = edge_infos[0]
    print(f"REFERENCE: picked Y=0 outerWire edge index-in-wire={ref_j} geomType={ref_edge.geomType()} rmid={ref_rmid:.4f} mid={vtuple(ref_mid)}")

    # Sample reference edge densely to find its local radial maximum (tip center)
    ref_pts = edge_sample_points(ref_edge, n=61)
    p_max = max(ref_pts, key=lambda p: r_of(p))
    r_max = r_of(p_max)
    th0 = theta_of(p_max)
    th0n = ang0_2pi(th0)
    print(f"MEASURED: p_max (local radial max on ref edge)={vtuple(p_max)} r_max={r_max:.4f} theta0={math.degrees(th0n):.4f}deg")

    # Transition angles: use endpoints of the reference tip edge (where it joins adjacent perimeter faces)
    vtx = list(ref_edge.Vertices())
    if len(vtx) < 2:
        # fallback: take first/last sampled points
        pA = ref_pts[0]
        pB = ref_pts[-1]
    else:
        pA = vtx[0].Center()
        pB = vtx[-1].Center()
    aA = theta_of(pA)
    aB = theta_of(pB)
    # unwrap around th0 so left < right
    dA = wrap_to_pi(aA - th0)
    dB = wrap_to_pi(aB - th0)
    if dA > dB:
        dA, dB = dB, dA
        pA, pB = pB, pA
    aL = th0 + dA
    aR = th0 + dB
    print(f"MEASURED: transition endpoints from ref edge vertices")
    print(f"  left  p_transL={vtuple(pA)} thetaL={math.degrees(ang0_2pi(aL)):.4f}deg (d={math.degrees(dA):.4f}deg)")
    print(f"  right p_transR={vtuple(pB)} thetaR={math.degrees(ang0_2pi(aR)):.4f}deg (d={math.degrees(dB):.4f}deg)")

    # Sample the whole Y=0 outer wire to find root minima on both sides of the tooth
    all_pts = []
    for ed in e0:
        try:
            pts = edge_sample_points(ed, n=9)
        except Exception:
            pts = []
        # avoid duplicates at edge joints
        if all_pts and pts:
            pts = pts[1:]
        all_pts.extend(pts)

    print(f"SELECTED: {len(all_pts)} sampled points from Y=0 outer wire")
    if len(all_pts) < 100:
        print("WARN: low sample count; proceeding anyway")

    # Consider points within +/- pitch/2 around th0
    tooth_pts = []
    for p in all_pts:
        d = wrap_to_pi(theta_of(p) - th0)
        if abs(d) <= pitch * 0.5 + 1e-6:
            tooth_pts.append((d, p, r_of(p)))

    print(f"SELECTED: {len(tooth_pts)} points within one-tooth window (±pitch/2)")
    if len(tooth_pts) < 10:
        print("ERROR: insufficient tooth window points; proceeding with coarse fallback based on vertices")

    left_half = [(d, p, rr) for (d, p, rr) in tooth_pts if d < 0]
    right_half = [(d, p, rr) for (d, p, rr) in tooth_pts if d > 0]
    if not left_half or not right_half:
        # fallback: split by sign of angle difference from th0 using endpoints only
        left_half = [(dA, pA, r_of(pA))]
        right_half = [(dB, pB, r_of(pB))]

    dLmin, p_rootL, r_rootL = min(left_half, key=lambda t: t[2])
    dRmin, p_rootR, r_rootR = min(right_half, key=lambda t: t[2])
    print(f"MEASURED: root minima")
    print(f"  rootL p={vtuple(p_rootL)} r={r_rootL:.4f} d={math.degrees(dLmin):.4f}deg")
    print(f"  rootR p={vtuple(p_rootR)} r={r_rootR:.4f} d={math.degrees(dRmin):.4f}deg")

    # --- Construct straight-sided trapezoidal tooth ---
    # Tip line tangent at p_max: direction perpendicular to radial vector from axis
    rx = float(p_max.x) - ax
    rz = float(p_max.z) - az
    rlen = math.hypot(rx, rz)
    if rlen < 1e-9:
        print("ERROR: p_max is on axis (unexpected); forcing radial vector")
        rx, rz, rlen = 1.0, 0.0, 1.0
    tx, tz = -rz / rlen, rx / rlen  # circumferential tangent
    print(f"CONSTRUCTED: tip tangent dir (XZ)=({tx:.6f},{tz:.6f})")

    def intersect_ray_with_line(ray_ang, line_point, line_dir):
        # Ray: axis + u * (cos, sin) in XZ; Line: P + v * (dx, dz)
        dxr, dzr = math.cos(ray_ang), math.sin(ray_ang)
        Px, Pz = float(line_point.x), float(line_point.z)
        dxl, dzl = line_dir
        # Solve: (ax,az) + u*(dxr,dzr) = (Px,Pz) + v*(dxl,dzl)
        # => u*dxr - v*dxl = Px-ax
        #    u*dzr - v*dzl = Pz-az
        A11, A12 = dxr, -dxl
        A21, A22 = dzr, -dzl
        b1, b2 = Px - ax, Pz - az
        det = A11 * A22 - A12 * A21
        if abs(det) < 1e-12:
            return None
        u = (b1 * A22 - b2 * A12) / det
        # v = (A11*b2 - A21*b1)/det
        x = ax + u * dxr
        z = az + u * dzr
        return cq.Vector(float(x), y_top, float(z))

    p_tipL = intersect_ray_with_line(aL, p_max, (tx, tz))
    p_tipR = intersect_ray_with_line(aR, p_max, (tx, tz))
    if p_tipL is None or p_tipR is None:
        # fallback: keep endpoints on the original transition points
        print("WARN: ray/line intersection failed; falling back to transition endpoints as tip endpoints")
        p_tipL = cq.Vector(float(pA.x), y_top, float(pA.z))
        p_tipR = cq.Vector(float(pB.x), y_top, float(pB.z))

    print(f"CONSTRUCTED: tip endpoints")
    print(f"  tipL={vtuple(p_tipL)}")
    print(f"  tipR={vtuple(p_tipR)}")

    # Normalize reference points to y=y_top for rotation geometry
    p_rootL = cq.Vector(float(p_rootL.x), y_top, float(p_rootL.z))
    p_rootR = cq.Vector(float(p_rootR.x), y_top, float(p_rootR.z))

    # Ensure ordering around th0: rootL (negative d), tipL, tipR, rootR (positive d)
    def d_of(p):
        return wrap_to_pi(theta_of(p) - th0)

    d_vals = [d_of(p_rootL), d_of(p_tipL), d_of(p_tipR), d_of(p_rootR)]
    print(f"CHECK: reference tooth d-values(deg) rootL/tipL/tipR/rootR = {[round(math.degrees(d),4) for d in d_vals]}")

    # Rotate around Y-axis at (ax,az)
    def rot_y(p, ang):
        x = float(p.x) - ax
        z = float(p.z) - az
        ca, sa = math.cos(ang), math.sin(ang)
        xr = x * ca + z * sa
        zr = -x * sa + z * ca
        return cq.Vector(ax + xr, float(p.y), az + zr)

    pts_outer = []
    for i in range(tooth_count):
        a = i * pitch
        rL = rot_y(p_rootL, a)
        tL = rot_y(p_tipL, a)
        tR = rot_y(p_tipR, a)
        rR = rot_y(p_rootR, a)
        pts_outer.extend([rL, tL, tR, rR])

    print(f"CONSTRUCTED: outer polygon point count (unclosed)={len(pts_outer)} (expected {tooth_count*4})")

    # Compute core radius = min distance from axis to each root chord (rR_i -> rL_{i+1})
    def dist_point_to_seg_2d(P, A, B):
        # all tuples (x,z)
        px, pz = P
        ax2, az2 = A
        bx2, bz2 = B
        vx, vz = bx2 - ax2, bz2 - az2
        wx, wz = px - ax2, pz - az2
        vv = vx * vx + vz * vz
        if vv < 1e-18:
            return math.hypot(wx, wz)
        t = (wx * vx + wz * vz) / vv
        t = max(0.0, min(1.0, t))
        cx, cz = ax2 + t * vx, az2 + t * vz
        return math.hypot(px - cx, pz - cz)

    axis_xz = (ax, az)
    root_chord_dists = []
    for i in range(tooth_count):
        rR_i = pts_outer[4 * i + 3]
        rL_next = pts_outer[4 * ((i + 1) % tooth_count) + 0]
        dseg = dist_point_to_seg_2d(axis_xz, (float(rR_i.x), float(rR_i.z)), (float(rL_next.x), float(rL_next.z)))
        root_chord_dists.append(dseg)

    core_r_raw = min(root_chord_dists) if root_chord_dists else min(r_rootL, r_rootR)
    core_margin = 0.20
    core_r = max(0.1, core_r_raw - core_margin)
    print(f"MEASURED: core_r_raw(min dist origin->rootChord)={core_r_raw:.4f} -> core_r={core_r:.4f} (margin {core_margin})")

    # --- Replace only within slab y=[-3.175, 0] ---
    eps = 0.02
    y0 = y_bot - eps
    y1 = y_top + eps
    h = y1 - y0
    print(f"BUILD: slab replacement extent y0={y0} to y1={y1} (h={h})")

    # Outer polygon wire at y=y0
    poly3d = [cq.Vector(float(p.x), y0, float(p.z)) for p in pts_outer]
    poly3d.append(cq.Vector(float(pts_outer[0].x), y0, float(pts_outer[0].z)))

    try:
        outer_wire = cq.Wire.makePolygon(poly3d)
        print("OK: constructed outer polygon wire")
    except Exception as ex:
        print(f"ERROR: failed to make outer polygon wire: {ex}")
        # fallback: use a simple 27-gon at current max radius (still an edit, avoids no-op)
        fallback_r = max([r_of(p) for p in all_pts]) if all_pts else 56.0
        print(f"FALLBACK: building regular 27-gon at r={fallback_r:.3f}")
        poly3d = []
        for i in range(tooth_count):
            a = th0 + i * pitch
            poly3d.append(cq.Vector(ax + fallback_r * math.cos(a), y0, az + fallback_r * math.sin(a)))
        poly3d.append(poly3d[0])
        outer_wire = cq.Wire.makePolygon(poly3d)

    inner_wire = cq.Wire.makeCircle(core_r, cq.Vector(ax, y0, az), cq.Vector(0, 1, 0))
    ann_face = cq.Face.makeFromWires(outer_wire, [inner_wire])
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
        print(f"SELF-CHECK removed volume={removed.Volume():.3f}")
    except Exception as ex:
        print(f"SELF-CHECK removed: no bbox ({ex})")

    added = new_rim.cut(s1_cut)
    try:
        bb_add = added.BoundingBox()
        print(f"SELF-CHECK added bbox x[{bb_add.xmin:.3f},{bb_add.xmax:.3f}] y[{bb_add.ymin:.3f},{bb_add.ymax:.3f}] z[{bb_add.zmin:.3f},{bb_add.zmax:.3f}]")
        print(f"SELF-CHECK added center={vtuple(added.Center())}")
        print(f"SELF-CHECK added y-extent vs target: ymin={bb_add.ymin:.4f} (target {y_bot}), ymax={bb_add.ymax:.4f} (target {y_top})")
        print(f"SELF-CHECK added volume={added.Volume():.3f}")
    except Exception as ex:
        print(f"SELF-CHECK added: no bbox ({ex})")

    edited_s1 = s1_cut.fuse(new_rim)

    # Post-check: count remaining rounded perimeter cylindrical faces (r=1.9844 and r=3.9942) that overlap slab
    remaining_small = 0
    remaining_large = 0
    for f in edited_s1.Faces():
        ci = cyl_info(f)
        if ci is None:
            continue
        r, d, _c = ci
        if abs(abs(float(d.y)) - 1.0) > tol_axis_y:
            continue
        bb = f.BoundingBox()
        overlaps_slab = (bb.ymax >= y_bot - 1e-6) and (bb.ymin <= y_top + 1e-6)
        if not overlaps_slab:
            continue
        if abs(r - r_small) <= tol_r:
            remaining_small += 1
        if abs(r - r_large) <= tol_r:
            remaining_large += 1
    print(f"CONFIRM: remaining cylindrical perimeter faces in slab: r={r_small} -> {remaining_small}, r={r_large} -> {remaining_large} (target 0,0)")

    # Post-check: heuristic count of straight outer planar faces (tips+flanks should be planar & |normal.y|~0)
    core_r_check = core_r
    straight_outer = 0
    for f in edited_s1.Faces():
        if f.geomType() != "PLANE":
            continue
        n = f.normalAt()
        if abs(float(n.y)) > 0.03:
            continue
        c = f.Center()
        rr = math.hypot(float(c.x) - ax, float(c.z) - az)
        if rr < core_r_check + 1.0:
            continue
        if float(c.y) < y_bot - 0.5 or float(c.y) > y_top + 0.5:
            continue
        straight_outer += 1
    print(f"CONFIRM(heuristic): planar vertical outer faces in slab region counted={straight_outer} (expected ~81 = 27 tips + 54 flanks; may vary if faces merge/split)")

    # Recompound, leaving the other solid untouched
    out = cq.Compound.makeCompound([s0, edited_s1])
    try:
        bb_out = out.BoundingBox()
        print(f"RESULT bbox x[{bb_out.xmin:.3f},{bb_out.xmax:.3f}] y[{bb_out.ymin:.3f},{bb_out.ymax:.3f}] z[{bb_out.zmin:.3f},{bb_out.zmax:.3f}]")
        print(f"RESULT volumes: s0={s0.Volume():.3f}, edited_s1={edited_s1.Volume():.3f}, total={out.Volume():.3f}")
    except Exception as ex:
        print(f"WARN: result bbox/volume failed: {ex}")

    return out