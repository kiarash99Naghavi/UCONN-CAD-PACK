def my_cad_function(args):
    import cadquery as cq

    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input
    base = shape.val() if hasattr(shape, "val") else shape

    # --- List every number the sub-goal names ---
    y_new_span = (50.8, 76.2)
    y_orig_span = (279.4, 304.8)
    z_cut = -115.0
    print(f"TARGETS: new-stand Y={y_new_span[0]}..{y_new_span[1]} mm, original-stand Y={y_orig_span[0]}..{y_orig_span[1]} mm, support Z={z_cut} mm")

    sols = base.Solids()
    print(f"INFO: imported solids={len(sols)}")
    if len(sols) < 21:
        print("ERROR: expected at least 21 solids (s0..s20) in current state")

    # --- Find the two stand solids by their Y spans (most robust to index shifts) ---
    def find_by_yspan(target_ymin, target_ymax, tol=1.0):
        matches = []
        for i, s in enumerate(sols):
            bb = s.BoundingBox()
            if abs(bb.ymin - target_ymin) <= tol and abs(bb.ymax - target_ymax) <= tol:
                matches.append(i)
        return matches

    idx_orig_candidates = find_by_yspan(y_orig_span[0], y_orig_span[1], tol=1.0)
    idx_new_candidates = find_by_yspan(y_new_span[0], y_new_span[1], tol=1.0)

    print(f"SELECTED: {len(idx_orig_candidates)} solids matching original-stand Y span ~{y_orig_span} idx={idx_orig_candidates}")
    print(f"SELECTED: {len(idx_new_candidates)} solids matching new-stand Y span ~{y_new_span} idx={idx_new_candidates}")

    def pick_best(cands, target_center_y):
        if not cands:
            return None
        best = None
        best_d = 1e9
        for i in cands:
            bb = sols[i].BoundingBox()
            cy = (bb.ymin + bb.ymax) / 2.0
            d = abs(cy - target_center_y)
            if d < best_d:
                best_d = d
                best = i
        return best

    idx_orig = pick_best(idx_orig_candidates, (y_orig_span[0] + y_orig_span[1]) / 2.0)
    idx_new = pick_best(idx_new_candidates, (y_new_span[0] + y_new_span[1]) / 2.0)

    # Fallback: if exact-span match failed, choose nearest-by-center among all solids
    def fallback_nearest_by_ycenter(target_center_y):
        best = None
        best_d = 1e9
        for i, s in enumerate(sols):
            bb = s.BoundingBox()
            cy = (bb.ymin + bb.ymax) / 2.0
            d = abs(cy - target_center_y)
            if d < best_d:
                best_d = d
                best = i
        return best

    if idx_orig is None:
        idx_orig = fallback_nearest_by_ycenter((y_orig_span[0] + y_orig_span[1]) / 2.0)
        print(f"WARN: no exact original-stand Y-span match; fallback picked idx={idx_orig}")
    if idx_new is None:
        idx_new = fallback_nearest_by_ycenter((y_new_span[0] + y_new_span[1]) / 2.0)
        print(f"WARN: no exact new-stand Y-span match; fallback picked idx={idx_new}")

    if idx_orig == idx_new:
        print(f"ERROR: stand selection collapsed to same solid idx={idx_orig}; refusing to cut to avoid damaging unrelated bodies")
        return shape

    s_orig = sols[idx_orig]
    s_new = sols[idx_new]

    bb_orig = s_orig.BoundingBox()
    bb_new = s_new.BoundingBox()
    print(
        "SELECTED: 1 solid for original stand (to trim)  "
        f"idx={idx_orig} bboxY=[{bb_orig.ymin:.3f},{bb_orig.ymax:.3f}] bboxZ=[{bb_orig.zmin:.3f},{bb_orig.zmax:.3f}]"
    )
    print(
        "SELECTED: 1 solid for new stand (to trim)  "
        f"idx={idx_new} bboxY=[{bb_new.ymin:.3f},{bb_new.ymax:.3f}] bboxZ=[{bb_new.zmin:.3f},{bb_new.zmax:.3f}]"
    )

    # --- Build the horizontal half-space cut tool: a huge box occupying Z < -115 ---
    # Remove only stand material below Z=-115.
    z_low = -2000.0
    Lx, Ly = 2000.0, 2000.0
    Lz = z_cut - z_low  # top at z_cut
    cutter = cq.Solid.makeBox(Lx, Ly, Lz, cq.Vector(-Lx / 2.0, -Ly / 2.0, z_low))
    bb_cutter = cutter.BoundingBox()
    print(
        "TOOL: cutter box for half-space Z<-115 (will be cut away)  "
        f"bboxZ=[{bb_cutter.zmin:.3f},{bb_cutter.zmax:.3f}] expect [{z_low:.3f},{z_cut:.3f}]"
    )

    # --- Cut EACH stand separately ---
    try:
        s_orig_trim = s_orig.cut(cutter)
        print("OK: cut original stand with Z<-115 half-space")
    except Exception as e:
        print(f"ERROR: cutting original stand failed: {e}")
        return shape

    try:
        s_new_trim = s_new.cut(cutter)
        print("OK: cut new stand with Z<-115 half-space")
    except Exception as e:
        print(f"ERROR: cutting new stand failed: {e}")
        return shape

    # --- Verification helpers ---
    def report_support(solid, name):
        bb = solid.BoundingBox()
        print(
            f"VERIFY: {name} bboxZmin={bb.zmin:.6f} target={z_cut:.3f} dZ={bb.zmin - z_cut:.6f}  "
            f"bboxZ=[{bb.zmin:.3f},{bb.zmax:.3f}]"
        )

        # Look for the lowest horizontal planar face at Z=-115
        flats = []
        faces = solid.Faces()
        for fi, f in enumerate(faces):
            try:
                if f.geomType() != "PLANE":
                    continue
                n = f.normalAt()
                c = f.Center()
                if abs(n.z) > 0.98 and abs(c.z - z_cut) < 0.5:
                    flats.append((fi, f.Area(), c, n))
            except Exception:
                continue

        print(f"SELECTED: {len(flats)} planar horizontal faces near Z={z_cut:.3f} on {name}")
        flats.sort(key=lambda t: t[1], reverse=True)
        for fi, a, c, n in flats[:6]:
            print(
                f"  FACE: idx={fi} area={a:.3f} center=({c.x:.3f},{c.y:.3f},{c.z:.3f}) "
                f"normal=({n.x:.3f},{n.y:.3f},{n.z:.3f})"
            )

        if not flats:
            print(f"WARN: {name} has no detected horizontal planar face near Z={z_cut:.3f}; cut may have been a no-op or tolerance too tight")

        # Ensure no material remains below Z=-115 (bbox check)
        if bb.zmin < z_cut - 1e-3:
            print(f"ERROR: {name} still has material below Z={z_cut:.3f} (bboxZmin={bb.zmin:.6f})")
        return bb.zmin

    zmin_orig = report_support(s_orig_trim, "original stand (trimmed)")
    zmin_new = report_support(s_new_trim, "new stand (trimmed)")
    print(f"COPLANAR CHECK: support zmin delta (new-orig) = {zmin_new - zmin_orig:.6f} (target 0.0)")

    # --- Recompound: replace only the two stand solids; all others pass through untouched ---
    out_sols = list(sols)
    out_sols[idx_orig] = s_orig_trim
    out_sols[idx_new] = s_new_trim
    print(f"INFO: modified solids indices = [{idx_orig}, {idx_new}] ; all other indices untouched")

    out = cq.Compound.makeCompound(out_sols)
    return out