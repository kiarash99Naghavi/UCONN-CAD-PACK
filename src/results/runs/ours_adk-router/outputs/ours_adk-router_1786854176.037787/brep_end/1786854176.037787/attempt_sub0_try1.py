def my_cad_function(args):
    import cadquery as cq
    from cadquery import Vector

    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input
    base = shape.val() if hasattr(shape, "val") else shape

    # --- Numbers explicitly named by sub-goal ---
    hole_mouth_center = Vector(67.5, 180.0, -21.0)  # planar face #6 center
    axis_dir = Vector(0.0, 1.0, 0.0)                # from cylindrical faces #29/#5
    rod_r = 7.0175
    rod_d = 14.035
    rod_len = 200.0

    print("[subgoal] hole_mouth_center:", hole_mouth_center.toTuple())
    print("[subgoal] axis_dir:", axis_dir.toTuple())
    print("[subgoal] rod_d:", rod_d, "rod_r:", rod_r, "rod_len:", rod_len)

    faces = base.Faces()
    print("Resolved face count:", len(faces))

    # Resolve and verify face #6 (planar mouth face)
    f6 = faces[6]
    c6 = Vector(f6.Center().toTuple())
    a6 = f6.Area()
    print("Face #6 center:", c6.toTuple(), "area:", a6, "geomType:", f6.geomType())
    print("Face #6 delta center vs target:", (c6 - hole_mouth_center).toTuple())

    # Resolve and print cylindrical face #29 (hole wall) for sanity
    f29 = faces[29]
    c29 = Vector(f29.Center().toTuple())
    print("Face #29 center:", c29.toTuple(), "geomType:", f29.geomType())
    try:
        ga = f29.geomAdaptor()
        ax = ga.Axis()
        d = ax.Direction()
        cyl_dir = Vector(d.X(), d.Y(), d.Z())
        print("Face #29 measured axis dir (best-effort):", cyl_dir.toTuple())
    except Exception as e:
        print("Face #29 axis read failed (ok):", repr(e))

    # --- Build rod ---
    # Overlap slightly into the part (-Y) to guarantee fusion, but keep the far end at Y=180+200=380.
    overlap = 0.05
    start = hole_mouth_center - axis_dir.normalized() * overlap
    ext_len = rod_len + overlap

    rod_solid = cq.Solid.makeCylinder(rod_r, ext_len, pnt=start, dir=axis_dir)
    out = cq.Workplane(obj=base).union(rod_solid)

    # --- Placement self-check (added material isolation & measurements) ---
    out_s = out.val()
    added = out_s.cut(base)
    bb = added.BoundingBox()
    added_center = Vector(added.Center().toTuple())

    target_far_end_center = hole_mouth_center + axis_dir.normalized() * rod_len

    print("[check] overlap:", overlap, "start:", start.toTuple(), "ext_len:", ext_len)
    print("[check] target_far_end_center approx:", target_far_end_center.toTuple())
    print("[check] added.Center():", added_center.toTuple())
    print("[check] added.BoundingBox(): xmin,xmax,ymin,ymax,zmin,zmax =",
          bb.xmin, bb.xmax, bb.ymin, bb.ymax, bb.zmin, bb.zmax)
    print("[check] measured added ymax (expected ~380.0):", bb.ymax, "delta:", bb.ymax - 380.0)

    # If it extruded the wrong way, rebuild along -axis_dir
    if bb.ymax < hole_mouth_center.y + rod_len * 0.5:
        print("[fix] Rod appears to extrude toward -Y; rebuilding along -axis_dir")
        axis_dir2 = axis_dir * -1
        start2 = hole_mouth_center - axis_dir2.normalized() * overlap
        rod_solid2 = cq.Solid.makeCylinder(rod_r, ext_len, pnt=start2, dir=axis_dir2)
        out = cq.Workplane(obj=base).union(rod_solid2)
        out_s = out.val()
        added = out_s.cut(base)
        bb2 = added.BoundingBox()
        print("[fix-check] new added ymax (expected ~380.0):", bb2.ymax, "delta:", bb2.ymax - 380.0)

    return out