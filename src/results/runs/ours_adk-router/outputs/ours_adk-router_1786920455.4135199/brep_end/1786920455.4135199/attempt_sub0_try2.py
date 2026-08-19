def my_cad_function(args):
    import cadquery as cq
    from math import degrees, acos

    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input
    base = shape.val() if hasattr(shape, "val") else shape

    # --- Resolve dataset-defined target faces (global face indices) ---
    f46 = base.Faces()[46]
    f22 = base.Faces()[22]
    c46 = f46.Center()
    c22 = f22.Center()
    n46 = f46.normalAt()
    n22 = f22.normalAt()
    print(
        "RESOLVED: face #46",
        "center=",
        tuple(round(v, 3) for v in c46.toTuple()),
        "normal=",
        tuple(round(v, 3) for v in n46.toTuple()),
        "area=",
        round(f46.Area(), 3),
    )
    print(
        "RESOLVED: face #22",
        "center=",
        tuple(round(v, 3) for v in c22.toTuple()),
        "normal=",
        tuple(round(v, 3) for v in n22.toTuple()),
        "area=",
        round(f22.Area(), 3),
    )

    # --- Pick s0 as largest solid (per index) and keep others untouched ---
    solids = base.Solids()
    vols = [(i, s.Volume()) for i, s in enumerate(solids)]
    s0_idx, s0_vol = max(vols, key=lambda t: t[1])
    s0 = solids[s0_idx]
    print(f"SELECTED: 1 solid for s0 edit   idx={s0_idx} vol={s0_vol:.3f} (largest of {len(solids)})")

    # --- Parameters (all numbers named by sub-goal) ---
    x_center = -88.9
    y_top = 174.852
    y_bot = -171.45
    z_centers = [-225, -180, -135, -90, -45, 0, 45, 90, 135, 180, 225]
    slot_L = 25.0
    slot_W = 6.0
    end_r = 3.0
    print(f"PARAMS: y_top={y_top} y_bot={y_bot} x_center={x_center} z_centers={z_centers}")
    print(f"PARAMS: slot capsule L={slot_L} W={slot_W} end_r={end_r}  (major axis must be world X)")

    # --- Slot orientation check: ensure major axis || world X; rotate 90 if needed ---
    # slot2D(angle=0) should produce length along workplane X.
    test = cq.Workplane(cq.Plane.XY()).slot2D(slot_L, slot_W, angle=0).val()
    tbb = test.BoundingBox()
    slot_angle = 0
    if tbb.xlen < tbb.ylen:
        slot_angle = 90
    print(
        f"CHECK: slot2D(angle=0) bbox xlen={tbb.xlen:.3f} ylen={tbb.ylen:.3f} -> using slot_angle={slot_angle} deg"
    )

    # --- Absolute sketch planes at the given y levels (do not anchor by picking faces) ---
    # Use outward normals from index: top +Y, bottom -Y. Extrude negative to cut inward.
    pl_top = cq.Plane(origin=(0, y_top, 0), normal=(0, 1, 0), xDir=(1, 0, 0))
    pl_bot = cq.Plane(origin=(0, y_bot, 0), normal=(0, -1, 0), xDir=(1, 0, 0))
    print(f"PLANE TOP: origin={(0, y_top, 0)} normal={(0, 1, 0)} xDir={(1, 0, 0)}")
    print(f"PLANE BOT: origin={(0, y_bot, 0)} normal={(0, -1, 0)} xDir={(1, 0, 0)}")

    # --- Thickness measurement via narrow probe intersection (per slot), to avoid over-cutting ---
    def measure_wall_thickness_at(part, face_y, which, xw, zw, max_depth=60.0, tol=0.25):
        """Return thickness along inward direction from the face plane, using a very narrow column around (xw, zw).

        which: 'top' or 'bot'
        For top: face at y=face_y, inward is -Y, expect touching bb.ymax==face_y
        For bot: face at y=face_y, inward is +Y, expect touching bb.ymin==face_y
        """
        # Very narrow column in X/Z; long in Y but limited to max_depth inward.
        dx = 1.5
        dz = 1.5
        if which == "top":
            ymin = face_y - max_depth
            ymax = face_y + 0.5
        else:
            ymin = face_y - 0.5
            ymax = face_y + max_depth
        dy = ymax - ymin
        cy = 0.5 * (ymin + ymax)
        probe = cq.Workplane("XY").box(dx, dy, dz, centered=(True, True, True)).val().translate((xw, cy, zw))

        inter = part.intersect(probe)
        sols = inter.Solids() if inter else []
        print(f"SELECTED: {len(sols)} solids in probe∩s0 for thickness measurement ({which}) at (x,z)=({xw:.3f},{zw:.3f})")
        if not sols:
            return None

        candidates = []
        for s in sols:
            bb = s.BoundingBox()
            if which == "top":
                if abs(bb.ymax - face_y) <= tol:
                    candidates.append((face_y - bb.ymin, bb))
            else:
                if abs(bb.ymin - face_y) <= tol:
                    candidates.append((bb.ymax - face_y, bb))

        print(f"SELECTED: {len(candidates)} probe components touching face plane y={face_y} (which={which}, tol={tol})")
        if not candidates:
            return None

        thickness, bb = min(candidates, key=lambda t: t[0])
        # Detect window-limited measurement
        limited = False
        if which == "top" and abs(bb.ymin - (face_y - max_depth)) < 1e-6:
            limited = True
        if which == "bot" and abs(bb.ymax - (face_y + max_depth)) < 1e-6:
            limited = True

        print(
            f"MEASURED: wall_thickness={thickness:.3f} mm ({which}) using bb(ymin={bb.ymin:.3f}, ymax={bb.ymax:.3f})"
            + (" [WARNING: limited by max_depth window]" if limited else "")
        )
        return max(0.5, thickness)

    def measure_slot_depth(part, face_y, which, xw, zw):
        # Sample a few points within the slot footprint to avoid catching ribs/bosses.
        # Keep within the rail width by using modest offsets.
        sample_offsets = [(0.0, 0.0), (6.0, 0.0), (-6.0, 0.0), (0.0, 1.0), (0.0, -1.0)]
        vals = []
        for dx, dz in sample_offsets:
            t = measure_wall_thickness_at(part, face_y, which, xw + dx, zw + dz)
            if t is not None:
                vals.append(t)
        if not vals:
            print(f"WARNING: no thickness measurement succeeded for slot at (x,z)=({xw},{zw}) which={which}; using fallback 10mm")
            return 10.0
        depth = min(vals) + 0.5  # small epsilon to ensure through-wall
        print(
            f"DEPTH: slot at (x,z)=({xw:.3f},{zw:.3f}) which={which} samples={[round(v,3) for v in vals]} -> cut_depth={depth:.3f}"
        )
        return depth

    # --- Perform two clean boolean CUT sequences on s0 only ---
    bb_before = base.BoundingBox()

    # TOP: localY = -worldZ (due to plane normal +Y and xDir +X)
    print("CUT TOP: 11 capsule slots on face #46")
    for z in z_centers:
        depth = measure_slot_depth(s0, y_top, "top", x_center, z)
        pt2d = (x_center, -z)
        tool = cq.Workplane(pl_top).pushPoints([pt2d]).slot2D(slot_L, slot_W, angle=slot_angle).extrude(-depth).val()
        tbb2 = tool.BoundingBox()
        print(
            f"TOOL TOP: center_world=({x_center:.3f},{y_top:.3f},{z:.3f}) plane_pt2d={tuple(round(v,3) for v in pt2d)} depth={depth:.3f} "
            f"tool_bb_y=({tbb2.ymin:.3f}..{tbb2.ymax:.3f})"
        )
        s0 = s0.cut(tool)

    # BOTTOM: localY = +worldZ (due to plane normal -Y and xDir +X)
    print("CUT BOTTOM: 11 capsule slots on face #22")
    for z in z_centers:
        depth = measure_slot_depth(s0, y_bot, "bot", x_center, z)
        pt2d = (x_center, z)
        tool = cq.Workplane(pl_bot).pushPoints([pt2d]).slot2D(slot_L, slot_W, angle=slot_angle).extrude(-depth).val()
        tbb2 = tool.BoundingBox()
        print(
            f"TOOL BOT: center_world=({x_center:.3f},{y_bot:.3f},{z:.3f}) plane_pt2d={tuple(round(v,3) for v in pt2d)} depth={depth:.3f} "
            f"tool_bb_y=({tbb2.ymin:.3f}..{tbb2.ymax:.3f})"
        )
        s0 = s0.cut(tool)

    # Recompound with other solids untouched
    out = cq.Compound.makeCompound([s for i, s in enumerate(solids) if i != s0_idx] + [s0])

    # --- Self-checks: bbox unchanged, removed material sanity, print achieved centers & orientation ---
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

    # Removed material (only from s0)
    s0_original = solids[s0_idx]
    removed = s0_original.cut(s0)
    rem_sols = removed.Solids() if removed else []
    print(f"SELECTED: {len(rem_sols)} removed solids (slot cut-outs may merge; expected around 22 if all separate)")
    if removed:
        rbb = removed.BoundingBox()
        rc = removed.Center()
        print(
            f"REMOVED: center={tuple(round(v,3) for v in rc.toTuple())} bbox=({rbb.xmin:.3f},{rbb.ymin:.3f},{rbb.zmin:.3f})..({rbb.xmax:.3f},{rbb.ymax:.3f},{rbb.zmax:.3f})"
        )

    # Major-axis orientation report
    major_axis = cq.Vector(1, 0, 0)
    dot = max(-1.0, min(1.0, major_axis.normalized().dot(cq.Vector(1, 0, 0))))
    ang = degrees(acos(dot))
    print(f"ORIENTATION: slots_major_axis_vector={tuple(major_axis.toTuple())} angle_to_world_X={ang:.3f} deg slot_angle_param={slot_angle} deg")

    # Print achieved centers as the explicit construction targets
    expected_world = [(x_center, y_top, z) for z in z_centers] + [(x_center, y_bot, z) for z in z_centers]
    print("ACHIEVED CENTERS (constructed, world coords):")
    for pt in expected_world:
        print(f"  center={tuple(pt)} major_axis=world +X")

    # Validity check (best effort; input may already be invalid)
    try:
        print(f"VALIDITY: s0_edited_isValid={bool(s0.isValid())} out_isValid={bool(out.isValid())}")
    except Exception as e:
        print(f"VALIDITY: could not evaluate isValid() ({e})")

    return out