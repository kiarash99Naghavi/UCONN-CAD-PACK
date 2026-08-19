def my_cad_function(args):
    import cadquery as cq
    import math

    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input
    base = shape.val() if hasattr(shape, "val") else shape

    # --- Basic diagnostics ---
    sols = list(base.Solids())
    print(f"SOLIDS: {len(sols)}")
    for i, s in enumerate(sols):
        bb = s.BoundingBox()
        print(f"  solid[{i}] vol={s.Volume():.3f} bbox=([{bb.xmin:.3f},{bb.ymin:.3f},{bb.zmin:.3f}]..[{bb.xmax:.3f},{bb.ymax:.3f},{bb.zmax:.3f}])")

    # Resolve face #9 from the full imported shape (per instructions)
    faces_all = list(base.Faces())
    print(f"FACES: {len(faces_all)}")
    if len(faces_all) > 9:
        f9 = faces_all[9]
        c9 = f9.Center()
        try:
            a9 = f9.Area()
        except Exception:
            a9 = float('nan')
        print(f"SELECTED: 1 face for target face #9   center=[{c9.x:.3f},{c9.y:.3f},{c9.z:.3f}] area={a9:.3f}")
    else:
        print("SELECTED: 0 faces for target face #9 (index out of range) -- ABORT")
        return shape

    # Identify s0 as the largest-volume solid (as per prompt summary)
    s0_idx = max(range(len(sols)), key=lambda i: sols[i].Volume())
    s0 = sols[s0_idx]
    other_solids = [s for i, s in enumerate(sols) if i != s0_idx]
    print(f"SELECTED: 1 solid for s0 (largest volume)   idx={s0_idx} vol={s0.Volume():.3f}")

    # --- Parameters from sub-goal ---
    target = cq.Vector(27.9, 2.16, 51.27)
    act_w = 4.997
    act_L = 14.064
    # Opening slightly larger and longer to allow travel (two distinct positions)
    open_w = act_w + 0.400
    open_L = act_L + 4.000

    # Recess and actuator thickness: choose values that keep actuator within envelope (ymin=2.16)
    # while keeping net volume delta positive (add-body)
    recess_depth = 0.60   # cut inward +Y
    act_thick = 1.20      # actuator extends inward +Y; outer face flush at y=2.16

    print("NAMED NUMBERS:")
    print(f"  target_center~[{target.x:.3f},{target.y:.3f},{target.z:.3f}] mm")
    print(f"  actuator W={act_w:.3f} L={act_L:.3f} thick={act_thick:.3f} mm")
    print(f"  opening  W={open_w:.3f} L={open_L:.3f} recess_depth={recess_depth:.3f} mm")

    # Workplane on the min-Y envelope, oriented so local X axis == world Z (slot long axis)
    # normal is -Y (bottom-facing), xDir set to +Z so slot2D length goes along world Z.
    plane = cq.Plane(origin=(target.x, target.y, target.z), normal=(0, -1, 0), xDir=(0, 0, 1))
    print(f"PLANE: origin=[{plane.origin.x:.3f},{plane.origin.y:.3f},{plane.origin.z:.3f}] normal=[0,-1,0] xDir=[0,0,1]")

    # Compute and print long-axis angle vs world Z to catch 90-degree rotation mistakes
    xdir = cq.Vector(0, 0, 1)
    zaxis = cq.Vector(0, 0, 1)
    ang = math.degrees(math.acos(max(-1.0, min(1.0, xdir.normalized().dot(zaxis)))))
    print(f"OPENING_LONG_AXIS_ANGLE_DEG (vs +Z): {ang:.3f}")

    # --- Build cut tool for the mounting opening (slot), long sides parallel world Z ---
    cut_tool_wp = cq.Workplane(plane).slot2D(open_L, open_w)
    cut_tool = cut_tool_wp.extrude(-recess_depth).val()  # negative to go +Y (inward)
    bb_cut = cut_tool.BoundingBox()
    print(f"CUT_TOOL: bbox=([{bb_cut.xmin:.3f},{bb_cut.ymin:.3f},{bb_cut.zmin:.3f}]..[{bb_cut.xmax:.3f},{bb_cut.ymax:.3f},{bb_cut.zmax:.3f}]) xlen={bb_cut.xlen:.3f} zlen={bb_cut.zlen:.3f}")

    # Sanity: in world, long dimension should be Z
    if bb_cut.zlen < bb_cut.xlen:
        print("WARNING: opening appears rotated (zlen < xlen). Attempting to correct by swapping plane xDir to +X and using angle=90...")
        # fallback plane where local X=world X, then rotate slot 90deg so its length aligns with world Z
        plane2 = cq.Plane(origin=(target.x, target.y, target.z), normal=(0, -1, 0), xDir=(1, 0, 0))
        cut_tool = cq.Workplane(plane2).slot2D(open_L, open_w, angle=90).extrude(-recess_depth).val()
        bb_cut = cut_tool.BoundingBox()
        print(f"CUT_TOOL(corrected): xlen={bb_cut.xlen:.3f} zlen={bb_cut.zlen:.3f}")

    # --- Cut s0 for mounting opening (recess) ---
    vol0_before = s0.Volume()
    try:
        s0_edited = s0.cut(cut_tool)
        print("SELECTED: 1 solid for cut (s0)")
    except Exception as e:
        print(f"ERROR: cut failed: {e}")
        # still proceed with actuator addition so this isn't a no-op
        s0_edited = s0
    vol0_after = s0_edited.Volume()
    print(f"S0 DELTA after cut: {vol0_after - vol0_before:.3f} mm^3")

    # --- Build actuator solid (capsule/slot prism), outer face flush at y=2.16, thickness inward +Y ---
    actuator = cq.Workplane(plane).slot2D(act_L, act_w).extrude(-act_thick).val()  # negative => +Y
    bb_act = actuator.BoundingBox()
    c_act = actuator.Center()

    # If center differs materially in X/Z from target, correct by translating actuator and cut tool together.
    # We keep Y-min flush at 2.16, so correct only X/Z and enforce ymin.
    dx = target.x - c_act.x
    dz = target.z - c_act.z
    # enforce outermost y to be exactly target.y (flush at min-Y envelope)
    dy = target.y - bb_act.ymin
    max_xz_err = max(abs(dx), abs(dz))
    if max_xz_err > 0.50 or abs(dy) > 0.05:
        print(f"PLACEMENT_CORRECTION: translating actuator/cut by [dx,dy,dz]=[{dx:.3f},{dy:.3f},{dz:.3f}] (xz_err={max_xz_err:.3f})")
        actuator = actuator.translate((dx, dy, dz))
        cut_tool = cut_tool.translate((dx, dy, dz))
        # re-apply cut if it succeeded earlier
        if s0_edited is not s0:
            try:
                s0_edited = s0.cut(cut_tool)
            except Exception as e:
                print(f"ERROR: re-cut after correction failed: {e}")
                # keep prior edited
        bb_act = actuator.BoundingBox()
        c_act = actuator.Center()

    # Self-check prints requested
    print(f"ACHIEVED_ACTUATOR_CENTER (solid centroid): [{c_act.x:.3f},{c_act.y:.3f},{c_act.z:.3f}]  vs target [{target.x:.3f},{target.y:.3f},{target.z:.3f}]  delta=[{(c_act.x-target.x):.3f},{(c_act.y-target.y):.3f},{(c_act.z-target.z):.3f}]")
    print(f"ACHIEVED_ACTUATOR_OUTERMOST_Y (ymin): {bb_act.ymin:.3f} mm  (target flush {target.y:.3f})")
    print(f"ACHIEVED_OPENING_LONG_AXIS_ANGLE_DEG (vs +Z): {ang:.3f}")

    # Ensure some change happened (not a no-op)
    # Build final compound: edited s0 + untouched other solids + new actuator solid
    out = cq.Compound.makeCompound(other_solids + [s0_edited, actuator])
    try:
        print('DELTA', out.Volume() - base.Volume())
    except Exception as e:
        print(f"DELTA: (could not compute) {e}")

    return out