def my_cad_function(args):
    import cadquery as cq
    from OCP.OCP.Standard import Standard_Failure

    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input
    base = shape.val() if hasattr(shape, "val") else shape

    sols = base.Solids()
    print(f"SELECTED: {len(sols)} solids from STEP")
    if len(sols) < 2:
        print("WARNING: expected 2 solids (s0 housing + s1 switch), proceeding anyway")

    if len(sols) == 0:
        print("SELECTED: 0 solids -> NO-OP")
        return shape

    s0 = sols[0]
    s1 = sols[1] if len(sols) > 1 else None

    bb0 = s0.BoundingBox()
    print(f"s0 pre: vol={s0.Volume():.3f} bbox=([{bb0.xmin:.3f},{bb0.ymin:.3f},{bb0.zmin:.3f}]..[{bb0.xmax:.3f},{bb0.ymax:.3f},{bb0.zmax:.3f}])")

    if s1 is None:
        print("SELECTED: 0 solids for s1 tool source (missing) -> NO-OP")
        return shape

    bb1 = s1.BoundingBox()
    print(f"s1 (unchanged body): vol={s1.Volume():.3f} bbox=([{bb1.xmin:.3f},{bb1.ymin:.3f},{bb1.zmin:.3f}]..[{bb1.xmax:.3f},{bb1.ymax:.3f},{bb1.zmax:.3f}])")
    print(f"s1 bbox size: x={bb1.xlen:.3f} (expected ~4.997), y={bb1.ylen:.3f}, z={bb1.zlen:.3f} (expected ~14.061)")

    # --- anchors from sub-goal (explicit numbers) ---
    named_center_top = cq.Vector(27.9, 22.101, 51.27)     # measured existing switch feature center near this
    named_center_bottom = cq.Vector(27.9, 2.16, 51.27)    # target nominal actuator center on min-Y side
    minY_housing = bb0.ymin
    sweep_dz = 2.0  # small nonzero translation along world Z
    clip_eps = 0.20 # keep cutter starting just outside the min-Y surface

    print("NAMED NUMBERS:")
    print(f"  named_center_top     = [{named_center_top.x:.3f}, {named_center_top.y:.3f}, {named_center_top.z:.3f}]")
    print(f"  named_center_bottom  = [{named_center_bottom.x:.3f}, {named_center_bottom.y:.3f}, {named_center_bottom.z:.3f}]")
    print(f"  housing minY (s0)    = {minY_housing:.3f} (index says 2.160)")
    print(f"  sweep_dz             = {sweep_dz:.3f}")
    print(f"  clip_eps             = {clip_eps:.3f}")

    # --- build transformed tool copy from existing switch body s1 ---
    # Turn from top-facing to bottom-facing: flip 180 degrees about world X axis through named_center_top
    axis_p1 = (named_center_top.x, named_center_top.y, named_center_top.z)
    axis_p2 = (named_center_top.x + 1.0, named_center_top.y, named_center_top.z)
    tool_rot = s1.rotate(axis_p1, axis_p2, 180)

    # Translate so the nominal actuator center moves from ~named_center_top to ~named_center_bottom
    dv = named_center_bottom.sub(named_center_top)
    tool_pos = tool_rot.translate((dv.x, dv.y, dv.z))

    bb_tool = tool_pos.BoundingBox()
    print(
        "Tool placed (pre-clip): "
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

    # Clip away any portion of the tool that would extend outward in -Y,
    # so the cutter extends inward (+Y) from near the housing min-Y surface.
    # (This keeps the named center placement while preventing a huge exterior cutter.)
    Lx = max(bb0.xlen, 200.0)
    Ly = 200.0
    Lz = max(bb0.zlen, 200.0)
    keeper = (
        cq.Workplane(cq.Plane.XY())
        .box(Lx, Ly, Lz, centered=(True, False, True))
        .translate((bb0.center.x, minY_housing - clip_eps, bb0.center.z))
        .val()
    )
    print(f"SELECTED: 1 keeper halfspace for +Y clipping (y0={minY_housing - clip_eps:.3f})")

    tool_clipped = tool_pos.intersect(keeper)
    print(f"SELECTED: {len(tool_clipped.Solids())} solids after clipping tool")
    if len(tool_clipped.Solids()) == 0:
        print("ERROR: clipped tool became empty -> NO-OP")
        return shape

    bb_toolc = tool_clipped.BoundingBox()
    print(
        "Tool placed (post-clip): "
        f"bbox center=({bb_toolc.center.x:.3f},{bb_toolc.center.y:.3f},{bb_toolc.center.z:.3f}) "
        f"size=({bb_toolc.xlen:.3f},{bb_toolc.ylen:.3f},{bb_toolc.zlen:.3f}) "
        f"y-range=[{bb_toolc.ymin:.3f},{bb_toolc.ymax:.3f}]"
    )
    print(f"CHECK: tool mouth near minY: tool.ymin={bb_toolc.ymin:.3f} vs minY={minY_housing:.3f} (should be close/<=)")
    print(f"CHECK: tool extends inward +Y: tool.ymax={bb_toolc.ymax:.3f} (should be > minY)")

    # --- swept envelope over small translation along world Z (union of discrete positions) ---
    offsets = (-sweep_dz / 2.0, 0.0, sweep_dz / 2.0)
    swept = None
    for i, oz in enumerate(offsets):
        inst = tool_clipped.translate((0.0, 0.0, oz))
        print(f"SELECTED: 1 tool instance for sweep step {i} at dZ={oz:.3f}")
        swept = inst if swept is None else swept.fuse(inst)

    print(f"SELECTED: {len(swept.Solids())} solids in swept cutter")
    if len(swept.Solids()) == 0:
        print("ERROR: swept cutter is empty -> NO-OP")
        return shape

    bb_swept = swept.BoundingBox()
    print(
        "Swept cutter: "
        f"bbox center=({bb_swept.center.x:.3f},{bb_swept.center.y:.3f},{bb_swept.center.z:.3f}) "
        f"size=({bb_swept.xlen:.3f},{bb_swept.ylen:.3f},{bb_swept.zlen:.3f})"
    )

    # Precompute overlap as a robust "removed" estimate (avoids void bbox from s0.cut(edited_s0) in no-op cases)
    overlap = s0.intersect(swept)
    print(f"SELECTED: {len(overlap.Solids())} solids in (s0 ∩ swept) overlap")
    if len(overlap.Solids()) == 0:
        print("ERROR: cutter does not intersect housing (s0) -> NO-OP")
        return shape

    bb_ov = overlap.BoundingBox()
    print(
        "OVERLAP (s0 ∩ swept): "
        f"bbox center=({bb_ov.center.x:.3f},{bb_ov.center.y:.3f},{bb_ov.center.z:.3f}) "
        f"y-range=[{bb_ov.ymin:.3f},{bb_ov.ymax:.3f}] size=({bb_ov.xlen:.3f},{bb_ov.ylen:.3f},{bb_ov.zlen:.3f})"
    )

    # --- cut only s0 ---
    edited_s0 = s0.cut(swept)
    print(f"after cut: solids={len(edited_s0.Solids())} vol={edited_s0.Volume():.3f} (pre vol={s0.Volume():.3f}, delta={edited_s0.Volume() - s0.Volume():.3f})")
    if len(edited_s0.Solids()) == 0:
        print("ERROR: cut produced empty housing (tool swallowed s0?) -> NO-OP")
        return shape

    # BBox invariants for housing itself
    bb0_post = edited_s0.BoundingBox()
    print(
        "BBOX s0 pre vs post: "
        f"pre_min=({bb0.xmin:.3f},{bb0.ymin:.3f},{bb0.zmin:.3f}) pre_max=({bb0.xmax:.3f},{bb0.ymax:.3f},{bb0.zmax:.3f}) | "
        f"post_min=({bb0_post.xmin:.3f},{bb0_post.ymin:.3f},{bb0_post.zmin:.3f}) post_max=({bb0_post.xmax:.3f},{bb0_post.ymax:.3f},{bb0_post.zmax:.3f})"
    )

    # Approximate opening mouth near min-Y surface: intersect overlap with a thin slab around y=minY
    slab_th = 0.60
    slab = (
        cq.Workplane(cq.Plane.XY())
        .box(Lx, slab_th, Lz, centered=(True, True, True))
        .translate((bb0.center.x, minY_housing, bb0.center.z))
        .val()
    )
    print(f"SELECTED: 1 slab for mouth probing at y={minY_housing:.3f} (th={slab_th:.3f})")

    mouth = overlap.intersect(slab)
    vcnt = len(mouth.Vertices())
    ecnt = len(mouth.Edges())
    fcnt = len(mouth.Faces())
    scnt = len(mouth.Solids())
    print(f"SELECTED: mouth probe entities: solids={scnt} faces={fcnt} edges={ecnt} vertices={vcnt}")

    mouth_center = None
    angle_deg = None
    if vcnt == 0 and ecnt == 0 and fcnt == 0 and scnt == 0:
        print("WARNING: mouth probe produced empty shape; falling back to overlap bbox center")
        mouth_center = cq.Vector(bb_ov.center.x, minY_housing, bb_ov.center.z)
        angle_deg = 0.0 if bb_ov.zlen >= bb_ov.xlen else 90.0
    else:
        try:
            bb_m = mouth.BoundingBox()
            mouth_center = cq.Vector(bb_m.center.x, minY_housing, bb_m.center.z)
            angle_deg = 0.0 if bb_m.zlen >= bb_m.xlen else 90.0
            print(
                "MOUTH (overlap ∩ slab) bbox: "
                f"center=({bb_m.center.x:.3f},{bb_m.center.y:.3f},{bb_m.center.z:.3f}) "
                f"size=({bb_m.xlen:.3f},{bb_m.ylen:.3f},{bb_m.zlen:.3f})"
            )
        except Standard_Failure:
            print("WARNING: mouth BoundingBox() failed (void box); falling back to overlap bbox")
            mouth_center = cq.Vector(bb_ov.center.x, minY_housing, bb_ov.center.z)
            angle_deg = 0.0 if bb_ov.zlen >= bb_ov.xlen else 90.0

    print(
        f"OPENING center (approx, on min-Y) = [{mouth_center.x:.3f}, {mouth_center.y:.3f}, {mouth_center.z:.3f}] ; "
        f"long-axis angle vs world Z = {angle_deg:.1f} deg"
    )
    print(
        "CHECK opening center vs named [27.9, 2.16, 51.27] delta: "
        f"dx={mouth_center.x - named_center_bottom.x:.3f}, "
        f"dy={mouth_center.y - named_center_bottom.y:.3f}, "
        f"dz={mouth_center.z - named_center_bottom.z:.3f}"
    )
    print(f"CHECK long-axis angle: {angle_deg:.1f} deg (want 0.0, not 90.0)")

    # Strict decrease check
    if edited_s0.Volume() >= s0.Volume():
        print("WARNING: s0 volume did NOT strictly decrease -> cut likely missed/failed")
    else:
        print("OK: s0 volume strictly decreased")

    # Recompound with s1 unchanged
    out = cq.Compound.makeCompound([edited_s0, s1])

    # Overall bbox should remain unchanged
    pre_all_bb = base.BoundingBox()
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

    return out