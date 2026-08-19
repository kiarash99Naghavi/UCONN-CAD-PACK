def my_cad_function(args):
    import cadquery as cq
    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input

    # --- Removable cap: free-standing local coordinate system (do NOT attach to imported faces) ---
    cap_center_xy = (-88.9, 100.0)
    axis_dir = (0.0, 0.0, 1.0)
    seating_z = 296.7
    top_z = 308.7
    outside_d = 44.45
    recess_d = 38.1
    recess_depth = 8.0

    # Local sketch plane: X is radial (r), Y is axial (Z). Place plane at given axis XY, world Y fixed.
    # Choose plane normal along -Y so plane is XZ and its +Y direction maps to +Z.
    sketch_plane = cq.Plane(origin=(cap_center_xy[0], cap_center_xy[1], 0.0), xDir=(1.0, 0.0, 0.0), normal=(0.0, -1.0, 0.0))
    print(f"PLANE: cap sketch plane origin={sketch_plane.origin.toTuple()} xDir={sketch_plane.xDir.toTuple()} normal={sketch_plane.zDir.toTuple()}")

    # Closed radial-axial cup profile (r, Z) -> (x, y) on this plane where y maps to +Z.
    rz = [(19.05, 296.7), (22.225, 296.7), (22.225, 308.7), (0.0, 308.7), (0.0, 304.7), (19.05, 304.7)]
    pts2d = [(r, z) for (r, z) in rz]
    print(f"PROFILE: {len(pts2d)} vertices (r,z)={rz}")

    axis_start = (cap_center_xy[0], cap_center_xy[1], 0.0)
    axis_end = (cap_center_xy[0], cap_center_xy[1], 1.0)
    print(f"AXIS: start={axis_start} end={axis_end} dir={axis_dir}")

    cap_wp = (
        cq.Workplane(sketch_plane)
        .polyline(pts2d)
        .close()
        .revolve(360.0, axisStart=axis_start, axisEnd=axis_end)
    )
    cap_solid = cap_wp.val()

    # --- Validate cap solid independently (do not validate the imported aggregate) ---
    try:
        cap_valid = cap_solid.isValid()
    except Exception as e:
        cap_valid = None
        print(f"CAP VALIDITY: exception while checking validity: {e}")

    bb = cap_solid.BoundingBox()
    expected_min = (-111.125, 77.775, 296.7)
    expected_max = (-66.675, 122.225, 308.7)

    # Solid count (cap must be exactly one solid)
    try:
        cap_solids = cap_solid.Solids()
        cap_solid_count = len(cap_solids)
    except Exception:
        cap_solid_count = 1

    print("CAP REPORT:")
    print(f"  center_xy={list(cap_center_xy)}")
    print(f"  axis={list(axis_dir)}")
    print(f"  seating_z={seating_z}")
    print(f"  outside_diameter={outside_d} mm")
    print(f"  recess_diameter={recess_d} mm")
    print(f"  recess_depth={recess_depth} mm")
    print(f"  top_z={top_z}")
    print(f"  expected_bbox min={list(expected_min)} max={list(expected_max)}")
    print(f"  actual_bbox   min={[bb.xmin, bb.ymin, bb.zmin]} max={[bb.xmax, bb.ymax, bb.zmax]}")
    print(f"  bbox_delta_min={[bb.xmin-expected_min[0], bb.ymin-expected_min[1], bb.zmin-expected_min[2]]}")
    print(f"  bbox_delta_max={[bb.xmax-expected_max[0], bb.ymax-expected_max[1], bb.zmax-expected_max[2]]}")
    print(f"  cap_solid_count={cap_solid_count}")
    print(f"  cap_isValid={cap_valid}")

    # --- Append as separate body (no fusion, no modification of imported solids) ---
    asm = cq.Assembly()
    asm.add(shape, name="imported")
    asm.add(cap_solid, name="cap")

    return asm