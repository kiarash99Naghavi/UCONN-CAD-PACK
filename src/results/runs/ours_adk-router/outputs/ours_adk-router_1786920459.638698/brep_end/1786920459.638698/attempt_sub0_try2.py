def my_cad_function(args):
    import cadquery as cq
    import math

    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input
    base = shape.val() if hasattr(shape, "val") else shape

    # --- Resolve solids (keep all except edited one untouched) ---
    solids = base.Solids()
    print(f"SELECTED: {len(solids)} solids in imported model")
    if len(solids) < 2:
        print("ERROR: Expected 2 solids (s0 housing + s1 existing switch-like body)")
        return shape

    s0 = solids[0]
    s1 = solids[1]
    print("s0 bbox", s0.BoundingBox().xmin, s0.BoundingBox().ymin, s0.BoundingBox().zmin, "..", s0.BoundingBox().xmax, s0.BoundingBox().ymax, s0.BoundingBox().zmax)
    print("s1 bbox", s1.BoundingBox().xmin, s1.BoundingBox().ymin, s1.BoundingBox().zmin, "..", s1.BoundingBox().xmax, s1.BoundingBox().ymax, s1.BoundingBox().zmax)

    # --- Resolve target face by global face index ---
    faces = base.Faces()
    print(f"SELECTED: {len(faces)} faces in compound")
    if len(faces) <= 9:
        print("ERROR: Face list too short to access face #9")
        return shape
    f9 = faces[9]
    c9 = f9.Center()
    try:
        a9 = f9.Area()
    except Exception:
        a9 = None
    print(f"SELECTED: 1 face for bottom BSPLINE face #9  center={list(map(float,[c9.x,c9.y,c9.z]))} area={a9}")

    # --- Target numbers from sub-goal ---
    target_center = (27.9, 2.16, 51.27)  # requested actuator outer-surface center (x, y(min), z)
    x0, y0, z0 = target_center
    width_act = 4.997
    length_act = 14.064
    act_thick = 2.50  # thickness extends inward +Y

    # Opening/pocket sized to allow travel along world Z
    travel_allow = 4.0  # additional length beyond actuator (total), gives clear distinct positions
    width_open = width_act + 0.60
    length_open = length_act + travel_allow

    r_act = 0.80
    r_open = 1.10

    print("NAMED: target_center", target_center)
    print("NAMED: actuator WxL", (width_act, length_act), "thickness", act_thick)
    print("NAMED: opening WxL", (width_open, length_open), "(long sides must be parallel world Z)")

    # --- Build a plane on the min-Y envelope, with in-plane Y aligned to world Z ---
    # normal = -Y (outward), xDir = +X, so in-plane yDir becomes +Z
    pl = cq.Plane(origin=(x0, y0, z0), xDir=(1, 0, 0), normal=(0, -1, 0))
    print("PLANE: origin", (x0, y0, z0), "normal", (0, -1, 0), "xDir", (1, 0, 0), "(plane yDir should align +Z)")

    # --- Probe local bottom-wall thickness at (x0,z0) ---
    # Intersect a very thin column around (x0,z0) with s0, then find the piece that touches ymin=2.16.
    col_dx = 0.8
    col_dz = 0.8
    col_y0 = s0.BoundingBox().ymin - 5.0
    col_y1 = s0.BoundingBox().ymax + 5.0
    col_dy = col_y1 - col_y0
    col = cq.Solid.makeBox(col_dx, col_dy, col_dz, cq.Vector(x0 - col_dx / 2, col_y0, z0 - col_dz / 2))
    probe_int = s0.intersect(col)
    probe_solids = probe_int.Solids()
    print(f"SELECTED: {len(probe_solids)} solids from thickness probe intersection")

    wall_thick = 1.5  # fallback
    if len(probe_solids) > 0:
        # choose the piece whose ymin is closest to global minY=2.16
        best = None
        best_d = 1e9
        for i, ps in enumerate(probe_solids):
            bb = ps.BoundingBox()
            d = abs(bb.ymin - y0)
            print(f"  probe_piece[{i}] bbox.y=[{bb.ymin:.3f},{bb.ymax:.3f}] d_to_y0={d:.3f}")
            if d < best_d:
                best_d = d
                best = ps
        if best is not None and best_d < 1.0:
            bb = best.BoundingBox()
            wall_thick = max(0.6, bb.ymax - y0)
    print(f"MEASURED: approx bottom wall_thick ~= {wall_thick:.3f} mm at xz=({x0},{z0})")

    # pocket depth must accommodate actuator thickness; extra depth beyond wall thickness likely cuts into internal void
    pocket_depth = max(act_thick + 0.30, wall_thick + 0.50)
    print(f"DERIVED: pocket_depth = {pocket_depth:.3f} mm")

    # --- Create opening/pocket CUT tool (non-circular, long sides parallel world Z) ---
    sk_open = cq.Sketch().rect(width_open, length_open).vertices().fillet(r_open)
    tool_open = cq.Workplane(pl).placeSketch(sk_open).extrude(-pocket_depth)  # negative => +Y direction
    print("TOOL_OPEN bbox", tool_open.val().BoundingBox().xmin, tool_open.val().BoundingBox().ymin, tool_open.val().BoundingBox().zmin, "..", tool_open.val().BoundingBox().xmax, tool_open.val().BoundingBox().ymax, tool_open.val().BoundingBox().zmax)

    # Orientation check: long axis should be world Z (i.e. zlen > xlen)
    bb_open = tool_open.val().BoundingBox()
    opening_long_axis_angle_deg = 0.0 if bb_open.zlen >= bb_open.xlen else 90.0
    print(f"CHECK: opening xlen={bb_open.xlen:.3f} zlen={bb_open.zlen:.3f} => long-axis angle ~ {opening_long_axis_angle_deg:.1f} deg (expect ~0)")

    # If it ended up rotated 90deg, rebuild by swapping rect dims
    if opening_long_axis_angle_deg > 45.0:
        print("CORRECTING: opening appears rotated 90deg; swapping sketch rect dimensions")
        sk_open = cq.Sketch().rect(length_open, width_open).vertices().fillet(r_open)
        tool_open = cq.Workplane(pl).placeSketch(sk_open).extrude(-pocket_depth)
        bb_open = tool_open.val().BoundingBox()
        opening_long_axis_angle_deg = 0.0 if bb_open.zlen >= bb_open.xlen else 90.0
        print(f"RECHECK: opening xlen={bb_open.xlen:.3f} zlen={bb_open.zlen:.3f} => long-axis angle ~ {opening_long_axis_angle_deg:.1f} deg")

    # --- Cut pocket/opening in s0 only ---
    s0_cut = s0.cut(tool_open.val())

    # Diagnostics: removed material (should be small if it mostly opens into void)
    try:
        removed = s0.cut(s0_cut)
        removed_vol = removed.Volume() if removed is not None else 0.0
    except Exception:
        removed_vol = None
    print("REMOVED volume (s0 - s0_after_cut)", removed_vol)

    # --- Create actuator solid (separate body), flush at y=2.16 and extending inward +Y ---
    sk_act = cq.Sketch().rect(width_act, length_act).vertices().fillet(r_act)
    actuator_wp = cq.Workplane(pl).placeSketch(sk_act).extrude(-act_thick)  # negative => +Y
    actuator = actuator_wp.val()

    bb_act = actuator.BoundingBox()
    achieved_center = (bb_act.center.x, bb_act.ymin, bb_act.center.z)  # report outer-face center by ymin
    achieved_outermost_y = bb_act.ymin
    dx = achieved_center[0] - x0
    dy = achieved_center[1] - y0
    dz = achieved_center[2] - z0
    print("SELF-CHECK: achieved actuator outer-face center", tuple(map(float, achieved_center)), "delta", (dx, dy, dz))
    print("SELF-CHECK: achieved actuator outermost Y", float(achieved_outermost_y), "(target y=2.16)")

    # Correct minor placement drift if any (should be ~0)
    if abs(dx) > 0.5 or abs(dy) > 0.2 or abs(dz) > 0.5:
        print("CORRECTING: translating actuator to better match requested center")
        actuator = actuator.translate(cq.Vector(-dx, -dy, -dz))
        bb_act = actuator.BoundingBox()
        achieved_center = (bb_act.center.x, bb_act.ymin, bb_act.center.z)
        achieved_outermost_y = bb_act.ymin
        dx = achieved_center[0] - x0
        dy = achieved_center[1] - y0
        dz = achieved_center[2] - z0
        print("RECHECK: achieved actuator outer-face center", tuple(map(float, achieved_center)), "delta", (dx, dy, dz))
        print("RECHECK: achieved actuator outermost Y", float(achieved_outermost_y))

    # Movement feasibility print (two distinct positions along Z within opening)
    travel = max(0.0, length_open - length_act)
    pos1 = (x0, y0, z0 - travel / 2 + 0.5)
    pos2 = (x0, y0, z0 + travel / 2 - 0.5)
    print(f"MOTION: opening length - actuator length = {travel:.3f} mm usable travel along world Z (approx)")
    print("MOTION: example distinct actuator outer-face centers (no envelope change):")
    print("  position_A", pos1)
    print("  position_B", pos2)

    print(f"REPORT: opening_long_axis_angle_deg={opening_long_axis_angle_deg:.1f} (expect ~0; long sides parallel world Z)")

    # --- Recompound: keep s1 untouched, add actuator as new solid ---
    out = cq.Compound.makeCompound([s0_cut, s1, actuator])

    # Signed volume delta (must be positive for add-body)
    try:
        delta_vol = out.Volume() - base.Volume()
    except Exception:
        delta_vol = None
    print("DELTA", delta_vol)

    return out