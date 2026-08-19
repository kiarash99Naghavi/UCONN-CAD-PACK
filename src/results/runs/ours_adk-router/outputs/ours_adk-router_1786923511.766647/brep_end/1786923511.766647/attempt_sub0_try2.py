def my_cad_function(args):
    import cadquery as cq

    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input
    base = shape.val() if hasattr(shape, "val") else shape

    def v3(p):
        return [float(p.x), float(p.y), float(p.z)]

    # --- Resolve the referenced faces to ensure indices match the provided geometry index ---
    f361 = base.Faces()[361]
    f362 = base.Faces()[362]
    f368 = base.Faces()[368]
    f370 = base.Faces()[370]
    f365 = base.Faces()[365]
    f366 = base.Faces()[366]

    print("FACE CHECK: #361 center=", v3(f361.Center()))
    print("FACE CHECK: #362 center=", v3(f362.Center()))
    print("FACE CHECK: #368 center=", v3(f368.Center()), " normal=", v3(f368.normalAt()))
    print("FACE CHECK: #370 center=", v3(f370.Center()), " normal=", v3(f370.normalAt()))
    print("FACE CHECK: #365 center=", v3(f365.Center()))
    print("FACE CHECK: #366 center=", v3(f366.Center()))

    # --- Find the owning solid for face #361 (body s17 in the geometry index) ---
    sols = list(base.Solids())
    print(f"INFO: total solids in STEP = {len(sols)}")

    idx_s17 = None
    for i, s in enumerate(sols):
        for ff in s.Faces():
            if ff.wrapped.IsSame(f361.wrapped):
                idx_s17 = i
                break
        if idx_s17 is not None:
            break

    print("SELECTED:", 1 if idx_s17 is not None else 0, "solid for s17 stand (via face #361)", "idx=", idx_s17)
    if idx_s17 is None:
        print("ERROR: could not locate the solid containing face #361; returning input unchanged")
        return shape

    stand = sols[idx_s17]
    bb_stand = stand.BoundingBox()
    print(
        "INFO: original stand bbox:",
        "xmin/xmax=", bb_stand.xmin, bb_stand.xmax,
        "ymin/ymax=", bb_stand.ymin, bb_stand.ymax,
        "zmin/zmax=", bb_stand.zmin, bb_stand.zmax,
    )
    print("INFO: original stand Center()=", v3(stand.Center()))

    # --- Mirror across transverse plane Y=177.8 mm ---
    # NOTE: cadquery Shape.mirror(cq.Plane(...)) is buggy in this environment (UnboundLocalError
    # for mirrorPlaneNormalVector). Use the string-plane API with a basePointVector instead.
    mirror_y = 177.8
    print("INFO: mirror about plane Y=", mirror_y, " (mirrorPlane='XZ', basePointVector=(0,Y,0))")

    mirrored = stand.mirror(mirrorPlane="XZ", basePointVector=cq.Vector(0.0, mirror_y, 0.0))

    # --- Trim only the copied stand below Z=-115.0 mm (keep Z >= -115) ---
    z_flat = -115.0
    bb_all = base.BoundingBox()
    xlen = (bb_all.xmax - bb_all.xmin) + 400.0
    ylen = (bb_all.ymax - bb_all.ymin) + 400.0
    ztop = bb_all.zmax + 200.0
    zlen = ztop - z_flat
    xmin = bb_all.xmin - 200.0
    ymin = bb_all.ymin - 200.0

    box_above = cq.Solid.makeBox(xlen, ylen, zlen, pnt=cq.Vector(xmin, ymin, z_flat))
    copied = mirrored.intersect(box_above)

    # --- Placement self-check for the copied stand ---
    bb_c = copied.BoundingBox()
    c_center = copied.Center()

    target_center_y = 63.5
    target_ymin = 50.8
    target_ymax = 76.2

    print(
        "CHECK: copied stand Center()=", v3(c_center),
        " target Y=", target_center_y,
        " dY=", float(c_center.y - target_center_y),
    )
    print(
        "CHECK: copied stand Y extent=", float(bb_c.ymin), "..", float(bb_c.ymax),
        " target=", target_ymin, "..", target_ymax,
        " dYmin=", float(bb_c.ymin - target_ymin),
        " dYmax=", float(bb_c.ymax - target_ymax),
    )
    print(
        "CHECK: copied stand support-flat Z level (bbox zmin)=", float(bb_c.zmin),
        " target=", z_flat,
        " dZ=", float(bb_c.zmin - z_flat),
    )

    # If materially off, correct via translation in Y (mirror should be exact) and re-trim.
    dy = target_center_y - float(c_center.y)
    need_rebuild = (
        abs(dy) > 1.0
        or abs(float(bb_c.ymin) - target_ymin) > 1.0
        or abs(float(bb_c.ymax) - target_ymax) > 1.0
        or abs(float(bb_c.zmin) - z_flat) > 0.25
    )
    if need_rebuild:
        print("INFO: correcting copied stand: translate dy=", dy, " and re-trim")
        corrected = mirrored.translate((0.0, dy, 0.0))
        copied = corrected.intersect(box_above)
        bb_c = copied.BoundingBox()
        c_center = copied.Center()
        print(
            "RECHECK: copied stand Center()=", v3(c_center),
            " dY=", float(c_center.y - target_center_y),
        )
        print("RECHECK: copied stand Y extent=", float(bb_c.ymin), "..", float(bb_c.ymax))
        print(
            "RECHECK: copied stand support-flat Z level (bbox zmin)=", float(bb_c.zmin),
            " dZ=", float(bb_c.zmin - z_flat),
        )

    # --- Recompound: keep all original solids unchanged, add the trimmed mirrored copy as a new solid ---
    out = cq.Compound.makeCompound(sols + [copied])

    # --- Added-material diagnostic (out - base) ---
    base_comp = cq.Compound.makeCompound(sols)
    added = out.cut(base_comp)
    bb_added = added.BoundingBox()
    print("ADDED: Center()=", v3(added.Center()))
    print(
        "ADDED: Y extent=", float(bb_added.ymin), "..", float(bb_added.ymax),
        " (target ~", target_ymin, "..", target_ymax, ")",
    )
    print("ADDED: support-flat Z(min)=", float(bb_added.zmin), " (target ", z_flat, ")")

    return out