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
        "TARGETS: housing vol=%.3f bbox%s..%s; remove body with common(housing)=%.3f at %s; retain two bodies with common=%.3f at %s and %s"
        % (
            target_housing_vol,
            str(list(target_housing_bbmin)),
            str(list(target_housing_bbmax)),
            target_common_central_vol,
            str(list(target_common_central_ctr)),
            target_common_mirror_vol,
            str(list(target_common_mirror_ctrs[0])),
            str(list(target_common_mirror_ctrs[1])),
        )
    )

    sols = list(base.Solids())
    print(f"INFO: imported solids count={len(sols)} (expected 23)")
    if len(sols) != 23:
        print("WARN: expected 23 solids from geometry index; continuing anyway")

    def bb_close(bb, mn, mx, tol=0.05):
        return (
            abs(bb.xmin - mn[0]) <= tol
            and abs(bb.ymin - mn[1]) <= tol
            and abs(bb.zmin - mn[2]) <= tol
            and abs(bb.xmax - mx[0]) <= tol
            and abs(bb.ymax - mx[1]) <= tol
            and abs(bb.zmax - mx[2]) <= tol
        )

    def dist(a, b):
        return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2) ** 0.5

    # --- Identify housing independently by volume + bbox ---
    housing_candidates = []
    for i, s in enumerate(sols):
        try:
            v = s.Volume()
            bb = s.BoundingBox()
        except Exception:
            continue
        if abs(v - target_housing_vol) < 1e-3 and bb_close(bb, target_housing_bbmin, target_housing_bbmax, tol=0.05):
            housing_candidates.append(i)

    print(f"SELECTED: {len(housing_candidates)} solids matching housing vol+bbox criteria idx={housing_candidates}")
    if len(housing_candidates) != 1:
        # Fallback to closest-by-volume, but still print diagnostics
        best_i = None
        best_dv = 1e99
        for i, s in enumerate(sols):
            dv = abs(s.Volume() - target_housing_vol)
            if dv < best_dv:
                best_dv = dv
                best_i = i
        print(f"ERROR: housing not uniquely identified. Fallback closest-by-volume idx={best_i} dV={best_dv:.3f}. Refusing to proceed.")
        return shape

    housing_idx = housing_candidates[0]
    housing = sols[housing_idx]
    bb = housing.BoundingBox()
    print(
        "SELECTED: 1 solid as housing idx=%d vol=%.3f bbox=[%.3f,%.3f,%.3f]..[%.3f,%.3f,%.3f]"
        % (housing_idx, housing.Volume(), bb.xmin, bb.ymin, bb.zmin, bb.xmax, bb.ymax, bb.zmax)
    )

    # --- Measure housing-common for each other imported body (no edits; intersects are probes) ---
    overlaps = []
    for i, s in enumerate(sols):
        if i == housing_idx:
            continue
        try:
            c = s.intersect(housing)
            cv = c.Volume()
            if cv <= 1e-9:
                continue
            cc = c.Center()
            ctr = (cc.x, cc.y, cc.z)
            overlaps.append((i, cv, ctr))
        except Exception as e:
            print(f"WARN: intersect probe failed for solid idx={i}: {e}")

    overlaps.sort(key=lambda t: t[1], reverse=True)
    print(f"INFO: solids with nonzero housing-common = {len(overlaps)}")
    for (i, cv, (x, y, z)) in overlaps:
        print(f"  COMMON: idx={i} vol={cv:.3f} centroid=({x:.3f},{y:.3f},{z:.3f})")

    # --- Select the SINGLE central cordholder candidate by exact target overlap ---
    central_idx = None
    central_dbg = []
    for (i, cv, ctr) in overlaps:
        dv = abs(cv - target_common_central_vol)
        dc = dist(ctr, target_common_central_ctr)
        central_dbg.append((i, cv, ctr, dv, dc))

    # Best by volume then centroid
    central_dbg.sort(key=lambda t: (t[3], t[4]))
    if central_dbg:
        i, cv, ctr, dv, dc = central_dbg[0]
        print(
            "SELECTED: 1 solid as CENTRAL cordholder candidate (probe only) idx=%d commonVol=%.3f commonCtr=(%.3f,%.3f,%.3f) dV=%.6f dCtr=%.6f"
            % (i, cv, ctr[0], ctr[1], ctr[2], dv, dc)
        )
        # Hard gate to avoid deleting wrong body
        if dv <= 0.5 and dc <= 0.5:
            central_idx = i
        else:
            print(
                "ERROR: best central-candidate does not match target tightly; refusing to suppress anything. "
                f"Got vol={cv:.6f} ctr={ctr}"
            )
            return shape
    else:
        print("ERROR: no solids have nonzero common with housing; cannot identify cordholder")
        return shape

    # --- Explicitly locate and retain the mirrored pair (must remain present, unchanged) ---
    mirror_found = []
    for (i, cv, ctr) in overlaps:
        if abs(cv - target_common_mirror_vol) > 0.5:
            continue
        if min(dist(ctr, target_common_mirror_ctrs[0]), dist(ctr, target_common_mirror_ctrs[1])) <= 0.5:
            mirror_found.append((i, cv, ctr))

    mirror_found.sort(key=lambda t: t[0])
    print(f"SELECTED: {len(mirror_found)} solids as mirrored-pair retain candidates (by common vol+centroid)")
    for (i, cv, (x, y, z)) in mirror_found:
        print(f"  RETAIN: idx={i} commonVol={cv:.3f} commonCtr=({x:.3f},{y:.3f},{z:.3f})")

    if len(mirror_found) != 2:
        print("ERROR: did not find both mirrored retain bodies within tight tolerance; refusing to suppress")
        return shape

    if central_idx in {i for (i, _, _) in mirror_found}:
        print(f"ERROR: central cordholder candidate idx={central_idx} collides with mirrored retain set; refusing")
        return shape

    print(f"ACTION: suppress/remove imported body at document/body level: idx={central_idx}")

    # --- Build an Assembly from ORIGINAL solids (no booleans, no recomposition/healing) ---
    # This avoids makeCompound() ordering/renumbering issues and keeps all retained bodies byte-identical.
    asm = cq.Assembly(name="root")

    kept_indices = [i for i in range(len(sols)) if i != central_idx]
    print(f"INFO: keeping {len(kept_indices)} solids (expected 22); removed=[{central_idx}]")

    # Pre-verify: record volumes of all input solids (for unchanged-geometry check)
    in_vols = {i: sols[i].Volume() for i in kept_indices}

    for i in kept_indices:
        asm.add(sols[i], name=f"s{i}")

    # --- Post-verify by re-probing assembly solids vs housing ---
    out_sols = list(asm.toCompound().Solids())
    print(f"VERIFY: output solids count={len(out_sols)} (expected 22)")

    # Re-find housing in output by strict criteria (should be unchanged)
    out_housing = None
    out_housing_idx = None
    for j, s in enumerate(out_sols):
        try:
            v = s.Volume()
            bb = s.BoundingBox()
        except Exception:
            continue
        if abs(v - target_housing_vol) < 1e-3 and bb_close(bb, target_housing_bbmin, target_housing_bbmax, tol=0.05):
            out_housing = s
            out_housing_idx = j
            break

    if out_housing is None:
        print("ERROR: could not re-identify housing in output; refusing")
        return asm

    # Scan overlaps vs housing again
    found_central_like = []
    found_mirror_like = []
    for j, s in enumerate(out_sols):
        if j == out_housing_idx:
            continue
        try:
            c = s.intersect(out_housing)
            cv = c.Volume()
            if cv <= 1e-9:
                continue
            cc = c.Center()
            ctr = (cc.x, cc.y, cc.z)
            if abs(cv - target_common_central_vol) <= 0.5 and dist(ctr, target_common_central_ctr) <= 0.5:
                found_central_like.append((j, cv, ctr))
            if abs(cv - target_common_mirror_vol) <= 0.5 and min(dist(ctr, target_common_mirror_ctrs[0]), dist(ctr, target_common_mirror_ctrs[1])) <= 0.5:
                found_mirror_like.append((j, cv, ctr))
        except Exception:
            pass

    print(f"VERIFY: central-overlap (1817.805 @ [0,31.75,-82.536]) matches in output = {len(found_central_like)} (expected 0)")
    for (j, cv, (x, y, z)) in found_central_like:
        print(f"  UNEXPECTED: outSolidIdx={j} commonVol={cv:.3f} commonCtr=({x:.3f},{y:.3f},{z:.3f})")

    print(f"VERIFY: mirrored-overlap (1121.985 @ x=±18.628,y=11.43,z=-121.114) matches in output = {len(found_mirror_like)} (expected 2)")
    for (j, cv, (x, y, z)) in found_mirror_like:
        print(f"  PRESENT: outSolidIdx={j} commonVol={cv:.3f} commonCtr=({x:.3f},{y:.3f},{z:.3f})")

    # --- Unchanged geometry check (volumes) for retained bodies ---
    # We cannot map output-solid indices back to original indices reliably without names,
    # so we instead ensure that the MULTISET of retained volumes matches exactly.
    out_vols = sorted([s.Volume() for s in out_sols])
    in_vols_sorted = sorted([in_vols[i] for i in kept_indices])
    if len(out_vols) != len(in_vols_sorted):
        print("ERROR: retained solids count mismatch in volume check")
    else:
        max_abs_dv = max(abs(a - b) for a, b in zip(out_vols, in_vols_sorted)) if out_vols else 0.0
        print(f"VERIFY: retained-volumes multiset max|dV|={max_abs_dv:.9f} (target 0.0)")
        if max_abs_dv > 1e-6:
            print("ERROR: at least one retained body's volume differs; geometry likely altered (should not happen)")

    return asm