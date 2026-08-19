def my_cad_function(args):
    import cadquery as cq
    from OCP.Standard import Standard_Failure

    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input
    base = shape.val() if hasattr(shape, "val") else shape

    sols = base.Solids()
    print(f"SELECTED: {len(sols)} solids from STEP")
    if len(sols) < 3:
        print("WARNING: expected 3 solids in input (s0 housing, s1 source body, s2 flawed actuator)")
    if len(sols) < 2:
        print("SELECTED: 0/insufficient solids -> NO-OP")
        return shape

    # Keep s0 and s1; REBUILD actuator (replace existing s2; do NOT add another)
    s0 = sols[0]
    s1 = sols[1]
    old_act = sols[2] if len(sols) > 2 else None

    bb0 = s0.BoundingBox()
    bb1 = s1.BoundingBox()
    print(
        "s0 housing: "
        f"vol={s0.Volume():.3f} "
        f"bbox=([{bb0.xmin:.3f},{bb0.ymin:.3f},{bb0.zmin:.3f}]..[{bb0.xmax:.3f},{bb0.ymax:.3f},{bb0.zmax:.3f}])"
    )
    print(
        "s1 source: "
        f"vol={s1.Volume():.3f} "
        f"bbox=([{bb1.xmin:.3f},{bb1.ymin:.3f},{bb1.zmin:.3f}]..[{bb1.xmax:.3f},{bb1.ymax:.3f},{bb1.zmax:.3f}]) "
        f"size=({bb1.xlen:.3f},{bb1.ylen:.3f},{bb1.zlen:.3f})"
    )
    if old_act is not None:
        bbo = old_act.BoundingBox()
        print(
            "old actuator (to be replaced): "
            f"vol={old_act.Volume():.3f} "
            f"bbox=([{bbo.xmin:.3f},{bbo.ymin:.3f},{bbo.zmin:.3f}]..[{bbo.xmax:.3f},{bbo.ymax:.3f},{bbo.zmax:.3f}]) "
            f"size=({bbo.xlen:.3f},{bbo.ylen:.3f},{bbo.zlen:.3f})"
        )

    # --- Named anchors / requirements ---
    named_center_top = cq.Vector(27.9, 22.101, 51.27)
    named_mouth_center_bottom = cq.Vector(27.9, 2.16, 51.27)  # mouth center (projected on y=minY)
    minY = bb0.ymin  # should be 2.16
    sweep_dz = 2.0

    print("NAMED NUMBERS:")
    print(f"  named_center_top             = [{named_center_top.x:.3f}, {named_center_top.y:.3f}, {named_center_top.z:.3f}]")
    print(f"  named_mouth_center_bottom    = [{named_mouth_center_bottom.x:.3f}, {named_mouth_center_bottom.y:.3f}, {named_mouth_center_bottom.z:.3f}]")
    print(f"  housing minY (s0)            = {minY:.3f} (want 2.160)")
    print(f"  sliding direction            = world +Z (angle want 0 deg)")
    print(f"  demo translation along Z     = ±{sweep_dz/2.0:.3f} mm")

    # Helpers
    def largest_solid(shp, label="shape"):
        try:
            ss = shp.Solids()
        except Exception:
            ss = []
        print(f"SELECTED: {len(ss)} solids from {label}")
        if not ss:
            return None
        ss_sorted = sorted(ss, key=lambda s: s.Volume(), reverse=True)
        if len(ss_sorted) > 1:
            vols = [s.Volume() for s in ss_sorted]
            print(f"INFO: {label} had multiple solids; keeping largest. vols={[round(v,6) for v in vols]}")
        return ss_sorted[0]

    Lx = max(bb0.xlen, 200.0)
    Lz = max(bb0.zlen, 200.0)
    slab_th = 0.50
    slab = (
        cq.Workplane(cq.Plane.XY())
        .box(Lx, slab_th, Lz, centered=(True, True, True))
        .translate((bb0.center.x, minY, bb0.center.z))
        .val()
    )
    print(f"SELECTED: 1 slab for mouth probing at y={minY:.3f} (th={slab_th:.3f})")

    def mouth_center_and_angle(shp):
        # intersect with a thin slab around y=minY and use that slice bbox
        try:
            m = shp.intersect(slab)
            bbm = m.BoundingBox()
            mc = cq.Vector(bbm.center.x, minY, bbm.center.z)
            angle = 0.0 if bbm.zlen >= bbm.xlen else 90.0
            return mc, angle, bbm
        except Standard_Failure:
            bb = shp.BoundingBox()
            mc = cq.Vector(bb.center.x, minY, bb.center.z)
            angle = 0.0 if bb.zlen >= bb.xlen else 90.0
            return mc, angle, bb

    # --- Recreate actuator as rigid transformed copy of s1 (NO clipping/intersection that could collapse thickness) ---
    # same bottom-facing transform: rotate 180 about world X through named_center_top, then translate down to minY plane
    axis_p1 = (named_center_top.x, named_center_top.y, named_center_top.z)
    axis_p2 = (named_center_top.x + 1.0, named_center_top.y, named_center_top.z)
    act = s1.rotate(axis_p1, axis_p2, 180)

    dv = named_mouth_center_bottom.sub(named_center_top)
    act = act.translate((dv.x, dv.y, dv.z))
    act = largest_solid(act, label="actuator after rotate+translate")
    if act is None:
        print("ERROR: actuator transform produced no solid -> NO-OP")
        return shape

    bb_a0 = act.BoundingBox()
    print(
        "Actuator copy (raw): "
        f"bbox.center=({bb_a0.center.x:.3f},{bb_a0.center.y:.3f},{bb_a0.center.z:.3f}) "
        f"size=({bb_a0.xlen:.3f},{bb_a0.ylen:.3f},{bb_a0.zlen:.3f}) y-range=[{bb_a0.ymin:.3f},{bb_a0.ymax:.3f}]"
    )

    # --- Enforce outermost exposed surface at y=minY WITHOUT boolean clipping (avoid degenerate thin results) ---
    bb_a1 = act.BoundingBox()
    dy = minY - bb_a1.ymin
    if abs(dy) > 1e-6:
        act = act.translate((0.0, dy, 0.0))
        print(f"CORRECT: translated actuator in Y by dy={dy:.6f} to set ymin=minY")

    # --- Align mouth center in XZ to named_mouth_center_bottom ---
    mc0, ang0, bbm0 = mouth_center_and_angle(act)
    dx = named_mouth_center_bottom.x - mc0.x
    dz = named_mouth_center_bottom.z - mc0.z
    print(
        "ACTUATOR PLACEMENT CHECK (pre-correct XZ/angle): "
        f"mouth_center=[{mc0.x:.3f},{mc0.y:.3f},{mc0.z:.3f}] "
        f"delta_to_target(dx,dz)=({dx:.3f},{dz:.3f}); angle={ang0:.1f} deg"
    )

    if abs(dx) > 0.01 or abs(dz) > 0.01:
        act = act.translate((dx, 0.0, dz))
        print(f"CORRECT: translated actuator in XZ by (dx,dz)=({dx:.6f},{dz:.6f})")

    # --- Ensure long-axis is parallel to world Z (angle 0, not 90) ---
    mc1, ang1, _ = mouth_center_and_angle(act)
    if abs(ang1 - 90.0) < 1e-6:
        p1 = (named_mouth_center_bottom.x, named_mouth_center_bottom.y, named_mouth_center_bottom.z)
        p2 = (named_mouth_center_bottom.x, named_mouth_center_bottom.y + 1.0, named_mouth_center_bottom.z)
        act = act.rotate(p1, p2, 90)
        print("CORRECT: actuator long-axis was 90 deg; rotated +90 deg about world Y through named_mouth_center_bottom")

    # Re-ensure ymin is exactly minY after any rotate
    bb_a2 = act.BoundingBox()
    dy2 = minY - bb_a2.ymin
    if abs(dy2) > 1e-6:
        act = act.translate((0.0, dy2, 0.0))
        print(f"CORRECT: re-translated actuator in Y by dy={dy2:.6f} to restore ymin=minY")

    # Final: ensure it's a SOLID and has positive thickness in +Y
    act = largest_solid(act, label="actuator final (before overlap trim)")
    if act is None:
        print("ERROR: actuator final produced no solid -> NO-OP")
        return shape

    bbF = act.BoundingBox()
    mcF, angF, _ = mouth_center_and_angle(act)
    print(
        "ACTUATOR FINAL REPORT (pre-overlap-trim): "
        f"bbox.center=({bbF.center.x:.3f},{bbF.center.y:.3f},{bbF.center.z:.3f}) "
        f"mouth_center=[{mcF.x:.3f},{mcF.y:.3f},{mcF.z:.3f}] "
        f"outermost_Y(ymin)={bbF.ymin:.3f} (want {minY:.3f}) "
        f"Y_thickness(ylen)={bbF.ylen:.3f} (MUST be >0) "
        f"long-axis angle vs world Z={angF:.1f} deg"
    )

    if bbF.ylen < 0.05:
        print("ERROR: actuator appears collapsed in Y (ylen<0.05mm). Will proceed but this is a hard failure.")

    # --- Confirm no overlap with housing; if overlap exists, trim actuator ONLY (do not subtract housing) ---
    ov_vol = 0.0
    try:
        ov = s0.intersect(act)
        ov_s = ov.Solids()
        ov_vol = sum(s.Volume() for s in ov_s) if ov_s else 0.0
        print(f"SELECTED: {len(ov_s)} solids in overlap (s0 ∩ actuator); overlap_vol={ov_vol:.6f} mm^3 (want ~0)")
    except Standard_Failure:
        print("WARNING: overlap test failed (Standard_Failure); proceeding")

    if ov_vol > 1e-4:
        try:
            act_trim = act.cut(s0)
            act_trim = largest_solid(act_trim, label="actuator after trimming overlap (act.cut(s0))")
            if act_trim is not None:
                act = act_trim
                bbT = act.BoundingBox()
                mcT, angT, _ = mouth_center_and_angle(act)
                print(
                    "ACTUATOR AFTER OVERLAP TRIM: "
                    f"bbox.center=({bbT.center.x:.3f},{bbT.center.y:.3f},{bbT.center.z:.3f}) "
                    f"mouth_center=[{mcT.x:.3f},{mcT.y:.3f},{mcT.z:.3f}] "
                    f"outermost_Y(ymin)={bbT.ymin:.3f} Y_thickness(ylen)={bbT.ylen:.3f} "
                    f"angle={angT:.1f}"
                )
        except Standard_Failure:
            print("WARNING: trimming actuator overlap via act.cut(s0) failed; leaving as-is")

    # --- Demonstrate two Z positions (within swept cavity) remain inside unchanged part envelope ---
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
            f"y-range=[{bb_i.ymin:.3f},{bb_i.ymax:.3f}] z-range=[{bb_i.zmin:.3f},{bb_i.zmax:.3f}] within s0 envelope={ok}"
        )

    # --- Output compound: s0 + s1 + rebuilt actuator (replace old s2) ---
    out = cq.Compound.makeCompound([s0, s1, act])
    print(f"SELECTED: {len(out.Solids())} solids in output compound (expect 3: s0 + s1 + actuator)")

    # Overall envelope should remain unchanged
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