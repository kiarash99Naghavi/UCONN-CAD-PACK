def my_cad_function(args):
    import cadquery as cq

    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input
    base = shape.val() if hasattr(shape, "val") else shape

    # --- Cap parameters from sub-goal (absolute coordinates) ---
    cap_center_xy = (-88.9, 100.0)
    cap_axis = (0.0, 0.0, 1.0)
    z_seat = 296.7
    z_top = 308.7
    cap_height = z_top - z_seat  # 12.0
    cap_od = 44.45
    cap_r = cap_od / 2.0  # 22.225

    recess_d = 38.1
    recess_r = recess_d / 2.0  # 19.05
    recess_depth = 8.0
    z_recess_top = z_seat + recess_depth  # 304.7

    roof_thickness = z_top - z_recess_top  # 4.0

    print("PARAMS: cap_center_xy=", cap_center_xy, " cap_axis=", cap_axis)
    print("PARAMS: z_seat=", z_seat, " z_recess_top=", z_recess_top, " z_top=", z_top)
    print("PARAMS: cap_od=", cap_od, " recess_d=", recess_d, " recess_depth=", recess_depth, " roof_thickness=", roof_thickness)

    # --- Build cap as a separate, pre-recessed solid (do NOT alter any imported body) ---
    cap_plane = cq.Plane(origin=(cap_center_xy[0], cap_center_xy[1], z_seat), normal=cap_axis)
    print("PLANE: cap underside plane origin=", cap_plane.origin.toTuple(), " normal=", cap_plane.zDir.toTuple())

    cap_blank = cq.Workplane(cap_plane).circle(cap_r).extrude(cap_height).val()
    print("BUILT: cap_blank")

    recess_tool = cq.Workplane(cap_plane).circle(recess_r).extrude(recess_depth).val()
    print("BUILT: recess_tool")

    cap = cap_blank.cut(recess_tool)

    # --- Self-checks / reporting ---
    cap_bb = cap.BoundingBox()
    cap_bb_center = ((cap_bb.xmin + cap_bb.xmax) / 2.0, (cap_bb.ymin + cap_bb.ymax) / 2.0, (cap_bb.zmin + cap_bb.zmax) / 2.0)
    cap_com = cap.Center()

    print("ACHIEVED: cap bbox center=", cap_bb_center)
    print("ACHIEVED: cap COM(center of mass)=", cap_com.toTuple())
    print("ACHIEVED: cap axis=", cap_axis)
    print("ACHIEVED: seating level zmin=", cap_bb.zmin, " (target=", z_seat, ")  delta=", cap_bb.zmin - z_seat)
    print("ACHIEVED: top level zmax=", cap_bb.zmax, " (target=", z_top, ")  delta=", cap_bb.zmax - z_top)
    print("ACHIEVED: outside diameter from bbox xlen/ylen=", (cap_bb.xlen, cap_bb.ylen), " (target OD=", cap_od, ")")
    print("ACHIEVED: recess diameter=", recess_d, " recess depth=", recess_depth, " recess top z=", z_recess_top, " roof thickness=", roof_thickness)

    # Confirm cap is a valid separate solid
    try:
        cap_valid = cap.isValid()
    except Exception as e:
        cap_valid = None
        print("WARN: cap.isValid() check failed:", e)
    print("CHECK: cap isValid=", cap_valid)

    # Check that we did not modify the imported base and that cap is separate
    try:
        common = cap.intersect(base)
        common_vol = common.Volume() if common is not None else None
    except Exception as e:
        common_vol = None
        print("WARN: cap.intersect(base) failed (base may be invalid compound):", e)
    print("CHECK: cap ∩ base volume (informational, can be >0 at joint)=", common_vol)

    # --- Return as an assembly with separate cap ---
    assy = cq.Assembly()
    assy.add(base, name="imported")
    assy.add(cap, name="cap")

    return assy