def my_cad_function(args):
    import cadquery as cq

    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input
    base = shape.val() if hasattr(shape, "val") else shape

    sols = base.Solids()
    print(f"INFO: imported solids={len(sols)}")

    # --- Identify housing body s0 by measured volume and bbox ---
    target_s0_vol = 6519959.249
    target_s0_bbox = (-101.6, 0.0, -127.0, 101.6, 355.6, 127.0)
    print(f"TARGETS: s0 vol={target_s0_vol} mm^3, s0 bbox min=({target_s0_bbox[0]},{target_s0_bbox[1]},{target_s0_bbox[2]}) max=({target_s0_bbox[3]},{target_s0_bbox[4]},{target_s0_bbox[5]})")

    s0_candidates = []
    for i, s in enumerate(sols):
        try:
            v = s.Volume()
            bb = s.BoundingBox()
            dv = abs(v - target_s0_vol)
            dbb = (
                abs(bb.xmin - target_s0_bbox[0]) + abs(bb.ymin - target_s0_bbox[1]) + abs(bb.zmin - target_s0_bbox[2]) +
                abs(bb.xmax - target_s0_bbox[3]) + abs(bb.ymax - target_s0_bbox[4]) + abs(bb.zmax - target_s0_bbox[5])
            )
            if dv < 1.0 and dbb < 1e-3:
                s0_candidates.append(i)
        except Exception as e:
            print(f"WARN: failed measuring solid idx={i}: {e}")

    print(f"SELECTED: {len(s0_candidates)} solids matching s0 vol+bbox idx={s0_candidates}")
    if len(s0_candidates) != 1:
        print("ERROR: could not uniquely identify housing s0; refusing to proceed")
        return shape

    s0_idx = s0_candidates[0]
    s0 = sols[s0_idx]
    s0_bb = s0.BoundingBox()
    print(
        f"SELECTED: 1 solid for housing s0 idx={s0_idx} "
        f"vol={s0.Volume():.3f} bbox=[{s0_bb.xmin:.3f},{s0_bb.ymin:.3f},{s0_bb.zmin:.3f}]..[{s0_bb.xmax:.3f},{s0_bb.ymax:.3f},{s0_bb.zmax:.3f}]"
    )

    # --- Compute common(volume, centroid) of each other body with housing; select mirrored pair ---
    target_common_vol = 1121.985
    target_centers = [cq.Vector(-18.628, 11.43, -121.114), cq.Vector(18.628, 11.43, -121.114)]
    print(f"TARGETS: cord-holder members each have common vol={target_common_vol} mm^3 with s0")
    print(f"TARGETS: common centroids at {[(v.x, v.y, v.z) for v in target_centers]}")

    overlaps = []  # (i, common_vol, center_vector, d_to_best_center)
    for i, s in enumerate(sols):
        if i == s0_idx:
            continue
        try:
            inter = s.intersect(s0)
            cv = inter.Volume() if inter is not None else 0.0
            if cv <= 1e-6:
                continue
            cc = inter.Center()
            d0 = (cc - target_centers[0]).Length
            d1 = (cc - target_centers[1]).Length
            dmin = min(d0, d1)
            overlaps.append((i, cv, cc, dmin))
        except Exception as e:
            print(f"WARN: intersect failed for idx={i} with s0: {e}")

    overlaps.sort(key=lambda t: t[1], reverse=True)
    print(f"INFO: nonzero overlaps with s0: {len(overlaps)}")
    for (i, cv, cc, dmin) in overlaps[:20]:
        print(f"  OVERLAP: idx={i} common_vol={cv:.3f} center=({cc.x:.3f},{cc.y:.3f},{cc.z:.3f}) d_to_target_center={dmin:.3f}")

    # Filter to the two intended cord-holder members using both volume and centroid proximity
    vol_tol = 1.0
    ctr_tol = 1.0
    cord_idxs = []
    for (i, cv, cc, dmin) in overlaps:
        if abs(cv - target_common_vol) <= vol_tol and dmin <= ctr_tol:
            cord_idxs.append(i)

    cord_idxs = sorted(set(cord_idxs))
    print(f"SELECTED: {len(cord_idxs)} solids matching cord-holder signature (common vol+centroid) idx={cord_idxs}")

    if len(cord_idxs) != 2:
        print("ERROR: did not find exactly 2 cord-holder member solids; refusing to proceed")
        return shape

    # Sanity-check: ensure one is near -X target and one near +X target
    def nearest_target(cc):
        d0 = (cc - target_centers[0]).Length
        d1 = (cc - target_centers[1]).Length
        return 0 if d0 <= d1 else 1

    info = []
    for i in cord_idxs:
        inter = sols[i].intersect(s0)
        cc = inter.Center()
        cv = inter.Volume()
        which = nearest_target(cc)
        info.append((i, cv, cc, which))

    for i, cv, cc, which in info:
        print(
            f"VERIFY: cord member idx={i} common_vol={cv:.3f} center=({cc.x:.3f},{cc.y:.3f},{cc.z:.3f}) "
            f"nearest_target={'-X' if which==0 else '+X'}"
        )

    if len(set([which for _, _, _, which in info])) != 2:
        print("ERROR: selected solids are not one per mirrored target center; refusing to proceed")
        return shape

    # --- Construct output compound: all original solids EXCEPT the two selected bodies ---
    out_sols = [s for i, s in enumerate(sols) if i not in set(cord_idxs)]
    print(f"INFO: removing solids idx={cord_idxs}; retained solids={len(out_sols)}")

    out = cq.Compound.makeCompound(out_sols)

    # --- Verification: housing remains identical (volume + bbox) ---
    # (We do not boolean on s0, so these should match exactly aside from floating precision.)
    s0_v_after = s0.Volume()
    s0_bb_after = s0.BoundingBox()
    print(
        f"VERIFY: s0 unchanged vol={s0_v_after:.3f} (dV={s0_v_after - target_s0_vol:.6f}) "
        f"bbox=[{s0_bb_after.xmin:.3f},{s0_bb_after.ymin:.3f},{s0_bb_after.zmin:.3f}].."
        f"[{s0_bb_after.xmax:.3f},{s0_bb_after.ymax:.3f},{s0_bb_after.zmax:.3f}]"
    )

    return out