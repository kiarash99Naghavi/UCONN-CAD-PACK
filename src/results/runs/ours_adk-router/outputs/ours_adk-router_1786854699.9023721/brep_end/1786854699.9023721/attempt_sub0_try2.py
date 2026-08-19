def my_cad_function(args):
    import cadquery as cq
    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input

    base = shape.val() if hasattr(shape, "val") else shape
    bb0 = base.BoundingBox()
    print("SCALE x10 about origin")
    print("bbox before:", [bb0.xmin, bb0.ymin, bb0.zmin], [bb0.xmax, bb0.ymax, bb0.zmax], "size=", [bb0.xlen, bb0.ylen, bb0.zlen])
    print("center before:", [(bb0.xmin + bb0.xmax) / 2, (bb0.ymin + bb0.ymax) / 2, (bb0.zmin + bb0.zmax) / 2])

    scaled = base.scale(10.0)  # uniform scale about (0,0,0) in absolute coordinates

    bb1 = scaled.BoundingBox()
    print("bbox after: ", [bb1.xmin, bb1.ymin, bb1.zmin], [bb1.xmax, bb1.ymax, bb1.zmax], "size=", [bb1.xlen, bb1.ylen, bb1.zlen])
    print("center after:", [(bb1.xmin + bb1.xmax) / 2, (bb1.ymin + bb1.ymax) / 2, (bb1.zmin + bb1.zmax) / 2])

    return scaled