def my_cad_function(args):
    import cadquery as cq

    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input
    base = shape.val() if hasattr(shape, "val") else shape

    solids = base.Solids()
    print(f"SELECTED: {len(solids)} solids in STEP")

    target_si = 17  # s17 per prompt
    if len(solids) <= target_si:
        print(f"ERROR: expected at least {target_si+1} solids, got {len(solids)}")
        return shape

    s17 = solids[target_si]
    bb17 = s17.BoundingBox()
    print(f"SELECTED: 1 solid for edit: s17 (solid index {target_si})  bbox=({bb17.xmin:.3f},{bb17.ymin:.3f},{bb17.zmin:.3f})..({bb17.xmax:.3f},{bb17.ymax:.3f},{bb17.zmax:.3f})")

    # Resolve and sanity-print the referenced faces on the ORIGINAL imported compound
    faces = base.Faces()
    need_face_idxs = [361, 362, 368, 370, 365, 366]
    ok = True
    for fi in need_face_idxs:
        if fi >= len(faces):
            print(f"ERROR: base.Faces()[{fi}] out of range (nFaces={len(faces)})")
            ok = False
        else:
            fc = faces[fi].Center()
            print(f"RESOLVED: face #{fi} center=({fc.x:.3f},{fc.y:.3f},{fc.z:.3f})")
    if not ok:
        return shape

    # Targets from prompt
    mirror_y = 177.8
    target_center_y = 63.5
    target_ymin = 50.8
    target_ymax = 76.2
    target_flat_z = -115.0
    print(f"TARGETS: mirror plane Y={mirror_y}  copied stand centerY~{target_center_y}  Y extent~[{target_ymin}..{target_ymax}]  support flat at Z={target_flat_z}")

    # 1) Mirror the stand body (s17) across plane Y=177.8 (i.e. XZ plane offset)
    try:
        mirrored = s17.mirror(mirrorPlane="XZ", basePointVector=(0, mirror_y, 0))
    except Exception as e:
        print("ERROR: Shape.mirror failed:", e)
        return shape

    bbm = mirrored.BoundingBox()
    print(f"MIRRORED (raw): center=({mirrored.Center().x:.3f},{mirrored.Center().y:.3f},{mirrored.Center().z:.3f})  Y extent=[{bbm.ymin:.3f}..{bbm.ymax:.3f}]")

    # Correct mirror placement if materially off
    center_y_raw = 0.5 * (bbm.ymin + bbm.ymax)
    dy = target_center_y - center_y_raw
    if abs(dy) > 1.0:
        print(f"ADJUST: mirrored stand centerY off by {dy:.3f}mm -> translating mirrored by dy={dy:.3f}")
        mirrored = mirrored.translate((0, dy, 0))
        bbm = mirrored.BoundingBox()
        print(f"MIRRORED (after translate): Y extent=[{bbm.ymin:.3f}..{bbm.ymax:.3f}] centerY={(0.5*(bbm.ymin+bbm.ymax)):.3f}")

    # 2) Trim ONLY the copied stand below Z=-115.0 by cutting away a huge box occupying z < -115
    # Box spans x,y well beyond part; z from very low up to -115.
    box_below = cq.Solid.makeBox(1200, 1200, 2000, cq.Vector(-600, -600, target_flat_z - 2000))  # top at -115
    trimmed = mirrored.cut(box_below)
    bbt = trimmed.BoundingBox()
    print(f"TRIMMED copied stand: zmin={bbt.zmin:.3f} (target {target_flat_z})  Y extent=[{bbt.ymin:.3f}..{bbt.ymax:.3f}]")

    # If still below target_flat_z, re-trim with a slightly higher box (shouldn't happen, but be robust)
    if bbt.zmin < target_flat_z - 0.25:
        # increase cut box height upward by the shortfall + 1mm
        short = (target_flat_z - bbt.zmin) + 1.0
        print(f"ADJUST: copied stand still extends below Z={target_flat_z} by {target_flat_z-bbt.zmin:.3f}mm -> re-cut with box raised by {short:.3f}mm")
        box_below2 = cq.Solid.makeBox(1200, 1200, 2000 + short, cq.Vector(-600, -600, target_flat_z - (2000 + short)))
        trimmed = mirrored.cut(box_below2)
        bbt = trimmed.BoundingBox()
        print(f"TRIMMED (after adjust): zmin={bbt.zmin:.3f} (target {target_flat_z})")

    # 3) Fuse trimmed copy back into s17
    edited_s17 = s17.fuse(trimmed)

    # Self-check: isolate added material
    try:
        added = edited_s17.cut(s17)
        bba = added.BoundingBox()
        center_added = added.Center()
        center_y = 0.5 * (bba.ymin + bba.ymax)
        print(
            "ADDED (copied stand) CHECK:",
            f"vol={added.Volume():.3f}",
            f"center=({center_added.x:.3f},{center_added.y:.3f},{center_added.z:.3f})",
            f"centerY_from_bbox={center_y:.3f}",
            f"Y extent=[{bba.ymin:.3f}..{bba.ymax:.3f}]",
            f"support-flat zmin={bba.zmin:.3f}"
        )

        # If materially off in Y, correct by shifting the copy before fusion and recompute.
        off = max(abs(center_y - target_center_y), abs(bba.ymin - target_ymin), abs(bba.ymax - target_ymax))
        if off > 1.0:
            dy2 = target_center_y - center_y
            print(f"ADJUST: added stand Y placement off materially (max off {off:.3f}mm). Applying dy={dy2:.3f} to copy and rebuilding union.")
            mirrored2 = mirrored.translate((0, dy2, 0))
            trimmed2 = mirrored2.cut(box_below)
            edited_s17 = s17.fuse(trimmed2)
            added2 = edited_s17.cut(s17)
            bba2 = added2.BoundingBox()
            center_added2 = added2.Center()
            center_y2 = 0.5 * (bba2.ymin + bba2.ymax)
            print(
                "ADDED (after adjust) CHECK:",
                f"vol={added2.Volume():.3f}",
                f"center=({center_added2.x:.3f},{center_added2.y:.3f},{center_added2.z:.3f})",
                f"centerY_from_bbox={center_y2:.3f}",
                f"Y extent=[{bba2.ymin:.3f}..{bba2.ymax:.3f}]",
                f"support-flat zmin={bba2.zmin:.3f}"
            )
    except Exception as e:
        print("WARNING: could not compute added material diagnostic (edited_s17.cut(s17)):", e)

    # Recompound all solids, only replacing s17
    out = cq.Compound.makeCompound([s for i, s in enumerate(solids) if i != target_si] + [edited_s17])

    # Volume delta against original compound
    try:
        print("DELTA", out.Volume() - base.Volume())
    except Exception as e:
        print("WARNING: could not compute DELTA volume:", e)

    return out