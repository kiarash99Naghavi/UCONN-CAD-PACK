def my_cad_function(args):
    import cadquery as cq
    import math

    # OCC imports (only what we use)
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_Cylinder, GeomAbs_Plane

    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input

    base = shape.val() if hasattr(shape, "val") else shape
    sols = base.Solids()
    if len(sols) != 2:
        print(f"ERROR: expected 2 solids, got {len(sols)}; returning input")
        return shape

    def bbox6(bb):
        return (
            f"xmin={bb.xmin:.6f}, xmax={bb.xmax:.6f}, "
            f"ymin={bb.ymin:.6f}, ymax={bb.ymax:.6f}, "
            f"zmin={bb.zmin:.6f}, zmax={bb.zmax:.6f}"
        )

    # Identify s1 as the larger-volume solid
    vols = [(i, s.Volume()) for i, s in enumerate(sols)]
    vols_sorted = sorted(vols, key=lambda t: t[1])
    s0_i, s1_i = vols_sorted[0][0], vols_sorted[1][0]
    s0, s1 = sols[s0_i], sols[s1_i]
    print(f"SELECTED: s1 solid index={s1_i} (larger) vol={s1.Volume():.3f} ; other vol={s0.Volume():.3f}")

    # ---- Named anchors / constants from prompt ----
    axis_dir = cq.Vector(0, 1, 0)
    y_bot = -3.175
    y_top = 0.0
    r_cyl_small = 1.9844
    r_cyl_large = 3.9942
    ref_pt = cq.Vector(40.783, -1.588, 38.481)
    gear_center_expected_xz = (0.0, 0.0)
    r_center_cyl = 15.8256

    print("ANCHORS:")
    print("  axis=[0,1,0]")
    print(f"  axial limits Y={y_bot}..{y_top}")
    print(f"  cylindrical families to remove: r={r_cyl_small} @Y~ -1.588 (27 faces), r={r_cyl_large} @Y~ -1.587 (27 faces)")
    print(f"  gear center from r={r_center_cyl} Y-axis full cylinder at X=Z=0 (face_idx #669 in index)")
    print("NOTE: previous attempt failed because measured root_r == tip_r, collapsing the tooth profile to a degenerate wire; extrude then crashed.")

    def cyl_info(face):
        ad = BRepAdaptor_Surface(face.wrapped, True)
        if ad.GetType() != GeomAbs_Cylinder:
            return None
        cyl = ad.Cylinder()
        ax = cyl.Axis().Direction()
        # origin point on axis
        loc = cyl.Axis().Location()
        return {
            "r": cyl.Radius(),
            "axis": cq.Vector(ax.X(), ax.Y(), ax.Z()),
            "p": cq.Vector(loc.X(), loc.Y(), loc.Z()),
        }

    def is_axis_y(v, tol=1e-3):
        return abs(abs(v.y) - 1.0) < tol and abs(v.x) < 1e-2 and abs(v.z) < 1e-2

    # ---- Select the two 27-member cylindrical face families on axis ~Y ----
    cyl_faces_small = []
    cyl_faces_large = []
    for f in s1.Faces():
        info = cyl_info(f)
        if not info:
            continue
        if not is_axis_y(info["axis"]):
            continue
        if abs(info["r"] - r_cyl_small) < 5e-3:
            cyl_faces_small.append(f)
        elif abs(info["r"] - r_cyl_large) < 5e-3:
            cyl_faces_large.append(f)

    print(f"SELECTED: {len(cyl_faces_small)} faces for r={r_cyl_small} cylindrical family on s1")
    print(f"SELECTED: {len(cyl_faces_large)} faces for r={r_cyl_large} cylindrical family on s1")

    if len(cyl_faces_small) != 27 or len(cyl_faces_large) != 27:
        print("ERROR: did not match the required 27+27 cylindrical face families; returning input unchanged")
        return shape

    # ---- Determine theta0 from the r=1.9844 face nearest to the provided reference point ----
    def face_center_vec(f):
        c = f.Center()
        return cq.Vector(c.x, c.y, c.z)

    ref_face = min(cyl_faces_small, key=lambda f: (face_center_vec(f) - ref_pt).Length)
    ref_c = face_center_vec(ref_face)
    theta0 = math.degrees(math.atan2(ref_c.z, ref_c.x))
    theta0_mod = (theta0 % 360.0 + 360.0) % 360.0
    print(f"REF: picked r={r_cyl_small} face nearest to [{ref_pt.x},{ref_pt.y},{ref_pt.z}]; center=({ref_c.x:.6f}, {ref_c.y:.6f}, {ref_c.z:.6f})")
    print(f"MEASURED theta0 (deg) = {theta0_mod:.6f}")

    # Also report the nearest r=3.9942 face to the same point (association sanity check)
    ref_face2 = min(cyl_faces_large, key=lambda f: (face_center_vec(f) - ref_pt).Length)
    ref2_c = face_center_vec(ref_face2)
    theta2 = (math.degrees(math.atan2(ref2_c.z, ref2_c.x)) % 360.0 + 360.0) % 360.0
    print(f"REF2: nearest r={r_cyl_large} face center=({ref2_c.x:.6f}, {ref2_c.y:.6f}, {ref2_c.z:.6f}) angle={theta2:.6f}")

    # ---- Confirm gear center from the full cylinder r=15.8256 on axis Y at X=Z=0 ----
    center_cyl_faces = []
    for f in s1.Faces():
        info = cyl_info(f)
        if not info:
            continue
        if abs(info["r"] - r_center_cyl) > 5e-3:
            continue
        if not is_axis_y(info["axis"], tol=5e-3):
            continue
        # expect axis line through X=Z~0
        if abs(info["p"].x) < 1e-2 and abs(info["p"].z) < 1e-2:
            center_cyl_faces.append((f, info))

    print(f"SELECTED: {len(center_cyl_faces)} faces matching center cylinder r~{r_center_cyl} axis~Y @X=Z~0")
    if len(center_cyl_faces) < 1:
        print("ERROR: could not confirm gear center cylinder; returning input unchanged")
        return shape
    cf, cinfo = center_cyl_faces[0]
    print(f"CONFIRM: found r={r_center_cyl} full cylinder-like face; axisPoint=({cinfo['p'].x:.6e},{cinfo['p'].y:.6e},{cinfo['p'].z:.6e}) (expect X=Z~0)")
    cx, cz = 0.0, 0.0
    print(f"GEAR CENTER XZ used for polar measurements: ({cx:.6f},{cz:.6f})  expected~{gear_center_expected_xz}")

    # ---- Find planar boundary faces at Y=0 and Y=-3.175 on s1 ----
    def planar_faces_at_y(solid, y_target, ny_sign, y_tol=1e-3):
        out = []
        for f in solid.Faces():
            ad = BRepAdaptor_Surface(f.wrapped, True)
            if ad.GetType() != GeomAbs_Plane:
                continue
            c = f.Center()
            if abs(c.y - y_target) > y_tol:
                continue
            n = f.normalAt()  # no args
            if ny_sign > 0 and n.y < 0.9:
                continue
            if ny_sign < 0 and n.y > -0.9:
                continue
            out.append(f)
        return out

    top_faces = planar_faces_at_y(s1, y_top, ny_sign=+1)
    bot_faces = planar_faces_at_y(s1, y_bot, ny_sign=-1)
    print(f"SELECTED: {len(top_faces)} planar face(s) at Y={y_top}, n~+Y for rim boundary measurement")
    print(f"SELECTED: {len(bot_faces)} planar face(s) at Y={y_bot}, n~-Y for rim boundary measurement")
    if not top_faces or not bot_faces:
        print("ERROR: could not find required planar boundary faces; returning input unchanged")
        return shape

    # Use the largest area planar face at each Y (more likely the rim boundary plane)
    top_face = max(top_faces, key=lambda f: f.Area())
    bot_face = max(bot_faces, key=lambda f: f.Area())
    print(f"CHOSEN top planar face: area={top_face.Area():.3f} center=({top_face.Center().x:.6f},{top_face.Center().y:.6f},{top_face.Center().z:.6f})")
    print(f"CHOSEN bot planar face: area={bot_face.Area():.3f} center=({bot_face.Center().x:.6f},{bot_face.Center().y:.6f},{bot_face.Center().z:.6f})")

    # ---- Sample wires on a face, pick the wire that actually contains the tooth perimeter ----
    def discretize_edge_safe(e, n=25):
        try:
            pts = e.discretize(n)
            if pts:
                return pts
        except Exception:
            pass
        # fallback to vertices
        try:
            return [v.toTuple() for v in e.Vertices()]
        except Exception:
            return []

    def sample_wire_points(wire, pts_per_edge=25):
        pts = []
        for e in wire.Edges():
            for p in discretize_edge_safe(e, n=pts_per_edge):
                # p may be gp_Pnt-like or tuple
                try:
                    pts.append((float(p.x), float(p.y), float(p.z)))
                except Exception:
                    try:
                        pts.append((float(p[0]), float(p[1]), float(p[2])))
                    except Exception:
                        continue
        return pts

    def r_ang_from_pt(x, z):
        dx, dz = x - cx, z - cz
        r = math.hypot(dx, dz)
        ang = math.degrees(math.atan2(dz, dx))
        ang = (ang % 360.0 + 360.0) % 360.0
        return r, ang

    def pick_tooth_wire(face, label):
        wires = face.Wires()
        print(f"WIRE SCAN on {label}: total_wires={len(wires)}")
        best = None
        best_rmax = -1
        best_stats = None
        for i, w in enumerate(wires):
            pts = sample_wire_points(w, pts_per_edge=20)
            if len(pts) < 10:
                print(f"  wire[{i}] edges={len(w.Edges())} -> too few pts ({len(pts)})")
                continue
            rs = [r_ang_from_pt(x, z)[0] for (x, _y, z) in pts]
            rmin, rmax = min(rs), max(rs)
            print(f"  wire[{i}] edges={len(w.Edges())} pts={len(pts)}  rmin={rmin:.6f}  rmax={rmax:.6f}  spread={rmax-rmin:.6f}")
            if rmax > best_rmax:
                best_rmax = rmax
                best = w
                best_stats = (rmin, rmax, pts)
        return best, best_stats

    top_wire, top_stats = pick_tooth_wire(top_face, "top(Y=0)")
    bot_wire, bot_stats = pick_tooth_wire(bot_face, "bot(Y=-3.175)")
    if not top_wire or not bot_wire:
        print("ERROR: could not pick tooth-perimeter wire on one/both boundary faces; returning input unchanged")
        return shape

    # Prefer the boundary (top or bottom) that has the larger radius spread (better to detect root/tip)
    top_rmin, top_rmax, top_pts = top_stats
    bot_rmin, bot_rmax, bot_pts = bot_stats
    top_spread = top_rmax - top_rmin
    bot_spread = bot_rmax - bot_rmin

    use_label = "top(Y=0)" if top_spread >= bot_spread else "bot(Y=-3.175)"
    use_pts = top_pts if top_spread >= bot_spread else bot_pts
    root_r0 = min(r_ang_from_pt(x, z)[0] for (x, _y, z) in use_pts)
    tip_r0 = max(r_ang_from_pt(x, z)[0] for (x, _y, z) in use_pts)

    print(f"MEASURED from {use_label} tooth-perimeter wire samples:")
    print(f"  root_r(min)={root_r0:.6f}  tip_r(max)={tip_r0:.6f}  spread={tip_r0-root_r0:.6f}")
    if tip_r0 - root_r0 < 0.2:
        print("ERROR: root/tip radii nearly equal -> tooth perimeter not detected (would degenerate the profile). Returning input unchanged.")
        return shape

    # ---- Determine root/tip angular widths in the reference tooth sector ----
    pitch = 360.0 / 27.0

    def wrap_deg(a):
        # to [-180, 180)
        a = (a + 180.0) % 360.0 - 180.0
        return a

    def angle_span_near_radius(pts, theta0_deg, radius_target, half_window_deg, want_label, tol0=0.05):
        # return (aL_abs, aR_abs, tol, n)
        theta0 = theta0_deg
        # build list of candidate (rel_ang, abs_ang, r)
        rel_list = []
        for (x, _y, z) in pts:
            r, a = r_ang_from_pt(x, z)
            rel = wrap_deg(a - theta0)
            if abs(rel) <= half_window_deg:
                rel_list.append((rel, a, r))
        if not rel_list:
            return None

        tol = tol0
        chosen = []
        for _ in range(10):
            chosen = [t for t in rel_list if abs(t[2] - radius_target) <= tol]
            if len(chosen) >= 8:
                break
            tol *= 1.5
        if len(chosen) < 2:
            return None

        rels = [t[0] for t in chosen]
        relL, relR = min(rels), max(rels)
        aL = (theta0 + relL) % 360.0
        aR = (theta0 + relR) % 360.0
        # Unwrap so that aR is ahead of aL in the local window
        # We'll return in local-unwrapped form as well
        return {
            "relL": relL,
            "relR": relR,
            "aL": aL,
            "aR": aR,
            "tol": tol,
            "n": len(chosen),
            "label": want_label,
        }

    # half-window just under half pitch to avoid grabbing neighbors
    half_win = 0.49 * pitch
    root_span = angle_span_near_radius(use_pts, theta0_mod, root_r0, half_win, "root")
    tip_span = angle_span_near_radius(use_pts, theta0_mod, tip_r0, half_win, "tip")
    if not root_span or not tip_span:
        print("ERROR: could not measure root/tip angular spans near reference sector; returning input unchanged")
        print(f"  root_span={root_span}")
        print(f"  tip_span={tip_span}")
        return shape

    print("REFERENCE TOOTH INTERSECTIONS (from chosen boundary loop samples):")
    print(f"  root: relL={root_span['relL']:.6f} relR={root_span['relR']:.6f}  tol={root_span['tol']:.4f}mm  n={root_span['n']}")
    print(f"  tip : relL={tip_span['relL']:.6f}  relR={tip_span['relR']:.6f}   tol={tip_span['tol']:.4f}mm  n={tip_span['n']}")
    print(f"  root angular width={root_span['relR']-root_span['relL']:.6f} deg")
    print(f"  tip  angular width={tip_span['relR']-tip_span['relL']:.6f} deg")

    # ---- print all 27 tooth center angles and successive differences ----
    angles = [theta0_mod + k * pitch for k in range(27)]
    angles_mod = [((a % 360.0) + 360.0) % 360.0 for a in angles]
    print("TOOTH CENTER ANGLES (mod 360):")
    for k, a in enumerate(angles_mod):
        print(f"  k={k:02d}  theta={a:.6f}")
    print("SUCCESSIVE DIFFERENCES (unwrapped, should all be 13.333333):")
    for k in range(26):
        d = angles[k + 1] - angles[k]
        print(f"  d[{k:02d}->{k+1:02d}] = {d:.6f}")

    # ---- Build one closed XZ tooth profile (root arc on root_r, straight flanks, straight tip) ----
    def polar_point(r, ang_deg):
        a = math.radians(ang_deg)
        return (cx + r * math.cos(a), cz + r * math.sin(a))

    # Use local-relative angles to avoid wrap issues
    root_relL, root_relR = root_span["relL"], root_span["relR"]
    tip_relL, tip_relR = tip_span["relL"], tip_span["relR"]

    # Convert to absolute (unwrapped around theta0)
    # (Angles passed to polar_point can be modded; trig is periodic)
    a_root_L = theta0_mod + root_relL
    a_root_R = theta0_mod + root_relR
    a_tip_L = theta0_mod + tip_relL
    a_tip_R = theta0_mod + tip_relR

    # Root arc mid point for threePointArc
    a_root_M = theta0_mod + 0.5 * (root_relL + root_relR)

    x_lr, z_lr = polar_point(root_r0, a_root_L)
    x_rr, z_rr = polar_point(root_r0, a_root_R)
    x_mr, z_mr = polar_point(root_r0, a_root_M)
    x_lt, z_lt = polar_point(tip_r0, a_tip_L)
    x_rt, z_rt = polar_point(tip_r0, a_tip_R)

    print("PROFILE POINTS (XZ at Y=-3.175 workplane):")
    print(f"  left_root  r={root_r0:.6f} a={a_root_L:.6f} -> ({x_lr:.6f},{z_lr:.6f})")
    print(f"  mid_root   r={root_r0:.6f} a={a_root_M:.6f} -> ({x_mr:.6f},{z_mr:.6f})")
    print(f"  right_root r={root_r0:.6f} a={a_root_R:.6f} -> ({x_rr:.6f},{z_rr:.6f})")
    print(f"  right_tip  r={tip_r0:.6f}  a={a_tip_R:.6f} -> ({x_rt:.6f},{z_rt:.6f})")
    print(f"  left_tip   r={tip_r0:.6f}  a={a_tip_L:.6f} -> ({x_lt:.6f},{z_lt:.6f})")

    # Sketch plane at Y=-3.175, normal +Y, coordinates are (x,z)
    plane = cq.Plane(origin=(0, y_bot, 0), normal=(0, 1, 0), xDir=(1, 0, 0))
    print(f"SKETCH PLANE origin={(0, y_bot, 0)} normal=(0,1,0) xDir=(1,0,0)")

    tooth_wp = (
        cq.Workplane(plane)
        .moveTo(x_lr, z_lr)
        .threePointArc((x_mr, z_mr), (x_rr, z_rr))
        .lineTo(x_rt, z_rt)          # straight right flank
        .lineTo(x_lt, z_lt)          # straight tip segment
        .close()                     # straight left flank back to left_root
    )

    # Extrude from Y=-3.175 to Y=0: distance 3.175 along +Y
    tooth_len = y_top - y_bot
    if tooth_len <= 0:
        print("ERROR: invalid axial limits; returning input")
        return shape

    try:
        tooth_solid = tooth_wp.extrude(tooth_len, both=False).val()
    except Exception as e:
        print(f"ERROR: tooth profile extrude failed: {e}")
        return shape

    # Pattern 27 copies about Y axis
    teeth = tooth_solid
    for k in range(1, 27):
        tk = tooth_solid.rotate((0, 0, 0), (0, 1, 0), k * pitch)
        teeth = teeth.fuse(tk)

    added_bb = teeth.BoundingBox()
    print("ADDED (new straight-tooth rim) bbox:")
    print(f"  {bbox6(added_bb)}")
    print(f"  EXPECT added ymin..ymax approx {y_bot}..{y_top}")

    # ---- Remove original rim in that axial interval with an annular cutter, then fuse the new teeth ----
    outer_cut_r = tip_r0 + 6.0
    inner_cut_r = root_r0

    cutter_wp = cq.Workplane(plane).circle(outer_cut_r).circle(inner_cut_r)
    cutter = cutter_wp.extrude(tooth_len, both=False).val()
    print(f"CUTTER annulus: inner_r={inner_cut_r:.6f}, outer_r={outer_cut_r:.6f}, extrude Y={y_bot}..{y_top}")

    s1_bb_before = s1.BoundingBox()
    print("s1 bbox BEFORE:")
    print(f"  {bbox6(s1_bb_before)}")

    s1_cut = s1.cut(cutter)
    s1_new = s1_cut.fuse(teeth)

    s1_bb_after = s1_new.BoundingBox()
    print("s1 bbox AFTER:")
    print(f"  {bbox6(s1_bb_after)}")

    # ---- Re-measure root/tip radii on the edited solid using the same procedure ----
    top_faces_after = planar_faces_at_y(s1_new, y_top, ny_sign=+1)
    bot_faces_after = planar_faces_at_y(s1_new, y_bot, ny_sign=-1)
    if top_faces_after and bot_faces_after:
        top_face_a = max(top_faces_after, key=lambda f: f.Area())
        bot_face_a = max(bot_faces_after, key=lambda f: f.Area())
        top_wire_a, top_stats_a = pick_tooth_wire(top_face_a, "top_after(Y=0)")
        bot_wire_a, bot_stats_a = pick_tooth_wire(bot_face_a, "bot_after(Y=-3.175)")
        if top_wire_a and bot_wire_a:
            trmin, trmax, tpts = top_stats_a
            brmin, brmax, bpts = bot_stats_a
            use_pts_a = tpts if (trmax - trmin) >= (brmax - brmin) else bpts
            root_r1 = min(r_ang_from_pt(x, z)[0] for (x, _y, z) in use_pts_a)
            tip_r1 = max(r_ang_from_pt(x, z)[0] for (x, _y, z) in use_pts_a)
            print("RADII CHECK (from boundary loop samples):")
            print(f"  BEFORE root={root_r0:.6f} tip={tip_r0:.6f}")
            print(f"  AFTER  root={root_r1:.6f} tip={tip_r1:.6f}")
            print(f"  deltas root={root_r1-root_r0:+.6f} tip={tip_r1-tip_r0:+.6f}")
        else:
            print("WARNING: could not pick tooth wire on edited solid for radii check")
    else:
        print("WARNING: could not find planar boundary faces on edited solid for radii check")

    # ---- Verify removal of the old rounded cylindrical tooth-end surfaces (r=1.9844 and r=3.9942) ----
    def count_cyl_faces_radius(solid, r_target, r_tol=5e-3):
        n = 0
        for f in solid.Faces():
            info = cyl_info(f)
            if not info:
                continue
            if abs(info["r"] - r_target) <= r_tol and is_axis_y(info["axis"], tol=5e-3):
                n += 1
        return n

    n_cyl_198 = count_cyl_faces_radius(s1_new, r_cyl_small)
    n_cyl_399 = count_cyl_faces_radius(s1_new, r_cyl_large)
    print(f"POST-CHECK: cylindrical faces on axis~Y with r~{r_cyl_small} remaining in s1_new: {n_cyl_198}")
    print(f"POST-CHECK: cylindrical faces on axis~Y with r~{r_cyl_large} remaining in s1_new: {n_cyl_399}")

    # ---- Re-compound with s0 untouched ----
    out_sols = []
    for i, s in enumerate(sols):
        out_sols.append(s1_new if i == s1_i else s)
    out = cq.Compound.makeCompound(out_sols)

    # Overall bbox sanity (unchanged envelope expectation)
    bb_in = base.BoundingBox()
    bb_out = out.BoundingBox()
    print("OVERALL bbox BEFORE (compound):")
    print(f"  {bbox6(bb_in)}")
    print("OVERALL bbox AFTER  (compound):")
    print(f"  {bbox6(bb_out)}")

    return out