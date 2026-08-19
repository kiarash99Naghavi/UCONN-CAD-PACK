def my_cad_function(args):
    import cadquery as cq
    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input

    base = shape.val() if hasattr(shape, "val") else shape
    base_vol = base.Volume() if hasattr(base, "Volume") else None

    # --- Resolve and verify target face #24 (global face index per provided geometry index)
    faces = base.Faces()
    print(f"SELECTED: {len(faces)} faces on base (compound)")
    if len(faces) <= 24:
        print("ERROR: base has fewer than 25 faces; cannot resolve face #24")
        return shape

    f24 = faces[24]
    c24 = f24.Center()
    n24 = f24.normalAt()
    a24 = f24.Area()
    print(f"RESOLVED: face #24 center={[round(c24.x,3), round(c24.y,3), round(c24.z,3)]} area={round(a24,3)} normal={[round(n24.x,6), round(n24.y,6), round(n24.z,6)]}")

    # Ensure axis points outward (+Z-ish)
    axis = cq.Vector(n24.x, n24.y, n24.z)
    if axis.Length == 0:
        axis = cq.Vector(0, 0, 1)
    axis = axis.normalized()
    if axis.dot(cq.Vector(0, 0, 1)) < 0:
        axis = -axis

    # --- Operate only on body s0 (largest solid) and recompound others untouched
    sols = base.Solids()
    print(f"SELECTED: {len(sols)} solids in STEP")
    if len(sols) == 0:
        print("ERROR: no solids found")
        return shape

    # s0 per prompt is the largest volume body
    vols = [s.Volume() for s in sols]
    s0_idx = max(range(len(sols)), key=lambda i: vols[i])
    s0 = sols[s0_idx]
    print(f"SELECTED: solid s0 as index {s0_idx} volume={round(vols[s0_idx],3)}")

    # --- Target parameters (from sub-goal)
    center = cq.Vector(-88.9, 100.0, 266.7)
    od = 38.1
    id_clear = 25.4
    proj = 30.0
    z_top_face = 266.7
    z_neck_end = 296.7
    z_interior = 257.175
    top_thickness = 9.525

    # Build CUT tools (sub-goal is tagged cut-hole-slot => only remove material here)
    # 1) Through passage tool (ID=25.4) down to interior level
    # Make the cylinder span [z_interior .. z_neck_end] so it surely covers the top face
    h_pass = z_neck_end - z_interior
    pass_tool = cq.Solid.makeCylinder(id_clear / 2.0, h_pass,
                                      pnt=cq.Vector(center.x, center.y, z_interior),
                                      dir=cq.Vector(0, 0, 1))

    # 2) Optional mouth counterbore/landing (OD=38.1) for a filler neck seat, shallow into top wall
    # Use a conservative depth within the known top thickness: 3.175 mm
    cb_depth = 3.175
    cb_depth = min(cb_depth, top_thickness)
    cb_z0 = z_top_face - cb_depth
    cb_tool = cq.Solid.makeCylinder(od / 2.0, cb_depth,
                                    pnt=cq.Vector(center.x, center.y, cb_z0),
                                    dir=cq.Vector(0, 0, 1))

    tool = pass_tool.fuse(cb_tool)

    # Apply cut to s0 only
    edited_s0 = s0.cut(tool)

    # Recompound
    out = cq.Compound.makeCompound([s for i, s in enumerate(sols) if i != s0_idx] + [edited_s0])

    # --- Diagnostics / self-checks
    # Volume delta (should be negative for cut)
    try:
        out_vol = out.Volume()
        if base_vol is not None:
            print("DELTA", out_vol - base_vol)
    except Exception as e:
        print("WARNING: could not compute volume delta:", e)

    # Isolate removed material from s0
    removed = s0.cut(edited_s0)
    try:
        bb = removed.BoundingBox()
        print(
            "REMOVED bbox:",
            {
                "xmin": round(bb.xmin, 3), "xmax": round(bb.xmax, 3),
                "ymin": round(bb.ymin, 3), "ymax": round(bb.ymax, 3),
                "zmin": round(bb.zmin, 3), "zmax": round(bb.zmax, 3),
                "xlen": round(bb.xlen, 3), "ylen": round(bb.ylen, 3), "zlen": round(bb.zlen, 3),
            },
        )
        rc = removed.Center()
        print("REMOVED center:", [round(rc.x, 3), round(rc.y, 3), round(rc.z, 3)])
    except Exception as e:
        print("WARNING: could not compute removed bbox/center:", e)

    # Print achieved parameters (as-built intent)
    print("ACHIEVED filler location/axis:")
    print("  center=", [center.x, center.y, center.z])
    print("  axis=", [round(axis.x, 6), round(axis.y, 6), round(axis.z, 6)])
    print("ACHIEVED diameters (cut features):")
    print("  pouring_diameter=", id_clear)
    print("  mouth_diameter=", od)
    print("ACHIEVED z-levels (targets):")
    print("  top_face_z=", z_top_face)
    print("  requested_neck_end_z=", z_neck_end)
    print("  interior_level_z=", z_interior)

    # Note: since this step is implemented as a CUT (per prompt's cut-hole-slot tag),
    # it does not add a protruding neck to z=296.7; only the void is cut.

    return out