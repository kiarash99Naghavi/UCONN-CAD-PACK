def my_cad_function(args):
    import cadquery as cq

    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input
    base = shape.val() if hasattr(shape, "val") else shape

    # --- Resolve and report the referenced anchor face (#11) ---
    faces = base.Faces()
    print(f"INFO: base has {len(faces)} faces")
    f11 = faces[11]
    c11 = f11.Center()
    n11 = f11.normalAt()
    print(
        "SELECTED: 1 face for anchor bottom face #11 "
        f"center=({c11.x:.3f},{c11.y:.3f},{c11.z:.3f}) normal=({n11.x:.3f},{n11.y:.3f},{n11.z:.3f}) area={f11.Area():.3f}"
    )

    # --- Parameters from sub-goal (explicit absolute anchors) ---
    z0 = -0.75
    flange_thk = 0.5
    z1 = z0 + flange_thk  # -0.25

    inner_xmin, inner_xmax = -1.0, 1.0
    inner_ymin, inner_ymax = -3.0, 3.0
    outer_xmin, outer_xmax = -3.0, 3.0
    outer_ymin, outer_ymax = -5.0, 5.0

    inner_w = inner_xmax - inner_xmin  # 2
    inner_h = inner_ymax - inner_ymin  # 6
    outer_w = outer_xmax - outer_xmin  # 6
    outer_h = outer_ymax - outer_ymin  # 10

    print(
        "INFO: target flange numbers: "
        f"z0={z0}, z1={z1}, thk={flange_thk}; "
        f"inner x=[{inner_xmin},{inner_xmax}] y=[{inner_ymin},{inner_ymax}]; "
        f"outer x=[{outer_xmin},{outer_xmax}] y=[{outer_ymin},{outer_ymax}]"
    )

    # --- Build flange as a rectangular ring on absolute plane z=z0, extrude +Z ---
    plane = cq.Plane(origin=(0, 0, z0), normal=(0, 0, 1))
    print(f"INFO: sketch plane origin={(0,0,z0)} normal={(0,0,1)}")

    flange_wp = (
        cq.Workplane(plane)
        .rect(outer_w, outer_h)  # outer perimeter
        .rect(inner_w, inner_h)  # inner cutout (keeps center unfilled)
        .extrude(flange_thk)
    )
    flange_solid = flange_wp.val()
    print("SELECTED: 1 solid for flange tool (rectangular ring)")

    # --- Placement self-check: isolate added material and print bounds ---
    added = flange_solid.cut(base)
    bb_add = added.BoundingBox()
    print(
        "CHECK: added flange bbox "
        f"x=[{bb_add.xmin:.3f},{bb_add.xmax:.3f}] y=[{bb_add.ymin:.3f},{bb_add.ymax:.3f}] z=[{bb_add.zmin:.3f},{bb_add.zmax:.3f}]"
    )
    print(
        "CHECK: target added flange bounds "
        f"x=[{outer_xmin:.3f},{outer_xmax:.3f}] y=[{outer_ymin:.3f},{outer_ymax:.3f}] z=[{z0:.3f},{z1:.3f}]"
    )
    dx0, dx1 = bb_add.xmin - outer_xmin, bb_add.xmax - outer_xmax
    dy0, dy1 = bb_add.ymin - outer_ymin, bb_add.ymax - outer_ymax
    dz0, dz1 = bb_add.zmin - z0, dz1t = bb_add.zmax - z1
    print(
        "CHECK: deltas (added - target) "
        f"dxmin={dx0:.3f} dxmax={dx1:.3f} dymin={dy0:.3f} dymax={dy1:.3f} dzmin={dz0:.3f} dzmax={dz1t:.3f}"
    )

    # --- Fuse to base ---
    out = base.fuse(flange_solid)

    # --- Verify final bbox unchanged in Z overall (must remain -0.75..0.75) ---
    bb_out = out.BoundingBox()
    print(
        "CHECK: final body bbox "
        f"x=[{bb_out.xmin:.3f},{bb_out.xmax:.3f}] y=[{bb_out.ymin:.3f},{bb_out.ymax:.3f}] z=[{bb_out.zmin:.3f},{bb_out.zmax:.3f}]"
    )
    print(
        "CHECK: expected final z-range [-0.750, 0.750] "
        f"dzmin={bb_out.zmin - (-0.75):.3f} dzmax={bb_out.zmax - (0.75):.3f}"
    )

    return out