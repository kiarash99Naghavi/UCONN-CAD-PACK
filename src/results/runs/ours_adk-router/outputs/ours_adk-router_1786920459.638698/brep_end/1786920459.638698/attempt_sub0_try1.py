def my_cad_function(args):
    import cadquery as cq
    import math

    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input

    base = shape.val() if hasattr(shape, "val") else shape
    solids = base.Solids()
    print(f"SELECTED: {len(solids)} solids in imported shape")
    for i, s in enumerate(solids):
        bb = s.BoundingBox()
        print(f"  solid[{i}] bbox min=({bb.xmin:.3f},{bb.ymin:.3f},{bb.zmin:.3f}) max=({bb.xmax:.3f},{bb.ymax:.3f},{bb.zmax:.3f})")

    faces = base.Faces()
    print(f"SELECTED: {len(faces)} faces in imported shape")
    if len(faces) > 9:
        f9 = faces[9]
        c9 = f9.Center()
        print(f"SELECTED: 1 face for target face #9  center=({c9.x:.3f},{c9.y:.3f},{c9.z:.3f})")
    else:
        print("SELECTED: 0 faces for target face #9 (index out of range) -- aborting edit")
        return shape

    if len(solids) < 2:
        print("SELECTED: 0 solids for s1 preservation (need 2 solids) -- aborting edit")
        return shape

    # Operate only on s0 (solid[0])
    s0 = solids[0]
    s1 = solids[1]

    # ---- Named targets from the sub-goal ----
    target_pt = cq.Vector(27.9, 2.16, 51.27)
    y_env_min = 2.16
    act_w = 4.997
    act_l = 14.064
    # thickness not specified; choose a reasonable slider thickness that stays inside +Y
    act_th = 2.0

    # Opening: provide travel so it can slide between two positions
    travel_each_side = 2.0
    open_l = act_l + 2.0 * travel_each_side
    open_w = act_w + 0.4  # small clearance

    # Recess depths (into +Y)
    slot_depth = act_th + 1.2
    cavity_depth = act_th + 8.0
    outside_overlap = 0.5

    print("TARGET NUMBERS:")
    print(f"  target surface-center approx = ({target_pt.x:.3f},{target_pt.y:.3f},{target_pt.z:.3f}) mm")
    print(f"  actuator W x L = {act_w:.3f} x {act_l:.3f} mm (slide along world Z)")
    print(f"  desired outer envelope Y(min) = {y_env_min:.3f} mm")

    # Build sketch plane with long axis along world Z (Plane xDir = world +Z)
    # normal +Y so extrude +Y goes inward
    plane_origin = (target_pt.x, y_env_min - outside_overlap, target_pt.z)
    pl = cq.Plane(origin=plane_origin, normal=(0, 1, 0), xDir=(0, 0, 1))
    print(f"SKETCH PLANE: origin=({plane_origin[0]:.3f},{plane_origin[1]:.3f},{plane_origin[2]:.3f}) normal=(0,1,0) xDir=(0,0,1)")

    # Tools: slot opening + larger rectangular cavity behind it
    slot_tool = (
        cq.Workplane(pl)
        .slot2D(open_l, open_w)
        .extrude(slot_depth + outside_overlap)
        .val()
    )
    cavity_tool = (
        cq.Workplane(pl)
        .rect(open_l + 6.0, open_w + 6.0)
        .extrude(cavity_depth + outside_overlap)
        .val()
    )

    print("SELECTED: 2 solids as cut tools for mounting opening + recess")
    bb_st = slot_tool.BoundingBox()
    bb_ct = cavity_tool.BoundingBox()
    print(f"  slot_tool bbox y=[{bb_st.ymin:.3f},{bb_st.ymax:.3f}]  (should straddle y={y_env_min:.3f} and extend inward +Y)")
    print(f"  cavity_tool bbox y=[{bb_ct.ymin:.3f},{bb_ct.ymax:.3f}] (should extend further inward +Y)")

    # Perform cuts on s0 only
    edited_s0 = s0.cut(slot_tool)
    edited_s0 = edited_s0.cut(cavity_tool)

    # Actuator as a separate movable solid (flush with y=2.16, thickness into +Y)
    actuator_center = cq.Vector(target_pt.x, y_env_min + act_th / 2.0, target_pt.z)
    actuator = (
        cq.Workplane("XY")
        .box(act_w, act_th, act_l, centered=(True, True, True))
        .val()
        .translate((actuator_center.x, actuator_center.y, actuator_center.z))
    )

    # --- Self-checks & correction in same attempt ---
    act_bb = actuator.BoundingBox()
    act_c = actuator.Center()

    # Compare *exposed face* center target (x,z should match; y should match outer surface)
    exposed_center = cq.Vector(act_c.x, act_bb.ymin, act_c.z)
    delta_exposed = exposed_center.sub(target_pt)

    # If materially off in X/Z (or Y for exposed plane), correct by translating actuator
    tol = 0.5
    if abs(delta_exposed.x) > tol or abs(delta_exposed.y) > tol or abs(delta_exposed.z) > tol:
        print("CORRECTION: actuator exposed-center deviates materially; translating actuator")
        actuator = actuator.translate((-delta_exposed.x, -delta_exposed.y, -delta_exposed.z))
        act_bb = actuator.BoundingBox()
        act_c = actuator.Center()
        exposed_center = cq.Vector(act_c.x, act_bb.ymin, act_c.z)
        delta_exposed = exposed_center.sub(target_pt)

    # Opening long-axis angle: with xDir=(0,0,1) it is world +Z
    long_axis = cq.Vector(0, 0, 1)
    world_z = cq.Vector(0, 0, 1)
    dot = max(-1.0, min(1.0, long_axis.normalized().dot(world_z)))
    angle_deg = math.degrees(math.acos(dot))

    print("ACHIEVED:")
    print(f"  actuator solid center         = ({act_c.x:.3f},{act_c.y:.3f},{act_c.z:.3f}) mm")
    print(f"  actuator exposed-face center  = ({exposed_center.x:.3f},{exposed_center.y:.3f},{exposed_center.z:.3f}) mm")
    print(f"  delta to target (exposed ctr) = ({delta_exposed.x:.3f},{delta_exposed.y:.3f},{delta_exposed.z:.3f}) mm")
    print(f"  actuator outermost Y (ymin)   = {act_bb.ymin:.3f} mm  (target flush y={y_env_min:.3f})")
    print(f"  opening long-axis angle to +Z = {angle_deg:.2f} deg (0 means parallel to world Z)")

    # Recompound: keep s1 untouched, add actuator as new separate solid
    out = cq.Compound.makeCompound([edited_s0, s1, actuator])

    # Generic added/removed material isolation
    try:
        added = out.cut(base)
        abb = added.BoundingBox()
        acn = added.Center()
        print(
            "ADDED (out - base): center=(%.3f,%.3f,%.3f) bbox y=[%.3f,%.3f]" %
            (acn.x, acn.y, acn.z, abb.ymin, abb.ymax)
        )
    except Exception as e:
        print(f"ADDED isolation failed: {e}")

    try:
        removed = base.cut(out)
        rbb = removed.BoundingBox()
        rcn = removed.Center()
        print(
            "REMOVED (base - out): center=(%.3f,%.3f,%.3f) bbox y=[%.3f,%.3f]" %
            (rcn.x, rcn.y, rcn.z, rbb.ymin, rbb.ymax)
        )
    except Exception as e:
        print(f"REMOVED isolation failed: {e}")

    return out