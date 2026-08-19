def my_cad_function(args):
    import cadquery as cq

    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input
    base = shape.val() if hasattr(shape, "val") else shape

    sols = base.Solids()
    print(f"IMPORTED: base valid={base.isValid()} solids={len(sols)}")

    # --- Sub-goal: add removable cap as ONE new, separate solid (unfused) ---
    # Named numbers (explicit self-check anchors)
    cx, cy = -88.9, 100.0
    axis = cq.Vector(0, 0, 1)
    r_outer = 22.225
    z_base = 296.7
    z_top = 308.7
    r_recess = 19.05
    z_recess_top = 304.7
    eps = 0.2  # extends cutter slightly below seating plane to avoid coincident boundary

    h_outer = z_top - z_base  # 12.0
    z_tool_base = z_base - eps
    h_tool = z_recess_top - z_tool_base  # 8.0 + eps

    print("CAP PARAMS:")
    print(f"  center (x,y)=({cx},{cy}) axis={list(axis.toTuple())}")
    print(f"  outer: r={r_outer} z=[{z_base}..{z_top}] h={h_outer}")
    print(f"  recess tool: r={r_recess} z=[{z_tool_base}..{z_recess_top}] h={h_tool} (eps={eps})")

    outer = cq.Solid.makeCylinder(
        r_outer,
        h_outer,
        pnt=cq.Vector(cx, cy, z_base),
        dir=axis,
        angleDegrees=360,
    )

    tool = cq.Solid.makeCylinder(
        r_recess,
        h_tool,
        pnt=cq.Vector(cx, cy, z_tool_base),
        dir=axis,
        angleDegrees=360,
    )

    cap = outer.cut(tool)

    # Validate cap independently
    cap_sols = cap.Solids()
    print(f"CAP: isValid={cap.isValid()} solids_in_cap={len(cap_sols)}")

    bb = cap.BoundingBox()
    print("CAP BBOX:")
    print(f"  actual   min [{bb.xmin:.6f}, {bb.ymin:.6f}, {bb.zmin:.6f}]  max [{bb.xmax:.6f}, {bb.ymax:.6f}, {bb.zmax:.6f}]")
    print("  required min [-111.125, 77.775, 296.7]  max [-66.675, 122.225, 308.7]")
    print("  deltas:")
    print(f"    xmin {bb.xmin - (-111.125):.6f}  ymin {bb.ymin - 77.775:.6f}  zmin {bb.zmin - 296.7:.6f}")
    print(f"    xmax {bb.xmax - (-66.675):.6f}  ymax {bb.ymax - 122.225:.6f}  zmax {bb.zmax - 308.7:.6f}")

    c = cap.Center()
    print("CAP CENTER (for sanity only; bbox/axis are primary):")
    print(f"  center=({c.x:.6f}, {c.y:.6f}, {c.z:.6f})")

    # Append cap as a new, separate body; do NOT fuse with any existing solid
    out = cq.Compound.makeCompound(list(sols) + [cap])
    out_sols = out.Solids()
    print(f"OUTPUT: solids={len(out_sols)} (expected {len(sols)+1})")

    return out