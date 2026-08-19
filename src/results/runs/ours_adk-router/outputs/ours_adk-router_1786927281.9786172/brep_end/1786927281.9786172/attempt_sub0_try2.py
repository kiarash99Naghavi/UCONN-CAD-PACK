def my_cad_function(args):
    import cadquery as cq
    from OCP.gp import gp_Pnt, gp_Dir, gp_Ax2, gp_Trsf
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
        n7 = f7.normalAt()  # must be no-args
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
    print(f"MIRROR PLANE (absolute): origin={mirror_origin} normal={mirror_normal} (world X={mx})")

    # --- FIX FOR PREVIOUS FAILURE ---
    # gp_Trsf.SetMirror does NOT accept gp_Pln in this build; it accepts gp_Ax2 for a plane mirror.
    ax2 = gp_Ax2(gp_Pnt(*mirror_origin), gp_Dir(*mirror_normal), gp_Dir(0.0, 1.0, 0.0))
    trsf = gp_Trsf()
    trsf.SetMirror(ax2)

    mir_wrapped = BRepBuilderAPI_Transform(s0.wrapped, trsf, True).Shape()
    mir_s0 = cq.Shape.cast(mir_wrapped)
    print("SELECTED: 1 solid for mirroring (s0) -> created 1 mirrored solid copy")

    bb0 = s0.BoundingBox()
    bbm = mir_s0.BoundingBox()
    print(
        "BBOX original s0: "
        f"X[{bb0.xmin:.3f}..{bb0.xmax:.3f}] Y[{bb0.ymin:.3f}..{bb0.ymax:.3f}] Z[{bb0.zmin:.3f}..{bb0.zmax:.3f}]"
    )
    print(
        "BBOX mirrored s0: "
        f"X[{bbm.xmin:.3f}..{bbm.xmax:.3f}] Y[{bbm.ymin:.3f}..{bbm.ymax:.3f}] Z[{bbm.zmin:.3f}..{bbm.zmax:.3f}]"
    )

    # Fuse original + mirrored into exactly one body
    fused = None
    try:
        fused = s0.fuse(mir_s0, glue=True)
        print("FUSE: used Shape.fuse(..., glue=True)")
    except TypeError as e:
        print(f"FUSE: glue=True not supported ({e}); falling back to Shape.fuse(other)")
        fused = s0.fuse(mir_s0)

    out_solids = fused.Solids()
    print(f"RESULT: solids_after_fuse={len(out_solids)}")

    # If fuse did not merge due to coincident contact, retry with fuzzy fuse via OCC
    if len(out_solids) != 1:
        print("WARNING: fuse did not yield exactly one solid; retrying with fuzzy OCC fuse")
        try:
            from OCP.BRepAlgoAPI import BRepAlgoAPI_Fuse

            op = BRepAlgoAPI_Fuse(s0.wrapped, mir_s0.wrapped)
            if hasattr(op, "SetFuzzyValue"):
                op.SetFuzzyValue(1e-5)
                print("FUSE: using BRepAlgoAPI_Fuse with fuzzy=1e-5")
            op.Build()
            fused = cq.Shape.cast(op.Shape())
            out_solids = fused.Solids()
            print(f"RESULT (fuzzy fuse): solids_after_fuse={len(out_solids)}")
        except Exception as e:
            print(f"ERROR: fuzzy fuse attempt failed: {e}")

    # Placement self-check: compute added material (mirrored part minus original)
    try:
        added = fused.cut(s0)
        bb_added = added.BoundingBox()
        c_added = added.Center()
        print(
            "ADDED (fused - original): "
            f"center=({c_added.x:.3f},{c_added.y:.3f},{c_added.z:.3f}) "
            f"bbox X[{bb_added.xmin:.3f}..{bb_added.xmax:.3f}] Y[{bb_added.ymin:.3f}..{bb_added.ymax:.3f}] Z[{bb_added.zmin:.3f}..{bb_added.zmax:.3f}]"
        )
    except Exception as e:
        print(f"WARN: could not compute ADDED shape via boolean diff: {e}")

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
        print("WARNING: still not exactly one solid; coincident copy may remain")

    return fused