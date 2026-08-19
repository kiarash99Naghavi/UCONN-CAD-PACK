def my_cad_function(args):
    import cadquery as cq

    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input
    base = shape.val() if hasattr(shape, "val") else shape

    def bb_tuple(bb):
        return {
            "xmin": bb.xmin, "xmax": bb.xmax,
            "ymin": bb.ymin, "ymax": bb.ymax,
            "zmin": bb.zmin, "zmax": bb.zmax,
            "xlen": bb.xlen, "ylen": bb.ylen, "zlen": bb.zlen,
            "center": (bb.center.x, bb.center.y, bb.center.z),
        }

    def v_tuple(v):
        return (v.x, v.y, v.z)

    # --- numbers named by the sub-goal ---
    target_overlap_vol = 2695.005
    target_overlap_centroid = (-91.661, 325.266, 26.352)
    print(f"TARGET: overlap vol={target_overlap_vol} mm^3 at centroid={target_overlap_centroid} mm")

    sols = base.Solids()
    print(f"SELECTED: {len(sols)} solids from imported STEP")
    if len(sols) <= 8:
        print("SELECTED: 0 solids for s8 (index 8 missing)  <-- BUG")
        return shape

    s0 = sols[0]
    s8 = sols[8]
    print("SELECTED: 1 solid for coffeepot body s0 (index 0)")
    print("SELECTED: 1 solid for handle body s8 (index 8)")

    bb0_before = s0.BoundingBox()
    bb8_before = s8.BoundingBox()
    print("s0 bbox BEFORE:", bb_tuple(bb0_before))
    print("s8 bbox BEFORE:", bb_tuple(bb8_before))

    # Measure current common
    common_before = s0.intersect(s8)
    vol_common_before = common_before.Volume() if common_before is not None else 0.0
    cen_common_before = common_before.Center() if common_before is not None else cq.Vector(0, 0, 0)
    print(f"MEASURED: common(s0,s8) BEFORE vol={vol_common_before:.3f} mm^3 center={v_tuple(cen_common_before)}")
    print(
        "DELTA vs target: dV=%.3f, dC=(%.3f, %.3f, %.3f)"
        % (
            vol_common_before - target_overlap_vol,
            cen_common_before.x - target_overlap_centroid[0],
            cen_common_before.y - target_overlap_centroid[1],
            cen_common_before.z - target_overlap_centroid[2],
        )
    )

    # --- Edit: remove exactly the s0∩s8 common material from s8, preserving s0 ---
    s8_cut = s8.cut(s0)

    # Verify common becomes zero
    common_after = s0.intersect(s8_cut)
    vol_common_after = common_after.Volume() if common_after is not None else 0.0
    cen_common_after = common_after.Center() if common_after is not None else cq.Vector(0, 0, 0)
    print(f"MEASURED: common(s0,s8) AFTER  vol={vol_common_after:.6f} mm^3 center={v_tuple(cen_common_after)}")

    # Verify bounding boxes unchanged for both bodies
    bb0_after = s0.BoundingBox()
    bb8_after = s8_cut.BoundingBox()
    print("s0 bbox AFTER :", bb_tuple(bb0_after))
    print("s8 bbox AFTER :", bb_tuple(bb8_after))

    def bb_delta(a, b):
        return {
            "dxmin": b.xmin - a.xmin, "dxmax": b.xmax - a.xmax,
            "dymin": b.ymin - a.ymin, "dymax": b.ymax - a.ymax,
            "dzmin": b.zmin - a.zmin, "dzmax": b.zmax - a.zmax,
        }

    print("CHECK: s0 bbox delta:", bb_delta(bb0_before, bb0_after))
    print("CHECK: s8 bbox delta:", bb_delta(bb8_before, bb8_after))

    # Placement self-check for the removed material (what changed on s8)
    removed_from_s8 = s8.cut(s8_cut)
    vol_removed = removed_from_s8.Volume() if removed_from_s8 is not None else 0.0
    cen_removed = removed_from_s8.Center() if removed_from_s8 is not None else cq.Vector(0, 0, 0)
    bb_removed = removed_from_s8.BoundingBox() if removed_from_s8 is not None else None
    print(f"SELF-CHECK: removed_from_s8 vol={vol_removed:.3f} mm^3 center={v_tuple(cen_removed)}")
    if bb_removed:
        print("SELF-CHECK: removed_from_s8 bbox:", bb_tuple(bb_removed))
        print(
            "SELF-CHECK DELTA vs target centroid: (%.3f, %.3f, %.3f)"
            % (
                cen_removed.x - target_overlap_centroid[0],
                cen_removed.y - target_overlap_centroid[1],
                cen_removed.z - target_overlap_centroid[2],
            )
        )

    # Recompound with only s8 replaced; all other solids untouched
    out_sols = list(sols)
    out_sols[8] = s8_cut
    out = cq.Compound.makeCompound(out_sols)

    return out