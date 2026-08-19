def my_cad_function(args):
    import cadquery as cq

    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input
    base = shape.val() if hasattr(shape, "val") else shape

    bb0 = base.BoundingBox()
    print("PRE  bbox size:", [bb0.xlen, bb0.ylen, bb0.zlen], "min:", [bb0.xmin, bb0.ymin, bb0.zmin], "max:", [bb0.xmax, bb0.ymax, bb0.zmax])
    print("Scale factor:", 10.0, "about origin [0,0,0]")

    scaled = None
    # Prefer native scale if available (about origin in CadQuery/OCP)
    try:
        scaled = base.scale(10.0)
        print("Used: base.scale(10.0)")
    except Exception as e_scale:
        print("base.scale failed, falling back to matrix transform:", repr(e_scale))
        # 4x4 scaling matrix about origin
        m = cq.Matrix(
            [
                [10.0, 0.0, 0.0, 0.0],
                [0.0, 10.0, 0.0, 0.0],
                [0.0, 0.0, 10.0, 0.0],
                [0.0, 0.0, 0.0, 1.0],
            ]
        )
        try:
            scaled = base.transformShape(m)
            print("Used: base.transformShape(Matrix(scale))")
        except Exception as e_tr:
            print("transformShape failed, trying transformGeometry:", repr(e_tr))
            scaled = base.transformGeometry(m)
            print("Used: base.transformGeometry(Matrix(scale))")

    bb1 = scaled.BoundingBox()
    print("POST bbox size:", [bb1.xlen, bb1.ylen, bb1.zlen], "min:", [bb1.xmin, bb1.ymin, bb1.zmin], "max:", [bb1.xmax, bb1.ymax, bb1.zmax])
    print(
        "POST expected approx min/max:",
        "x[-10,10] y[-30,30] z[-7.5,7.5]",
        "deltas:",
        {
            "xmin": bb1.xmin - (-10.0),
            "xmax": bb1.xmax - (10.0),
            "ymin": bb1.ymin - (-30.0),
            "ymax": bb1.ymax - (30.0),
            "zmin": bb1.zmin - (-7.5),
            "zmax": bb1.zmax - (7.5),
        },
    )

    return scaled