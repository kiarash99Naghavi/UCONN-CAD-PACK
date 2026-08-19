def my_cad_function(args):
    import cadquery as cq
    import math

    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input
    base = shape.val() if hasattr(shape, "val") else shape

    def v3(p):
        return (float(p.x), float(p.y), float(p.z))

    # --- Split solids, pick target s1 (large gear) ---
    sols = base.Solids()
    print(f"SELECTED: {len(sols)} solids total")
    for i, s in enumerate(sols):
        bb = s.BoundingBox()
        try:
            vol = s.Volume()
        except Exception:
            vol = float('nan')
        print(
            f"  solid[{i}] volume={vol:.3f} bbox=([{bb.xmin:.3f},{bb.ymin:.3f},{bb.zmin:.3f}]..[{bb.xmax:.3f},{bb.ymax:.3f},{bb.zmax:.3f}])"
        )

    # Choose s1 as largest volume
    vols = [s.Volume() for s in sols]
    s1_idx = max(range(len(sols)), key=lambda i: vols[i])
    s1 = sols[s1_idx]
    print(f"SELECTED: solid[{s1_idx}] as target body s1 (largest volume)")

    # Identify other solid(s) to keep untouched
    other_idxs = [i for i in range(len(sols)) if i != s1_idx]
    if other_idxs:
        print(f"SELECTED: {len(other_idxs)} other solids as untouched: idx={other_idxs}")

    # --- Resolve reference faces by global face index as per geometry index ---
    faces_all = base.Faces()
    print(f"SELECTED: {len(faces_all)} faces in imported shape")

    ref_face_1_idx = 671  # r=1.9844 family
    ref_face_2_idx = 779  # r=3.9942 family

    if ref_face_1_idx >= len(faces_all) or ref_face_2_idx >= len(faces_all):
        print("ERROR: reference face index out of range -> NO-OP")
        return shape

    f1 = faces_all[ref_face_1_idx]
    f2 = faces_all[ref_face_2_idx]
    c1 = f1.Center()
    c2 = f2.Center()
    print(f"SELECTED: 1 face for reference face_idx={ref_face_1_idx} center={v3(c1)} area={f1.Area():.6f}")
    print(f"SELECTED: 1 face for reference face_idx={ref_face_2_idx} center={v3(c2)} area={f2.Area():.6f}")

    # --- Confirm gear axis is Y (as required) ---
    gear_axis = cq.Vector(0.0, 1.0, 0.0)
    print(f"GEAR AXIS (required): {v3(gear_axis)}")

    # --- Pitch/tooth center angles (must print + confirm) ---
    n_teeth = 27
    pitch = 360.0 / n_teeth

    def angle_deg_from_xz(x, z):
        a = math.degrees(math.atan2(z, x))
        if a < 0:
            a += 360.0
        return a

    a1 = angle_deg_from_xz(c1.x, c1.z)
    a2 = angle_deg_from_xz(c2.x, c2.z)
    ref_angle = (a1 + a2) * 0.5

    # Normalize
    ref_angle = ref_angle % 360.0

    print(f"PITCH CHECK: computed pitch=360/27={pitch:.12f} deg (target 13.333333333333) delta={pitch-13.333333333333:+.3e}")
    print(f"CLOCKING REFERENCE: using tooth from faces #{ref_face_1_idx}/#{ref_face_2_idx} -> ref_angle={ref_angle:.6f} deg")

    tooth_center_angles = [((ref_angle + i * pitch) % 360.0) for i in range(n_teeth)]
    print("TOOTH CENTER ANGLES (deg):")
    for i, a in enumerate(tooth_center_angles):
        print(f"  tooth[{i:02d}] center_angle={a:.6f}")
    print(f"CONFIRM: tooth[00] center_angle={tooth_center_angles[0]:.6f} (should match ref_angle {ref_angle:.6f})")

    # --- Use the repeated rounded-tooth face families to measure root/tip radii robustly ---
    # Families called out in prompt (complete 27-tooth outer rim)
    fam_r1 = [671, 675, 679, 683, 687, 691, 695, 699, 703, 707, 711, 715, 719, 723, 727, 731, 735, 739, 743, 747, 751, 755, 759, 763, 767, 771, 775]
    fam_r2 = [779, 781, 783, 785, 787, 789, 791, 793, 795, 797, 799, 801, 803, 805, 807, 809, 811, 813, 815, 817, 819, 821, 823, 825, 827, 829, 831]

    fam_idxs = fam_r1 + fam_r2
    tooth_faces = []
    for idx in fam_idxs:
        if idx < len(faces_all):
            tooth_faces.append(faces_all[idx])
    print(f"SELECTED: {len(tooth_faces)} faces from the two rounded-tooth families for rim radius measurement")

    # Sample points from edges of these faces (these are guaranteed to be on the outer tooth region)
    r_samples = []
    sampled_edges = 0
    for f in tooth_faces:
        for e in f.Edges():
            sampled_edges += 1
            try:
                pts = e.discretize(40)
            except Exception:
                pts = [v.Center() for v in e.Vertices()]
            for p in pts:
                x, z = float(p.x), float(p.z)
                r_samples.append(math.hypot(x, z))

    print(f"SELECTED: {sampled_edges} edges from those faces for radial sampling")
    if not r_samples:
        print("SELECTED: 0 radial samples from rounded-tooth families -> NO-OP")
        return shape

    r_samples.sort()
    n = len(r_samples)

    def percentile(sorted_list, p):
        if not sorted_list:
            return None
        p = max(0.0, min(1.0, p))
        i = int(round(p * (len(sorted_list) - 1)))
        return float(sorted_list[i])

    r_min = float(r_samples[0])
    r_p02 = percentile(r_samples, 0.02)
    r_p05 = percentile(r_samples, 0.05)
    r_p95 = percentile(r_samples, 0.95)
    r_p98 = percentile(r_samples, 0.98)
    r_max = float(r_samples[-1])

    # Root radius: use a low percentile (robust vs a stray point)
    r_root = float(r_p02)
    # Tip radius: use max (preserve the existing envelope)
    r_tip = float(r_max)

    print("RIM RADII FROM ROUNDED-TOOTH FAMILIES (r=1.9844 and r=3.9942):")
    print(f"  r_min={r_min:.6f}  r_p02={r_p02:.6f}  r_p05={r_p05:.6f}  r_p95={r_p95:.6f}  r_p98={r_p98:.6f}  r_max={r_max:.6f}")
    print(f"  ==> using r_root=r_p02={r_root:.6f} ; r_tip=r_max={r_tip:.6f}")

    # Cross-check against s1 bounding box extents in X/Z
    bb1 = s1.BoundingBox()
    x_ext = max(abs(float(bb1.xmin)), abs(float(bb1.xmax)))
    z_ext = max(abs(float(bb1.zmin)), abs(float(bb1.zmax)))
    print(f"S1 BBOX X/Z EXTENTS CHECK: x_ext={x_ext:.6f} z_ext={z_ext:.6f} (tip radius measured {r_tip:.6f})")

    # --- Axial extent must be preserved (Y axis) ---
    y_min, y_max = float(bb1.ymin), float(bb1.ymax)
    thickness = float(bb1.ylen)
    y_mid = 0.5 * (y_min + y_max)
    print(f"AXIAL EXTENT s1: y_min={y_min:.6f} y_max={y_max:.6f} thickness={thickness:.6f} y_mid={y_mid:.6f}")

    # --- Build one straight-sided tooth (flat tip chord) extruded along Y ---
    # Choose a thinner-than-half-pitch tooth to minimize bbox shrink due to flat chord vs arc.
    tooth_pitch_fraction = 0.40  # fraction of pitch occupied by tooth (straight flanks)
    tooth_thickness_ang = pitch * tooth_pitch_fraction
    half_ang = 0.5 * tooth_thickness_ang
    print(f"TOOTH ANGULAR THICKNESS: fraction={tooth_pitch_fraction:.3f} tooth_thickness={tooth_thickness_ang:.6f} deg half_angle={half_ang:.6f} deg")

    # Slight inward overlap to ensure fuse with core
    overlap_radial = 0.50
    r_base = max(0.1, r_root - overlap_radial)

    def pol_xz(r, a_deg):
        a = math.radians(a_deg)
        return (r * math.cos(a), r * math.sin(a))  # (x,z) in sketch coordinates

    # Tooth is drawn centered at angle=0, then rotated to each tooth center angle.
    p1 = pol_xz(r_base, -half_ang)
    p2 = pol_xz(r_tip, -half_ang)
    p3 = pol_xz(r_tip, +half_ang)
    p4 = pol_xz(r_base, +half_ang)
    print(f"TOOTH 2D PROFILE (XZ @ y_mid): p1={p1}, p2={p2}, p3={p3}, p4={p4}")

    tooth_plane = cq.Plane(origin=(0.0, y_mid, 0.0), xDir=(1.0, 0.0, 0.0), normal=(0.0, -1.0, 0.0))
    print(f"SKETCH PLANE: origin=(0,{y_mid:.6f},0) normal=(0,-1,0) xDir=(1,0,0)")

    half_thick = 0.5 * thickness
    tooth_solid = (
        cq.Workplane(tooth_plane)
        .polyline([p1, p2, p3, p4])
        .close()
        .extrude(half_thick, both=True)
        .val()
    )
    bb_t = tooth_solid.BoundingBox()
    print(f"TOOTH SOLID: y_span=[{bb_t.ymin:.6f},{bb_t.ymax:.6f}] (target [{y_min:.6f},{y_max:.6f}])")

    # Create 27 teeth via rotation about Y
    teeth_list = []
    for i, ang in enumerate(tooth_center_angles):
        teeth_list.append(tooth_solid.rotate((0, 0, 0), (0, 1, 0), ang))
    print(f"SELECTED: {len(teeth_list)} rotated tooth solids")
    teeth = cq.Compound.makeCompound(teeth_list)

    # --- Remove old rounded tooth rim fully by trimming s1 to the measured root cylinder ---
    eps_y = 0.5
    cyl_h = thickness + 2.0 * eps_y
    keep_cyl = cq.Solid.makeCylinder(
        r_root,
        cyl_h,
        pnt=cq.Vector(0.0, y_min - eps_y, 0.0),
        dir=cq.Vector(0.0, 1.0, 0.0),
    )
    print(f"ROOT KEEP CYLINDER: r={r_root:.6f} h={cyl_h:.6f} y_span=[{(y_min-eps_y):.6f},{(y_max+eps_y):.6f}]")

    try:
        s1_trim = s1.intersect(keep_cyl)
        print("BOOLEAN: s1 intersect(root-cylinder) -> old outer rounded rim removed outside root diameter")
    except Exception as e:
        print(f"ERROR: intersect failed: {e} -> NO-OP")
        return shape

    # Fuse new teeth onto the trimmed body
    try:
        s1_new = s1_trim.fuse(teeth)
        print("BOOLEAN: trimmed base fuse(new straight teeth) -> s1 reprofiled")
    except Exception as e:
        print(f"ERROR: fuse teeth failed: {e} -> trying iterative fuse")
        s1_new = s1_trim
        ok = 0
        for t in teeth_list:
            try:
                s1_new = s1_new.fuse(t)
                ok += 1
            except Exception:
                pass
        print(f"ITERATIVE FUSE: fused {ok}/{len(teeth_list)} teeth")

    # --- Self-check: bbox must preserve outer envelope and axial extent ---
    bb_before = s1.BoundingBox()
    bb_after = s1_new.BoundingBox()
    print(
        "BBOX CHECK (s1):\n"
        f"  BEFORE: ([{bb_before.xmin:.3f},{bb_before.ymin:.3f},{bb_before.zmin:.3f}]..[{bb_before.xmax:.3f},{bb_before.ymax:.3f},{bb_before.zmax:.3f}])\n"
        f"  AFTER : ([{bb_after.xmin:.3f},{bb_after.ymin:.3f},{bb_after.zmin:.3f}]..[{bb_after.xmax:.3f},{bb_after.ymax:.3f},{bb_after.zmax:.3f}])\n"
        f"  DELTA : dxmin={bb_after.xmin-bb_before.xmin:+.3f} dxmax={bb_after.xmax-bb_before.xmax:+.3f} "
        f"dzmin={bb_after.zmin-bb_before.zmin:+.3f} dzmax={bb_after.zmax-bb_before.zmax:+.3f} "
        f"dymin={bb_after.ymin-bb_before.ymin:+.3f} dymax={bb_after.ymax-bb_before.ymax:+.3f}"
    )

    # Added/removed diagnostics
    try:
        added = s1_new.cut(s1)
        bb_a = added.BoundingBox()
        ca = added.Center()
        print(f"SELF-CHECK ADDED: center={v3(ca)} bbox=([{bb_a.xmin:.3f},{bb_a.ymin:.3f},{bb_a.zmin:.3f}]..[{bb_a.xmax:.3f},{bb_a.ymax:.3f},{bb_a.zmax:.3f}])")
    except Exception as e:
        print(f"SELF-CHECK ADDED: failed to compute (non-fatal): {e}")

    try:
        removed = s1.cut(s1_new)
        bb_r = removed.BoundingBox()
        cr = removed.Center()
        print(f"SELF-CHECK REMOVED: center={v3(cr)} bbox=([{bb_r.xmin:.3f},{bb_r.ymin:.3f},{bb_r.zmin:.3f}]..[{bb_r.xmax:.3f},{bb_r.ymax:.3f},{bb_r.zmax:.3f}])")
    except Exception as e:
        print(f"SELF-CHECK REMOVED: failed to compute (non-fatal): {e}")

    # --- Recompound: keep all other solids untouched and keep original order ---
    solids_out = []
    for i, s in enumerate(sols):
        solids_out.append(s1_new if i == s1_idx else s)
    out = cq.Compound.makeCompound(solids_out) if len(solids_out) > 1 else s1_new
    print(f"OUTPUT: recompounded {len(solids_out)} solids; edited solid index={s1_idx}")

    return out