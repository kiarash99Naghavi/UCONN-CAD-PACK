def my_cad_function(args):
    import cadquery as cq

    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input
    base = shape.val() if hasattr(shape, "val") else shape

    sols = base.Solids()
    print(f"INFO: imported solids={len(sols)}")
    if len(sols) < 1:
        print("SELECTED: 0 solids (ERROR)")
        return shape

    s0 = sols[0]
    print("SELECTED: 1 solid for editing (s0 assumed = solids[0])")
    bb0 = s0.BoundingBox()
    v0 = s0.Volume()
    print(f"INFO: s0 bbox min=({bb0.xmin:.3f},{bb0.ymin:.3f},{bb0.zmin:.3f}) max=({bb0.xmax:.3f},{bb0.ymax:.3f},{bb0.zmax:.3f})")
    try:
        print(f"INFO: s0 valid={s0.isValid()}  vol0={v0:.3f} mm^3")
    except Exception as e:
        print(f"WARN: s0.isValid() failed: {e}")
        print(f"INFO: vol0={v0:.3f} mm^3")

    # --- Named numbers / anchors (explicit, absolute) ---
    x0, y0 = -88.9, 100.0
    z_base = 266.7
    z_top = 296.7
    z_interior = 257.175
    axis_dir = cq.Vector(0, 0, 1)

    boss_od = 38.1
    boss_r = boss_od / 2.0
    boss_h = z_top - z_base  # 30.0

    bore_d = 25.4
    bore_r = bore_d / 2.0
    bore_h = z_top - z_interior  # 39.525

    print("NAMED TARGETS:")
    print(f"  axis through [{x0:.3f},{y0:.3f},*]  axis_dir=[{axis_dir.x:.1f},{axis_dir.y:.1f},{axis_dir.z:.1f}]")
    print(f"  boss OD={boss_od:.3f} (r={boss_r:.3f}) from z={z_base:.3f} to z={z_top:.3f} (h={boss_h:.3f})")
    print(f"  bore D={bore_d:.3f} (r={bore_r:.3f}) from z={z_top:.3f} down to z={z_interior:.3f} (h={bore_h:.3f})")

    # --- 1) BOSS: solid cylinder, fused only to s0 ---
    # Use a small internal overlap so the fuse is unambiguous, while the *added* material still starts at z=266.7.
    overlap = 0.2
    boss_tool_base = cq.Vector(x0, y0, z_base - overlap)
    boss_tool_h = boss_h + overlap
    boss_tool = cq.Solid.makeCylinder(boss_r, boss_tool_h, boss_tool_base, axis_dir)
    bbb = boss_tool.BoundingBox()
    print(f"INFO: boss tool bbox z=[{bbb.zmin:.3f}..{bbb.zmax:.3f}] (includes {overlap:.3f}mm internal overlap)")

    s0_after_boss = s0.fuse(boss_tool)
    v1 = s0_after_boss.Volume()
    print(f"VOLUME: after boss union v1={v1:.3f} mm^3  (delta={v1 - v0:.3f})")

    try:
        nsol1 = len(s0_after_boss.Solids())
    except Exception:
        nsol1 = -1
    print(f"INFO: after boss union solids_in_result={nsol1}")

    # Placement self-check: isolate added material from union
    try:
        added = s0_after_boss.cut(s0)
        added_bb = added.BoundingBox()
        added_center = added.Center()
        print(f"CHECK: added-by-boss center=({added_center.x:.3f},{added_center.y:.3f},{added_center.z:.3f})")
        print(f"CHECK: added-by-boss bbox x=[{added_bb.xmin:.3f}..{added_bb.xmax:.3f}] y=[{added_bb.ymin:.3f}..{added_bb.ymax:.3f}] z=[{added_bb.zmin:.3f}..{added_bb.zmax:.3f}]")
        print(f"CHECK: added-by-boss zmin vs target base z={z_base:.3f}: delta={added_bb.zmin - z_base:+.6f}")
        print(f"CHECK: added-by-boss zmax vs target top  z={z_top:.3f}: delta={added_bb.zmax - z_top:+.6f}")
    except Exception as e:
        print(f"WARN: could not compute added-by-boss (union diagnostic): {e}")

    # --- 2) BORE: one continuous coaxial cut from boss end down to interior z ---
    bore_tool_start = cq.Vector(x0, y0, z_top)
    bore_tool = cq.Solid.makeCylinder(bore_r, bore_h, bore_tool_start, cq.Vector(0, 0, -1))
    bbh = bore_tool.BoundingBox()
    print(f"INFO: bore tool bbox z=[{bbh.zmin:.3f}..{bbh.zmax:.3f}] (should span {z_interior:.3f}..{z_top:.3f})")

    s0_final = s0_after_boss.cut(bore_tool)
    v2 = s0_final.Volume()
    print(f"VOLUME: after boring v2={v2:.3f} mm^3  (delta from v1={v2 - v1:.3f})")
    print(f"VOLUME: final net change vs original (v2-v0)={v2 - v0:.3f} mm^3")

    # Removal diagnostic
    try:
        removed = s0_after_boss.cut(s0_final)
        rem_bb = removed.BoundingBox()
        rem_center = removed.Center()
        print(f"CHECK: removed-by-bore center=({rem_center.x:.3f},{rem_center.y:.3f},{rem_center.z:.3f})")
        print(f"CHECK: removed-by-bore bbox x=[{rem_bb.xmin:.3f}..{rem_bb.xmax:.3f}] y=[{rem_bb.ymin:.3f}..{rem_bb.ymax:.3f}] z=[{rem_bb.zmin:.3f}..{rem_bb.zmax:.3f}]")
    except Exception as e:
        print(f"WARN: could not compute removed-by-bore diagnostic: {e}")

    # Validity check
    try:
        print(f"VERIFY: edited s0 valid={s0_final.isValid()}")
        print(f"VERIFY: edited s0 solids={len(s0_final.Solids())}  shapeType={s0_final.ShapeType()}")
    except Exception as e:
        print(f"WARN: validity/solids check failed: {e}")

    # Required explicit feature report (achieved targets)
    print("ACHIEVED FEATURE PARAMETERS (as instructed):")
    print(f"  center_xy=({x0:.3f},{y0:.3f})")
    print(f"  axis=[0,0,1]")
    print(f"  boss_outside_diameter={boss_od:.3f} mm")
    print(f"  bore_diameter={bore_d:.3f} mm")
    print(f"  exterior_neck_base_z={z_base:.3f}")
    print(f"  neck_end_z={z_top:.3f}")
    print(f"  bore_interior_end_z={z_interior:.3f}")

    # Requirement checks: union adds, bore removes, net positive
    print("REQUIREMENT CHECKS:")
    print(f"  union_added_material? {v1 > v0} (v1-v0={v1 - v0:.3f})")
    print(f"  bore_removed_material? {v2 < v1} (v2-v1={v2 - v1:.3f})")
    print(f"  final_net_positive_added? {v2 > v0} (v2-v0={v2 - v0:.3f})")

    # Reassemble untouched bodies with edited s0
    out_sols = list(sols)
    out_sols[0] = s0_final
    out = cq.Compound.makeCompound(out_sols)
    return out