def my_cad_function(args):
    import cadquery as cq
    import math
    from OCP.BRepAdaptor import BRepAdaptor_Surface

    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input
    base = shape.val() if hasattr(shape, "val") else shape

    def bbox6(bb):
        return {
            "xmin": bb.xmin, "xmax": bb.xmax,
            "ymin": bb.ymin, "ymax": bb.ymax,
            "zmin": bb.zmin, "zmax": bb.zmax,
            "xlen": bb.xlen, "ylen": bb.ylen, "zlen": bb.zlen,
        }

    def vdot(a, b):
        return a.x * b.x + a.y * b.y + a.z * b.z

    def vlen(a):
        return math.sqrt(a.x * a.x + a.y * a.y + a.z * a.z)

    def is_axis_y(vec, tol=1e-3):
        vv = cq.Vector(vec)
        L = vv.Length
        if L <= 0:
            return False
        vv = vv.multiply(1.0 / L)
        return abs(abs(vv.y) - 1.0) <= tol and abs(vv.x) <= tol and abs(vv.z) <= tol

    def cyl_info(face):
        try:
            if face.geomType() != "CYLINDER":
                return None
            ad = BRepAdaptor_Surface(face.wrapped)
            cy = ad.Cylinder()
            ax = cy.Axis()
            d = ax.Direction()
            loc = ax.Location()
            return {
                "r": float(cy.Radius()),
                "axis_dir": cq.Vector(d.X(), d.Y(), d.Z()),
                "axis_loc": cq.Vector(loc.X(), loc.Y(), loc.Z()),
            }
        except Exception:
            return None

    def angle_deg(x, z):
        a = math.degrees(math.atan2(z, x))
        a = a % 360.0
        return a

    def unwrap_deg(a, ref):
        # return a (in degrees) shifted by +/-360 so it's closest to ref
        while a - ref > 180.0:
            a -= 360.0
        while a - ref < -180.0:
            a += 360.0
        return a

    def sample_wire(wire, n_per_edge=60):
        pts = []
        for e in wire.Edges():
            try:
                vs = e.discretize(n_per_edge)
                for p in vs:
                    pts.append((float(p.x), float(p.y), float(p.z)))
            except Exception:
                # fallback: just vertices
                for v in e.Vertices():
                    p = v.toTuple()
                    pts.append((float(p[0]), float(p[1]), float(p[2])))
        return pts

    def planar_faces_at_y(solid, y_target, ny_sign=+1, y_tol=1e-3, n_tol=1e-3):
        out = []
        for f in solid.Faces():
            if f.geomType() != "PLANE":
                continue
            c = f.Center()
            if abs(c.y - y_target) > y_tol:
                continue
            n = f.normalAt()  # no args
            if ny_sign > 0:
                if n.y < 1.0 - n_tol:
                    continue
            else:
                if n.y > -1.0 + n_tol:
                    continue
            out.append(f)
        return out

    def pick_outer_tooth_wire(planar_face, center_x=0.0, center_z=0.0):
        wires = planar_face.Wires()
        print(f"WIRE SCAN: total_wires={len(wires)}")
        best = None
        best_key = None
        best_pts = None
        for i, w in enumerate(wires):
            pts = sample_wire(w, n_per_edge=50)
            if not pts:
                print(f"  wire[{i}] EMPTY")
                continue
            rs = [math.hypot(x - center_x, z - center_z) for (x, _y, z) in pts]
            rmin = min(rs)
            rmax = max(rs)
            spread = rmax - rmin
            # Prefer wire with largest rmax (outermost), then largest spread, then most pts
            key = (rmax, spread, len(pts))
            print(f"  wire[{i}] edges={len(w.Edges())} pts={len(pts)} rmin={rmin:.6f} rmax={rmax:.6f} spread={spread:.6f}")
            if best is None or key > best_key:
                best = w
                best_key = key
                best_pts = pts
        return best, best_pts

    def find_span_angles(pts, theta0, pitch_deg, root_r, tip_r, center_x=0.0, center_z=0.0):
        # Filter points into the reference tooth sector window around theta0
        half = pitch_deg * 0.5
        # Build lists of (unwrapped_angle, r)
        ar = []
        for (x, _y, z) in pts:
            a0 = angle_deg(x - center_x, z - center_z)
            a = unwrap_deg(a0, theta0)
            if a < theta0 - half or a > theta0 + half:
                continue
            r = math.hypot(x - center_x, z - center_z)
            ar.append((a, r))
        if not ar:
            return None

        def grow_collect(mode="root"):
            # mode root: r within root_r+tol; mode tip: r within tip_r-tol
            tol = 0.005
            for _ in range(10):
                if mode == "root":
                    sel = [a for (a, r) in ar if r <= root_r + tol]
                else:
                    sel = [a for (a, r) in ar if r >= tip_r - tol]
                sel.sort()
                if len(sel) >= 5 or tol >= 0.5:
                    return sel, tol
                tol *= 2.0
            return sel, tol

        root_angles, root_tol = grow_collect("root")
        tip_angles, tip_tol = grow_collect("tip")

        if not root_angles:
            print(f"WARNING: could not find root-radius points near root_r within window; root_tol ended {root_tol:.4f}")
        if not tip_angles:
            print(f"WARNING: could not find tip-radius points near tip_r within window; tip_tol ended {tip_tol:.4f}")

        # Root: pick the two root-circle angles closest to theta0 on each side
        root_left = [a for a in root_angles if a < theta0]
        root_right = [a for a in root_angles if a > theta0]
        if root_left and root_right:
            a_root_L = max(root_left)
            a_root_R = min(root_right)
        else:
            # fallback: min/max in window
            a_root_L = min(root_angles) if root_angles else theta0 - pitch_deg * 0.35
            a_root_R = max(root_angles) if root_angles else theta0 + pitch_deg * 0.35

        # Tip: corners correspond to extreme angles at max radius within this tooth sector
        if tip_angles:
            a_tip_L = min(tip_angles)
            a_tip_R = max(tip_angles)
        else:
            a_tip_L = theta0 - pitch_deg * 0.18
            a_tip_R = theta0 + pitch_deg * 0.18

        # sanity clamp into window
        a_root_L = max(a_root_L, theta0 - pitch_deg * 0.499)
        a_root_R = min(a_root_R, theta0 + pitch_deg * 0.499)
        a_tip_L = max(a_tip_L, theta0 - pitch_deg * 0.499)
        a_tip_R = min(a_tip_R, theta0 + pitch_deg * 0.499)

        return {
            "a_root_L": a_root_L, "a_root_R": a_root_R,
            "a_tip_L": a_tip_L, "a_tip_R": a_tip_R,
            "root_tol": root_tol, "tip_tol": tip_tol,
        }

    # --- Split solids and pick s1 ---
    sols = list(base.Solids())
    if len(sols) != 2:
        print(f"WARNING: expected 2 solids, found {len(sols)}; proceeding with all solids compound")
    vols = [s.Volume() for s in sols]
    s1_i = int(max(range(len(sols)), key=lambda i: vols[i]))
    s1 = sols[s1_i]
    print(f"SELECTED: s1 solid index={s1_i} (largest) vol={vols[s1_i]:.3f}; other vols={[round(v,3) for v in vols]}")

    # --- Named anchors from prompt ---
    y_bot = -3.175
    y_top = 0.0
    r_cyl_small = 1.9844
    r_cyl_large = 3.9942
    ref_pt = cq.Vector(40.783, -1.588, 38.481)
    pitch = 360.0 / 27.0
    axis_origin = (0.0, 0.0, 0.0)
    axis_dir = (0.0, 1.0, 0.0)
    center_x, center_z = 0.0, 0.0

    print("ANCHORS:")
    print(f"  axis=[0,1,0]")
    print(f"  axial limits Y={y_bot}..{y_top}")
    print(f"  cylindrical families to remove/replace: r={r_cyl_small} (27 faces), r={r_cyl_large} (27 faces)")
    print(f"  ref cylindrical-face center near {tuple(ref_pt.toTuple())}")

    # --- Select cylindrical face families on s1 ---
    cyl_198 = []
    cyl_399 = []
    for idx, f in enumerate(s1.Faces()):
        info = cyl_info(f)
        if not info:
            continue
        if not is_axis_y(info["axis_dir"], tol=5e-3):
            continue
        if abs(info["r"] - r_cyl_small) <= 5e-3:
            cyl_198.append(f)
        if abs(info["r"] - r_cyl_large) <= 5e-3:
            cyl_399.append(f)
    print(f"SELECTED: {len(cyl_198)} faces for r~{r_cyl_small} cylindrical family on s1")
    print(f"SELECTED: {len(cyl_399)} faces for r~{r_cyl_large} cylindrical family on s1")

    # Pick reference faces
    if cyl_198:
        f_ref = min(cyl_198, key=lambda ff: ff.Center().sub(ref_pt).Length)
        c_ref = f_ref.Center()
        a1 = angle_deg(c_ref.x - center_x, c_ref.z - center_z)
        print(f"REF: picked r={r_cyl_small} face nearest to ref_pt; center=({c_ref.x:.6f},{c_ref.y:.6f},{c_ref.z:.6f})")
        print(f"REF: angle a1(deg)={a1:.6f}")
    else:
        # fallback
        a1 = 0.0
        print("WARNING: could not find r=1.9844 cylindrical family; using fallback theta")

    if cyl_399 and cyl_198:
        # pick 'associated' large radius face by nearest center in XZ to f_ref center
        c_ref = f_ref.Center()
        f_ref2 = min(cyl_399, key=lambda ff: math.hypot(ff.Center().x - c_ref.x, ff.Center().z - c_ref.z))
        c_ref2 = f_ref2.Center()
        a2 = angle_deg(c_ref2.x - center_x, c_ref2.z - center_z)
        print(f"REF2: picked associated r={r_cyl_large} face by nearest XZ; center=({c_ref2.x:.6f},{c_ref2.y:.6f},{c_ref2.z:.6f})")
        print(f"REF2: angle a2(deg)={a2:.6f}")
    else:
        a2 = a1

    # theta0: average the two angles (unwrap to avoid wrap discontinuity)
    a2u = unwrap_deg(a2, a1)
    theta0 = (a1 + a2u) * 0.5
    theta0_mod = theta0 % 360.0
    print(f"MEASURED theta0 (deg) = {theta0_mod:.6f} (from avg of a1={a1:.6f}, a2={a2:.6f} unwrapped={a2u:.6f})")

    # Print all 27 tooth-center angles and diffs
    angles = [(theta0_mod + k * pitch) % 360.0 for k in range(27)]
    print("TOOTH CENTER ANGLES (deg, mod 360):")
    for k, a in enumerate(angles):
        print(f"  k={k:02d}  theta={a:.6f}")
    print("TOOTH CENTER successive diffs (deg):")
    for k in range(26):
        d = (angles[k + 1] - angles[k]) % 360.0
        print(f"  d[{k:02d}->{k+1:02d}] = {d:.6f} (expect {pitch:.6f})")

    # --- Determine planar boundary faces at Y=0 and Y=-3.175 (on s1) ---
    top_faces = planar_faces_at_y(s1, y_top, ny_sign=+1, y_tol=1e-3, n_tol=1e-3)
    bot_faces = planar_faces_at_y(s1, y_bot, ny_sign=-1, y_tol=1e-3, n_tol=1e-3)
    print(f"SELECTED: {len(top_faces)} planar face(s) at Y={y_top}, n~+Y for rim boundary measurement")
    print(f"SELECTED: {len(bot_faces)} planar face(s) at Y={y_bot}, n~-Y for rim boundary measurement")

    # Choose largest-area on each
    top_face = max(top_faces, key=lambda f: f.Area()) if top_faces else None
    bot_face = max(bot_faces, key=lambda f: f.Area()) if bot_faces else None
    if top_face:
        c = top_face.Center()
        print(f"CHOSEN top planar face: area={top_face.Area():.3f} center=({c.x:.6f},{c.y:.6f},{c.z:.6f})")
    if bot_face:
        c = bot_face.Center()
        print(f"CHOSEN bot planar face: area={bot_face.Area():.3f} center=({c.x:.6f},{c.y:.6f},{c.z:.6f})")

    # --- Measure root/tip radii from boundary loop samples (prefer top face) ---
    use_face = top_face if top_face else bot_face
    if not use_face:
        # last resort: use bbox for radii (still do the edit)
        bb = s1.BoundingBox()
        tip_r0 = max(math.hypot(bb.xmax, bb.zmax), math.hypot(bb.xmin, bb.zmin))
        root_r0 = tip_r0 - 3.0
        pts_wire = []
        print("WARNING: could not find planar boundary faces at Y=0 or Y=-3.175; using bbox fallback radii")
    else:
        print(f"MEASUREMENT face y={use_face.Center().y:.6f} using its outermost wire")
        w_tooth, pts_wire = pick_outer_tooth_wire(use_face, center_x=center_x, center_z=center_z)
        if not w_tooth or not pts_wire:
            bb = s1.BoundingBox()
            tip_r0 = max(math.hypot(bb.xmax, bb.zmax), math.hypot(bb.xmin, bb.zmin))
            root_r0 = tip_r0 - 3.0
            print("WARNING: could not pick/samples a wire; using bbox fallback radii")
        else:
            rs = [math.hypot(x - center_x, z - center_z) for (x, _y, z) in pts_wire]
            root_r0 = min(rs)
            tip_r0 = max(rs)
            print(f"MEASURED RADII from sampled boundary loop:")
            print(f"  root_r(min)={root_r0:.6f}")
            print(f"  tip_r(max) ={tip_r0:.6f}")
            print(f"  spread      ={(tip_r0-root_r0):.6f}")

    if tip_r0 - root_r0 < 1e-3:
        # force non-degenerate to avoid no-op
        tip_r0 = root_r0 + 1.0
        print("WARNING: root/tip nearly equal from measurement; forcing tip_r = root_r + 1.0 to avoid degenerate profile")

    # --- Measure reference tooth root/tip angular spans from the sampled boundary ---
    span = None
    if pts_wire:
        span = find_span_angles(pts_wire, theta0, pitch, root_r0, tip_r0, center_x=center_x, center_z=center_z)

    if not span:
        # fallback spans
        print("WARNING: could not derive angular spans from boundary samples; using fallback spans")
        span = {
            "a_root_L": theta0 - pitch * 0.20,
            "a_root_R": theta0 + pitch * 0.20,
            "a_tip_L": theta0 - pitch * 0.12,
            "a_tip_R": theta0 + pitch * 0.12,
            "root_tol": None,
            "tip_tol": None,
        }

    a_root_L = span["a_root_L"]
    a_root_R = span["a_root_R"]
    a_tip_L = span["a_tip_L"]
    a_tip_R = span["a_tip_R"]

    print("REFERENCE TOOTH SPANS (deg, unwrapped around theta0):")
    print(f"  theta0={theta0:.6f} (mod {theta0_mod:.6f}) pitch={pitch:.6f}")
    print(f"  root angles: L={a_root_L:.6f}, R={a_root_R:.6f}  (width={a_root_R-a_root_L:.6f})")
    print(f"  tip  angles: L={a_tip_L:.6f}, R={a_tip_R:.6f}  (width={a_tip_R-a_tip_L:.6f})")

    # Polar point helper (absolute)
    def polar_point(r, ang_deg):
        a = math.radians(ang_deg)
        return (center_x + r * math.cos(a), center_z + r * math.sin(a))

    # Root arc mid angle
    a_root_M = 0.5 * (a_root_L + a_root_R)

    x_lr, z_lr = polar_point(root_r0, a_root_L)
    x_mr, z_mr = polar_point(root_r0, a_root_M)
    x_rr, z_rr = polar_point(root_r0, a_root_R)
    x_lt, z_lt = polar_point(tip_r0, a_tip_L)
    x_rt, z_rt = polar_point(tip_r0, a_tip_R)

    print("PROFILE POINTS (XZ):")
    print(f"  left_root  r={root_r0:.6f} a={a_root_L:.6f} -> ({x_lr:.6f},{z_lr:.6f})")
    print(f"  mid_root   r={root_r0:.6f} a={a_root_M:.6f} -> ({x_mr:.6f},{z_mr:.6f})")
    print(f"  right_root r={root_r0:.6f} a={a_root_R:.6f} -> ({x_rr:.6f},{z_rr:.6f})")
    print(f"  left_tip   r={tip_r0:.6f}  a={a_tip_L:.6f} -> ({x_lt:.6f},{z_lt:.6f})")
    print(f"  right_tip  r={tip_r0:.6f}  a={a_tip_R:.6f} -> ({x_rt:.6f},{z_rt:.6f})")

    # --- Build one tooth protrusion profile (root arc + straight flanks + straight tip) ---
    plane = cq.Plane(origin=(0.0, y_bot, 0.0), normal=(0, 1, 0), xDir=(1, 0, 0))
    print(f"SKETCH PLANE origin={(0.0, y_bot, 0.0)} normal=(0,1,0) xDir=(1,0,0)")

    tooth_wp = (
        cq.Workplane(plane)
        .moveTo(x_lr, z_lr)
        .threePointArc((x_mr, z_mr), (x_rr, z_rr))
        .lineTo(x_rt, z_rt)  # straight right flank
        .lineTo(x_lt, z_lt)  # straight tip segment
        .close()             # straight left flank back to left_root
    )

    tooth_len = y_top - y_bot
    if tooth_len <= 0:
        print("ERROR: invalid axial limits; forcing tooth_len=3.175")
        tooth_len = 3.175

    tooth_solid = tooth_wp.extrude(tooth_len, both=False).val()

    # --- Pattern 27 teeth about Y axis ---
    teeth = tooth_solid
    for k in range(1, 27):
        tk = tooth_solid.rotate(axis_origin, axis_dir, k * pitch)
        teeth = teeth.fuse(tk)

    added_bb = teeth.BoundingBox()
    print("ADDED (new straight-tooth protrusions) bbox:")
    print(f"  {bbox6(added_bb)}")
    print(f"  EXPECT added ymin..ymax approx {y_bot}..{y_top}")

    # --- Remove original rim in the axial interval with an explicit annular cutter ---
    outer_cut_r = tip_r0 + 10.0
    inner_cut_r = max(0.0, root_r0 - 0.001)  # tiny inset for robustness

    cutter = (
        cq.Workplane(plane)
        .circle(outer_cut_r)
        .circle(inner_cut_r)
        .extrude(tooth_len, both=False)
        .val()
    )
    print(f"CUTTER annulus: inner_r={inner_cut_r:.6f}, outer_r={outer_cut_r:.6f}, extrude Y={y_bot}..{y_top}")

    s1_bb_before = s1.BoundingBox()
    print("s1 bbox BEFORE:")
    print(f"  {bbox6(s1_bb_before)}")

    s1_cut = s1.cut(cutter)
    s1_new = s1_cut.fuse(teeth)

    s1_bb_after = s1_new.BoundingBox()
    print("s1 bbox AFTER:")
    print(f"  {bbox6(s1_bb_after)}")

    # --- Post-check: re-measure root/tip on Y=0 planar face if possible ---
    top_faces_after = planar_faces_at_y(s1_new, y_top, ny_sign=+1, y_tol=2e-3, n_tol=2e-3)
    print(f"SELECTED: {len(top_faces_after)} planar face(s) at Y={y_top} on edited s1 for radii re-check")
    if top_faces_after:
        fchk = max(top_faces_after, key=lambda f: f.Area())
        print(f"CHOSEN radii-check face: area={fchk.Area():.3f} center={tuple([round(v,6) for v in fchk.Center().toTuple()])}")
        wchk, pchk = pick_outer_tooth_wire(fchk, center_x=center_x, center_z=center_z)
        if pchk:
            rs1 = [math.hypot(x - center_x, z - center_z) for (x, _y, z) in pchk]
            root_r1 = min(rs1)
            tip_r1 = max(rs1)
            print("RADII CHECK (boundary loop samples):")
            print(f"  BEFORE root={root_r0:.6f} tip={tip_r0:.6f}")
            print(f"  AFTER  root={root_r1:.6f} tip={tip_r1:.6f}")
            print(f"  deltas root={root_r1-root_r0:+.6f} tip={tip_r1-tip_r0:+.6f}")
        else:
            print("WARNING: radii-check sampling failed on edited shape")
    else:
        print("WARNING: could not find Y=0 planar face on edited s1 for radii re-check")

    # --- Verify removal of old rounded tooth cylinders r=1.9844 and r=3.9942 on axis~Y ---
    def count_cyl_faces_radius(solid, r_target, r_tol=5e-3):
        n = 0
        for f in solid.Faces():
            info = cyl_info(f)
            if not info:
                continue
            if abs(info["r"] - r_target) <= r_tol and is_axis_y(info["axis_dir"], tol=5e-3):
                n += 1
        return n

    n_cyl_198 = count_cyl_faces_radius(s1_new, r_cyl_small)
    n_cyl_399 = count_cyl_faces_radius(s1_new, r_cyl_large)
    print(f"POST-CHECK: cylindrical faces on axis~Y with r~{r_cyl_small} remaining in s1_new: {n_cyl_198}")
    print(f"POST-CHECK: cylindrical faces on axis~Y with r~{r_cyl_large} remaining in s1_new: {n_cyl_399}")

    # --- Re-compound with other solids untouched ---
    out_sols = []
    for i, s in enumerate(sols):
        out_sols.append(s1_new if i == s1_i else s)
    out = cq.Compound.makeCompound(out_sols)

    bb_in = base.BoundingBox()
    bb_out = out.BoundingBox()
    print("OVERALL bbox BEFORE (compound):")
    print(f"  {bbox6(bb_in)}")
    print("OVERALL bbox AFTER  (compound):")
    print(f"  {bbox6(bb_out)}")

    return out