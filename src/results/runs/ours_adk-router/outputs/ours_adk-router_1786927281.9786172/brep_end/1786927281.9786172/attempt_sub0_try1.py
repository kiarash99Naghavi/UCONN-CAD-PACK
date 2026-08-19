def my_cad_function(args):
    import cadquery as cq
    from OCP.gp import gp_Pln, gp_Pnt, gp_Dir, gp_Trsf
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform

    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input

    base = shape.val() if hasattr(shape, "val") else shape
    sols = base.Solids()
    print(f"INPUT: solids={len(sols)}")
    if len(sols) == 0:
        print("SELECTED: 0 solids in input (cannot proceed)")
        return shape

    s0 = sols[0]

    # Resolve face #7 exactly as instructed and verify
    faces = s0.Faces()
    print(f"INPUT s0: faces={len(faces)}")
    if len(faces) <= 7:
        print("SELECTED: 0 faces for face#7 (index out of range)")
        return shape

    f7 = faces[7]
    c7 = f7.Center()
    try:
        n7 = f7.normalAt()
    except Exception as e:
        n7 = None
        print(f"WARN: normalAt() failed on face#7: {e}")
    print(
        "SELECTED: 1 face for mirror plane (face#7) "
        f"center=({c7.x:.3f},{c7.y:.3f},{c7.z:.3f}) "
        + (f"normal=({n7.x:.3f},{n7.y:.3f},{n7.z:.3f})" if n7 else "normal=(None)")
    )

    # Mirror plane is world plane X=80.0 mm (coincident with face#7)
    mx = 80.0
    mirror_origin = (mx, 0.0, 0.0)
    mirror_normal = (1.0, 0.0, 0.0)
    print(f"MIRROR PLANE: origin={mirror_origin} normal={mirror_normal} (world X={mx})")

    # Perform mirror of complete solid s0 across that plane
    pln = gp_Pln(gp_Pnt(*mirror_origin), gp_Dir(*mirror_normal))
    trsf = gp_Trsf()
    trsf.SetMirror(pln)
    mir_wrapped = BRepBuilderAPI_Transform(s0.wrapped, trsf, True).Shape()
    mir_s0 = cq.Shape.cast(mir_wrapped)
    print("SELECTED: 1 solid for mirroring (s0) -> created 1 mirrored solid copy")

    # Fuse original + mirrored into exactly one body
    fused = s0.fuse(mir_s0)

    out_solids = fused.Solids()
    print(f"RESULT: solids_after_fuse={len(out_solids)}")

    bb = fused.BoundingBox()
    print(
        "RESULT BBOX: "
        f"X[{bb.xmin:.3f}..{bb.xmax:.3f}]  Y[{bb.ymin:.3f}..{bb.ymax:.3f}]  Z[{bb.zmin:.3f}..{bb.zmax:.3f}]"
    )

    # Verify against required extents
    exp = {
        "xmin": -25.0,
        "xmax": 185.0,
        "ymin": 11.0,
        "ymax": 34.0,
        "zmin": -3.0,
        "zmax": 11.0,
    }
    deltas = {
        "xmin": bb.xmin - exp["xmin"],
        "xmax": bb.xmax - exp["xmax"],
        "ymin": bb.ymin - exp["ymin"],
        "ymax": bb.ymax - exp["ymax"],
        "zmin": bb.zmin - exp["zmin"],
        "zmax": bb.zmax - exp["zmax"],
    }
    print(
        "VERIFY deltas (actual-expected): "
        f"dxmin={deltas['xmin']:.3f}, dxmax={deltas['xmax']:.3f}, "
        f"dymin={deltas['ymin']:.3f}, dymax={deltas['ymax']:.3f}, "
        f"dzmin={deltas['zmin']:.3f}, dzmax={deltas['zmax']:.3f}"
    )

    if len(out_solids) != 1:
        print("WARNING: fuse did not yield exactly one solid; coincident copy may remain")

    return fused