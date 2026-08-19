def my_cad_function(args):
    import cadquery as cq
    from math import acos, degrees

    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input
    base = shape.val() if hasattr(shape, "val") else shape

    # --- Resolve and print target faces by indexed tag (global face list on imported shape) ---
    all_faces = base.Faces()
    f46 = all_faces[46]
    f22 = all_faces[22]
    c46 = f46.Center()
    c22 = f22.Center()
    n46 = f46.normalAt()
    n22 = f22.normalAt()
    print(f"RESOLVED: face #46 center={tuple(round(v,3) for v in c46.toTuple())} normal={tuple(round(v,3) for v in n46.toTuple())} area={round(f46.Area(),3)}")
    print(f"RESOLVED: face #22 center={tuple(round(v,3) for v in c22.toTuple())} normal={tuple(round(v,3) for v in n22.toTuple())} area={round(f22.Area(),3)}")

    # --- Identify s0 as the largest solid (per index s0 is largest) ---
    solids = base.Solids()
    vols = [s.Volume() for s in solids]
    s0_idx = max(range(len(solids)), key=lambda i: vols[i])
    s0 = solids[s0_idx]
    print(f"SELECTED: 1 solid for s0 edit   idx={s0_idx} vol={vols[s0_idx]:.3f} (largest of {len(solids)})")

    # --- Parameters from sub-goal (list every named number) ---
    y_top = 174.852
    y_bot = -171.45
    x_center = -88.9
    z_centers = [-225, -180, -135, -90, -45, 0, 45, 90, 135, 180, 225]
    slot_L = 25.0
    slot_W = 6.0
    slot_r = 3.0
    print(f"PARAMS: y_top={y_top} y_bot={y_bot} x_center={x_center} z_centers={z_centers}")
    print(f"PARAMS: slot overall L={slot_L} W={slot_W} end_r={slot_r} (capsule)")

    # --- Determine slot2D default major-axis orientation; rotate 90deg if needed ---
    # slot2D should normally be along local X when angle=0, but we verify.
    test_wire = cq.Workplane(cq.Plane.XY()).slot2D(slot_L, slot_W, angle=0).val()
    test_bb = test_wire.BoundingBox()
    slot_angle = 0
    if test_bb.xlen < test_bb.ylen:
        slot_angle = 90
    print(f"CHECK: slot2D(angle=0) bbox xlen={test_bb.xlen:.3f} ylen={test_bb.ylen:.3f} -> using slot_angle={slot_angle} deg to keep major axis || world X")

    # --- Helper: measure local thickness/depth along inward direction using a small probe prism ---
    def measure_inward_depth(solid, plane_origin, plane_normal, plane_xdir, expect_touch_y, touch_is_max):
        """Return depth from face plane into material along -plane_normal (since we will extrude negative).
        We intersect a small extruded probe with the solid, then select the component that touches the face plane."""
        pl = cq.Plane(origin=plane_origin, normal=plane_normal, xDir=plane_xdir)
        wp = cq.Workplane(pl)
        # 2x2 mm probe at the slot-center location (in plane coords)
        # NOTE: caller sets correct local Y mapping; we pass that in via plane coords points.
        # Here we sketch at (0,0) and will translate the probe by building plane origin at the center point.
        probe_len = 300.0
        probe = wp.rect(2.0, 2.0, centered=True).extrude(-probe_len).val()
        common = solid.intersect(probe)
        common_sols = common.Solids() if common else []
        print(f"SELECTED: {len(common_sols)} solids in probe∩s0 for thickness measurement at origin={tuple(round(v,3) for v in plane_origin)}")
        if len(common_sols) == 0:
            return 20.0
        # pick the component that touches the face plane (within tol)
        tol = 0.25
        touching = []
        for cs in common_sols:
            bb = cs.BoundingBox()
            if touch_is_max:
                if abs(bb.ymax - expect_touch_y) <= tol:
                    touching.append((cs, bb))
            else:
                if abs(bb.ymin - expect_touch_y) <= tol:
                    touching.append((cs, bb))
        print(f"SELECTED: {len(touching)} probe components touching face plane y={expect_touch_y} (tol={tol})")
        if len(touching) == 0:
            # fallback: choose closest by that extreme
            best = None
            best_d = 1e9
            for cs in common_sols:
                bb = cs.BoundingBox()
                y_ext = bb.ymax if touch_is_max else bb.ymin
                d = abs(y_ext - expect_touch_y)
                if d < best_d:
                    best_d = d
                    best = (cs, bb)
            touching = [best]
            print(f"FALLBACK: using closest-touch component with |dy|={best_d:.3f}")
        # choose the touching component with smallest depth (closest inner surface), conservative
        depths = []
        for cs, bb in touching:
            if touch_is_max:
                depth = bb.ymax - bb.ymin
                # but if ymax is the face plane, depth into material is (y_face - bb.ymin)
                depth = expect_touch_y - bb.ymin
            else:
                depth = bb.ymax - expect_touch_y
            depths.append((depth, bb))
        depth, bb = min(depths, key=lambda t: t[0])
        print(f"MEASURED: inward depth={depth:.3f} using bb(ymin={bb.ymin:.3f}, ymax={bb.ymax:.3f})")
        return max(0.1, depth)

    # --- Build planes and measure depths at one representative location per face ---
    # Use the middle z=0 for thickness measurement
    z0 = 0.0

    # Top face plane: y=y_top, outward normal +Y (per index), inward is -Y (extrude negative)
    # For plane coords mapping: with normal +Y and xDir +X, plane yDir becomes -Z, so localY = -worldZ.
    top_plane_normal = (0, 1, 0)
    top_plane_xdir = (1, 0, 0)
    top_local_y = -z0
    top_origin_at_center = (x_center, y_top, z0)
    depth_top = measure_inward_depth(
        s0,
        plane_origin=top_origin_at_center,
        plane_normal=top_plane_normal,
        plane_xdir=top_plane_xdir,
        expect_touch_y=y_top,
        touch_is_max=True,
    )

    # Bottom face plane: y=y_bot, outward normal -Y (per index), inward is +Y (extrude negative)
    # With normal -Y and xDir +X, plane yDir becomes +Z, so localY = worldZ.
    bot_plane_normal = (0, -1, 0)
    bot_plane_xdir = (1, 0, 0)
    bot_origin_at_center = (x_center, y_bot, z0)
    depth_bot = measure_inward_depth(
        s0,
        plane_origin=bot_origin_at_center,
        plane_normal=bot_plane_normal,
        plane_xdir=bot_plane_xdir,
        expect_touch_y=y_bot,
        touch_is_max=False,
    )

    cut_depth_top = depth_top + 1.0
    cut_depth_bot = depth_bot + 1.0
    print(f"CUT DEPTHS: top={cut_depth_top:.3f} (measured {depth_top:.3f}+1)  bottom={cut_depth_bot:.3f} (measured {depth_bot:.3f}+1)")

    # --- Build slot cutting tools (11 slots per face) ---
    # Construct absolute sketch planes at the given y levels (do not anchor by picking faces).
    pl_top = cq.Plane(origin=(0, y_top, 0), normal=top_plane_normal, xDir=top_plane_xdir)
    pl_bot = cq.Plane(origin=(0, y_bot, 0), normal=bot_plane_normal, xDir=bot_plane_xdir)
    print(f"PLANE TOP: origin={(0, y_top, 0)} normal={top_plane_normal} xDir={top_plane_xdir}")
    print(f"PLANE BOT: origin={(0, y_bot, 0)} normal={bot_plane_normal} xDir={bot_plane_xdir}")

    # Slot centers in each plane's 2D coordinates
    pts_top = [(x_center, -z) for z in z_centers]  # localY=-Z on top plane
    pts_bot = [(x_center, z) for z in z_centers]   # localY=+Z on bottom plane
    print(f"SLOT CENTERS (world): top/bot x={x_center} z={z_centers} major_axis=world X")

    wp_top = cq.Workplane(pl_top)
    tool_top = wp_top.pushPoints(pts_top).slot2D(slot_L, slot_W, angle=slot_angle).extrude(-cut_depth_top).val()
    print(f"SELECTED: {len(pts_top)} slots for TOP face tool (face #46)")

    wp_bot = cq.Workplane(pl_bot)
    tool_bot = wp_bot.pushPoints(pts_bot).slot2D(slot_L, slot_W, angle=slot_angle).extrude(-cut_depth_bot).val()
    print(f"SELECTED: {len(pts_bot)} slots for BOTTOM face tool (face #22)")

    tool = tool_top.fuse(tool_bot)

    # --- Cut only s0, leave other solids untouched ---
    s0_edited = s0.cut(tool)
    out = cq.Compound.makeCompound([s for i, s in enumerate(solids) if i != s0_idx] + [s0_edited])

    # --- Self-checks: bbox unchanged, and report removed material location ---
    bb_before = base.BoundingBox()
    bb_after = out.BoundingBox()
    print(
        "BBOX before:",
        (round(bb_before.xmin, 3), round(bb_before.ymin, 3), round(bb_before.zmin, 3)),
        "..",
        (round(bb_before.xmax, 3), round(bb_before.ymax, 3), round(bb_before.zmax, 3)),
    )
    print(
        "BBOX after :",
        (round(bb_after.xmin, 3), round(bb_after.ymin, 3), round(bb_after.zmin, 3)),
        "..",
        (round(bb_after.xmax, 3), round(bb_after.ymax, 3), round(bb_after.zmax, 3)),
    )

    removed = s0.cut(s0_edited)
    rem_sols = removed.Solids() if removed else []
    print(f"SELECTED: {len(rem_sols)} removed solids (expected 22-ish, one per slot if separated)")
    if removed:
        rbb = removed.BoundingBox()
        rc = removed.Center()
        print(
            f"REMOVED: center={tuple(round(v,3) for v in rc.toTuple())} bbox=({rbb.xmin:.3f},{rbb.ymin:.3f},{rbb.zmin:.3f})..({rbb.xmax:.3f},{rbb.ymax:.3f},{rbb.zmax:.3f})"
        )

    # --- Print achieved centers & major-axis orientation; verify each expected slot hit something nearby ---
    major_axis = cq.Vector(1, 0, 0)
    # angle between major axis and world X
    dot = max(-1.0, min(1.0, major_axis.normalized().dot(cq.Vector(1, 0, 0))))
    ang = degrees(acos(dot))
    print(f"ORIENTATION: major_axis_vector={tuple(major_axis.toTuple())} angle_to_world_X={ang:.3f} deg")

    expected_world = []
    for z in z_centers:
        expected_world.append((x_center, y_top, z))
    for z in z_centers:
        expected_world.append((x_center, y_bot, z))

    # Map removed solids by their centers (best-effort)
    rem_centers = []
    for i, rs in enumerate(rem_sols):
        cc = rs.Center()
        rem_centers.append((i, cc))
        print(f"REM_SOLID[{i}]: center={tuple(round(v,3) for v in cc.toTuple())}")

    def closest_removed(pt):
        if not rem_centers:
            return None, None, None
        p = cq.Vector(*pt)
        best = None
        for i, c in rem_centers:
            d = (c - p).Length
            if best is None or d < best[0]:
                best = (d, i, c)
        return best

    for pt in expected_world:
        d_i_c = closest_removed(pt)
        if d_i_c[0] is None:
            print(f"ACHIEVED: expected_center={tuple(pt)} -> NO removed solids found")
            continue
        d, i, c = d_i_c
        dx, dy, dz = (c - cq.Vector(*pt)).toTuple()
        print(
            f"ACHIEVED: expected_center={tuple(pt)} closest_removed_idx={i} actual_center={tuple(round(v,3) for v in c.toTuple())} "
            f"delta=({dx:.3f},{dy:.3f},{dz:.3f}) dist={d:.3f}"
        )

    return out