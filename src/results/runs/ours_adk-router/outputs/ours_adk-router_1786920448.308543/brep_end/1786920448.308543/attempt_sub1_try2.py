def my_cad_function(args):
    import cadquery as cq

    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input
    base = shape.val() if hasattr(shape, "val") else shape

    # --- Cap parameters from sub-goal (absolute coordinates) ---
    cap_center = (-88.9, 100.0)
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

    # Also named in requirement text (roof over the 25.4 pouring opening):
    pour_opening_d = 25.4

    print("PARAMS (named): cap_center=", cap_center, " axis=", cap_axis)
    print("PARAMS (named): z_seat=", z_seat, " z_recess_top=", z_recess_top, " z_top=", z_top)
    print("PARAMS (named): cap_od=", cap_od, " recess_d=", recess_d, " recess_depth=", recess_depth,
          " roof_thickness=", roof_thickness, " pour_opening_d=", pour_opening_d)

    # --- Imported solids (do NOT alter) ---
    base_sols = base.Solids()
    print(f"SELECTED: {len(base_sols)} solids from imported shape for recomposition (must remain unchanged)")
    if len(base_sols) > 0:
        print("INFO: imported s0 volume (for reference only)=", base_sols[0].Volume())

    # --- Build cap as a separate, watertight solid using direct OCC primitives (avoid Workplane ambiguity) ---
    p0 = cq.Vector(cap_center[0], cap_center[1], z_seat)
    vdir = cq.Vector(*cap_axis)

    cap_blank = cq.Solid.makeCylinder(cap_r, cap_height, pnt=p0, dir=vdir)
    recess_tool = cq.Solid.makeCylinder(recess_r, recess_depth, pnt=p0, dir=vdir)

    cap = cap_blank.cut(recess_tool)

    # --- Cap reporting / self-checks ---
    cap_bb = cap.BoundingBox()
    cap_center_bb = ((cap_bb.xmin + cap_bb.xmax) / 2.0, (cap_bb.ymin + cap_bb.ymax) / 2.0, (cap_bb.zmin + cap_bb.zmax) / 2.0)

    print("ACHIEVED: cap bbox= min=({:.3f},{:.3f},{:.3f}) max=({:.3f},{:.3f},{:.3f})".format(
        cap_bb.xmin, cap_bb.ymin, cap_bb.zmin, cap_bb.xmax, cap_bb.ymax, cap_bb.zmax
    ))
    print("ACHIEVED: cap bbox center=", tuple(round(x, 6) for x in cap_center_bb),
          " (target xy=", cap_center, ")",
          " delta_xy=({:.6f},{:.6f})".format(cap_center_bb[0] - cap_center[0], cap_center_bb[1] - cap_center[1]))

    print("ACHIEVED: seating level zmin=", cap_bb.zmin, " (target=", z_seat, ") delta=", cap_bb.zmin - z_seat)
    print("ACHIEVED: top level zmax=", cap_bb.zmax, " (target=", z_top, ") delta=", cap_bb.zmax - z_top)
    print("ACHIEVED: outside diameter from bbox xlen/ylen=", (cap_bb.xlen, cap_bb.ylen), " (target OD=", cap_od, ")")
    print("ACHIEVED: recess diameter=", recess_d, " recess depth=", recess_depth,
          " recess top z=", z_recess_top, " roof thickness=", roof_thickness)

    # Validate cap solid
    try:
        cap_valid = cap.isValid()
    except Exception as e:
        cap_valid = None
        print("WARN: cap.isValid() check failed:", e)
    try:
        cap_type = cap.ShapeType()
    except Exception:
        cap_type = None
    print("CHECK: cap ShapeType=", cap_type, " isValid=", cap_valid, " (cap must be a valid separate solid)")

    # Informational: intersection with imported base (should be 0 for a free-standing cap; if touching is desired later, this may be >0)
    try:
        common = cap.intersect(base)
        common_vol = common.Volume() if common is not None else None
    except Exception as e:
        common_vol = None
        print("WARN: cap.intersect(base) failed (base may contain invalid solids):", e)
    print("CHECK: cap ∩ imported volume (informational)=", common_vol)

    # --- Compose output without modifying any imported body: keep all original solids + add cap as new solid ---
    out = cq.Compound.makeCompound(list(base_sols) + [cap])
    out_sols = out.Solids()
    print(f"SELECTED: {len(out_sols)} solids in output compound (expected {len(base_sols)+1})")

    # Identify cap in the output by nearest volume to the computed cap volume
    cap_expected_vol = 3.141592653589793 * (cap_r**2 * cap_height - recess_r**2 * recess_depth)
    vols = [s.Volume() for s in out_sols]
    cap_idx = min(range(len(vols)), key=lambda i: abs(vols[i] - cap_expected_vol)) if vols else None
    print("CHECK: expected cap volume (analytic)=", cap_expected_vol)
    if cap_idx is not None:
        print("CHECK: closest solid to expected cap volume idx=", cap_idx, " vol=", vols[cap_idx],
              " abs_err=", abs(vols[cap_idx] - cap_expected_vol))

    return out