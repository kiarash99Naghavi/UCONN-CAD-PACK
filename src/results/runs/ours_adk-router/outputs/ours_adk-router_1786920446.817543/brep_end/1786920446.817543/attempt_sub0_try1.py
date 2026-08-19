def my_cad_function(args):
    import cadquery as cq
    import math

    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input
    base = shape.val() if hasattr(shape, "val") else shape

    def v3(v):
        return (float(v.x), float(v.y), float(v.z))

    def norm_deg(a):
        a = a % 360.0
        if a < 0:
            a += 360.0
        return a

    # --- Identify solids (keep s0 untouched, edit only the large s1) ---
    solids = base.Solids()
    print(f"SELECTED: {len(solids)} solids total")
    if len(solids) != 2:
        print("WARNING: expected 2 solids as per index")

    vols = []
    for i, s in enumerate(solids):
        try:
            v = float(s.Volume())
        except Exception:
            v = float('nan')
        vols.append(v)
        bb = s.BoundingBox()
        print(f"  solid[{i}] volume={v:.3f} bbox=([{bb.xmin:.3f},{bb.ymin:.3f},{bb.zmin:.3f}]..[{bb.xmax:.3f},{bb.ymax:.3f},{bb.zmax:.3f}])")

    s1_idx = max(range(len(solids)), key=lambda i: vols[i])
    s0_idx = [i for i in range(len(solids)) if i != s1_idx][0] if len(solids) > 1 else None
    s1 = solids[s1_idx]
    s0 = solids[s0_idx] if s0_idx is not None else None
    print(f"SELECTED: solid[{s1_idx}] as target body s1 (largest volume)")
    if s0 is not None:
        print(f"SELECTED: solid[{s0_idx}] as untouched body s0")

    # --- Resolve the reference faces by absolute face indices (as instructed) ---
    faces_all = base.Faces()
    print(f"SELECTED: {len(faces_all)} faces in imported compound")

    ref_face_idxs = [671, 779]
    ref_faces = []
    for fi in ref_face_idxs:
        if fi < 0 or fi >= len(faces_all):
            print(f"SELECTED: 0 faces for reference (face_idx {fi} out of range)")
            continue
        f = faces_all[fi]
        c = f.Center()
        a = None
        try:
            a = float(f.Area())
        except Exception:
            pass
        print(f"SELECTED: 1 face for reference face_idx={fi}  center={v3(c)} area={a}")
        ref_faces.append(f)

    # --- Extract clocking reference angle from cylindrical axis locations of the two families ---
    ref_angles = []
    ref_axis_pts = []
    try:
        from OCP.BRepAdaptor import BRepAdaptor_Surface
        from OCP.GeomAbs import GeomAbs_Cylinder
        for f, fi in zip(ref_faces, ref_face_idxs):
            ad = BRepAdaptor_Surface(f.wrapped, True)
            if ad.GetType() != GeomAbs_Cylinder:
                print(f"WARNING: face_idx={fi} is not a CYLINDER per adaptor; type={int(ad.GetType())}")
                continue
            cyl = ad.Cylinder()
            p = cyl.Axis().Location()  # gp_Pnt on cylinder axis
            px, py, pz = float(p.X()), float(p.Y()), float(p.Z())
            ref_axis_pts.append((px, py, pz))
            ang = norm_deg(math.degrees(math.atan2(pz, px)))
            ref_angles.append(ang)
            print(f"REF CYL AXIS: face_idx={fi} axis_pt=({px:.3f},{py:.3f},{pz:.3f}) -> angle={ang:.6f} deg")
    except Exception as e:
        print(f"WARNING: failed to read cylindrical axis via OCP adaptor: {e}")

    if len(ref_angles) == 0:
        # Fallback to face centroids (less ideal but preserves rough clocking)
        for f, fi in zip(ref_faces, ref_face_idxs):
            c = f.Center()
            ang = norm_deg(math.degrees(math.atan2(c.z, c.x)))
            ref_angles.append(ang)
            print(f"REF FALLBACK: face_idx={fi} centroid angle={ang:.6f} deg")

    ref_angle = sum(ref_angles) / len(ref_angles) if ref_angles else 0.0
    pitch = 360.0 / 27.0
    print(f"PITCH CHECK: computed pitch=360/27={pitch:.12f} deg (target 13.333...) delta={pitch-13.333333333333:.12e}")
    print(f"CLOCKING REFERENCE: using tooth from faces #671/#779 -> ref_angle={ref_angle:.6f} deg")

    tooth_center_angles = [norm_deg(ref_angle + i * pitch) for i in range(27)]
    print("TOOTH CENTER ANGLES (deg):")
    for i, a in enumerate(tooth_center_angles):
        print(f"  tooth[{i:02d}] center_angle={a:.6f}")
    # confirm first tooth aligns to reference
    print(f"CONFIRM: tooth[00] center_angle={tooth_center_angles[0]:.6f} (should match ref_angle {ref_angle:.6f})")

    # --- Determine axial extent and outer profile radii (root & tip) by section at mid-Y ---
    bb1 = s1.BoundingBox()
    y_min, y_max = float(bb1.ymin), float(bb1.ymax)
    thickness = float(bb1.ylen)
    y_mid = 0.5 * (y_min + y_max)
    print(f"AXIAL EXTENT s1: y_min={y_min:.6f} y_max={y_max:.6f} thickness={thickness:.6f} y_mid={y_mid:.6f}")

    sec_plane = cq.Plane(origin=(0.0, y_mid, 0.0), normal=(0.0, 1.0, 0.0))
    print(f"SECTION PLANE: origin=(0,{y_mid:.6f},0) normal=(0,1,0)")
    try:
        sec = s1.section(sec_plane)
    except Exception as e:
        print(f"ERROR: failed to section s1 at y_mid: {e}")
        return shape

    sec_edges = sec.Edges()
    print(f"SELECTED: {len(sec_edges)} section edges at y={y_mid:.6f}")
    if len(sec_edges) == 0:
        print("SELECTED: 0 edges for section -> NO-OP (cannot measure radii)")
        return shape

    wires = cq.Wire.combine(sec_edges)
    print(f"SELECTED: {len(wires)} wires from section")
    if len(wires) == 0:
        print("SELECTED: 0 wires from section -> NO-OP (cannot measure radii)")
        return shape

    # choose outer boundary wire by largest XZ span
    def wire_score(w):
        bb = w.BoundingBox()
        return float(bb.xlen + bb.zlen)

    outer_wire = max(wires, key=wire_score)
    bb_w = outer_wire.BoundingBox()
    print(f"SELECTED: 1 outer wire by max span, bbox xlen={bb_w.xlen:.3f} zlen={bb_w.zlen:.3f} center=({bb_w.center.x:.3f},{bb_w.center.y:.3f},{bb_w.center.z:.3f})")

    # sample points along the outer wire to get radial min/max
    r_min = 1e9
    r_max = -1e9
    for e in outer_wire.Edges():
        try:
            pts = e.discretize(60)
        except Exception:
            pts = [v.toTuple() for v in e.Vertices()]
        for p in pts:
            if hasattr(p, "x"):
                x, z = float(p.x), float(p.z)
            else:
                x, z = float(p[0]), float(p[2])
            r = math.hypot(x, z)
            r_min = min(r_min, r)
            r_max = max(r_max, r)

    r_root = float(r_min)
    r_tip = float(r_max)
    print(f"MEASURED RADII from outer section wire: r_root(min)={r_root:.6f}  r_tip(max)={r_tip:.6f}")
    print(f"DIAMETERS: root_d={2*r_root:.6f} tip_d={2*r_tip:.6f}")

    # --- Build new spur teeth as straight-sided prisms extruded along Y ---
    half_tooth_ang = (pitch * 0.5) * 0.5  # tooth thickness = pitch*0.5, half-angle = pitch/4
    print(f"TOOTH ANGULAR THICKNESS: tooth_thickness={pitch*0.5:.6f} deg, half_angle={half_tooth_ang:.6f} deg")

    # overlap into root body to ensure fusion
    overlap = 0.20
    r_base = max(0.1, r_root - overlap)
    a1 = math.radians(-half_tooth_ang)
    a2 = math.radians(+half_tooth_ang)

    def pol(r, a_rad):
        return (r * math.cos(a_rad), r * math.sin(a_rad))  # (x, z)

    p1 = pol(r_base, a1)
    p2 = pol(r_tip, a1)
    p3 = pol(r_tip, a2)
    p4 = pol(r_base, a2)
    print(f"TOOTH 2D PROFILE (XZ plane at y_mid): p1={p1}, p2={p2}, p3={p3}, p4={p4}")

    tooth_plane = cq.Plane(origin=(0.0, y_mid, 0.0), xDir=(1.0, 0.0, 0.0), normal=(0.0, 1.0, 0.0))
    print(f"SKETCH PLANE for teeth: origin=(0,{y_mid:.6f},0) normal=(0,1,0) xDir=(1,0,0)")

    tooth_wp = (
        cq.Workplane(tooth_plane)
        .polyline([p1, p2, p3, p4])
        .close()
        .extrude(thickness, both=True)
    )
    tooth_solid = tooth_wp.val()
    bb_t = tooth_solid.BoundingBox()
    print(f"TOOTH SOLID: bbox=([{bb_t.xmin:.3f},{bb_t.ymin:.3f},{bb_t.zmin:.3f}]..[{bb_t.xmax:.3f},{bb_t.ymax:.3f},{bb_t.zmax:.3f}])")

    # Create 27 teeth via rotation around the measured gear axis (0,1,0)
    teeth = None
    for i, ang in enumerate(tooth_center_angles):
        t = tooth_solid.rotate((0, 0, 0), (0, 1, 0), ang)
        if teeth is None:
            teeth = t
        else:
            teeth = teeth.fuse(t)
    print("SELECTED: 27 rotated tooth solids fused into one")

    # --- Remove existing rounded-tooth rim by trimming to root cylinder, then add new teeth ---
    eps_h = 2.0
    cyl_h = thickness + 2.0 * eps_h
    cyl_pnt = cq.Vector(0.0, y_mid - cyl_h / 2.0, 0.0)
    cyl_keep = cq.Solid.makeCylinder(r_root, cyl_h, pnt=cyl_pnt, dir=cq.Vector(0.0, 1.0, 0.0))
    print(f"ROOT KEEP CYLINDER: r={r_root:.6f} h={cyl_h:.6f} y_span=[{(y_mid-cyl_h/2):.6f},{(y_mid+cyl_h/2):.6f}]")

    try:
        s1_trim = s1.intersect(cyl_keep)
        print("BOOLEAN: s1 intersect root-cylinder -> trimmed base for new teeth")
    except Exception as e:
        print(f"ERROR: intersect failed; cannot remove old rounded teeth safely: {e}")
        return shape

    try:
        s1_new = s1_trim.fuse(teeth)
        print("BOOLEAN: trimmed base fuse new teeth -> s1 reprofiled")
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
    print(f"AXIAL CHECK AFTER: y_min={bb_after.ymin:.6f} (target {y_min:.6f}, delta {bb_after.ymin-y_min:+.6f})  y_max={bb_after.ymax:.6f} (target {y_max:.6f}, delta {bb_after.ymax-y_max:+.6f})")

    # --- Recompound, keeping s0 byte-identical ---
    if s0 is not None:
        out = cq.Compound.makeCompound([s0, s1_new] if s0_idx < s1_idx else [s1_new, s0])
    else:
        out = s1_new

    return out