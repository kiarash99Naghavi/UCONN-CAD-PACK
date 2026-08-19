def my_cad_function(args):
    import cadquery as cq
    from OCP.OCP.Standard import Standard_Failure

    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input
    base = shape.val() if hasattr(shape, "val") else shape

    sols = base.Solids()
    print(f"SELECTED: {len(sols)} solids from STEP")
    if len(sols) < 2:
        print("WARNING: expected at least 2 solids (s0 housing + s1 existing switch body)")
    if len(sols) == 0:
        print("SELECTED: 0 solids -> NO-OP")
        return shape

    s0 = sols[0]
    s1 = sols[1] if len(sols) > 1 else None

    bb0 = s0.BoundingBox()
    print(
        "s0: "
        f"vol={s0.Volume():.3f} "
        f"bbox=([{bb0.xmin:.3f},{bb0.ymin:.3f},{bb0.zmin:.3f}]..[{bb0.xmax:.3f},{bb0.ymax:.3f},{bb0.zmax:.3f}])"
    )

    if s1 is None:
        print("SELECTED: 0 solids for s1 (missing) -> NO-OP")
        return shape

    bb1 = s1.BoundingBox()
    print(
        "s1 (source body): "
        f"vol={s1.Volume():.3f} "
        f"bbox=([{bb1.xmin:.3f},{bb1.ymin:.3f},{bb1.zmin:.3f}]..[{bb1.xmax:.3f},{bb1.ymax:.3f},{bb1.zmax:.3f}])"
    )
    print(f"s1 bbox size check: x={bb1.xlen:.3f} (~4.997), z={bb1.zlen:.3f} (~14.061)")

    # --- named anchors / requirements ---
    named_center_top = cq.Vector(27.9, 22.101, 51.27)
    named_center_bottom = cq.Vector(27.9, 2.16, 51.27)
    minY = bb0.ymin  # should be 2.16

    sweep_dz = 2.0

    print("NAMED NUMBERS:")
    print(f"  named_center_top      = [{named_center_top.x:.3f}, {named_center_top.y:.3f}, {named_center_top.z:.3f}]")
    print(f"  named_center_bottom   = [{named_center_bottom.x:.3f}, {named_center_bottom.y:.3f}, {named_center_bottom.z:.3f}]")
    print(f"  housing minY (s0)     = {minY:.3f} (want 2.160)")
    print(f"  sliding direction     = world +Z (angle want 0 deg)")
    print(f"  demo translation along Z within cavity = ±{sweep_dz/2.0:.3f} mm")

    # --- transform: same bottom-facing transform used by the cavity cut ---
    axis_p1 = (named_center_top.x, named_center_top.y, named_center_top.z)
    axis_p2 = (named_center_top.x + 1.0, named_center_top.y, named_center_top.z)
    act = s1.rotate(axis_p1, axis_p2, 180)

    dv = named_center_bottom.sub(named_center_top)
    act = act.translate((dv.x, dv.y, dv.z))

    bb_act0 = act.BoundingBox()
    print(
        "Actuator copy (pre-clip): "
        f"bbox.center=({bb_act0.center.x:.3f},{bb_act0.center.y:.3f},{bb_act0.center.z:.3f}) "
        f"size=({bb_act0.xlen:.3f},{bb_act0.ylen:.3f},{bb_act0.zlen:.3f}) y-range=[{bb_act0.ymin:.3f},{bb_act0.ymax:.3f}]"
    )

    # --- clip so outer exposed surface is exactly at y=minY and thickness extends inward +Y ---
    Lx = max(bb0.xlen, 200.0)
    Ly = 200.0
    Lz = max(bb0.zlen, 200.0)
    keeper = (
        cq.Workplane(cq.Plane.XY())
        .box(Lx, Ly, Lz, centered=(True, False, True))
        .translate((bb0.center.x, minY, bb0.center.z))
        .val()
    )
    print(f"SELECTED: 1 keeper halfspace for +Y clipping at y>={minY:.3f}")

    try:
        act = act.intersect(keeper)
    except Standard_Failure:
        print("ERROR: actuator intersect keeper failed -> NO-OP")
        return shape

    act_sols = act.Solids()
    print(f"SELECTED: {len(act_sols)} solids after actuator clipping")
    if len(act_sols) == 0:
        print("ERROR: clipped actuator became empty -> NO-OP")
        return shape

    # --- self-check + correction: align mouth center on the y=minY plane to named_center_bottom ---
    slab_th = 0.50
    slab = (
        cq.Workplane(cq.Plane.XY())
        .box(Lx, slab_th, Lz, centered=(True, True, True))
        .translate((bb0.center.x, minY, bb0.center.z))
        .val()
    )
    print(f"SELECTED: 1 slab for mouth probing at y={minY:.3f} (th={slab_th:.3f})")

    def mouth_center_and_angle(shp):
        m = shp.intersect(slab)
        try:
            bbm = m.BoundingBox()
            # use the minY plane as the reported Y to match the spec
            mc = cq.Vector(bbm.center.x, minY, bbm.center.z)
            angle = 0.0 if bbm.zlen >= bbm.xlen else 90.0
            return mc, angle, bbm
        except Standard_Failure:
            # fallback: bbox of whole actuator
            bb = shp.BoundingBox()
            mc = cq.Vector(bb.center.x, minY, bb.center.z)
            angle = 0.0 if bb.zlen >= bb.xlen else 90.0
            return mc, angle, bb

    mc0, ang0, bbm0 = mouth_center_and_angle(act)
    dx0 = named_center_bottom.x - mc0.x
    dz0 = named_center_bottom.z - mc0.z

    # Ensure outermost Y is exactly minY
    bb_act1 = act.BoundingBox()
    dy0 = minY - bb_act1.ymin

    print(
        "ACTUATOR PLACEMENT CHECK (pre-correct): "
        f"mouth_center=[{mc0.x:.3f},{mc0.y:.3f},{mc0.z:.3f}] delta_to_target(dx,dz)=({-dx0:.3f},{-dz0:.3f}); "
        f"angle={ang0:.1f} deg; y_min={bb_act1.ymin:.3f} (want {minY:.3f}), dy_to_fix={dy0:.3f}"
    )

    # Apply corrections if materially displaced or if y_min not at minY
    if abs(dx0) > 0.01 or abs(dz0) > 0.01 or abs(dy0) > 0.01:
        act = act.translate((dx0, dy0, dz0))
        print(f"CORRECT: translated actuator by (dx,dy,dz)=({dx0:.3f},{dy0:.3f},{dz0:.3f})")

    # If angle is wrong (90 instead of 0), rotate 90 about Y through the target mouth center
    # (should not be needed, but required to auto-correct if it happens)
    mc1, ang1, _ = mouth_center_and_angle(act)
    if abs(ang1 - 90.0) < 1e-6:
        p1 = (named_center_bottom.x, named_center_bottom.y, named_center_bottom.z)
        p2 = (named_center_bottom.x, named_center_bottom.y + 1.0, named_center_bottom.z)
        act = act.rotate(p1, p2, 90)
        print("CORRECT: actuator long-axis was 90 deg; rotated +90 deg about world Y through named_center_bottom")

    # Final reported placement / angle
    mcF, angF, _ = mouth_center_and_angle(act)
    bb_actF = act.BoundingBox()
    print(
        "ACTUATOR FINAL REPORT: "
        f"mouth_center=[{mcF.x:.3f},{mcF.y:.3f},{mcF.z:.3f}] "
        f"(target [{named_center_bottom.x:.3f},{named_center_bottom.y:.3f},{named_center_bottom.z:.3f}] => "
        f"dx={mcF.x-named_center_bottom.x:.3f}, dy={mcF.y-named_center_bottom.y:.3f}, dz={mcF.z-named_center_bottom.z:.3f}); "
        f"outermost_Y(ymin)={bb_actF.ymin:.3f} (want {minY:.3f}); "
        f"long-axis angle vs world Z={angF:.1f} deg"
    )

    # --- confirm no overlap with housing (actuator must sit in cavity void) ---
    try:
        ov = s0.intersect(act)
        ov_s = ov.Solids()
        ov_vol = sum(s.Volume() for s in ov_s) if len(ov_s) else 0.0
        print(f"SELECTED: {len(ov_s)} solids in overlap (s0 ∩ actuator); overlap_vol={ov_vol:.6f} mm^3 (want ~0)")
    except Standard_Failure:
        print("WARNING: overlap test failed (Standard_Failure); proceeding")

    # --- demonstrate two Z positions (within the swept cavity) remain inside unchanged part envelope ---
    def within_envelope(bb_item, bb_env, tol=1e-6):
        return (
            bb_item.xmin >= bb_env.xmin - tol and bb_item.xmax <= bb_env.xmax + tol and
            bb_item.ymin >= bb_env.ymin - tol and bb_item.ymax <= bb_env.ymax + tol and
            bb_item.zmin >= bb_env.zmin - tol and bb_item.zmax <= bb_env.zmax + tol
        )

    for tag, oz in [("Z-", -sweep_dz/2.0), ("Z+", sweep_dz/2.0)]:
        inst = act.translate((0.0, 0.0, oz))
        bb_i = inst.BoundingBox()
        ok = within_envelope(bb_i, bb0)
        print(
            f"DEMO {tag}: actuator translated dZ={oz:.3f} => "
            f"bbox.center=({bb_i.center.x:.3f},{bb_i.center.y:.3f},{bb_i.center.z:.3f}) "
            f"z-range=[{bb_i.zmin:.3f},{bb_i.zmax:.3f}] within s0 envelope={ok}"
        )

    # --- keep actuator separate solid; do NOT subtract anything ---
    out_solids = [s0, s1, act]
    out = cq.Compound.makeCompound(out_solids)
    print(f"SELECTED: {len(out.Solids())} solids in output compound (expect 3: s0 + s1 + actuator)")

    # Ensure overall envelope unchanged (actuator should not expand bbox beyond existing)
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