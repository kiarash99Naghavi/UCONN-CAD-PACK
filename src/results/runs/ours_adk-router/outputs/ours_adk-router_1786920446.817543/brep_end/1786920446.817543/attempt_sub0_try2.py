def my_cad_function(args):
    import cadquery as cq
    import math

    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input

    def v3(p):
        return (float(p.x), float(p.y), float(p.z))

    base = shape.val() if hasattr(shape, "val") else shape
    sols = base.Solids()
    print(f"SELECTED: {len(sols)} solids total")
    for i, s in enumerate(sols):
        bb = s.BoundingBox()
        print(f"  solid[{i}] volume={s.Volume():.3f} bbox=([{bb.xmin:.3f},{bb.ymin:.3f},{bb.zmin:.3f}]..[{bb.xmax:.3f},{bb.ymax:.3f},{bb.zmax:.3f}])")

    if len(sols) < 2:
        print("SELECTED: 0/2 solids? unexpected; returning original")
        return shape

    # s1 is the larger 113mm gear
    s1_idx = max(range(len(sols)), key=lambda i: sols[i].Volume())
    s0_idx = 0 if s1_idx != 0 else 1
    s1 = sols[s1_idx]
    s0 = sols[s0_idx] if len(sols) > 1 else None
    print(f"SELECTED: solid[{s1_idx}] as target body s1 (largest volume)")
    if s0 is not None:
        print(f"SELECTED: solid[{s0_idx}] as untouched body s0")

    faces_all = base.Faces()
    print(f"SELECTED: {len(faces_all)} faces in imported compound")

    # --- Resolve reference faces (must match geometry index) ---
    ref_idx_1 = 671
    ref_idx_2 = 779
    if ref_idx_1 >= len(faces_all) or ref_idx_2 >= len(faces_all):
        print(f"ERROR: reference face indices out of range: {ref_idx_1},{ref_idx_2} with nFaces={len(faces_all)}")
        return shape

    f1 = faces_all[ref_idx_1]
    f2 = faces_all[ref_idx_2]
    print(f"SELECTED: 1 face for reference face_idx={ref_idx_1}  center={v3(f1.Center())} area={f1.Area():.6f}")
    print(f"SELECTED: 1 face for reference face_idx={ref_idx_2}  center={v3(f2.Center())} area={f2.Area():.6f}")

    # --- Extract cylinder axis points for clocking (OCP adaptor, robust on imported STEP) ---
    try:
        from OCP.BRepAdaptor import BRepAdaptor_Surface
        from OCP.GeomAbs import GeomAbs_Cylinder
    except Exception as e:
        print(f"ERROR: cannot import OCP adaptors to read cylinder axis: {e}")
        return shape

    def cyl_axis_point_and_radius(face):
        ad = BRepAdaptor_Surface(face.wrapped)
        if ad.GetType() != GeomAbs_Cylinder:
            return None
        cyl = ad.Cylinder()
        ax = cyl.Axis()  # gp_Ax1
        loc = ax.Location()  # gp_Pnt
        d = ax.Direction()
        return (cq.Vector(loc.X(), loc.Y(), loc.Z()), cq.Vector(d.X(), d.Y(), d.Z()), float(cyl.Radius()))

    c1 = cyl_axis_point_and_radius(f1)
    c2 = cyl_axis_point_and_radius(f2)
    if c1 is None or c2 is None:
        print("ERROR: one or both reference faces are not cylindrical according to adaptor -> NO-OP")
        return shape

    axpt1, axdir1, rad1 = c1
    axpt2, axdir2, rad2 = c2
    print(f"REF CYL 1: axis_pt={v3(axpt1)} axis_dir={v3(axdir1)} cyl_r={rad1:.6f}")
    print(f"REF CYL 2: axis_pt={v3(axpt2)} axis_dir={v3(axdir2)} cyl_r={rad2:.6f}")

    # confirm axis parallel to measured gear axis [0,1,0]
    def dir_parallel_y(dv):
        dvn = dv.normalized()
        return (abs(float(dvn.x)) < 1e-3 and abs(abs(float(dvn.y)) - 1.0) < 1e-3 and abs(float(dvn.z)) < 1e-3)

    print(f"AXIS CHECK: ref1_parallel_Y={dir_parallel_y(axdir1)}  ref2_parallel_Y={dir_parallel_y(axdir2)}")

    def ang_deg_from_xz(v):
        a = math.degrees(math.atan2(float(v.z), float(v.x)))
        a = a % 360.0
        return a

    a1 = ang_deg_from_xz(axpt1)
    a2 = ang_deg_from_xz(axpt2)
    print(f"REF ANGLES: face#{ref_idx_1} axis_pt angle={a1:.6f} deg ; face#{ref_idx_2} axis_pt angle={a2:.6f} deg")

    # circular mean for reference tooth center (between the two rounded surfaces)
    u1 = (math.cos(math.radians(a1)), math.sin(math.radians(a1)))
    u2 = (math.cos(math.radians(a2)), math.sin(math.radians(a2)))
    um = (u1[0] + u2[0], u1[1] + u2[1])
    ref_angle = math.degrees(math.atan2(um[1], um[0])) % 360.0

    n_teeth = 27
    pitch = 360.0 / n_teeth
    print(f"PITCH CHECK: computed pitch=360/{n_teeth}={pitch:.12f} deg (target 13.333333333333) delta={pitch-13.333333333333:+.3e}")
    print(f"CLOCKING REFERENCE: using tooth from faces #{ref_idx_1}/#{ref_idx_2} -> ref_angle={ref_angle:.6f} deg")

    tooth_center_angles = [((ref_angle + i * pitch) % 360.0) for i in range(n_teeth)]
    print("TOOTH CENTER ANGLES (deg):")
    for i, a in enumerate(tooth_center_angles):
        print(f"  tooth[{i:02d}] center_angle={a:.6f}")
    print(f"CONFIRM: tooth[00] center_angle={tooth_center_angles[0]:.6f} (should match ref_angle {ref_angle:.6f})")

    # --- Measure root/tip radii from s1 geometry (no Solid.section API exists) ---
    bb1 = s1.BoundingBox()
    y_min, y_max = float(bb1.ymin), float(bb1.ymax)
    thickness = float(bb1.ylen)
    y_mid = 0.5 * (y_min + y_max)
    print(f"AXIAL EXTENT s1: y_min={y_min:.6f} y_max={y_max:.6f} thickness={thickness:.6f} y_mid={y_mid:.6f}")

    # Discretize edges sparsely to estimate radial envelope on XZ (axis is Y)
    r_samples = []
    edges = s1.Edges()
    print(f"SELECTED: {len(edges)} edges on s1 for radial sampling")
    for e in edges:
        try:
            pts = e.discretize(8)
        except Exception:
            pts = [v.Center() for v in e.Vertices()]
        for p in pts:
            if hasattr(p, "x"):
                x, z = float(p.x), float(p.z)
            else:
                x, z = float(p[0]), float(p[2])
            r = math.hypot(x, z)
            if r > 30.0:  # ignore inner detail
                r_samples.append(r)

    if len(r_samples) == 0:
        print("SELECTED: 0 radial samples -> NO-OP")
        return shape

    r_tip = max(r_samples)
    # Root estimate: min radius among points near the outer rim
    rim_band = 8.0
    r_outer_band = [r for r in r_samples if r > (r_tip - rim_band)]
    print(f"MEASURE: r_tip(max)={r_tip:.6f}; using rim_band={rim_band:.3f} => {len(r_outer_band)} samples in outer band")
    if len(r_outer_band) == 0:
        print("ERROR: 0 samples in outer band; cannot measure r_root -> NO-OP")
        return shape
    r_root = min(r_outer_band)
    print(f"MEASURED RADII: r_root(min in outer band)={r_root:.6f}  r_tip(max)={r_tip:.6f}")
    print(f"DIAMETERS: root_d={2*r_root:.6f} tip_d={2*r_tip:.6f}")

    # --- Build one straight-sided tooth (trapezoid in XZ), extruded along Y ---
    tooth_thickness_ang = pitch * 0.5  # tooth occupies half the pitch
    half_ang = tooth_thickness_ang * 0.5
    print(f"TOOTH ANGULAR THICKNESS: tooth_thickness={tooth_thickness_ang:.6f} deg, half_angle={half_ang:.6f} deg")

    overlap_radial = 0.30
    r_base = max(0.1, r_root - overlap_radial)

    def pol(r, a_deg):
        a = math.radians(a_deg)
        return (r * math.cos(a), r * math.sin(a))  # (x,z)

    p1 = pol(r_base, -half_ang)
    p2 = pol(r_tip, -half_ang)
    p3 = pol(r_tip, +half_ang)
    p4 = pol(r_base, +half_ang)
    print(f"TOOTH 2D PROFILE (XZ @ y_mid): p1={p1}, p2={p2}, p3={p3}, p4={p4}")

    # Plane with normal -Y so 2D coordinates map to (X,Z) naturally
    tooth_plane = cq.Plane(origin=(0.0, y_mid, 0.0), xDir=(1.0, 0.0, 0.0), normal=(0.0, -1.0, 0.0))
    print(f"SKETCH PLANE: origin=(0,{y_mid:.6f},0) normal=(0,-1,0) xDir=(1,0,0)")

    half_thick = thickness * 0.5
    tooth_wp = (
        cq.Workplane(tooth_plane)
        .polyline([p1, p2, p3, p4])
        .close()
        .extrude(half_thick, both=True)
    )
    tooth_solid = tooth_wp.val()
    bb_t = tooth_solid.BoundingBox()
    print(f"TOOTH SOLID: y_span=[{bb_t.ymin:.6f},{bb_t.ymax:.6f}] (target [{y_min:.6f},{y_max:.6f}])")

    # Create 27 teeth via rotation around axis [0,1,0]
    teeth_list = []
    for i, ang in enumerate(tooth_center_angles):
        t = tooth_solid.rotate((0, 0, 0), (0, 1, 0), ang)
        teeth_list.append(t)
    print(f"SELECTED: {len(teeth_list)} rotated tooth solids")
    teeth = cq.Compound.makeCompound(teeth_list)

    # --- Remove old rounded tooth rim by trimming to root cylinder, then add new teeth ---
    eps_y = 0.5
    cyl_h = thickness + 2.0 * eps_y
    cyl_pnt = cq.Vector(0.0, y_min - eps_y, 0.0)
    keep_cyl = cq.Solid.makeCylinder(r_root, cyl_h, pnt=cyl_pnt, dir=cq.Vector(0.0, 1.0, 0.0))
    print(f"ROOT KEEP CYLINDER: r={r_root:.6f} h={cyl_h:.6f} y_span=[{(y_min-eps_y):.6f},{(y_max+eps_y):.6f}]")

    try:
        s1_trim = s1.intersect(keep_cyl)
        print("BOOLEAN: s1 intersect root-cylinder -> trimmed base (old rounded teeth removed)")
    except Exception as e:
        print(f"ERROR: intersect failed; cannot remove old rounded teeth safely: {e}")
        return shape

    try:
        s1_new = s1_trim.fuse(teeth)
        print("BOOLEAN: trimmed base fuse new straight teeth -> s1 reprofiled")
    except Exception as e:
        print(f"ERROR: fuse teeth failed: {e}")
        return shape

    # --- Placement self-check: isolate added/removed volumes ---
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

    bb_after = s1_new.BoundingBox()
    print(
        f"AXIAL CHECK AFTER: y_min={bb_after.ymin:.6f} (target {y_min:.6f}, delta {bb_after.ymin-y_min:+.6f})  "
        f"y_max={bb_after.ymax:.6f} (target {y_max:.6f}, delta {bb_after.ymax-y_max:+.6f})"
    )

    # --- Recompound, keeping s0 byte-identical and preserving original solid order ---
    if s0 is not None:
        solids_out = [None] * len(sols)
        solids_out[s0_idx] = s0
        solids_out[s1_idx] = s1_new
        out = cq.Compound.makeCompound(solids_out)
    else:
        out = s1_new

    return out