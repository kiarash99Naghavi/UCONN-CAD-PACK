def my_cad_function(args):
    import cadquery as cq
    import math
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_Cylinder, GeomAbs_Plane

    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input

    base = shape.val() if hasattr(shape, "val") else shape
    sols = base.Solids()
    if len(sols) != 2:
        print(f"WARNING: expected 2 solids, got {len(sols)}")

    # --- identify s1 as the larger solid (keep s0 unchanged) ---
    vols = [s.Volume() for s in sols]
    s1_i = max(range(len(sols)), key=lambda i: vols[i])
    s0_i = 1 - s1_i if len(sols) == 2 else None
    s1 = sols[s1_i]
    s0 = sols[s0_i] if s0_i is not None else None
    print(f"SELECTED: s1 solid index={s1_i} (larger) vol={vols[s1_i]:.3f} ; other vol={(vols[s0_i] if s0_i is not None else float('nan')):.3f}")

    # --- list key anchors explicitly (as required) ---
    axis_dir = cq.Vector(0, 1, 0)
    y0 = 0.0
    y1 = -3.175
    print("ANCHORS:")
    print("  axis=[0,1,0]")
    print("  axial limits Y=-3.175..0")
    print("  cylindrical families to remove: r=1.9844 @Y~ -1.588 (27 faces), r=3.9942 @Y~ -1.587 (27 faces)")
    print("  gear center from r=15.8256 Y-axis full cylinder at X=Z=0 (face_idx #669 in index)")

    # --- helpers ---
    def unwrap_deg(a, ref):
        while a - ref > 180:
            a -= 360
        while a - ref < -180:
            a += 360
        return a

    def solid_cyl_faces_by_radius_y(solid, r_target, y_target, r_tol=1e-3, y_tol=5e-3, axis_tol=1e-3):
        out = []
        for f in solid.Faces():
            ad = BRepAdaptor_Surface(f.wrapped, True)
            if ad.GetType() != GeomAbs_Cylinder:
                continue
            cyl = ad.Cylinder()
            r = cyl.Radius()
            if abs(r - r_target) > r_tol:
                continue
            loc = cyl.Location()
            ax = cyl.Axis().Direction()
            # axis parallel to +Y/-Y
            if abs(abs(ax.Y()) - 1.0) > axis_tol:
                continue
            # use face centroid Y to filter family position
            cy = f.Center().y
            if abs(cy - y_target) > y_tol:
                continue
            out.append(f)
        return out

    def find_plane_face_at_y(solid, y_target, ny_sign=None, y_tol=1e-3):
        cand = []
        for f in solid.Faces():
            ad = BRepAdaptor_Surface(f.wrapped, True)
            if ad.GetType() != GeomAbs_Plane:
                continue
            c = f.Center()
            if abs(c.y - y_target) > y_tol:
                continue
            n = f.normalAt()  # no args
            if ny_sign is not None:
                if ny_sign > 0 and n.y < 0.5:
                    continue
                if ny_sign < 0 and n.y > -0.5:
                    continue
            cand.append(f)
        if not cand:
            return None
        # pick largest area
        cand.sort(key=lambda ff: ff.Area(), reverse=True)
        return cand[0]

    def sample_outer_wire_r_theta(face, n_per_edge=40):
        ow = face.outerWire()
        pts = []
        for e in ow.Edges():
            try:
                ds = e.discretize(n_per_edge)
            except Exception:
                ds = [e.startPoint(), e.endPoint()]
            for p in ds:
                r = math.hypot(p.x, p.z)
                th = math.degrees(math.atan2(p.z, p.x))
                pts.append((r, th, p.x, p.z))
        return pts

    def measure_root_tip_radii_from_face(face):
        pts = sample_outer_wire_r_theta(face)
        rs = [p[0] for p in pts]
        return (min(rs), max(rs), pts)

    def angle_span_near_radius(pts, theta_ref, pitch_deg, r_root, r_tip, want="root"):
        # in window around theta_ref, find angle min/max for points close to root or tip radius
        window_half = 0.5 * pitch_deg
        wmin, wmax = theta_ref - window_half, theta_ref + window_half
        # unwrap angles around theta_ref
        pwin = []
        for (r, th, x, z) in pts:
            th_u = unwrap_deg(th, theta_ref)
            if wmin <= th_u <= wmax:
                pwin.append((r, th_u, x, z))
        if not pwin:
            raise ValueError("No points in reference pitch window")

        # adaptive tolerance to get enough candidates
        if want == "root":
            target = r_root
            for tol in [0.005, 0.01, 0.02, 0.05, 0.1, 0.2]:
                angs = [th for (r, th, x, z) in pwin if r <= target + tol]
                if len(angs) >= 6:
                    return (min(angs), max(angs), tol, len(angs))
            angs = [th for (r, th, x, z) in pwin if r <= target + 0.3]
            return (min(angs), max(angs), 0.3, len(angs))
        else:
            target = r_tip
            for tol in [0.005, 0.01, 0.02, 0.05, 0.1, 0.2]:
                angs = [th for (r, th, x, z) in pwin if r >= target - tol]
                if len(angs) >= 6:
                    return (min(angs), max(angs), tol, len(angs))
            angs = [th for (r, th, x, z) in pwin if r >= target - 0.3]
            return (min(angs), max(angs), 0.3, len(angs))

    def polar_point(r, deg):
        a = math.radians(deg)
        return (r * math.cos(a), r * math.sin(a))

    def bbox6(bb):
        return (bb.xmin, bb.xmax, bb.ymin, bb.ymax, bb.zmin, bb.zmax)

    # --- anchor selection: the 27-member cylindrical families on s1 ---
    fam_r1 = solid_cyl_faces_by_radius_y(s1, 1.9844, -1.588, r_tol=5e-3, y_tol=5e-2)
    fam_r2 = solid_cyl_faces_by_radius_y(s1, 3.9942, -1.587, r_tol=5e-3, y_tol=5e-2)
    print(f"SELECTED: {len(fam_r1)} faces for r=1.9844 cylindrical family on s1")
    print(f"SELECTED: {len(fam_r2)} faces for r=3.9942 cylindrical family on s1")

    # reference clocking from the family member near the given center point
    ref_pt = cq.Vector(40.783, -1.588, 38.481)
    if fam_r1:
        ref_face = min(fam_r1, key=lambda f: (f.Center() - ref_pt).Length)
        ref_c = ref_face.Center()
        theta0 = math.degrees(math.atan2(ref_c.z, ref_c.x))
        theta0_mod = (theta0 % 360 + 360) % 360
        print(f"REF: picked r=1.9844 face nearest to [40.783,-1.588,38.481]; center={ref_c.toTuple()}")
    else:
        # fallback: use the provided point
        theta0 = math.degrees(math.atan2(ref_pt.z, ref_pt.x))
        theta0_mod = (theta0 % 360 + 360) % 360
        print("WARNING: r=1.9844 family not found as faces; using provided point for theta0")
    print(f"MEASURED theta0 (deg) = {theta0_mod:.6f}")

    # --- gear center from the full r=15.8256 cylinder at X=Z=0 ---
    # (use as confirmation only; model uses origin for axis)
    cyl_full = None
    for f in s1.Faces():
        ad = BRepAdaptor_Surface(f.wrapped, True)
        if ad.GetType() != GeomAbs_Cylinder:
            continue
        cyl = ad.Cylinder()
        if abs(cyl.Radius() - 15.8256) < 5e-3:
            ax = cyl.Axis().Direction()
            if abs(abs(ax.Y()) - 1.0) < 1e-3:
                loc = cyl.Location()
                if abs(loc.X()) < 1e-2 and abs(loc.Z()) < 1e-2:
                    cyl_full = f
                    break
    if cyl_full:
        ccf = cyl_full.Center()
        print(f"CONFIRM: found r=15.8256 full cylinder-like face; center={ccf.toTuple()} (expect X=Z~0)")
    else:
        print("WARNING: could not confirm r=15.8256 full cylinder face; assuming axis passes through X=Z=0")

    # --- measure root & tip radii from planar boundary loop at Y=0 (face_idx #778 in index) ---
    top_y0 = find_plane_face_at_y(s1, y0, ny_sign=+1, y_tol=1e-3)
    bot_y1 = find_plane_face_at_y(s1, y1, ny_sign=-1, y_tol=1e-3)
    print(f"SELECTED: {1 if top_y0 else 0} planar face at Y=0, n~+Y for rim boundary measurement")
    print(f"SELECTED: {1 if bot_y1 else 0} planar face at Y=-3.175, n~-Y for rim boundary measurement")
    if not top_y0 or not bot_y1:
        print("ERROR: could not find required planar boundary faces; returning input unchanged")
        return shape

    root_r0, tip_r0, pts0 = measure_root_tip_radii_from_face(top_y0)
    print(f"MEASURED from Y=0 outer boundary loop: root_r(min)={root_r0:.6f}  tip_r(max)={tip_r0:.6f}")

    # --- measure root/tip angular widths from reference tooth sector ---
    pitch = 360.0 / 27.0
    root_aL, root_aR, root_tol, root_n = angle_span_near_radius(pts0, theta0_mod, pitch, root_r0, tip_r0, want="root")
    tip_aL, tip_aR, tip_tol, tip_n = angle_span_near_radius(pts0, theta0_mod, pitch, root_r0, tip_r0, want="tip")
    print("REFERENCE TOOTH INTERSECTIONS (from Y=0 boundary loop samples):")
    print(f"  root angles: L={root_aL:.6f}  R={root_aR:.6f}  (tol={root_tol}mm, n={root_n})")
    print(f"  tip  angles: L={tip_aL:.6f}  R={tip_aR:.6f}  (tol={tip_tol}mm, n={tip_n})")
    print(f"  root angular width={root_aR-root_aL:.6f} deg")
    print(f"  tip  angular width={tip_aR-tip_aL:.6f} deg")

    # --- print all 27 tooth center angles and successive differences ---
    angles = [theta0_mod + k * pitch for k in range(27)]
    angles_mod = [((a % 360) + 360) % 360 for a in angles]
    print("TOOTH CENTER ANGLES (mod 360):")
    for k, a in enumerate(angles_mod):
        print(f"  k={k:02d}  theta={a:.6f}")
    print("SUCCESSIVE DIFFERENCES (unwrapped, should all be 13.333333):")
    for k in range(26):
        d = angles[k+1] - angles[k]
        print(f"  d[{k:02d}->{k+1:02d}] = {d:.6f}")

    # --- build one tooth-sector profile on XZ plane and extrude Y=-3.175..0 ---
    # points on exact root & tip circles, using measured angles
    x_lr, z_lr = polar_point(root_r0, root_aL)
    x_rr, z_rr = polar_point(root_r0, root_aR)
    x_lt, z_lt = polar_point(tip_r0, tip_aL)
    x_rt, z_rt = polar_point(tip_r0, tip_aR)
    mid_root_ang = 0.5 * (root_aL + root_aR)
    x_mr, z_mr = polar_point(root_r0, mid_root_ang)

    print("PROFILE POINTS (XZ at Y=-3.175 workplane):")
    print(f"  left_root  (r={root_r0:.6f}, a={root_aL:.6f}) -> ({x_lr:.6f},{z_lr:.6f})")
    print(f"  right_root (r={root_r0:.6f}, a={root_aR:.6f}) -> ({x_rr:.6f},{z_rr:.6f})")
    print(f"  left_tip   (r={tip_r0:.6f},  a={tip_aL:.6f}) -> ({x_lt:.6f},{z_lt:.6f})")
    print(f"  right_tip  (r={tip_r0:.6f},  a={tip_aR:.6f}) -> ({x_rt:.6f},{z_rt:.6f})")

    plane = cq.Plane(origin=(0, y1, 0), normal=(0, 1, 0), xDir=(1, 0, 0))
    print(f"SKETCH PLANE origin={(0, y1, 0)} normal=(0,1,0) xDir=(1,0,0)")

    tooth_wp = (
        cq.Workplane(plane)
        .moveTo(x_lr, z_lr)
        .threePointArc((x_mr, z_mr), (x_rr, z_rr))
        .lineTo(x_rt, z_rt)          # straight right flank
        .lineTo(x_lt, z_lt)          # straight tip segment
        .close()                     # straight left flank back to left_root
    )

    tooth_solid = tooth_wp.extrude(abs(y0 - y1), both=False).val()

    # pattern 27 copies about Y axis
    teeth = tooth_solid
    for k in range(1, 27):
        tk = tooth_solid.rotate((0, 0, 0), (0, 1, 0), k * pitch)
        teeth = teeth.fuse(tk)

    added_bb = teeth.BoundingBox()
    print("ADDED (new teeth rim) bbox:")
    print(f"  xmin..xmax = {added_bb.xmin:.6f} .. {added_bb.xmax:.6f}")
    print(f"  ymin..ymax = {added_bb.ymin:.6f} .. {added_bb.ymax:.6f}   (expect -3.175..0)")
    print(f"  zmin..zmax = {added_bb.zmin:.6f} .. {added_bb.zmax:.6f}")

    # --- remove original rim in that axial interval with an annular cutter, then union new rim ---
    outer_cut_r = tip_r0 + 5.0
    inner_cut_r = root_r0  # do not cut inside the measured root radius

    cutter_wp = cq.Workplane(plane).circle(outer_cut_r).circle(inner_cut_r)
    cutter = cutter_wp.extrude(abs(y0 - y1), both=False).val()

    print(f"CUTTER annulus: inner_r={inner_cut_r:.6f}, outer_r={outer_cut_r:.6f}, extrude Y={y1}..{y0}")

    s1_bb_before = s1.BoundingBox()
    print("s1 bbox BEFORE:")
    print(f"  {bbox6(s1_bb_before)}")

    s1_cut = s1.cut(cutter)
    s1_new = s1_cut.fuse(teeth)

    s1_bb_after = s1_new.BoundingBox()
    print("s1 bbox AFTER:")
    print(f"  {bbox6(s1_bb_after)}")

    # --- compare root/tip radii before/after using the Y=0 planar boundary on edited s1 ---
    top_y0_after = find_plane_face_at_y(s1_new, y0, ny_sign=+1, y_tol=1e-3)
    if top_y0_after:
        root_r1, tip_r1, _pts1 = measure_root_tip_radii_from_face(top_y0_after)
        print("RADII CHECK (from Y=0 outer boundary loop):")
        print(f"  BEFORE root={root_r0:.6f} tip={tip_r0:.6f}")
        print(f"  AFTER  root={root_r1:.6f} tip={tip_r1:.6f}")
        print(f"  deltas root={root_r1-root_r0:+.6f} tip={tip_r1-tip_r0:+.6f}")
    else:
        print("WARNING: could not find Y=0 planar face on edited s1 for radius check")

    # --- verify removal of the old rounded cylindrical tooth-end surfaces (r=1.9844 and r=3.9942) ---
    def count_cyl_faces_radius(solid, r_target, r_tol=5e-3):
        n = 0
        for f in solid.Faces():
            ad = BRepAdaptor_Surface(f.wrapped, True)
            if ad.GetType() != GeomAbs_Cylinder:
                continue
            cyl = ad.Cylinder()
            if abs(cyl.Radius() - r_target) <= r_tol:
                ax = cyl.Axis().Direction()
                if abs(abs(ax.Y()) - 1.0) < 1e-3:
                    n += 1
        return n

    n_cyl_198 = count_cyl_faces_radius(s1_new, 1.9844)
    n_cyl_399 = count_cyl_faces_radius(s1_new, 3.9942)
    print(f"POST-CHECK: cylindrical faces on axis~Y with r~1.9844 remaining in s1_new: {n_cyl_198}")
    print(f"POST-CHECK: cylindrical faces on axis~Y with r~3.9942 remaining in s1_new: {n_cyl_399}")

    # --- re-compound with s0 untouched ---
    out_sols = []
    for i, s in enumerate(sols):
        if i == s1_i:
            out_sols.append(s1_new)
        else:
            out_sols.append(s)

    out = cq.Compound.makeCompound(out_sols)
    return out