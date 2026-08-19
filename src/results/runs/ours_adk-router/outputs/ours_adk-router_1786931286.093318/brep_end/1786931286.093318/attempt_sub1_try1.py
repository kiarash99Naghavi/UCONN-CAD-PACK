def my_cad_function(args):
    import cadquery as cq
    import math

    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input
    base = shape.val() if hasattr(shape, "val") else shape

    # --- Named numbers (explicit) ---
    axis_origin = cq.Vector(80.0, 24.0, 4.0)
    axis_dir = cq.Vector(0.0, 1.0, 0.0)
    axis_p1 = axis_origin
    axis_p2 = axis_origin.add(axis_dir)

    target_landings = [225.0, 270.0, 315.0, 0.0, 45.0, 90.0, 135.0]  # degrees in XZ plane
    source_landing_nominal = 180.0  # per instruction

    print("TARGET pattern axis / angles:")
    print(f"  axis_origin={[float(axis_origin.x), float(axis_origin.y), float(axis_origin.z)]}")
    print(f"  axis_dir   ={[float(axis_dir.x), float(axis_dir.y), float(axis_dir.z)]}")
    print(f"  source_landing_nominal={source_landing_nominal}")
    print(f"  target_landings={target_landings}")

    # --- Resolve and report key index faces (diagnostics only) ---
    faces = base.Faces()
    want_faces = [1, 6, 7, 10, 3]
    for fi in want_faces:
        if fi < len(faces):
            f = faces[fi]
            try:
                c = f.Center()
            except Exception:
                c = cq.Vector(0, 0, 0)
            try:
                a = f.Area()
            except Exception:
                a = None
            try:
                n = f.normalAt()
                nxyz = [float(n.x), float(n.y), float(n.z)]
            except Exception:
                nxyz = None
            print(
                f"SELECTED: 1 face for index face #{fi}  idx=[{fi}] "
                f"center={[float(c.x), float(c.y), float(c.z)]} area={float(a) if a is not None else None} normal={nxyz}"
            )
        else:
            print(f"SELECTED: 0 faces for index face #{fi} (out of range)  idx=[{fi}]  nFaces={len(faces)}")

    # --- Identify solids: pattern the largest (arm) and keep the rest unchanged (hub/bearing/etc.) ---
    solids = list(base.Solids())
    print(f"SELECTED: {len(solids)} solids in imported shape")
    if len(solids) == 0:
        print("SELECTED: 0 solids (unexpected) - returning input shape")
        return base

    vols = []
    for i, s in enumerate(solids):
        try:
            v = float(s.Volume())
        except Exception:
            v = float('nan')
        bb = s.BoundingBox()
        vols.append((v, i))
        print(
            f"  solid[{i}]: vol={v:.3f} bbox=(xmin={bb.xmin:.3f}, xmax={bb.xmax:.3f}, ymin={bb.ymin:.3f}, ymax={bb.ymax:.3f}, zmin={bb.zmin:.3f}, zmax={bb.zmax:.3f})"
        )

    # pick largest by volume (arm)
    vols_sorted = sorted([t for t in vols if not math.isnan(t[0])], reverse=True)
    arm_idx = vols_sorted[0][1] if vols_sorted else 0
    source_solid = solids[arm_idx]
    other_solids = [s for i, s in enumerate(solids) if i != arm_idx]
    print(f"SELECTED: 1 solid as source arm (largest volume)  idx=[{arm_idx}]")
    print(f"SELECTED: {len(other_solids)} solids kept unchanged (non-arm bodies)")

    # --- Reference point for landing angle measurement ---
    # Use cylindrical face #10 center if available (per index it's on the source arm and sits at x=0, z=4)
    ref_pt = None
    if 10 < len(faces):
        try:
            ref_pt = faces[10].Center()
        except Exception:
            ref_pt = None
    if ref_pt is None:
        # fallback: use source solid center
        ref_pt = source_solid.Center()
        print("WARNING: could not resolve face #10 center; using source solid Center() as reference")

    print(f"REFERENCE point for landing measurement: {[float(ref_pt.x), float(ref_pt.y), float(ref_pt.z)]}")

    def norm_angle_deg(a):
        a = a % 360.0
        return a + 360.0 if a < 0 else a

    def ang_diff_deg(a, b):
        # shortest signed difference a-b in degrees
        d = (a - b + 180.0) % 360.0 - 180.0
        return d

    def angle_xz_from_axis(pt_vec):
        v = pt_vec.sub(axis_origin)
        ang = math.degrees(math.atan2(float(v.z), float(v.x)))
        return norm_angle_deg(ang)

    def rotate_point_about_y(p, deg):
        # rotate point p around the +Y axis that passes through axis_origin by deg
        th = math.radians(deg)
        v = p.sub(axis_origin)
        x = float(v.x)
        z = float(v.z)
        xr = x * math.cos(th) + z * math.sin(th)
        zr = -x * math.sin(th) + z * math.cos(th)
        return cq.Vector(axis_origin.x + xr, p.y, axis_origin.z + zr)

    base_measured = angle_xz_from_axis(ref_pt)
    print(f"MEASURED source landing angle from reference (XZ plane): {base_measured:.3f} deg (nominal treated as {source_landing_nominal:.3f})")

    # --- Build rotated instances ---
    occupied = [norm_angle_deg(source_landing_nominal)]  # treat source as 180 as instructed
    placed_solids = [source_solid]

    print("Placing 7 rotated copies (uniform 45-deg spacing):")

    for t in target_landings:
        # nominal delta based on instruction's 180-degree source landing
        delta = norm_angle_deg(t - source_landing_nominal)
        if delta > 180.0:
            delta -= 360.0

        # try nominal sign first
        trial_delta = float(delta)
        pt_trial = rotate_point_about_y(ref_pt, trial_delta)
        ang_trial = angle_xz_from_axis(pt_trial)

        # occupancy check: if too close to any occupied angle, flip sign and retry
        too_close = any(abs(ang_diff_deg(ang_trial, occ)) <= 10.0 for occ in occupied)

        if too_close:
            trial_delta2 = -trial_delta
            pt_trial2 = rotate_point_about_y(ref_pt, trial_delta2)
            ang_trial2 = angle_xz_from_axis(pt_trial2)
            too_close2 = any(abs(ang_diff_deg(ang_trial2, occ)) <= 10.0 for occ in occupied)

            print(
                f"  target={t:7.3f} deg: nominal_delta={delta:+8.3f} -> achieved={ang_trial:8.3f} (TOO CLOSE), "
                f"flipping sign -> delta={trial_delta2:+8.3f} achieved={ang_trial2:8.3f} "
                f"{'(STILL TOO CLOSE)' if too_close2 else ''}"
            )
            trial_delta = trial_delta2
            ang_trial = ang_trial2
        else:
            print(
                f"  target={t:7.3f} deg: nominal_delta={delta:+8.3f} -> achieved={ang_trial:8.3f}"
            )

        # record occupied and create rotated copy
        occupied.append(norm_angle_deg(ang_trial))
        try:
            cp = source_solid.rotate(
                (float(axis_p1.x), float(axis_p1.y), float(axis_p1.z)),
                (float(axis_p2.x), float(axis_p2.y), float(axis_p2.z)),
                float(trial_delta),
            )
            placed_solids.append(cp)
        except Exception as e:
            print(f"  ERROR rotating copy for target={t:.3f}: {e}")

    print(f"SELECTED: {len(placed_solids)} arm instances total (including source) expected=8")

    # --- Compose output: keep other solids unchanged + all arm instances ---
    out_shapes = list(other_solids) + placed_solids
    out = cq.Compound.makeCompound(out_shapes) if len(out_shapes) > 1 else out_shapes[0]

    # --- Self-check: report achieved angles list ---
    print("Achieved occupied landing angles (deg, XZ plane):")
    for i, ang in enumerate(occupied):
        print(f"  arm[{i}] angle={norm_angle_deg(ang):.3f}")

    # Also report output solids count
    try:
        out_sols = list(out.Solids())
        print(f"OUTPUT: solids={len(out_sols)} (unchanged bodies kept: {len(other_solids)}; arm instances: {len(placed_solids)})")
    except Exception as e:
        print(f"OUTPUT: could not enumerate solids ({e})")

    return out