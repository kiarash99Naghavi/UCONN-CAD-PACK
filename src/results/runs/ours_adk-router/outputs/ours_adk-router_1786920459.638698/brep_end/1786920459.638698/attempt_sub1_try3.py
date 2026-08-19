def my_cad_function(args):
    import cadquery as cq
    from OCP.Standard import Standard_Failure

    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input
    base = shape.val() if hasattr(shape, "val") else shape

    # --- pull solids and identify s0 housing, s1 source switch body, s2 actuator candidate ---
    sols = base.Solids()
    print(f"SELECTED: {len(sols)} solids from STEP")
    if len(sols) < 3:
        print("WARNING: expected 3 solids (s0 housing + s1 source + s2 actuator). Will proceed anyway.")
    if len(sols) == 0:
        print("SELECTED: 0 solids -> NO-OP")
        return shape

    # Heuristic identification (robust to reordering):
    # - housing: largest volume
    # - source switch body: higher ymin (top-mounted) among remaining
    # - actuator: ymin near housing ymin among remaining
    vols = [(i, s.Volume()) for i, s in enumerate(sols)]
    vols_sorted = sorted(vols, key=lambda t: t[1], reverse=True)
    idx_housing = vols_sorted[0][0]

    s0 = sols[idx_housing]
    bb0 = s0.BoundingBox()
    minY = bb0.ymin
    print(
        "s0(housing): "
        f"idx={idx_housing} vol={s0.Volume():.3f} "
        f"bbox=([{bb0.xmin:.3f},{bb0.ymin:.3f},{bb0.zmin:.3f}]..[{bb0.xmax:.3f},{bb0.ymax:.3f},{bb0.zmax:.3f}])"
    )

    # Remaining indices
    rem = [i for i in range(len(sols)) if i != idx_housing]
    if len(rem) == 0:
        print("SELECTED: 0 remaining solids besides housing -> NO-OP")
        return shape

    # pick source as the one with larger ymin; actuator as the one closest to minY
    rem_bbs = []
    for i in rem:
        bb = sols[i].BoundingBox()
        rem_bbs.append((i, bb))
        print(
            f"rem solid: idx={i} vol={sols[i].Volume():.3f} "
            f"bbox.y=[{bb.ymin:.3f},{bb.ymax:.3f}] size=({bb.xlen:.3f},{bb.ylen:.3f},{bb.zlen:.3f})"
        )

    if len(rem_bbs) == 1:
        idx_source = rem_bbs[0][0]
        idx_act_old = rem_bbs[0][0]
        print("WARNING: only one non-housing solid; treating it as both source and actuator (best-effort).")
    else:
        idx_source = max(rem_bbs, key=lambda t: t[1].ymin)[0]
        idx_act_old = min(rem_bbs, key=lambda t: abs(t[1].ymin - minY))[0]

    s1 = sols[idx_source]
    s2_old = sols[idx_act_old]
    bb1 = s1.BoundingBox()
    bb2 = s2_old.BoundingBox()

    print(
        "SELECTED: 1 solid for source switch body s1 "
        f"idx={idx_source} bbox.y=[{bb1.ymin:.3f},{bb1.ymax:.3f}]"
    )
    print(
        "SELECTED: 1 solid for existing actuator candidate s2 "
        f"idx={idx_act_old} bbox.y=[{bb2.ymin:.3f},{bb2.ymax:.3f}]"
    )

    # --- named anchors / requirements ---
    named_center_top = cq.Vector(27.9, 22.101, 51.27)
    named_center_bottom = cq.Vector(27.9, minY, 51.27)
    sweep_dz = 2.0

    print("NAMED NUMBERS:")
    print(f"  named_center_top    = [{named_center_top.x:.3f},{named_center_top.y:.3f},{named_center_top.z:.3f}]")
    print(f"  named_center_bottom = [{named_center_bottom.x:.3f},{named_center_bottom.y:.3f},{named_center_bottom.z:.3f}]")
    print(f"  housing minY (outer surface plane) = {minY:.3f} (want 2.160)")
    print("  sliding direction parallel to world Z (angle want 0 deg)")

    # --- rebuild actuator as rigid transformed copy of s1 with correct +Y thickness ---
    # Bottom-facing transform: rotate 180 about world X through named_center_top, then translate to bottom
    axis_p1 = (named_center_top.x, named_center_top.y, named_center_top.z)
    axis_p2 = (named_center_top.x + 1.0, named_center_top.y, named_center_top.z)

    try:
        act = s1.rotate(axis_p1, axis_p2, 180)
    except Exception as e:
        print(f"ERROR: rotate failed: {e} -> NO-OP")
        return shape

    dv = named_center_bottom.sub(named_center_top)
    act = act.translate((dv.x, dv.y, dv.z))

    bb_act_pre = act.BoundingBox()
    print(
        "Actuator copy (after rotate+translate, pre-trim): "
        f"center=({bb_act_pre.center.x:.3f},{bb_act_pre.center.y:.3f},{bb_act_pre.center.z:.3f}) "
        f"y-range=[{bb_act_pre.ymin:.3f},{bb_act_pre.ymax:.3f}] ylen={bb_act_pre.ylen:.3f}"
    )

    # Trim anything below y=minY using a cutter box (cut is safer than intersect against accidental tangency)
    Lx = max(bb0.xlen, 200.0)
    Ly = 400.0
    Lz = max(bb0.zlen, 200.0)

    below = (
        cq.Workplane(cq.Plane.XY())
        .box(Lx, Ly, Lz, centered=(True, False, True))  # y: [0, Ly]
        .translate((bb0.center.x, minY - Ly, bb0.center.z))  # y: [minY-Ly, minY]
        .val()
    )
    print(f"SELECTED: 1 cutter halfspace for trimming y<{minY:.3f}")

    try:
        act = act.cut(below)
    except Standard_Failure as e:
        print(f"ERROR: actuator cut(below) failed: {e} -> NO-OP")
        return shape

    act_sols = act.Solids()
    print(f"SELECTED: {len(act_sols)} solids after y-trim")
    if len(act_sols) == 0:
        print("ERROR: trimmed actuator has 0 solids -> NO-OP")
        return shape

    # If multiple solids, keep the largest
    if len(act_sols) > 1:
        act = max(act_sols, key=lambda s: s.Volume())
        print("SELECTED: 1 largest solid from trimmed actuator")

    # Ensure outer exposed surface is exactly at y=minY
    bb_act = act.BoundingBox()
    dy_fix = minY - bb_act.ymin
    if abs(dy_fix) > 1e-4:
        act = act.translate((0.0, dy_fix, 0.0))
        print(f"CORRECT: translated actuator in Y by dy={dy_fix:.6f} to set ymin=minY")

    # Mouth probe slab around y=minY to estimate opening center and long-axis orientation
    slab_th = 0.50
    slab = (
        cq.Workplane(cq.Plane.XY())
        .box(Lx, slab_th, Lz, centered=(True, True, True))
        .translate((bb0.center.x, minY, bb0.center.z))
        .val()
    )
    print(f"SELECTED: 1 slab for mouth probing at y={minY:.3f} (th={slab_th:.3f})")

    def mouth_center_and_angle(shp):
        try:
            m = shp.intersect(slab)
            bbm = m.BoundingBox()
            mc = cq.Vector(bbm.center.x, minY, bbm.center.z)
            # angle: 0 if long axis is Z, 90 if long axis is X (simple bbox heuristic)
            angle = 0.0 if bbm.zlen >= bbm.xlen else 90.0
            return mc, angle, bbm
        except Exception:
            bb = shp.BoundingBox()
            mc = cq.Vector(bb.center.x, minY, bb.center.z)
            angle = 0.0 if bb.zlen >= bb.xlen else 90.0
            return mc, angle, bb

    mc0, ang0, bbm0 = mouth_center_and_angle(act)
    # Align in XZ to named center if materially displaced
    dx = named_center_bottom.x - mc0.x
    dz = named_center_bottom.z - mc0.z
    if abs(dx) > 0.01 or abs(dz) > 0.01:
        act = act.translate((dx, 0.0, dz))
        print(f"CORRECT: translated actuator in XZ by (dx,dz)=({dx:.3f},{dz:.3f})")

    # If angle is wrong (90), rotate about world Y through the named center
    mc1, ang1, _ = mouth_center_and_angle(act)
    if abs(ang1 - 90.0) < 1e-6:
        p1 = (named_center_bottom.x, named_center_bottom.y, named_center_bottom.z)
        p2 = (named_center_bottom.x, named_center_bottom.y + 1.0, named_center_bottom.z)
        act = act.rotate(p1, p2, 90)
        print("CORRECT: actuator long-axis was 90 deg; rotated +90 deg about world Y")

    # Final actuator checks
    mcF, angF, _ = mouth_center_and_angle(act)
    bb_actF = act.BoundingBox()
    print(
        "ACTUATOR FINAL REPORT: "
        f"bbox.center=({bb_actF.center.x:.3f},{bb_actF.center.y:.3f},{bb_actF.center.z:.3f}) "
        f"mouth_center=[{mcF.x:.3f},{mcF.y:.3f},{mcF.z:.3f}] "
        f"outermost_Y(ymin)={bb_actF.ymin:.3f} (want {minY:.3f}) "
        f"ylen={bb_actF.ylen:.3f} (MUST be > 0; thickness into +Y) "
        f"long-axis angle vs world Z={angF:.1f} deg"
    )

    # --- confirm no overlap with housing; if tiny overlap exists, trim actuator by housing (do NOT modify housing) ---
    def overlap_volume(a, b):
        try:
            ov = a.intersect(b)
            ov_s = ov.Solids()
            ov_vol = sum(s.Volume() for s in ov_s) if ov_s else 0.0
            return ov_vol, len(ov_s)
        except Exception:
            return None, 0

    ov_vol, ov_n = overlap_volume(s0, act)
    if ov_vol is None:
        print("WARNING: overlap test failed; proceeding")
    else:
        print(f"SELECTED: {ov_n} solids in overlap (s0 ∩ actuator); overlap_vol={ov_vol:.6f} mm^3 (want ~0)")
        if ov_vol > 1e-3:
            print("CORRECT: overlap is material; trimming actuator by cutting away housing volume (housing unchanged)")
            try:
                act = act.cut(s0)
            except Exception as e:
                print(f"WARNING: trimming actuator by housing failed: {e}")
            ov2_vol, ov2_n = overlap_volume(s0, act)
            if ov2_vol is not None:
                print(f"RECHECK overlap: solids={ov2_n} overlap_vol={ov2_vol:.6f} mm^3")

    # --- demonstrate two Z positions remain inside unchanged part envelope ---
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
            f"center=({bb_i.center.x:.3f},{bb_i.center.y:.3f},{bb_i.center.z:.3f}) "
            f"y=[{bb_i.ymin:.3f},{bb_i.ymax:.3f}] z=[{bb_i.zmin:.3f},{bb_i.zmax:.3f}] within s0 envelope={ok}"
        )

    # --- rebuild compound with housing + source + corrected actuator (replace old actuator; do NOT add another) ---
    out_solids = []
    for i, s in enumerate(sols):
        if i == idx_act_old:
            continue
        out_solids.append(s)
    out_solids.append(act)

    out = cq.Compound.makeCompound(out_solids)
    print(f"SELECTED: {len(out.Solids())} solids in output compound (expect {len(sols)}; actuator replaced)")

    # --- signed volume delta (should be >= 0 for add-body step) ---
    try:
        delta_v = out.Volume() - base.Volume()
        print(f"DELTA volume (out - base) = {delta_v:.6f} mm^3 (want >= 0 for add-body)")
    except Exception as e:
        print(f"WARNING: could not compute DELTA volume: {e}")

    # Ensure overall bbox envelope unchanged (actuator should not expand beyond existing part envelope)
    pre_all_bb = base.BoundingBox()
    post_all_bb = out.BoundingBox()
    print(
        "BBOX ALL pre vs post: "
        f"pre_min=({pre_all_bb.xmin:.3f},{pre_all_bb.ymin:.3f},{pre_all_bb.zmin:.3f}) pre_max=({pre_all_bb.xmax:.3f},{pre_all_bb.ymax:.3f},{pre_all_bb.zmax:.3f}) | "
        f"post_min=({post_all_bb.xmin:.3f},{post_all_bb.ymin:.3f},{post_all_bb.zmin:.3f}) post_max=({post_all_bb.xmax:.3f},{post_all_bb.ymax:.3f},{post_all_bb.zmax:.3f})"
    )

    return out