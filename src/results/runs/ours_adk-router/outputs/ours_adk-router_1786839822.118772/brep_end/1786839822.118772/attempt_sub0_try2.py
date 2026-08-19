def my_cad_function(args):
    import cadquery as cq

    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input
    base = shape.val() if hasattr(shape, "val") else shape

    # --- Numbers named in the sub-goal ---
    r = 7.5
    d = 15.0
    z_min = -60.0
    z_max = 0.0
    h = z_max - z_min
    p0 = (15.0, 7.5)
    p1 = (153.0, 7.5)

    print("SUBGOAL numbers:")
    print(f"  r={r} (d={d}), z_min={z_min}, z_max={z_max}, h={h}")
    print(f"  target axes XY: {p0} and {p1}, axis direction +Z")

    # --- Sanity-check: resolve cylindrical faces #0 and #1 from the index ---
    try:
        f0 = base.Faces()[0]
        f1 = base.Faces()[1]
        print("Resolved face #0:", f0.geomType(), "Center=", f0.Center(), "BBox=", f0.BoundingBox())
        print("Resolved face #1:", f1.geomType(), "Center=", f1.Center(), "BBox=", f1.BoundingBox())
    except Exception as e:
        print("WARNING: Could not resolve/print faces #0/#1:", e)

    # --- Build two full-height cylinders (flush with z=-60 and z=0) ---
    cyl0 = cq.Solid.makeCylinder(r, h, cq.Vector(p0[0], p0[1], z_min), cq.Vector(0, 0, 1))
    cyl1 = cq.Solid.makeCylinder(r, h, cq.Vector(p1[0], p1[1], z_min), cq.Vector(0, 0, 1))

    cyls = cq.Compound.makeCompound([cyl0, cyl1])
    out = base.fuse(cyls)

    # --- Placement self-check: isolate added material and verify extents/axes ---
    added = out.cut(base)
    bb = added.BoundingBox()
    c_added = added.Center()

    print("ADDED material checks:")
    print("  added.Center()=", c_added)
    print("  added.BoundingBox()=", bb)
    print(f"  z extents: min={bb.zmin} (target {z_min}, delta {bb.zmin - z_min}), max={bb.zmax} (target {z_max}, delta {bb.zmax - z_max})")

    # Check each cylinder individually for exact XY axis center and Z span
    for i, (cyl, (x, y)) in enumerate([(cyl0, p0), (cyl1, p1)]):
        bbi = cyl.BoundingBox()
        cx = 0.5 * (bbi.xmin + bbi.xmax)
        cy = 0.5 * (bbi.ymin + bbi.ymax)
        czmin = bbi.zmin
        czmax = bbi.zmax
        print(f"  cyl{i} bbox= {bbi}")
        print(f"    cyl{i} axis XY from bbox center=({cx}, {cy}) vs target=({x}, {y}) deltas=({cx - x}, {cy - y})")
        print(f"    cyl{i} z-span=({czmin}, {czmax}) vs target=({z_min}, {z_max}) deltas=({czmin - z_min}, {czmax - z_max})")

    return out