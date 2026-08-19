def my_cad_function(args):
    import cadquery as cq

    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input
    base = shape.val() if hasattr(shape, "val") else shape

    sols = base.Solids()
    print(f"INFO: imported solids={len(sols)}")
    if len(sols) != 23:
        print("WARN: expected 23 solids per brief; continuing anyway (selection is signature-based)")

    # --- Identify housing body s0 by measured volume and bbox ---
    target_s0_vol = 6519959.249
    target_s0_bbox = (-101.6, 0.0, -127.0, 101.6, 355.6, 127.0)  # xmin,ymin,zmin,xmax,ymax,zmax
    print(
        "TARGETS: s0 vol=%.3f mm^3, s0 bbox min=(%.3f,%.3f,%.3f) max=(%.3f,%.3f,%.3f)"
        % (target_s0_vol, *target_s0_bbox)
    )

    s0_candidates = []
    for i, s in enumerate(sols):
        try:
            v = s.Volume()
            bb = s.BoundingBox()
            dv = abs(v - target_s0_vol)
            db = (
                abs(bb.xmin - target_s0_bbox[0]) + abs(bb.ymin - target_s0_bbox[1]) + abs(bb.zmin - target_s0_bbox[2]) +
                abs(bb.xmax - target_s0_bbox[3]) + abs(bb.ymax - target_s0_bbox[4]) + abs(bb.zmax - target_s0_bbox[5])
            )
            if dv < 1.0 and db < 1e-3:
                s0_candidates.append(i)
        except Exception as e:
            print(f"WARN: failed measuring solid idx={i}: {e}")

    print(f"SELECTED: {len(s0_candidates)} solids matching housing s0 (vol+bbox) idx={s0_candidates}")
    if len(s0_candidates) != 1:
        print("ERROR: could not uniquely identify housing s0; refusing to proceed")
        return shape

    s0_idx = s0_candidates[0]
    s0 = sols[s0_idx]
    s0_bb = s0.BoundingBox()
    print(
        f"SELECTED: 1 solid for housing s0 idx={s0_idx} vol={s0.Volume():.3f} "
        f"bbox=[{s0_bb.xmin:.3f},{s0_bb.ymin:.3f},{s0_bb.zmin:.3f}]..[{s0_bb.xmax:.3f},{s0_bb.ymax:.3f},{s0_bb.zmax:.3f}]"
    )

    # --- Compute common(volume, centroid) of each other body with housing; select mirrored pair ---
    target_common_vol = 1121.985
    target_centers = [cq.Vector(-18.628, 11.43, -121.114), cq.Vector(18.628, 11.43, -121.114)]
    print(f"TARGETS: cord-holder members each have common vol={target_common_vol:.3f} mm^3 with s0")
    print("TARGETS: common centroids at (%.3f,%.3f,%.3f) and (%.3f,%.3f,%.3f)" % (
        target_centers[0].x, target_centers[0].y, target_centers[0].z,
        target_centers[1].x, target_centers[1].y, target_centers[1].z,
    ))

    # Use copies for intersection measurement to avoid any chance of mutating the originals.
    # Output solids will be the original imported solids (except removed ones), untouched.
    s0_m = s0.copy()

    overlaps = []  # (idx, common_vol, common_center, d_to_nearest_target)
    for i, s in enumerate(sols):
        if i == s0_idx:
            continue
        try:
            inter = s.copy().intersect(s0_m)
            cv = inter.Volume() if inter is not None else 0.0
            if cv <= 1e-8:
                continue
            cc = inter.Center()
            d0 = (cc - target_centers[0]).Length
            d1 = (cc - target_centers[1]).Length
            overlaps.append((i, cv, cc, min(d0, d1)))
        except Exception as e:
            print(f"WARN: intersect(measure) failed for idx={i} with s0: {e}")

    overlaps.sort(key=lambda t: t[1], reverse=True)
    print(f"INFO: nonzero overlaps with s0: {len(overlaps)}")
    for (i, cv, cc, dmin) in overlaps[:30]:
        print(f"  OVERLAP: idx={i} common_vol={cv:.3f} center=({cc.x:.3f},{cc.y:.3f},{cc.z:.3f}) d_to_target_center={dmin:.3f}")

    vol_tol = 0.5
    ctr_tol = 0.75
    cord_idxs = []
    for (i, cv, cc, dmin) in overlaps:
        if abs(cv - target_common_vol) <= vol_tol and dmin <= ctr_tol:
            cord_idxs.append(i)

    cord_idxs = sorted(set(cord_idxs))
    print(
        f"SELECTED: {len(cord_idxs)} solids matching cord-holder signature "
        f"(common vol~{target_common_vol:.3f}±{vol_tol}, center within {ctr_tol}mm) idx={cord_idxs}"
    )
    if len(cord_idxs) != 2:
        print("ERROR: did not find exactly 2 cord-holder member solids; refusing to proceed")
        return shape

    # Verify mirrored: one near -X target and one near +X target, and both have the right common signature
    which_targets = []
    for i in cord_idxs:
        inter = sols[i].copy().intersect(s0_m)
        cv = inter.Volume()
        cc = inter.Center()
        d0 = (cc - target_centers[0]).Length
        d1 = (cc - target_centers[1]).Length
        which = 0 if d0 <= d1 else 1
        which_targets.append(which)
        print(
            f"VERIFY: cord member idx={i} common_vol={cv:.3f} center=({cc.x:.3f},{cc.y:.3f},{cc.z:.3f}) "
            f"d_to_-X={d0:.3f} d_to_+X={d1:.3f} nearest_target={'-X' if which==0 else '+X'}"
        )

    if len(set(which_targets)) != 2:
        print("ERROR: selected solids are not one per mirrored target center; refusing to proceed")
        return shape

    # --- Construct output compound: all original solids EXCEPT the two selected bodies ---
    remove_set = set(cord_idxs)
    out_sols = [s for i, s in enumerate(sols) if i not in remove_set]
    print(f"INFO: removing solids idx={cord_idxs}; retained solids={len(out_sols)}")

    # --- Verification: retained solids are not modified (volume+bbox fingerprint), and s0 unchanged ---
    # This is a diagnostic print; geometrically identical is guaranteed by passing originals through unchanged.
    def fp(s):
        bb = s.BoundingBox()
        return (
            round(s.Volume(), 6),
            round(bb.xmin, 6), round(bb.ymin, 6), round(bb.zmin, 6),
            round(bb.xmax, 6), round(bb.ymax, 6), round(bb.zmax, 6),
        )

    orig_fps = [fp(s) for s in sols]
    out_fps = [fp(s) for s in out_sols]
    print(f"VERIFY: fingerprint counts orig={len(orig_fps)} out={len(out_fps)} (expect orig-2)")

    removed_fps = [orig_fps[i] for i in cord_idxs]
    # report removed bodies' own volume/bbox so QA can see they were removed whole
    for ridx in cord_idxs:
        s = sols[ridx]
        bb = s.BoundingBox()
        print(
            f"VERIFY: REMOVED WHOLE BODY idx={ridx} vol={s.Volume():.3f} "
            f"bbox=[{bb.xmin:.3f},{bb.ymin:.3f},{bb.zmin:.3f}]..[{bb.xmax:.3f},{bb.ymax:.3f},{bb.zmax:.3f}]"
        )

    # s0 unchanged checks
    s0_v_after = s0.Volume()
    s0_bb_after = s0.BoundingBox()
    print(
        f"VERIFY: s0 unchanged vol={s0_v_after:.3f} (dV={s0_v_after - target_s0_vol:.6f}) "
        f"bbox=[{s0_bb_after.xmin:.3f},{s0_bb_after.ymin:.3f},{s0_bb_after.zmin:.3f}].."
        f"[{s0_bb_after.xmax:.3f},{s0_bb_after.ymax:.3f},{s0_bb_after.zmax:.3f}]"
    )

    # Ensure removed fingerprints are absent from output fingerprints (best-effort diagnostic)
    missing = sum(1 for rfp in removed_fps if rfp not in out_fps)
    print(f"VERIFY: removed-body fingerprints absent from output: {missing}/{len(removed_fps)}")

    # Ensure no remaining solid matches the cord-holder overlap signature (measurement-only)
    s0_m2 = s0.copy()
    survivors_matching = []
    for i, s in enumerate(out_sols):
        try:
            inter = s.copy().intersect(s0_m2)
            cv = inter.Volume() if inter is not None else 0.0
            if abs(cv - target_common_vol) <= vol_tol:
                cc = inter.Center()
                d0 = (cc - target_centers[0]).Length
                d1 = (cc - target_centers[1]).Length
                if min(d0, d1) <= ctr_tol:
                    survivors_matching.append((i, cv, cc))
        except Exception:
            pass
    print(f"SELECTED: {len(survivors_matching)} remaining solids still matching cord-holder signature (should be 0)")
    for i, cv, cc in survivors_matching:
        print(f"  ERROR: survivor idx(out)={i} common_vol={cv:.3f} center=({cc.x:.3f},{cc.y:.3f},{cc.z:.3f})")
    if survivors_matching:
        print("ERROR: cord-holder signature still present after removal; refusing to proceed")
        return shape

    out = cq.Compound.makeCompound(out_sols)
    return out