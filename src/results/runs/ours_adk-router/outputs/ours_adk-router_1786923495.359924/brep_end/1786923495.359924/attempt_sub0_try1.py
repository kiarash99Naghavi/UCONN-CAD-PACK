def my_cad_function(args):
    import cadquery as cq
    import math

    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input
    base = shape.val() if hasattr(shape, "val") else shape

    # --- Resolve solids (keep all untouched except we will ADD a new solid) ---
    solids = base.Solids()
    print(f"SELECTED: {len(solids)} solids from imported STEP")
    for i, s in enumerate(solids):
        bb = s.BoundingBox()
        print(f"  solid s{i}: bbox=({bb.xmin:.3f},{bb.ymin:.3f},{bb.zmin:.3f})..({bb.xmax:.3f},{bb.ymax:.3f},{bb.zmax:.3f})")

    if len(solids) < 3:
        print("ERROR: Expected 3 solids (s0,s1,s2) but found fewer; returning input")
        return shape

    s1 = solids[1]

    # --- Resolve the referenced faces on the GLOBAL face list (per instruction) ---
    faces = base.Faces()
    print(f"SELECTED: {len(faces)} faces on base shape (global face indexing)")

    ref_face_idxs = [38, 39, 40, 42, 43, 44, 45]
    resolved = []
    for idx in ref_face_idxs:
        if idx >= len(faces):
            print(f"SELECTED: 0 faces for face_idx #{idx} (out of range)")
            continue
        f = faces[idx]
        c = f.Center()
        a = f.Area()
        # normalAt() takes no args
        n = f.normalAt()
        print(f"SELECTED: 1 face for face_idx #{idx}  center=[{c.x:.3f},{c.y:.3f},{c.z:.3f}]  area={a:.3f}  normal=[{n.x:.3f},{n.y:.3f},{n.z:.3f}]")
        resolved.append((idx, f))

    if not any(idx == 38 for idx, _ in resolved):
        print("ERROR: Could not resolve face #38; returning input")
        return shape

    f38 = dict(resolved)[38]
    bb38 = f38.BoundingBox()
    r = max(abs(bb38.xmin), abs(bb38.xmax), abs(bb38.zmin), abs(bb38.zmax)) + 1.0  # margin
    print(f"face #38 bbox: x[{bb38.xmin:.3f},{bb38.xmax:.3f}] z[{bb38.zmin:.3f},{bb38.zmax:.3f}] -> central region half-span r={r:.3f} mm")

    # --- Rotation sign check and final angle selection ---
    target_a1 = 151.75
    target_a2 = 331.75
    existing_arms = [33.5, 213.5, 90.0, 270.0]

    def ang_from_vec_xz(v):
        # angle measured from +X toward +Z
        ang = math.degrees(math.atan2(v.z, v.x))
        ang = (ang + 360.0) % 360.0
        return ang

    def circ_diff(a, b):
        d = (a - b + 180.0) % 360.0 - 180.0
        return abs(d)

    def arms_from_rotation(rot_deg):
        a = math.radians(rot_deg)
        # rotate +Z (0,0,1) about +Y by rot_deg (right-hand rule)
        vx = math.sin(a)
        vz = math.cos(a)
        ang1 = ang_from_vec_xz(cq.Vector(vx, 0, vz))
        ang2 = (ang1 + 180.0) % 360.0
        return ang1, ang2

    # Try +61.75 first per instruction, but reverse if it conflicts (within ~10°) with existing blades
    rot_try = 61.75
    a1, a2 = arms_from_rotation(rot_try)
    min_diff_try = min(circ_diff(a1, ea) for ea in existing_arms)
    print(f"ROTATION TRY: +{rot_try:.2f} deg -> arm angles ~{a1:.2f} / {a2:.2f} deg; closest existing-arm diff={min_diff_try:.2f} deg")

    if min_diff_try <= 10.0:
        rot_try = -61.75
        a1, a2 = arms_from_rotation(rot_try)
        min_diff_try = min(circ_diff(a1, ea) for ea in existing_arms)
        print(f"ROTATION REVERSED: {rot_try:.2f} deg -> arm angles ~{a1:.2f} / {a2:.2f} deg; closest existing-arm diff={min_diff_try:.2f} deg")

    print(f"FINAL: rotation={rot_try:.2f} deg; expected landing ~{target_a1:.2f}/{target_a2:.2f} deg; achieved ~{a1:.2f}/{a2:.2f} deg")

    # --- Duplicate (copy) s1 blade and rotate ---
    blade_rot = s1.rotate((0, 0, 0), (0, 1, 0), rot_try)

    # --- Thin ONLY the central crossing portion to 0.42mm in Y, centered at Y=6.35 (6.14..6.56) ---
    # Use a compact XZ region based on face #38 footprint (r) to localize the thinning.
    y_center = 6.35
    y_thk = 0.42
    y_min = y_center - y_thk / 2.0  # 6.14
    y_max = y_center + y_thk / 2.0  # 6.56

    # Region box (tall in Y) to isolate central crossing portion
    region_ylen = 40.0
    region_box = cq.Solid.makeBox(2 * r, region_ylen, 2 * r, cq.Vector(-r, y_center - region_ylen / 2.0, -r))
    slab_box = cq.Solid.makeBox(2 * r, y_thk, 2 * r, cq.Vector(-r, y_min, -r))

    print(f"CENTRAL TARGET Y LIMITS: {y_min:.2f}..{y_max:.2f} mm (center {y_center:.2f}, thk {y_thk:.2f})")

    central_before = blade_rot.intersect(region_box)
    # Tool = everything in central region EXCEPT the kept thin slab
    tool_remove = central_before.cut(slab_box)

    blade_thinned = blade_rot.cut(tool_remove)

    # --- Self-check: measure central region Y extents after thinning ---
    central_after = blade_thinned.intersect(region_box)
    bb_ca = central_after.BoundingBox()
    print(
        "CENTRAL AFTER (within region_box): "
        f"y[{bb_ca.ymin:.3f},{bb_ca.ymax:.3f}]  (delta to target ymin={bb_ca.ymin - y_min:+.3f}, ymax={bb_ca.ymax - y_max:+.3f})"
    )

    bb_new = blade_thinned.BoundingBox()
    print(
        "NEW BLADE bbox: "
        f"({bb_new.xmin:.3f},{bb_new.ymin:.3f},{bb_new.zmin:.3f})..({bb_new.xmax:.3f},{bb_new.ymax:.3f},{bb_new.zmax:.3f})"
    )

    # Added-material diagnostics (new solid relative to original compound)
    # Here the new blade is an additional solid (not a boolean fuse into existing), so 'added' == blade_thinned.
    c_added = blade_thinned.Center()
    print(f"ADDED (new blade) center=[{c_added.x:.3f},{c_added.y:.3f},{c_added.z:.3f}]")

    # --- Return as a new compound with the original 3 solids untouched plus the new blade solid ---
    out = cq.Compound.makeCompound([solids[0], solids[1], solids[2], blade_thinned])
    return out