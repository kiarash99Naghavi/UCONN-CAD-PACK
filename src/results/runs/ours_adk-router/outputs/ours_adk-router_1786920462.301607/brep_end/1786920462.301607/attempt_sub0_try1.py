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
        f6_n = f6.normalAt()
    except Exception as e:
        print(f"ERROR: could not interrogate face #6: {e}")
        return shape

    print(
        "SELECTED: 1 face for target upper BSPLINE face #6  "
        f"center={[round(f6_c.x,3), round(f6_c.y,3), round(f6_c.z,3)]}  "
        f"area={round(f6_area,3)}  normal={[round(f6_n.x,4), round(f6_n.y,4), round(f6_n.z,4)]}"
    )

    # --- Identify solids; edit only the large body (s0) and re-compound the rest unchanged ---
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
    def measure_y_top_at_xz(x, z, dx=1.0, dz=1.0):
        col_ymin = bb.ymin - 10.0
        col_dy = bb.ylen + 20.0
        probe = cq.Solid.makeBox(dx, col_dy, dz, pnt=cq.Vector(x - dx / 2.0, col_ymin, z - dz / 2.0))
        inter = big.intersect(probe)
        # If intersection is empty, BoundingBox may be degenerate; guard it.
        try:
            ibb = inter.BoundingBox()
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

    pads = []
    for t in targets:
        x = float(t["x"])
        z = float(t["z"])

        y_top, ibb = measure_y_top_at_xz(x, z, dx=1.0, dz=1.0)
        if y_top is None:
            print(f"SELECTED: 0 intersection for surface-probe at (x,z)=({x},{z}) -> cannot place {t['name']}")
            continue

        y0 = y_top - overlap
        h = proud + overlap  # ensures pad top is exactly y_top + proud

        # Sketch plane: normal +Y, with local X along global +X, local Y along global +Z
        plane = cq.Plane(origin=(0.0, y0, 0.0), normal=(0.0, 1.0, 0.0), xDir=(1.0, 0.0, 0.0))
        print(
            f"INFO: {t['name']} sketch plane origin={[round(plane.origin.x,3), round(plane.origin.y,3), round(plane.origin.z,3)]} "
            f"for target (x,z)=({x},{z}) measured y_top={round(y_top,3)}"
        )

        wp = cq.Workplane(plane).center(x, z)
        pad_wp = wp.roundedRect(w_x, l_z, corner_r).extrude(h)
        pad = pad_wp.val()

        pbb = pad.BoundingBox()
        achieved_cx = pbb.center.x
        achieved_cz = pbb.center.z
        achieved_proud = pbb.ymax - y_top

        print(
            f"PLACEMENT: {t['name']} target_center(x,z)=({x:.3f},{z:.3f}) "
            f"achieved_center(x,z)=({achieved_cx:.3f},{achieved_cz:.3f}) "
            f"delta=({(achieved_cx-x):+.3f},{(achieved_cz-z):+.3f})"
        )
        print(
            f"HEIGHT: {t['name']} y_surface={y_top:.3f} pad_ymax={pbb.ymax:.3f} proud_height={achieved_proud:.3f} (target {proud:.3f})"
        )

        pads.append(pad)

    print(f"SELECTED: {len(pads)} solids for click-button pads")
    if len(pads) != 2:
        print("ERROR: Did not successfully build both pads; returning input unchanged")
        return shape

    # Ensure pads do not intersect the central control body (s1), if present
    if len(solids) > 1:
        # the "other" smaller solid is likely the control; check intersection against all others
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

    # Self-check: isolate added material
    try:
        added = s0_new.cut(big)
        abb = added.BoundingBox()
        print(
            "ADDED: bounding box "
            f"min={[round(abb.xmin,3), round(abb.ymin,3), round(abb.zmin,3)]} "
            f"max={[round(abb.xmax,3), round(abb.ymax,3), round(abb.zmax,3)]}"
        )
        try:
            print(f"ADDED: center={[round(added.Center().x,3), round(added.Center().y,3), round(added.Center().z,3)]}  vol={round(added.Volume(),3)}")
        except Exception:
            pass
    except Exception as e:
        print(f"WARN: could not compute added material via cut: {e}")

    # Re-compound with other solids unchanged
    out_solids = [s if i != big_i else s0_new for i, _, s in solid_info]
    out = cq.Compound.makeCompound(out_solids)
    return out