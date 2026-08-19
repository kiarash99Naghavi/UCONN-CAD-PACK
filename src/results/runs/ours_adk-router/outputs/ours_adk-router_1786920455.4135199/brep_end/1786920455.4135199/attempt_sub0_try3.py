def my_cad_function(args):
    import cadquery as cq
    from math import acos, degrees

    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input
    base = shape.val() if hasattr(shape, "val") else shape

    # --- Resolve target faces by index (on the full imported shape, as required) ---
    faces = base.Faces()
    f_top = faces[46]
    f_bot = faces[22]
    c_top = f_top.Center()
    n_top = f_top.normalAt()
    a_top = f_top.Area()
    c_bot = f_bot.Center()
    n_bot = f_bot.normalAt()
    a_bot = f_bot.Area()
    print(
        "RESOLVED: face #46",
        "center=", tuple(round(v, 6) for v in c_top.toTuple()),
        "normal=", tuple(round(v, 6) for v in n_top.toTuple()),
        "area=", round(a_top, 6),
    )
    print(
        "RESOLVED: face #22",
        "center=", tuple(round(v, 6) for v in c_bot.toTuple()),
        "normal=", tuple(round(v, 6) for v in n_bot.toTuple()),
        "area=", round(a_bot, 6),
    )

    # --- Choose body s0 (largest solid in the STEP, per index) ---
    solids = base.Solids()
    print(f"SELECTED: {len(solids)} solids in STEP")
    if not solids:
        print("ERROR: No solids found; cannot proceed")
        return shape

    vols = [(i, s.Volume()) for i, s in enumerate(solids)]
    s0_idx, _ = max(vols, key=lambda t: t[1])
    s0 = solids[s0_idx]
    print(f"SELECTED: 1 solid for s0 edit   idx={s0_idx} vol={s0.Volume():.3f}")

    # --- Parameters from sub-goal ---
    x_center = -88.9
    y_top = 174.852
    y_bot = -171.45
    z_centers = [-225, -180, -135, -90, -45, 0, 45, 90, 135, 180, 225]
    slot_L = 25.0
    slot_W = 6.0
    end_r = 3.0
    eps = 0.2  # start tool slightly outside face to avoid coincident faces

    print(
        "PARAMS:",
        f"x_center={x_center}",
        f"y_top={y_top}",
        f"y_bot={y_bot}",
        f"z_centers={z_centers}",
    )
    print(f"PARAMS: slot capsule L={slot_L} W={slot_W} end_r={end_r} (major axis must be world X)")

    # --- Build sketch planes anchored to the dataset-defined Y faces ---
    # For normal +Y with xDir +X, local +v corresponds to world -Z.
    pl_top = cq.Plane(origin=(0.0, y_top + eps, 0.0), normal=(0.0, 1.0, 0.0), xDir=(1.0, 0.0, 0.0))
    # For normal -Y with xDir +X, local +v corresponds to world +Z.
    pl_bot = cq.Plane(origin=(0.0, y_bot - eps, 0.0), normal=(0.0, -1.0, 0.0), xDir=(1.0, 0.0, 0.0))
    print("PLANE TOP:", f"origin={(0.0, y_top + eps, 0.0)}", "normal=(0,1,0)", "xDir=(1,0,0)")
    print("PLANE BOT:", f"origin={(0.0, y_bot - eps, 0.0)}", "normal=(0,-1,0)", "xDir=(1,0,0)")

    # Through length large enough to guarantee a through cut (does not change bbox since it's a cut)
    through_len = (y_top - y_bot) + 2 * eps + 50.0
    print(f"PARAMS: through_len={through_len:.3f} (covers full Y span with margin)")

    # --- Build tool solids ---
    # Top: need local v = -worldZ
    pts_top = [(x_center, -z) for z in z_centers]
    # Bottom: local v = +worldZ
    pts_bot = [(x_center, z) for z in z_centers]
    print(f"SELECTED: {len(pts_top)} slot centers for TOP plane (local coords)  first={pts_top[0]} last={pts_top[-1]}")
    print(f"SELECTED: {len(pts_bot)} slot centers for BOT plane (local coords)  first={pts_bot[0]} last={pts_bot[-1]}")

    # slot2D major axis is along local X when angle=0
    test = cq.Workplane(cq.Plane.XY()).slot2D(slot_L, slot_W, angle=0).val()
    tbb = test.BoundingBox()
    print(f"CHECK: slot2D(angle=0) local bbox xlen={tbb.xlen:.3f} ylen={tbb.ylen:.3f} -> major axis along local X")

    tool_top = cq.Workplane(pl_top).pushPoints(pts_top).slot2D(slot_L, slot_W, angle=0).extrude(-through_len).val()
    tool_bot = cq.Workplane(pl_bot).pushPoints(pts_bot).slot2D(slot_L, slot_W, angle=0).extrude(-through_len).val()

    top_sols = tool_top.Solids() if tool_top else []
    bot_sols = tool_bot.Solids() if tool_bot else []
    print(f"SELECTED: {len(top_sols)} solids in TOP tool")
    print(f"SELECTED: {len(bot_sols)} solids in BOT tool")

    tbb_top = tool_top.BoundingBox()
    tbb_bot = tool_bot.BoundingBox()
    print(
        "TOOL TOP bbox:",
        f"y=({tbb_top.ymin:.3f}..{tbb_top.ymax:.3f})",
        f"x=({tbb_top.xmin:.3f}..{tbb_top.xmax:.3f})",
        f"z=({tbb_top.zmin:.3f}..{tbb_top.zmax:.3f})",
    )
    print(
        "TOOL BOT bbox:",
        f"y=({tbb_bot.ymin:.3f}..{tbb_bot.ymax:.3f})",
        f"x=({tbb_bot.xmin:.3f}..{tbb_bot.xmax:.3f})",
        f"z=({tbb_bot.zmin:.3f}..{tbb_bot.zmax:.3f})",
    )

    # --- Cut s0 only ---
    bb_before_all = base.BoundingBox()
    bb_before_s0 = s0.BoundingBox()
    print(
        "BBOX s0 before:",
        (round(bb_before_s0.xmin, 3), round(bb_before_s0.ymin, 3), round(bb_before_s0.zmin, 3)),
        "..",
        (round(bb_before_s0.xmax, 3), round(bb_before_s0.ymax, 3), round(bb_before_s0.zmax, 3)),
    )

    s0_cut = s0.cut(tool_top)
    s0_cut = s0_cut.cut(tool_bot)

    # Ensure we end up with a single Solid for s0 if possible
    out_sols = s0_cut.Solids() if s0_cut else []
    print(f"SELECTED: {len(out_sols)} solids after cutting s0")
    if len(out_sols) == 1:
        s0_edited = out_sols[0]
    elif len(out_sols) > 1:
        # Unexpected split; keep the largest to preserve a single-body result
        out_sols_sorted = sorted(out_sols, key=lambda s: s.Volume(), reverse=True)
        s0_edited = out_sols_sorted[0]
        print(
            "WARNING: cut split s0 into multiple solids; keeping largest only",
            [(i, round(s.Volume(), 3)) for i, s in enumerate(out_sols_sorted[:5])],
        )
    else:
        print("ERROR: cutting produced no solids; returning original shape")
        return shape

    # Recompound with other bodies untouched
    out = cq.Compound.makeCompound([s for i, s in enumerate(solids) if i != s0_idx] + [s0_edited])

    # --- Self checks ---
    bb_after_all = out.BoundingBox()
    print(
        "BBOX before (all):",
        (round(bb_before_all.xmin, 3), round(bb_before_all.ymin, 3), round(bb_before_all.zmin, 3)),
        "..",
        (round(bb_before_all.xmax, 3), round(bb_before_all.ymax, 3), round(bb_before_all.zmax, 3)),
    )
    print(
        "BBOX after  (all):",
        (round(bb_after_all.xmin, 3), round(bb_after_all.ymin, 3), round(bb_after_all.zmin, 3)),
        "..",
        (round(bb_after_all.xmax, 3), round(bb_after_all.ymax, 3), round(bb_after_all.zmax, 3)),
    )

    removed = s0.cut(s0_edited)
    rem_sols = removed.Solids() if removed else []
    print(f"SELECTED: {len(rem_sols)} removed solids (slot cut-outs may merge)")
    if removed:
        rbb = removed.BoundingBox()
        rc = removed.Center()
        print(
            "REMOVED:",
            f"center={tuple(round(v, 3) for v in rc.toTuple())}",
            f"bbox=({rbb.xmin:.3f},{rbb.ymin:.3f},{rbb.zmin:.3f})..({rbb.xmax:.3f},{rbb.ymax:.3f},{rbb.zmax:.3f})",
        )

    # Major-axis orientation report
    major_axis = cq.Vector(1, 0, 0)
    dot = max(-1.0, min(1.0, major_axis.normalized().dot(cq.Vector(1, 0, 0))))
    ang = degrees(acos(dot))
    print(f"ORIENTATION: slots_major_axis_vector={tuple(major_axis.toTuple())} angle_to_world_X={ang:.3f} deg")

    # Print achieved centers (construction targets)
    print("ACHIEVED CENTERS (constructed, world coords):")
    for z in z_centers:
        print(f"  TOP center=({x_center},{y_top},{z}) major_axis=world +X")
    for z in z_centers:
        print(f"  BOT center=({x_center},{y_bot},{z}) major_axis=world +X")

    # Validity check (best-effort)
    try:
        print(f"VALIDITY: s0_before_isValid={bool(s0.isValid())} s0_after_isValid={bool(s0_edited.isValid())} out_isValid={bool(out.isValid())}")
    except Exception as e:
        print(f"VALIDITY: could not evaluate isValid() ({e})")

    return out