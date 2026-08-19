def my_cad_function(args):
    import cadquery as cq

    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input
    base = shape.val() if hasattr(shape, "val") else shape

    # --- Sub-goal constants ---
    target_common_vol = 1121.985
    target_common_ctr = cq.Vector(-18.628, 11.43, -121.114)
    print(
        "TARGETS: delete body s15 identified by overlap with s0: "
        f"common_vol={target_common_vol} mm^3 at ctr=({target_common_ctr.x},{target_common_ctr.y},{target_common_ctr.z})"
    )

    sols = base.Solids()
    print(f"INFO: imported solids={len(sols)}")

    # --- Identify s0 as the largest-volume solid (device body) ---
    vols = [(i, s.Volume()) for i, s in enumerate(sols)]
    vols_sorted = sorted(vols, key=lambda t: t[1], reverse=True)
    for i, v in vols_sorted[:10]:
        bb = sols[i].BoundingBox()
        print(
            f"INFO: solid idx={i} vol={v:.3f} bbox=[({bb.xmin:.3f},{bb.ymin:.3f},{bb.zmin:.3f})..({bb.xmax:.3f},{bb.ymax:.3f},{bb.zmax:.3f})]"
        )

    s0_idx = vols_sorted[0][0] if vols_sorted else None
    if s0_idx is None:
        print("ERROR: no solids found")
        return shape

    s0 = sols[s0_idx]
    s0_vol_before = s0.Volume()
    s0_bb = s0.BoundingBox()
    print(
        "SELECTED: 1 solid for s0 (device body; must remain unchanged)  "
        f"idx={s0_idx} vol={s0_vol_before:.3f} bbox=[({s0_bb.xmin:.3f},{s0_bb.ymin:.3f},{s0_bb.zmin:.3f})..({s0_bb.xmax:.3f},{s0_bb.ymax:.3f},{s0_bb.zmax:.3f})]"
    )

    # --- Find s15 by its measured intersection with s0 ---
    def bboxes_overlap(a, b, eps=0.0):
        return not (
            a.xmax < b.xmin - eps
            or a.xmin > b.xmax + eps
            or a.ymax < b.ymin - eps
            or a.ymin > b.ymax + eps
            or a.zmax < b.zmin - eps
            or a.zmin > b.zmax + eps
        )

    candidates = []
    for i, s in enumerate(sols):
        if i == s0_idx:
            continue
        bb = s.BoundingBox()
        if not bboxes_overlap(bb, s0_bb, eps=0.0):
            continue
        candidates.append(i)

    print(f"SELECTED: {len(candidates)} solids with bbox overlap against s0 for intersection-test idx={candidates}")

    matches = []
    for i in candidates:
        s = sols[i]
        try:
            common = s.intersect(s0)
            cv = common.Volume()
            if cv <= 1e-6:
                continue
            cc = common.Center()
            matches.append((i, cv, cc))
            print(
                f"INFO: common(s0, idx={i}) vol={cv:.3f} ctr=({cc.x:.3f},{cc.y:.3f},{cc.z:.3f})"
            )
        except Exception as e:
            print(f"WARN: intersection failed for idx={i}: {e}")

    # Filter to the named overlap volume and centroid near the NEGATIVE-X location
    vol_tol = 5.0  # mm^3
    ctr_tol = 2.0  # mm

    def ctr_dist(a, b):
        d = a.sub(b)
        return (d.x * d.x + d.y * d.y + d.z * d.z) ** 0.5

    s15_idx = None
    best_score = 1e99
    for i, cv, cc in matches:
        if abs(cv - target_common_vol) > vol_tol:
            continue
        if cc.x > 0:
            continue  # enforce negative-X member
        d = ctr_dist(cc, target_common_ctr)
        if d > ctr_tol:
            continue
        score = d + abs(cv - target_common_vol) * 0.01
        if score < best_score:
            best_score = score
            s15_idx = i

    if s15_idx is None:
        print(
            "ERROR: could not uniquely identify s15 by the specified overlap. "
            "Refusing to delete any body."
        )
        return shape

    # Report the chosen match
    chosen_common = sols[s15_idx].intersect(s0)
    chosen_cv = chosen_common.Volume()
    chosen_cc = chosen_common.Center()
    bb15 = sols[s15_idx].BoundingBox()
    print(
        "SELECTED: 1 solid to delete as s15 (cord-holder member)  "
        f"idx={s15_idx}  common_vol={chosen_cv:.3f} (target {target_common_vol})  "
        f"common_ctr=({chosen_cc.x:.3f},{chosen_cc.y:.3f},{chosen_cc.z:.3f})  "
        f"dCtr={ctr_dist(chosen_cc, target_common_ctr):.3f}  "
        f"bbox=[({bb15.xmin:.3f},{bb15.ymin:.3f},{bb15.zmin:.3f})..({bb15.xmax:.3f},{bb15.ymax:.3f},{bb15.zmax:.3f})]"
    )

    # --- Delete s15 completely by recompounding all other solids unchanged ---
    out_sols = [s for i, s in enumerate(sols) if i != s15_idx]
    print(f"INFO: deleted idx={s15_idx}; solids count {len(sols)} -> {len(out_sols)}")

    out = cq.Compound.makeCompound(out_sols)

    # --- Verification: s0 must be unchanged, and s15 must be absent ---
    # Find s0 in output by the exact same solid object if present; else by volume proximity
    out_base = out
    out_sols2 = out_base.Solids()

    # locate s0: prefer identity match
    out_s0_idx = None
    for i, s in enumerate(out_sols2):
        if s.wrapped is s0.wrapped:
            out_s0_idx = i
            break
    if out_s0_idx is None:
        # fallback by closest volume
        out_s0_idx = min(range(len(out_sols2)), key=lambda i: abs(out_sols2[i].Volume() - s0_vol_before))
        print(f"WARN: s0 identity not preserved (unexpected); fallback by closest volume picked idx={out_s0_idx}")

    out_s0 = out_sols2[out_s0_idx]
    s0_vol_after = out_s0.Volume()
    out_s0_bb = out_s0.BoundingBox()
    print(
        "VERIFY: s0 volume before/after  "
        f"before={s0_vol_before:.6f} after={s0_vol_after:.6f} dV={s0_vol_after - s0_vol_before:.6f}"
    )
    print(
        "VERIFY: s0 bbox before/after  "
        f"before=[({s0_bb.xmin:.6f},{s0_bb.ymin:.6f},{s0_bb.zmin:.6f})..({s0_bb.xmax:.6f},{s0_bb.ymax:.6f},{s0_bb.zmax:.6f})] "
        f"after=[({out_s0_bb.xmin:.6f},{out_s0_bb.ymin:.6f},{out_s0_bb.zmin:.6f})..({out_s0_bb.xmax:.6f},{out_s0_bb.ymax:.6f},{out_s0_bb.zmax:.6f})]"
    )

    # Verify that no remaining solid has the same overlap signature with s0
    remaining_matches = 0
    for i, s in enumerate(out_sols2):
        if i == out_s0_idx:
            continue
        try:
            common = s.intersect(out_s0)
            cv = common.Volume()
            if abs(cv - target_common_vol) <= vol_tol:
                cc = common.Center()
                if cc.x < 0 and ctr_dist(cc, target_common_ctr) <= ctr_tol:
                    remaining_matches += 1
                    print(
                        f"ERROR: overlap signature still present with solid idx={i} common_vol={cv:.3f} ctr=({cc.x:.3f},{cc.y:.3f},{cc.z:.3f})"
                    )
        except Exception:
            pass

    print(f"VERIFY: remaining solids with the s15 overlap signature (should be 0) = {remaining_matches}")

    return out