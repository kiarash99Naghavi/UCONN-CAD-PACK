def my_cad_function(args):
    import cadquery as cq
    from math import sqrt

    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input
    base = shape.val() if hasattr(shape, "val") else shape

    # --- Index alignment sanity check: resolve one of the referenced faces ---
    try:
        f419 = base.Faces()[419]
        print(f"CHECK: face[419] area={f419.Area():.3f} center={tuple(round(v,3) for v in f419.Center().toTuple())}")
    except Exception as e:
        print(f"WARN: could not resolve face[419] for index alignment check: {e}")

    sols = base.Solids()
    print(f"INFO: imported solids={len(sols)}")

    # --- Find the existing horizontal button body (s39) by its bbox/center ---
    expected_src_center = (-237.0, 340.0, 318.5)

    def bb_center_tuple(s):
        bb = s.BoundingBox()
        return ((bb.xmin + bb.xmax) / 2.0, (bb.ymin + bb.ymax) / 2.0, (bb.zmin + bb.zmax) / 2.0)

    def dist(a, b):
        return sqrt((a[0]-b[0])**2 + (a[1]-b[1])**2 + (a[2]-b[2])**2)

    best_i = None
    best_d = 1e99
    for i, s in enumerate(sols):
        c = bb_center_tuple(s)
        d = dist(c, expected_src_center)
        if d < best_d:
            best_d = d
            best_i = i

    if best_i is None or best_d > 5.0:
        print(f"SELECTED: 0 solids for button body (nearest distance {best_d:.3f} mm too large)")
        return shape

    button = sols[best_i]
    bb_btn = button.BoundingBox()
    c_btn = bb_center_tuple(button)
    size_btn = (bb_btn.xlen, bb_btn.ylen, bb_btn.zlen)
    print(f"SELECTED: 1 solid for button body  solid_idx={best_i} bb_center={tuple(round(v,3) for v in c_btn)} dist_to_expected={best_d:.3f} mm")
    print(f"INFO: button bbox min={tuple(round(v,3) for v in (bb_btn.xmin, bb_btn.ymin, bb_btn.zmin))} max={tuple(round(v,3) for v in (bb_btn.xmax, bb_btn.ymax, bb_btn.zmax))} size={tuple(round(v,3) for v in size_btn)}")

    # Source placement check
    src_delta = (c_btn[0]-expected_src_center[0], c_btn[1]-expected_src_center[1], c_btn[2]-expected_src_center[2])
    print(f"CHECK: source center target={expected_src_center} achieved={tuple(round(v,3) for v in c_btn)} delta={tuple(round(v,3) for v in src_delta)}")

    # --- Duplicate twice: +20mm Y and -20mm Y from source ---
    targets = {
        "upper": (-237.0, 360.0, 318.5),
        "lower": (-237.0, 320.0, 318.5),
    }

    def make_copy_to_target(src_solid, target_center, label):
        # initial translate by nominal pitch from expected source
        nominal_vec = (target_center[0] - expected_src_center[0], target_center[1] - expected_src_center[1], target_center[2] - expected_src_center[2])
        cp = src_solid.copy().translate(nominal_vec)
        c1 = bb_center_tuple(cp)
        d1 = (target_center[0]-c1[0], target_center[1]-c1[1], target_center[2]-c1[2])
        print(f"CHECK: {label} after nominal translate achieved_center={tuple(round(v,3) for v in c1)} target={target_center} delta={tuple(round(v,3) for v in d1)}")

        # Correct in the same attempt if off by more than 0.5mm in any axis
        if max(abs(d1[0]), abs(d1[1]), abs(d1[2])) > 0.5:
            cp = cp.translate(d1)
            c2 = bb_center_tuple(cp)
            d2 = (target_center[0]-c2[0], target_center[1]-c2[1], target_center[2]-c2[2])
            print(f"CHECK: {label} corrected achieved_center={tuple(round(v,3) for v in c2)} target={target_center} delta={tuple(round(v,3) for v in d2)}")
            return cp, c2, d2
        return cp, c1, d1

    upper_solid, upper_center, upper_delta = make_copy_to_target(button, targets["upper"], "upper")
    lower_solid, lower_center, lower_delta = make_copy_to_target(button, targets["lower"], "lower")

    # --- Congruency / horizontal alignment checks (translation-only should preserve) ---
    def bb_size_tuple(s):
        bb = s.BoundingBox()
        return (bb.xlen, bb.ylen, bb.zlen)

    size_src = bb_size_tuple(button)
    size_up = bb_size_tuple(upper_solid)
    size_lo = bb_size_tuple(lower_solid)

    print(f"CHECK: bbox sizes (xlen,ylen,zlen) src={tuple(round(v,3) for v in size_src)} upper={tuple(round(v,3) for v in size_up)} lower={tuple(round(v,3) for v in size_lo)}")
    print(f"CHECK: X-alignment centers x src/up/lo = {c_btn[0]:.3f}, {upper_center[0]:.3f}, {lower_center[0]:.3f}")

    # --- Recompound: keep all original solids untouched, add two copies ---
    out = cq.Compound.makeCompound(list(sols) + [upper_solid, lower_solid])

    # --- Added-material isolation self-check ---
    try:
        added = out.cut(base)
        add_sols = added.Solids() if hasattr(added, "Solids") else []
        print(f"SELECTED: {len(add_sols)} solids in (out \ base) for added-material check")
        bb_added = added.BoundingBox()
        print(f"CHECK: added bbox min={tuple(round(v,3) for v in (bb_added.xmin, bb_added.ymin, bb_added.zmin))} max={tuple(round(v,3) for v in (bb_added.xmax, bb_added.ymax, bb_added.zmax))}")
    except Exception as e:
        print(f"WARN: added-material isolation check failed: {e}")

    # Final center report
    print(f"RESULT CENTERS (bbox centers): src={tuple(round(v,3) for v in c_btn)} upper={tuple(round(v,3) for v in upper_center)} lower={tuple(round(v,3) for v in lower_center)}")

    return out