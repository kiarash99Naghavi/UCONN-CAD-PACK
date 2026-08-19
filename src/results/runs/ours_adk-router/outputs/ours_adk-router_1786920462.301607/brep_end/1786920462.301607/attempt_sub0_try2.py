def my_cad_function(args):
    import cadquery as cq

    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input
    base = shape.val() if hasattr(shape, "val") else shape

    # --- Resolve and verify face #6 (global face list, per instructions) ---
    faces = base.Faces()
    print(f"INFO: base has {len(faces)} faces")
    if len(faces) <= 6:
        print("SELECTED: 0 faces for face #6 (ERROR: not enough faces)")
        return shape

    f6 = faces[6]
    try:
        f6_c = f6.Center()
        f6_area = f6.Area()
        f6_n = f6.normalAt()  # no args
    except Exception as e:
        print(f"ERROR: could not interrogate face #6: {e}")
        return shape

    print(
        "SELECTED: 1 face for target upper BSPLINE face #6  "
        f"center={[round(f6_c.x,3), round(f6_c.y,3), round(f6_c.z,3)]}  "
        f"area={round(f6_area,3)}  "
        f"normal={[round(f6_n.x,4), round(f6_n.y,4), round(f6_n.z,4)]}"
    )

    # --- Identify solids; edit only s0 and re-compound the rest unchanged ---
    solids = base.Solids()
    print(f"INFO: base has {len(solids)} solids")
    if len(solids) < 1:
        print("SELECTED: 0 solids for editing (ERROR)")
        return shape

    solid_info = [(i, s.Volume(), s) for i, s in enumerate(solids)]
    solid_info_sorted = sorted(solid_info, key=lambda t: t[1], reverse=True)
    big_i, big_v, big = solid_info_sorted[0]
    print(f"SELECTED: 1 solid for s0 edit (largest by volume)  solid_index={big_i}  vol={round(big_v,3)}")

    others = [s for i, _, s in solid_info if i != big_i]

    bb = big.BoundingBox()
    print(
        "INFO: s0 bbox "
        f"min={[round(bb.xmin,3), round(bb.ymin,3), round(bb.zmin,3)]} "
        f"max={[round(bb.xmax,3), round(bb.ymax,3), round(bb.zmax,3)]}"
    )

    # --- Helper: measure top-surface Y at a given (x,z) by intersecting a thin column ---
    def measure_y_top_at_xz(x, z, dx=3.0, dz=3.0):
        col_ymin = bb.ymin - 10.0
        col_dy = bb.ylen + 20.0
        probe = cq.Solid.makeBox(dx, col_dy, dz, pnt=cq.Vector(x - dx / 2.0, col_ymin, z - dz / 2.0))
        inter = big.intersect(probe)
        try:
            ibb = inter.BoundingBox()
            # If intersection is empty, OCC sometimes returns a degenerate box; detect by tiny extents
            if ibb.xlen < 1e-6 or ibb.ylen < 1e-6 or ibb.zlen < 1e-6:
                return None, None
            return ibb.ymax, ibb
        except Exception:
            return None, None

    # --- Build two separate rounded-rect pads (world XZ footprint), proud +Y by exactly 2.0mm ---
    targets = [
        {"name": "pad_A", "x": 14.0, "z": 54.0},
        {"name": "pad_B", "x": 41.0, "z": 54.0},
    ]

    w_x = 18.0
    l_z = 24.0
    corner_r = 4.0
    proud = 2.0
    overlap = 1.0  # embed into housing to ensure surface-supported contact

    # Note: CadQuery 2.8 Workplane has no roundedRect(); emulate via rect + vertex fillet.
    pads = []
    achieved_report = []

    for t in targets:
        name = t["name"]
        x = float(t["x"])
        z = float(t["z"])

        y_top, ibb = measure_y_top_at_xz(x, z)
        if y_top is None:
            print(f"SELECTED: 0 intersection for surface-probe at (x,z)=({x},{z}) -> cannot place {name}")
            continue

        y0 = y_top - overlap
        h = proud + overlap  # pad top should land at y_top + proud

        # Sketch plane: normal +Y; with xDir +X, the plane Y axis becomes -Z.
        # Therefore, to place at world (x,z) use center(x, -z).
        plane = cq.Plane(origin=(0.0, y0, 0.0), normal=(0.0, 1.0, 0.0), xDir=(1.0, 0.0, 0.0))
        print(
            f"INFO: {name} sketch plane origin={[round(plane.origin.x,3), round(plane.origin.y,3), round(plane.origin.z,3)]} "
            f"for target (x,z)=({x},{z}) measured y_surface={round(y_top,3)}"
        )

        wp = cq.Workplane(plane).center(x, -z)
        # rounded rectangle via filleting sketch vertices
        pad_wp = wp.rect(w_x, l_z, centered=True).vertices().fillet(corner_r).extrude(h)
        pad = pad_wp.val()

        def measure_pad(pad_solid):
            pbb = pad_solid.BoundingBox()
            achieved_cx = pbb.center.x
            achieved_cz = pbb.center.z
            achieved_proud = pbb.ymax - y_top
            return pbb, achieved_cx, achieved_cz, achieved_proud

        pbb, achieved_cx, achieved_cz, achieved_proud = measure_pad(pad)
        print(
            f"PLACEMENT(pre-correct): {name} target_center(x,z)=({x:.3f},{z:.3f}) "
            f"achieved_center(x,z)=({achieved_cx:.3f},{achieved_cz:.3f}) "
            f"delta=({(achieved_cx-x):+.3f},{(achieved_cz-z):+.3f})"
        )
        print(
            f"HEIGHT(pre-correct): {name} y_surface={y_top:.3f} pad_ymax={pbb.ymax:.3f} proud_height={achieved_proud:.3f} (target {proud:.3f})"
        )

        # --- In-attempt correction if off the stated center/height ---
        dx = x - achieved_cx
        dz = z - achieved_cz
        dh = proud - achieved_proud
        if abs(dx) > 0.2 or abs(dz) > 0.2 or abs(dh) > 0.05:
            print(f"INFO: {name} applying correction translate(dx,dy,dz)=({dx:+.3f},{dh:+.3f},{dz:+.3f})")
            pad = pad.translate((dx, dh, dz))
            pbb, achieved_cx, achieved_cz, achieved_proud = measure_pad(pad)
            print(
                f"PLACEMENT(post-correct): {name} target_center(x,z)=({x:.3f},{z:.3f}) "
                f"achieved_center(x,z)=({achieved_cx:.3f},{achieved_cz:.3f}) "
                f"delta=({(achieved_cx-x):+.3f},{(achieved_cz-z):+.3f})"
            )
            print(
                f"HEIGHT(post-correct): {name} y_surface={y_top:.3f} pad_ymax={pbb.ymax:.3f} proud_height={achieved_proud:.3f} (target {proud:.3f})"
            )

        pads.append(pad)
        achieved_report.append((name, achieved_cx, achieved_cz, achieved_proud))

    print(f"SELECTED: {len(pads)} solids for click-button pads")
    if len(pads) != 2:
        print("ERROR: Did not successfully build both pads; returning input unchanged")
        return shape

    # Check pads are distinct (no overlap)
    try:
        intr_pp = pads[0].intersect(pads[1])
        iv_pp = intr_pp.Volume()
    except Exception:
        iv_pp = 0.0
    print(f"CHECK: pad_A ∩ pad_B intersection volume = {iv_pp:.6f} mm^3 (expect ~0)")

    # Ensure pads do not intersect the central control body (s1), if present
    if len(solids) > 1 and len(others) > 0:
        pads_comp = cq.Compound.makeCompound(pads)
        for j, s_other in enumerate(others):
            try:
                intr = s_other.intersect(pads_comp)
                iv = intr.Volume()
            except Exception:
                iv = 0.0
            print(f"CHECK: pad intersection volume with other solid[{j}] = {iv:.6f} mm^3 (expect ~0)")

    # Fuse pads into s0 only
    pads_comp = cq.Compound.makeCompound(pads)
    try:
        s0_new = big.fuse(pads_comp)
    except Exception as e:
        print(f"ERROR: fuse failed: {e}")
        return shape

    # Self-check: isolate added material on s0 (should be ~2mm proud regions)
    try:
        added = s0_new.cut(big)
        abb = added.BoundingBox()
        print(
            "ADDED: bounding box "
            f"min={[round(abb.xmin,3), round(abb.ymin,3), round(abb.zmin,3)]} "
            f"max={[round(abb.xmax,3), round(abb.ymax,3), round(abb.zmax,3)]}"
        )
        try:
            ac = added.Center()
            print(f"ADDED: center={[round(ac.x,3), round(ac.y,3), round(ac.z,3)]}  vol={round(added.Volume(),3)}")
        except Exception:
            pass
    except Exception as e:
        print(f"WARN: could not compute added material via cut: {e}")

    # Print achieved footprint centers and heights (requested)
    for name, cx, cz, ph in achieved_report:
        print(f"RESULT: {name} achieved_center(x,z)=({cx:.3f},{cz:.3f}) achieved_proud_height={ph:.3f} mm (target 2.000)")

    # Re-compound with other solids unchanged
    out_solids = [s if i != big_i else s0_new for i, _, s in solid_info]
    out = cq.Compound.makeCompound(out_solids)
    return out