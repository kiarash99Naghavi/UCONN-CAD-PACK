def my_cad_function(args):
    import cadquery as cq

    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input
    base = shape.val() if hasattr(shape, "val") else shape

    # --- Sub-goal: add removable cap as a NEW, separate solid (no fusing, no face-anchoring) ---
    x0, y0 = -88.9, 100.0
    axis_dir = (0, 0, 1)

    # Free-standing local coordinate system:
    # Use an XZ sketch plane located at y=y0, with +X as radial and +Z as axial.
    # Achieve that by setting plane normal to -Y so the plane's local Y axis points +Z.
    cap_plane = cq.Plane(origin=(x0, y0, 0.0), xDir=(1.0, 0.0, 0.0), normal=(0.0, -1.0, 0.0))
    print(f"PLANE: origin=({x0},{y0},0.0) normal=(0,-1,0) xDir=(1,0,0)  (free-standing, not attached to any face)")

    # Closed radial-axial cup profile in (radius, Z) mm:
    rz = [
        (19.05, 296.7),
        (22.225, 296.7),
        (22.225, 308.7),
        (0.0, 308.7),
        (0.0, 304.7),
        (19.05, 304.7),
    ]
    print(f"PROFILE (r,z) vertices: {rz}")

    # Revolve 360 degrees about the local Z axis through (x0,y0,*).
    axis_start = (x0, y0, 0.0)
    axis_end = (x0, y0, 1.0)

    cap_wp = (
        cq.Workplane(cap_plane)
        .polyline(rz)
        .close()
        .revolve(360.0, axis_start, axis_end)
    )
    cap_val = cap_wp.val() if hasattr(cap_wp, "val") else cap_wp

    # --- Validate cap solid independently ---
    # Ensure it's exactly one solid
    try:
        cap_solids = cap_val.Solids()
        cap_solid_count = len(cap_solids)
    except Exception:
        cap_solids = []
        cap_solid_count = 0

    cap_bbox = cap_val.BoundingBox()
    cap_valid = cap_val.isValid()

    exp_min = (-111.125, 77.775, 296.7)
    exp_max = (-66.675, 122.225, 308.7)

    print("CAP REPORT (targets from sub-goal):")
    print(f"  centerXY=[{x0},{y0}]  axis={list(axis_dir)}")
    print("  seating Z=296.7")
    print("  outside diameter=44.45 mm")
    print("  recess diameter=38.1 mm")
    print("  recess depth=8.0 mm")
    print("  top Z=308.7")
    print(f"  expected bbox min={list(exp_min)} max={list(exp_max)}")

    print("CAP MEASURED:")
    print(f"  cap ShapeType={cap_val.ShapeType()}")
    print(f"  cap solids (count) = {cap_solid_count}")
    print(f"  cap isValid = {cap_valid}")
    print(
        "  cap bbox min="
        f"[{cap_bbox.xmin:.3f},{cap_bbox.ymin:.3f},{cap_bbox.zmin:.3f}]  "
        "max="
        f"[{cap_bbox.xmax:.3f},{cap_bbox.ymax:.3f},{cap_bbox.zmax:.3f}]"
    )
    print(
        "  bbox deltas (measured - expected) min="
        f"[{cap_bbox.xmin-exp_min[0]:.3f},{cap_bbox.ymin-exp_min[1]:.3f},{cap_bbox.zmin-exp_min[2]:.3f}]  "
        "max="
        f"[{cap_bbox.xmax-exp_max[0]:.3f},{cap_bbox.ymax-exp_max[1]:.3f},{cap_bbox.zmax-exp_max[2]:.3f}]"
    )

    # --- Append as a separate body in an assembly (no modifications to imported solids) ---
    assy = cq.Assembly()
    assy.add(base, name="radiator")
    assy.add(cap_val, name="cap")

    return assy