def my_cad_function(args):
    import cadquery as cq

    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input
    base = shape.val() if hasattr(shape, "val") else shape

    # --- Targets from sub-goal ---
    target_housing_vol = 6519959.249
    target_housing_bbmin = (-101.6, 0.0, -127.0)
    target_housing_bbmax = (101.6, 355.6, 127.0)

    target_common_central_vol = 1817.805
    target_common_central_ctr = (0.0, 31.75, -82.536)

    target_common_mirror_vol = 1121.985
    target_common_mirror_ctrs = [(-18.628, 11.43, -121.114), (18.628, 11.43, -121.114)]

    print(
        "TARGETS: housing vol=6519959.249 bbox[-101.6,0.0,-127.0]..[101.6,355.6,127.0]; "
        "delete body with common(housing)=1817.805 @ [0.0,31.75,-82.536]; "
        "retain bodies with common(housing)=1121.985 @ x=±18.628, y=11.43, z=-121.114"
    )

    sols = base.Solids()
    print(f"INFO: imported solids count = {len(sols)} (expected 23)")

    # --- Identify housing independently by volume + bbox ---
    def bb_close(bb, bbmin, bbmax, tol=0.5):
        return (
            abs(bb.xmin - bbmin[0]) <= tol and abs(bb.ymin - bbmin[1]) <= tol and abs(bb.zmin - bbmin[2]) <= tol and
            abs(bb.xmax - bbmax[0]) <= tol and abs(bb.ymax - bbmax[1]) <= tol and abs(bb.zmax - bbmax[2]) <= tol
        )

    housing_idx = None
    housing_candidates = []
    for i, s in enumerate(sols):
        try:
            v = s.Volume()
            bb = s.BoundingBox()
        except Exception:
            continue
        if abs(v - target_housing_vol) < 5.0 and bb_close(bb, target_housing_bbmin, target_housing_bbmax, tol=0.5):
            housing_candidates.append(i)

    print(f"SELECTED: {len(housing_candidates)} solids matching housing vol+bbox criteria idx={housing_candidates}")
    if len(housing_candidates) != 1:
        # Fallback: choose closest by volume then bbox distance
        best = None
        best_score = 1e99
        for i, s in enumerate(sols):
            try:
                v = s.Volume()
                bb = s.BoundingBox()
            except Exception:
                continue
            score = abs(v - target_housing_vol)
            # add bbox corner deltas as weak tie-break
            score += 0.1 * (
                abs(bb.xmin - target_housing_bbmin[0]) + abs(bb.ymin - target_housing_bbmin[1]) + abs(bb.zmin - target_housing_bbmin[2]) +
                abs(bb.xmax - target_housing_bbmax[0]) + abs(bb.ymax - target_housing_bbmax[1]) + abs(bb.zmax - target_housing_bbmax[2])
            )
            if score < best_score:
                best_score = score
                best = i
        housing_idx = best
        print(f"WARN: housing not uniquely matched; fallback picked idx={housing_idx} score={best_score:.3f}")
    else:
        housing_idx = housing_candidates[0]

    housing = sols[housing_idx]
    hv = housing.Volume()
    hbb = housing.BoundingBox()
    print(
        f"SELECTED: 1 solid as housing idx={housing_idx} vol={hv:.3f} "
        f"bbox=[{hbb.xmin:.3f},{hbb.ymin:.3f},{hbb.zmin:.3f}]..[{hbb.xmax:.3f},{hbb.ymax:.3f},{hbb.zmax:.3f}]"
    )

    # --- Measure housing-common overlaps for every other solid ---
    overlaps = []  # (i, commonVol, commonCtr)
    for i, s in enumerate(sols):
        if i == housing_idx:
            continue
        try:
            common = s.intersect(housing)
            cv = common.Volume()
            if cv <= 1e-6:
                continue
            cc = common.Center()
            overlaps.append((i, cv, (cc.x, cc.y, cc.z)))
        except Exception as e:
            print(f"WARN: intersect failed for solid idx={i}: {e}")

    overlaps.sort(key=lambda t: t[1], reverse=True)
    print(f"INFO: solids with nonzero housing-common = {len(overlaps)}")
    for (i, cv, (x, y, z)) in overlaps[:20]:
        print(f"  COMMON: idx={i} vol={cv:.3f} centroid=({x:.3f},{y:.3f},{z:.3f})")

    # --- Locate the CENTRAL cordholder candidate by (common volume, centroid) ---
    def dist(a, b):
        return ((a[0]-b[0])**2 + (a[1]-b[1])**2 + (a[2]-b[2])**2) ** 0.5

    central_idx = None
    central_dbg = []
    for (i, cv, ctr) in overlaps:
        dv = abs(cv - target_common_central_vol)
        dc = dist(ctr, target_common_central_ctr)
        central_dbg.append((i, cv, ctr, dv, dc))

    # choose best by weighted score
    best = None
    best_score = 1e99
    for (i, cv, ctr, dv, dc) in central_dbg:
        score = dv + 10.0 * dc
        if score < best_score:
            best_score = score
            best = (i, cv, ctr, dv, dc)
    if best:
        central_idx = best[0]
        print(
            "SELECTED: 1 solid as CENTRAL cordholder candidate to suppress "
            f"idx={central_idx} commonVol={best[1]:.3f} commonCtr=({best[2][0]:.3f},{best[2][1]:.3f},{best[2][2]:.3f}) "
            f"dV={best[3]:.3f} dCtr={best[4]:.3f} score={best_score:.3f}"
        )
    else:
        print("ERROR: no overlaps found at all; cannot identify cordholder")
        return shape

    # Hard gate: ensure it truly matches the 1817.805mm^3 @ [0,31.75,-82.536] target
    if abs(best[1] - target_common_central_vol) > 25.0 or dist(best[2], target_common_central_ctr) > 2.0:
        print(
            "ERROR: best central candidate does not match target within tolerance; "
            f"got commonVol={best[1]:.3f} (target {target_common_central_vol}), "
            f"commonCtr={best[2]} (target {target_common_central_ctr}); refusing to delete"
        )
        return shape

    # --- Explicitly locate and retain the mirrored pair bodies (common=1121.985 @ x=±18.628, y=11.43, z=-121.114) ---
    mirrored_found = []
    for (i, cv, ctr) in overlaps:
        if abs(cv - target_common_mirror_vol) > 25.0:
            continue
        # match either centroid
        if min(dist(ctr, target_common_mirror_ctrs[0]), dist(ctr, target_common_mirror_ctrs[1])) <= 2.0:
            mirrored_found.append((i, cv, ctr))

    mirrored_found.sort(key=lambda t: t[0])
    print(f"SELECTED: {len(mirrored_found)} solids as mirrored-pair retain candidates (by common vol+centroid)")
    for (i, cv, (x, y, z)) in mirrored_found:
        print(f"  RETAIN: idx={i} commonVol={cv:.3f} commonCtr=({x:.3f},{y:.3f},{z:.3f})")

    if len(mirrored_found) < 2:
        print("WARN: did not find both mirrored 1121.985mm^3-overlap bodies within tolerance")

    # Ensure we are NOT deleting one of the mirrored pair
    mirrored_idx_set = {i for (i, _, _) in mirrored_found}
    if central_idx in mirrored_idx_set:
        print(f"ERROR: central candidate idx={central_idx} overlaps mirrored retain set; refusing to delete")
        return shape

    # --- Suppress (remove) the central cordholder solid at the document/body level ---
    kept = []
    removed = []
    for i, s in enumerate(sols):
        if i == central_idx:
            removed.append(i)
        else:
            kept.append(s)

    print(f"ACTION: suppressing/removing solid idx={central_idx} (central cordholder).")
    print(f"INFO: removed count={len(removed)} kept count={len(kept)}")
    if len(kept) != len(sols) - 1:
        print("ERROR: kept solids count mismatch; refusing to proceed")
        return shape

    # --- Geometry unchanged check for retained bodies (volumes should be identical since we didn't edit) ---
    for j, s in enumerate(sols):
        if j == central_idx:
            continue
        v0 = s.Volume()
        v1 = kept[j if j < central_idx else j - 1].Volume()
        dv = v1 - v0
        if abs(dv) > 1e-6:
            print(f"ERROR: retained solid idx={j} volume changed unexpectedly dV={dv:.9f}")
        # keep this concise: only flag problems

    out = cq.Compound.makeCompound(kept)

    # --- Post-verify: central 1817.805 overlap body is absent, mirrored 1121.985 are still present ---
    out_sols = out.Solids()
    print(f"VERIFY: output solids count={len(out_sols)} (expected 22)")

    # Re-identify housing in output by same criteria (it should still exist)
    out_housing_idx = None
    for i, s in enumerate(out_sols):
        try:
            v = s.Volume()
            bb = s.BoundingBox()
        except Exception:
            continue
        if abs(v - target_housing_vol) < 5.0 and bb_close(bb, target_housing_bbmin, target_housing_bbmax, tol=0.5):
            out_housing_idx = i
            break
    if out_housing_idx is None:
        # fallback closest by volume
        best_i = None
        best_dv = 1e99
        for i, s in enumerate(out_sols):
            dv = abs(s.Volume() - target_housing_vol)
            if dv < best_dv:
                best_dv = dv
                best_i = i
        out_housing_idx = best_i
        print(f"WARN: could not re-find housing by strict criteria; fallback picked idx={out_housing_idx}")

    out_housing = out_sols[out_housing_idx]

    # Scan overlaps vs housing again
    found_central_like = []
    found_mirror_like = []
    for i, s in enumerate(out_sols):
        if i == out_housing_idx:
            continue
        try:
            c = s.intersect(out_housing)
            cv = c.Volume()
            if cv <= 1e-6:
                continue
            cc = c.Center()
            ctr = (cc.x, cc.y, cc.z)
            if abs(cv - target_common_central_vol) < 25.0 and dist(ctr, target_common_central_ctr) < 2.0:
                found_central_like.append((i, cv, ctr))
            if abs(cv - target_common_mirror_vol) < 25.0 and min(dist(ctr, target_common_mirror_ctrs[0]), dist(ctr, target_common_mirror_ctrs[1])) < 2.0:
                found_mirror_like.append((i, cv, ctr))
        except Exception:
            pass

    print(f"VERIFY: central-overlap (1817.805 @ [0,31.75,-82.536]) matches in output = {len(found_central_like)} (expected 0)")
    for (i, cv, (x, y, z)) in found_central_like:
        print(f"  UNEXPECTED: idx={i} commonVol={cv:.3f} commonCtr=({x:.3f},{y:.3f},{z:.3f})")

    print(f"VERIFY: mirrored-overlap (1121.985 @ x=±18.628,y=11.43,z=-121.114) matches in output = {len(found_mirror_like)} (expected 2)")
    for (i, cv, (x, y, z)) in found_mirror_like:
        print(f"  PRESENT: idx={i} commonVol={cv:.3f} commonCtr=({x:.3f},{y:.3f},{z:.3f})")

    return out