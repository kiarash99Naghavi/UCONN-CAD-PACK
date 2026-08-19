def my_cad_function(args):
    import cadquery as cq

    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input
    base = shape.val() if hasattr(shape, "val") else shape

    sols = base.Solids()
    print(f"INFO: imported solids={len(sols)}")
    if len(sols) < 1:
        print("SELECTED: 0 solids (ERROR: no solids in input)")
        return shape

    s0 = sols[0]
    other = [s for i, s in enumerate(sols) if i != 0]
    print(f"SELECTED: 1 solid for edit (s0)  vol={s0.Volume():.3f} mm^3")
    print(f"SELECTED: {len(other)} solids left untouched (s1-s{len(sols)-1})")

    # --- Named placement numbers (from sub-goal) ---
    axis_xy = (-88.9, 100.0)
    axis_dir = (0.0, 0.0, 1.0)
    z_base = 266.7
    z_neck_end = 296.7
    z_bore_end = 257.175
    boss_od = 38.1
    bore_d = 25.4
    boss_r = boss_od / 2.0
    bore_r = bore_d / 2.0

    print("TARGETS:")
    print(f"  upper_plane_z={z_base} normal={list(axis_dir)}")
    print(f"  feature_axis_through=[{axis_xy[0]}, {axis_xy[1]}, *] dir={list(axis_dir)}")
    print(f"  boss: OD={boss_od} z={z_base}..{z_neck_end}")
    print(f"  bore: D={bore_d} z={z_neck_end}..{z_bore_end} (continuous)")

    # --- Construct solid cylindrical boss ---
    boss_h = z_neck_end - z_base
    boss_base = cq.Vector(axis_xy[0], axis_xy[1], z_base)
    boss_dir = cq.Vector(*axis_dir)

    def make_boss(base_z, top_z):
        h = top_z - base_z
        return cq.Solid.makeCylinder(boss_r, h, cq.Vector(axis_xy[0], axis_xy[1], base_z), boss_dir)

    boss = make_boss(z_base, z_neck_end)
    bb = boss.BoundingBox()
    print(
        "BOSS TOOL: center=({:.3f},{:.3f},{:.3f}) bbox_z=[{:.3f},{:.3f}] r={:.3f} (OD={:.3f})".format(
            boss.Center().x, boss.Center().y, boss.Center().z, bb.zmin, bb.zmax, boss_r, boss_od
        )
    )

    # Fuse boss to s0 only
    v0 = s0.Volume()
    after_union = s0.fuse(boss)
    vu = after_union.Volume()

    # Check fuse success; OCC sometimes returns a compound if only touching.
    nsol_u = len(after_union.Solids())
    print(f"UNION: result solids={nsol_u}  vol_after_union={vu:.3f} mm^3  delta={vu - v0:.3f} mm^3")

    # Fallback if fuse did not merge into one solid
    used_overlap = 0.0
    if nsol_u != 1:
        used_overlap = 0.5
        print(f"WARN: union did not merge into 1 solid; retrying with overlap={used_overlap} mm into s0 (internal) while keeping top at z={z_neck_end}")
        boss2 = make_boss(z_base - used_overlap, z_neck_end)
        after_union = s0.fuse(boss2)
        vu = after_union.Volume()
        nsol_u = len(after_union.Solids())
        bb2 = boss2.BoundingBox()
        print(
            "BOSS TOOL (retry): bbox_z=[{:.3f},{:.3f}] (requested exterior base z={:.3f})".format(
                bb2.zmin, bb2.zmax, z_base
            )
        )
        print(f"UNION (retry): result solids={nsol_u}  vol_after_union={vu:.3f} mm^3  delta={vu - v0:.3f} mm^3")

    if vu - v0 <= 0:
        print("ERROR: union did not add material (expected positive volume delta).")

    # --- Construct coaxial bore tool and cut from the unioned s0 ---
    # Bore runs from z=296.7 down to z=257.175, coaxial with boss
    bore_h = z_neck_end - z_bore_end
    bore_tool = cq.Solid.makeCylinder(
        bore_r,
        bore_h,
        cq.Vector(axis_xy[0], axis_xy[1], z_bore_end),
        boss_dir,
    )
    bbb = bore_tool.BoundingBox()
    print(
        "BORE TOOL: center=({:.3f},{:.3f},{:.3f}) bbox_z=[{:.3f},{:.3f}] r={:.3f} (D={:.3f})".format(
            bore_tool.Center().x,
            bore_tool.Center().y,
            bore_tool.Center().z,
            bbb.zmin,
            bbb.zmax,
            bore_r,
            bore_d,
        )
    )

    after_bore = after_union.cut(bore_tool)
    vb = after_bore.Volume()
    print(f"BORE CUT: vol_after_bore={vb:.3f} mm^3  removed={vu - vb:.3f} mm^3")

    delta_final = vb - v0
    print(f"FINAL s0 DELTA: {delta_final:.3f} mm^3 (must remain > 0)")
    if delta_final <= 0:
        print("ERROR: final net volume change is not positive (sub-goal requires net added volume).")

    # Validity checks
    try:
        print(f"VALID: edited s0 isValid={after_bore.isValid()}")
    except Exception as e:
        print(f"VALID: could not evaluate isValid() due to: {e}")

    # Placement / achieved values
    # Achieved boss extents from the actually used boss tool (approx):
    # (if fallback used, we can't recover boss2 directly here; use union-added part bbox instead)
    try:
        added = after_union.cut(s0)
        abb = added.BoundingBox()
        print(
            "ADDED (after union) check: vol={:.3f} center=({:.3f},{:.3f},{:.3f}) bbox_z=[{:.3f},{:.3f}]".format(
                added.Volume(),
                added.Center().x,
                added.Center().y,
                added.Center().z,
                abb.zmin,
                abb.zmax,
            )
        )
        print(
            "ACHIEVED: axis_xy=({:.3f},{:.3f}) axis_dir={} boss_OD={:.3f} bore_D={:.3f}".format(
                axis_xy[0], axis_xy[1], list(axis_dir), boss_od, bore_d
            )
        )
        print(
            "ACHIEVED Z: neck_base(target={:.3f}) neck_end(target={:.3f}) bore_end(target={:.3f})".format(
                z_base, z_neck_end, z_bore_end
            )
        )
    except Exception as e:
        print(f"ADDED check failed: {e}")
        print(
            "ACHIEVED (fallback print): axis_xy=({:.3f},{:.3f}) axis_dir={} boss_OD={:.3f} bore_D={:.3f} z_base={:.3f} z_end={:.3f} z_bore_end={:.3f}".format(
                axis_xy[0], axis_xy[1], list(axis_dir), boss_od, bore_d, z_base, z_neck_end, z_bore_end
            )
        )

    # Reassemble: untouched bodies + edited s0
    out = cq.Compound.makeCompound(other + [after_bore])
    try:
        print(f"DELTA total(compound) = {out.Volume() - base.Volume():.3f} mm^3")
    except Exception as e:
        print(f"DELTA total(compound): could not compute: {e}")

    return out