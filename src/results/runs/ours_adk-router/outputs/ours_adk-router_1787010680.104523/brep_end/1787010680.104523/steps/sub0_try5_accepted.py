def my_cad_function(args):
    import cadquery as cq

    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input

    base = shape.val() if hasattr(shape, "val") else shape
    solids = base.Solids()
    print(f"INPUT: solids={len(solids)} faces={len(base.Faces())} edges={len(base.Edges())}")
    if len(solids) != 1:
        print("FATAL: expected exactly 1 solid in the STEP; returning input unchanged")
        return shape

    s = solids[0]

    # --- Named constraints from the sub-goal ---
    exp_min = (-949.62, -506.698, 26.8)
    exp_max = (-163.62, -338.409, 595.312)
    z_limit = float(exp_max[2])
    print(f"CONSTRAINTS: exp_min={exp_min} exp_max={exp_max} z_limit={z_limit}")

    bb_in = s.BoundingBox()
    print(
        "BBOX IN: "
        f"min=[{bb_in.xmin:.3f},{bb_in.ymin:.3f},{bb_in.zmin:.3f}] "
        f"max=[{bb_in.xmax:.3f},{bb_in.ymax:.3f},{bb_in.zmax:.3f}]"
    )
    print(
        "BBOX IN DELTA vs expected: "
        f"dmin=[{bb_in.xmin-exp_min[0]:.3f},{bb_in.ymin-exp_min[1]:.3f},{bb_in.zmin-exp_min[2]:.3f}] "
        f"dmax=[{bb_in.xmax-exp_max[0]:.3f},{bb_in.ymax-exp_max[1]:.3f},{bb_in.zmax-exp_max[2]:.3f}]"
    )

    # The only remaining defect reported by QA is a +Z protrusion (+0.059mm). Trim anything above z=595.312.
    # Build a large cutter box that starts just above the limit plane, so geometry at exactly z_limit is preserved.
    eps = 1e-5
    z0 = z_limit + eps

    pad = 20.0
    x0 = bb_in.xmin - pad
    y0 = bb_in.ymin - pad
    z_height = max(5.0, (bb_in.zmax - z0) + 50.0)  # guaranteed to cover any protrusion
    x_len = bb_in.xlen + 2.0 * pad
    y_len = bb_in.ylen + 2.0 * pad

    cutter = cq.Solid.makeBox(x_len, y_len, z_height, pnt=cq.Vector(x0, y0, z0))
    bb_cut = cutter.BoundingBox()
    print(
        "CUTTER: remove region above +Z limit "
        f"z0={z0:.6f} box_min=[{bb_cut.xmin:.3f},{bb_cut.ymin:.3f},{bb_cut.zmin:.3f}] "
        f"box_max=[{bb_cut.xmax:.3f},{bb_cut.ymax:.3f},{bb_cut.zmax:.3f}]"
    )

    print("BOOL: cutting protrusion above z_limit")
    out = s.cut(cutter)

    # Refine to remove any splitter faces created by the trim
    try:
        out = out.clean()
        print("REFINE: out.clean() succeeded")
    except Exception as e:
        print(f"REFINE: out.clean() failed: {e}")
        try:
            out = cq.Workplane(obj=out).clean().val()
            print("REFINE: Workplane(obj=out).clean().val() succeeded")
        except Exception as e2:
            print(f"REFINE: Workplane clean also failed: {e2}")

    # --- Self-checks ---
    print(f"OUTPUT: solids={len(out.Solids())} faces={len(out.Faces())} edges={len(out.Edges())}")

    bb_out = out.BoundingBox()
    print(
        "BBOX OUT: "
        f"min=[{bb_out.xmin:.3f},{bb_out.ymin:.3f},{bb_out.zmin:.3f}] "
        f"max=[{bb_out.xmax:.3f},{bb_out.ymax:.3f},{bb_out.zmax:.3f}]"
    )
    print(
        "BBOX OUT DELTA vs expected: "
        f"dmin=[{bb_out.xmin-exp_min[0]:.3f},{bb_out.ymin-exp_min[1]:.3f},{bb_out.zmin-exp_min[2]:.3f}] "
        f"dmax=[{bb_out.xmax-exp_max[0]:.3f},{bb_out.ymax-exp_max[1]:.3f},{bb_out.zmax-exp_max[2]:.3f}]"
    )

    # Isolate removed material (what we trimmed away)
    try:
        removed = s.cut(out)
        bb_rem = removed.BoundingBox()
        c_rem = removed.Center()
        print(
            f"REMOVED: volume={removed.Volume():.6f} center=({c_rem.x:.3f},{c_rem.y:.3f},{c_rem.z:.3f}) "
            f"bbox=([{bb_rem.xmin:.3f},{bb_rem.ymin:.3f},{bb_rem.zmin:.3f}].."
            f"[{bb_rem.xmax:.3f},{bb_rem.ymax:.3f},{bb_rem.zmax:.3f}])"
        )
        print(f"REMOVED: zmax={bb_rem.zmax:.6f} (should be > {z_limit:.6f} if protrusion existed)")
    except Exception as e:
        print(f"REMOVED: could not compute (s.cut(out)) due to: {e}")

    # Final sanity: ensure max Z is not above limit (allow tiny tolerance)
    dz = bb_out.zmax - z_limit
    print(f"FINAL CHECK: out.zmax - z_limit = {dz:.6f} mm")

    return out