def my_cad_function(args):
    import cadquery as cq

    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input
    base = shape.val() if hasattr(shape, "val") else shape

    solids = list(base.Solids())
    print("Imported solids:", len(solids))
    for i, s in enumerate(solids):
        bb = s.BoundingBox()
        try:
            vol = s.Volume()
        except Exception:
            vol = None
        print(f"  solid[{i}] vol={vol} bbox=({bb.xmin:.3f},{bb.ymin:.3f},{bb.zmin:.3f})..({bb.xmax:.3f},{bb.ymax:.3f},{bb.zmax:.3f})")

    if len(solids) < 2:
        print("ERROR: Expected 2 solids; cannot proceed safely.")
        return shape

    s0 = solids[0]
    s1 = solids[1]

    # Named targets from the sub-goal
    y_top = 3.175
    y_bot = 2.175
    h = y_top - y_bot  # 1.0
    r_cyl = 15.75
    r_top = 14.75
    print("Targets: y_bot=", y_bot, "y_top=", y_top, "height=", h, "r_cyl=", r_cyl, "r_top=", r_top)

    # Build a ring-shaped chamfer cut tool: (big cylinder) - (inner conical frustum)
    # This avoids subtracting the entire (solid) frustum which would remove the hub interior.
    Rbig = 16.5  # just larger than 15.75; kept small to be local to hub region
    p0 = cq.Vector(0, y_bot, 0)
    axis = cq.Vector(0, 1, 0)

    big_cyl = cq.Solid.makeCylinder(Rbig, h, pnt=p0, dir=axis)
    inner_frustum = cq.Solid.makeCone(r_cyl, r_top, h, pnt=p0, dir=axis)
    tool = big_cyl.cut(inner_frustum)

    bb_tool = tool.BoundingBox()
    print(f"Tool bbox: ({bb_tool.xmin:.3f},{bb_tool.ymin:.3f},{bb_tool.zmin:.3f})..({bb_tool.xmax:.3f},{bb_tool.ymax:.3f},{bb_tool.zmax:.3f})")
    print("Tool y-span deltas:", "ymin-delta", bb_tool.ymin - y_bot, "ymax-delta", bb_tool.ymax - y_top)

    # Apply cut ONLY to SOLID #0
    s0_mod = s0.cut(tool)

    # Self-check removed region
    try:
        removed = s0.cut(s0_mod)
        bb_rem = removed.BoundingBox()
        print("Removed volume:", removed.Volume())
        print(f"Removed bbox: ({bb_rem.xmin:.3f},{bb_rem.ymin:.3f},{bb_rem.zmin:.3f})..({bb_rem.xmax:.3f},{bb_rem.ymax:.3f},{bb_rem.zmax:.3f})")
        print("Removed y-span vs targets:", "ymin", bb_rem.ymin, "(delta", bb_rem.ymin - y_bot, ")",
              "ymax", bb_rem.ymax, "(delta", bb_rem.ymax - y_top, ")")
    except Exception as e:
        print("Could not compute removed diagnostics:", e)

    bb0_before = s0.BoundingBox()
    bb0_after = s0_mod.BoundingBox()
    print("SOLID#0 bbox before:", (bb0_before.xmin, bb0_before.ymin, bb0_before.zmin), "..", (bb0_before.xmax, bb0_before.ymax, bb0_before.zmax))
    print("SOLID#0 bbox after :", (bb0_after.xmin, bb0_after.ymin, bb0_after.zmin), "..", (bb0_after.xmax, bb0_after.ymax, bb0_after.zmax))

    # Recombine without touching SOLID #1
    out = cq.Compound.makeCompound([s0_mod, s1])
    return out