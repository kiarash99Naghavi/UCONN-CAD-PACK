def my_cad_function(args):
    import cadquery as cq

    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input
    base = shape.val() if hasattr(shape, "val") else shape

    # --- resolve solids (keep s1 unchanged; cut only s0) ---
    sols = base.Solids()
    print(f"SELECTED: {len(sols)} solids from STEP")
    if len(sols) != 2:
        print("WARNING: expected 2 solids (s0 housing + s1 switch), proceeding anyway")

    s0 = sols[0]
    s1 = sols[1] if len(sols) > 1 else None

    bb0 = s0.BoundingBox()
    print(f"s0 pre: vol={s0.Volume():.3f} bbox=([{bb0.xmin:.3f},{bb0.ymin:.3f},{bb0.zmin:.3f}]..[{bb0.xmax:.3f},{bb0.ymax:.3f},{bb0.zmax:.3f}])")
    if s1 is None:
        print("SELECTED: 0 solids for s1 tool source (missing) -> NO-OP")
        return shape

    bb1 = s1.BoundingBox()
    print(f"s1 (unchanged body) : vol={s1.Volume():.3f} bbox=([{bb1.xmin:.3f},{bb1.ymin:.3f},{bb1.zmin:.3f}]..[{bb1.xmax:.3f},{bb1.ymax:.3f},{bb1.zmax:.3f}])")
    print(f"s1 bbox size: x={bb1.xlen:.3f} (expected ~4.997), y={bb1.ylen:.3f}, z={bb1.zlen:.3f} (expected ~14.061)")

    # --- anchors from sub-goal (explicit numbers) ---
    named_center_top = cq.Vector(27.9, 22.101, 51.27)   # measured feature center near this
    named_center_bottom = cq.Vector(27.9, 2.16, 51.27)   # target actuator center on min-Y side
    minY_housing = bb0.ymin
    sweep_dz = 2.0  # small nonzero translation along world Z to create 2-position envelope

    print("NAMED NUMBERS:")
    print(f"  named_center_top     = [{named_center_top.x:.3f}, {named_center_top.y:.3f}, {named_center_top.z:.3f}]")
    print(f"  named_center_bottom  = [{named_center_bottom.x:.3f}, {named_center_bottom.y:.3f}, {named_center_bottom.z:.3f}]")
    print(f"  housing minY (s0)    = {minY_housing:.3f} (index says 2.160)")
    print(f"  sweep_dz             = {sweep_dz:.3f}")

    # --- build transformed tool copy from existing switch body s1 ---
    # Turn from current top-facing to bottom-facing: flip 180 degrees about world X axis through the named center
    axis_p1 = (named_center_top.x, named_center_top.y, named_center_top.z)
    axis_p2 = (named_center_top.x + 1.0, named_center_top.y, named_center_top.z)
    tool_rot = s1.rotate(axis_p1, axis_p2, 180)

    # Translate so nominal actuator center moves from ~[27.9,22.101,51.27] to ~[27.9,2.16,51.27]
    dv = named_center_bottom.sub(named_center_top)
    tool_pos = tool_rot.translate((dv.x, dv.y, dv.z))

    bb_tool = tool_pos.BoundingBox()
    print(
        "Tool placed (pre-sweep): "
        f"bbox center=({bb_tool.center.x:.3f},{bb_tool.center.y:.3f},{bb_tool.center.z:.3f}) "
        f"size=({bb_tool.xlen:.3f},{bb_tool.ylen:.3f},{bb_tool.zlen:.3f}) "
        f"y-range=[{bb_tool.ymin:.3f},{bb_tool.ymax:.3f}]"
    )
    print(
        f"Tool vs target center delta (bbox.center - named_center_bottom): "
        f"dx={bb_tool.center.x - named_center_bottom.x:.3f}, "
        f"dy={bb_tool.center.y - named_center_bottom.y:.3f}, "
        f"dz={bb_tool.center.z - named_center_bottom.z:.3f}"
    )

    # Ensure it extends inward in +Y from the min-Y surface (at least some of it must be at y>minY)
    print(f"CHECK: tool intersects minY? tool.ymin={bb_tool.ymin:.3f} vs minY={minY_housing:.3f} (should be <= for mouth)")
    print(f"CHECK: tool extends inward +Y? tool.ymax={bb_tool.ymax:.3f} (should be > minY)")

    # --- swept envelope over small translation along world Z (union of positions) ---
    offsets = (-sweep_dz / 2.0, 0.0, sweep_dz / 2.0)
    swept = None
    for i, oz in enumerate(offsets):
        inst = tool_pos.translate((0.0, 0.0, oz))
        print(f"SELECTED: 1 tool instance for sweep step {i} at dZ={oz:.3f}")
        swept = inst if swept is None else swept.fuse(inst)

    bb_swept = swept.BoundingBox()
    print(
        "Swept cutter: "
        f"bbox center=({bb_swept.center.x:.3f},{bb_swept.center.y:.3f},{bb_swept.center.z:.3f}) "
        f"size=({bb_swept.xlen:.3f},{bb_swept.ylen:.3f},{bb_swept.zlen:.3f})"
    )

    # --- cut only s0 ---
    edited_s0 = s0.cut(swept)

    # Placement self-check: compute removed material and report center/angle
    removed = s0.cut(edited_s0)
    rem_vol = removed.Volume() if removed.Volume() is not None else 0.0
    bb_rem = removed.BoundingBox()

    # long-axis angle relative to world Z: 0 deg means aligned with Z, 90 deg means aligned with X
    angle_deg = 0.0 if bb_rem.zlen >= bb_rem.xlen else 90.0

    print(f"s0 post: vol={edited_s0.Volume():.3f} (delta={edited_s0.Volume() - s0.Volume():.3f})")
    print(
        "REMOVED (s0 - edited_s0): "
        f"vol={rem_vol:.3f} "
        f"bbox center=({bb_rem.center.x:.3f},{bb_rem.center.y:.3f},{bb_rem.center.z:.3f}) "
        f"ymin={bb_rem.ymin:.3f} ymax={bb_rem.ymax:.3f} "
        f"size=({bb_rem.xlen:.3f},{bb_rem.ylen:.3f},{bb_rem.zlen:.3f})"
    )
    print(
        f"OPENING center (approx) = [{bb_rem.center.x:.3f}, {bb_rem.center.y:.3f}, {bb_rem.center.z:.3f}] ; "
        f"long-axis angle vs world Z = {angle_deg:.1f} deg"
    )

    # bbox invariants
    pre_all_bb = base.BoundingBox()
    out = cq.Compound.makeCompound([edited_s0, s1])
    post_all_bb = out.BoundingBox()
    print(
        "BBOX ALL pre vs post: "
        f"pre_min=({pre_all_bb.xmin:.3f},{pre_all_bb.ymin:.3f},{pre_all_bb.zmin:.3f}) pre_max=({pre_all_bb.xmax:.3f},{pre_all_bb.ymax:.3f},{pre_all_bb.zmax:.3f}) | "
        f"post_min=({post_all_bb.xmin:.3f},{post_all_bb.ymin:.3f},{post_all_bb.zmin:.3f}) post_max=({post_all_bb.xmax:.3f},{post_all_bb.ymax:.3f},{post_all_bb.zmax:.3f})"
    )
    print(
        "CHECK bbox deltas: "
        f"dmin=({post_all_bb.xmin - pre_all_bb.xmin:.6f},{post_all_bb.ymin - pre_all_bb.ymin:.6f},{post_all_bb.zmin - pre_all_bb.zmin:.6f}) "
        f"dmax=({post_all_bb.xmax - pre_all_bb.xmax:.6f},{post_all_bb.ymax - pre_all_bb.ymax:.6f},{post_all_bb.zmax - pre_all_bb.zmax:.6f})"
    )

    # Strict decrease check
    if edited_s0.Volume() >= s0.Volume():
        print("WARNING: s0 volume did NOT strictly decrease -> cut likely missed or failed")
    else:
        print("OK: s0 volume strictly decreased")

    # Opening near min-Y check
    print(
        f"CHECK opening on min-Y side: removed.ymin={bb_rem.ymin:.3f} vs housing minY={minY_housing:.3f} (should be close/<=)"
    )

    return out