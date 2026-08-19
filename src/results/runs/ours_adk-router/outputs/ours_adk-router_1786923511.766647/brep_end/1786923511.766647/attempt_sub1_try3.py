def my_cad_function(args):
    import cadquery as cq

    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input
    base = shape.val() if hasattr(shape, "val") else shape

    # --- Targets ---
    z_cut = -115.0
    y_span_orig = (279.4, 304.8)   # context (original stand region)
    y_span_new = (50.8, 76.2)      # context (new stand region)
    print(f"TARGETS: support plane Z={z_cut} ; orig stand expected Y~{y_span_orig[0]}..{y_span_orig[1]} ; new stand expected Y~{y_span_new[0]}..{y_span_new[1]}")

    sols = list(base.Solids())
    print(f"INFO: imported solids={len(sols)}")

    # --- Build keep-halfspace (Z >= z_cut) as a huge box ---
    Lx, Ly = 4000.0, 4000.0
    z_high = 2000.0
    keep_box = cq.Solid.makeBox(Lx, Ly, z_high - z_cut, cq.Vector(-Lx/2.0, -Ly/2.0, z_cut))
    bbk = keep_box.BoundingBox()
    print(f"TOOL: keep_box (intersect to keep Z>=cut) bboxZ=[{bbk.zmin:.3f},{bbk.zmax:.3f}] (expect [{z_cut:.3f},{z_high:.3f}])")

    # --- Helpers ---
    def bb_str(bb):
        return f"X[{bb.xmin:.3f},{bb.xmax:.3f}] Y[{bb.ymin:.3f},{bb.ymax:.3f}] Z[{bb.zmin:.3f},{bb.zmax:.3f}]"

    def find_solid_by_yspan(target_span, tol=1.0):
        ty0, ty1 = target_span
        cands = []
        for i, s in enumerate(sols):
            bb = s.BoundingBox()
            if abs(bb.ymin - ty0) <= tol and abs(bb.ymax - ty1) <= tol:
                cands.append(i)
        return cands

    def pick_single_solid(result_shape, name):
        # result_shape may be Solid or Compound; ensure we return ONE solid (largest by volume)
        try:
            res_sols = list(result_shape.Solids())
        except Exception:
            res_sols = []
        if not res_sols:
            # If OCC returns a single solid without being iterable, fall back
            if isinstance(result_shape, cq.Solid):
                res_sols = [result_shape]
        print(f"SELECTED: {len(res_sols)} solids in {name} boolean result")
        for j, ss in enumerate(res_sols[:10]):
            bb = ss.BoundingBox()
            try:
                v = ss.Volume()
            except Exception:
                v = float('nan')
            print(f"  CANDIDATE[{j}]: vol={v:.3f} bb={bb_str(bb)}")
        if not res_sols:
            print(f"ERROR: {name} boolean produced no solids; keeping original solid unchanged")
            return None
        # pick max volume
        best = max(res_sols, key=lambda s: s.Volume())
        bb = best.BoundingBox()
        print(f"PICKED: largest solid for {name}: vol={best.Volume():.3f} bb={bb_str(bb)}")
        return best

    def report_support_faces(solid, name):
        bb = solid.BoundingBox()
        print(f"VERIFY: {name} bboxZmin={bb.zmin:.6f} target={z_cut:.3f} dZ={bb.zmin - z_cut:.6f} bb={bb_str(bb)}")
        flats = []
        for fi, f in enumerate(solid.Faces()):
            try:
                if f.geomType() != "PLANE":
                    continue
                n = f.normalAt()
                c = f.Center()
                if abs(n.z) > 0.98 and abs(c.z - z_cut) < 0.5:
                    flats.append((fi, f.Area(), c, n))
            except Exception:
                continue
        flats.sort(key=lambda t: t[1], reverse=True)
        print(f"SELECTED: {len(flats)} horizontal planar faces near Z={z_cut:.3f} on {name}")
        for fi, a, c, n in flats[:6]:
            print(f"  FACE: idx={fi} area={a:.3f} center=({c.x:.3f},{c.y:.3f},{c.z:.3f}) normal=({n.x:.3f},{n.y:.3f},{n.z:.3f})")
        if bb.zmin < z_cut - 1e-3:
            print(f"ERROR: {name} still has material below Z={z_cut:.3f} (bboxZmin={bb.zmin:.6f})")
        return bb

    # --- Select exactly the two stand bodies to edit: prefer indices s17 and s22, but verify by Y-span ---
    # Geometry index naming indicates s<N> corresponds to solids()[N]
    idx_orig_pref = 17
    idx_new_pref = 22

    def resolve_index(pref_idx, target_span, label):
        chosen = None
        if pref_idx is not None and pref_idx < len(sols):
            bb = sols[pref_idx].BoundingBox()
            ok = (abs(bb.ymin - target_span[0]) <= 2.0 and abs(bb.ymax - target_span[1]) <= 2.0)
            print(f"CHECK: preferred {label} idx={pref_idx} bbY=[{bb.ymin:.3f},{bb.ymax:.3f}] expect~{target_span} ok={ok}")
            if ok:
                chosen = pref_idx
        if chosen is None:
            cands = find_solid_by_yspan(target_span, tol=2.0)
            print(f"SELECTED: {len(cands)} solids matching {label} Y-span~{target_span} (tol=2.0) idx={cands}")
            if cands:
                # choose the one closest to span center (should all match), then by volume
                tyc = (target_span[0] + target_span[1]) / 2.0
                cands.sort(key=lambda i: (abs(((sols[i].BoundingBox().ymin + sols[i].BoundingBox().ymax) / 2.0) - tyc), -sols[i].Volume()))
                chosen = cands[0]
        if chosen is None:
            # last resort: nearest by Y-center
            tyc = (target_span[0] + target_span[1]) / 2.0
            chosen = min(range(len(sols)), key=lambda i: abs(((sols[i].BoundingBox().ymin + sols[i].BoundingBox().ymax) / 2.0) - tyc))
            bb = sols[chosen].BoundingBox()
            print(f"WARN: no Y-span match for {label}; fallback chose idx={chosen} bbY=[{bb.ymin:.3f},{bb.ymax:.3f}]")
        return chosen

    idx_orig = resolve_index(idx_orig_pref, y_span_orig, "original stand")
    idx_new = resolve_index(idx_new_pref, y_span_new, "new stand")

    if idx_orig == idx_new:
        print(f"ERROR: resolved same solid for both stands idx={idx_orig}; forcing different by choosing next best for new stand")
        # brute: pick any other solid with the new-span ycenter distance
        tyc = (y_span_new[0] + y_span_new[1]) / 2.0
        others = [i for i in range(len(sols)) if i != idx_orig]
        idx_new = min(others, key=lambda i: abs(((sols[i].BoundingBox().ymin + sols[i].BoundingBox().ymax) / 2.0) - tyc))

    bb_o = sols[idx_orig].BoundingBox()
    bb_n = sols[idx_new].BoundingBox()
    print(f"SELECTED: 1 solid for original stand edit idx={idx_orig} bb={bb_str(bb_o)}")
    print(f"SELECTED: 1 solid for new stand edit      idx={idx_new} bb={bb_str(bb_n)}")

    # --- Trim each stand by intersecting with keep half-space (Z >= -115) ---
    edited = {}
    for idx, label in [(idx_orig, "original stand"), (idx_new, "new stand")]:
        s = sols[idx]
        try:
            inter = s.intersect(keep_box)
            print(f"OK: intersected {label} with keep_box (keep Z>= {z_cut})")
        except Exception as e:
            print(f"ERROR: intersect failed on {label}: {e} ; leaving it unchanged")
            edited[idx] = s
            continue

        picked = pick_single_solid(inter, f"{label} (intersect)")
        if picked is None:
            edited[idx] = s
            continue

        # One more safety intersect (idempotent) to shave numerical fuzz
        try:
            picked2 = picked.intersect(keep_box)
            picked2_s = pick_single_solid(picked2, f"{label} (re-intersect)")
            if picked2_s is not None:
                picked = picked2_s
        except Exception as e:
            print(f"WARN: re-intersect failed on {label}: {e} (using first intersect result)")

        edited[idx] = picked

    # --- Verify support flats and no material below ---
    bb_orig_after = report_support_faces(edited[idx_orig], "original stand (trimmed)")
    bb_new_after = report_support_faces(edited[idx_new], "new stand (trimmed)")
    print(f"COPLANAR CHECK: zmin(new-orig) = {bb_new_after.zmin - bb_orig_after.zmin:.6f} (target 0.0)")

    # --- Recompound: replace only the two edited stand solids; keep all others untouched ---
    out_sols = list(sols)
    out_sols[idx_orig] = edited[idx_orig]
    out_sols[idx_new] = edited[idx_new]
    print(f"INFO: modified solids indices=[{idx_orig}, {idx_new}] ; output solids count will remain {len(out_sols)}")

    out = cq.Compound.makeCompound(out_sols)
    print(f"INFO: out compound solids={len(list(out.Solids()))} (should equal {len(sols)})")
    return out