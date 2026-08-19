def my_cad_function(args):
    import cadquery as cq

    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input
    base = shape.val() if hasattr(shape, "val") else shape

    sols = base.Solids()
    print(f"INFO: imported solids={len(sols)}")
    if len(sols) <= 17:
        print("ERROR: expected at least 18 solids so s17 exists (0-based index 17)")
        return shape

    # --- Anchor verification by explicit face_idx from the provided geometry index ---
    all_faces = base.Faces()
    all_edges = base.Edges()

    # s17 Y-bounding planar faces (from index): #370 at Y=279.4, #368 at Y=304.8
    for fi, expected_y in [(370, 279.4), (368, 304.8)]:
        try:
            f = all_faces[fi]
            c = f.Center()
            n = f.normalAt()
            print(f"CHECK: face_idx={fi} center={tuple(round(v,3) for v in (c.x,c.y,c.z))} normal={tuple(round(v,3) for v in (n.x,n.y,n.z))}  (expected Y~{expected_y})  dY={round(c.y-expected_y,3)}")
        except Exception as e:
            print(f"WARN: could not resolve face_idx={fi}: {e}")

    # s17 mounting cylinders (from index): cylindrical faces #361 and #362 (r=7.62, axis ~X)
    for fi in [361, 362]:
        try:
            f = all_faces[fi]
            c = f.Center()
            # normalAt is not axis; still useful sanity check + center
            print(f"CHECK: cyl face_idx={fi} center={tuple(round(v,3) for v in (c.x,c.y,c.z))} (expected Y near 291.7)")
        except Exception as e:
            print(f"WARN: could not resolve cyl face_idx={fi}: {e}")

    # s17 U-shaped BSPLINE faces (from index): #365 and #366 centered near Y=292.1
    for fi in [365, 366]:
        try:
            f = all_faces[fi]
            c = f.Center()
            print(f"CHECK: bspline face_idx={fi} center={tuple(round(v,3) for v in (c.x,c.y,c.z))} (expected Y near 292.1)")
        except Exception as e:
            print(f"WARN: could not resolve bspline face_idx={fi}: {e}")

    # --- Select s17 by solid index (per geometry index body labeling) ---
    s17 = sols[17]
    bb17 = s17.BoundingBox()
    ycenter17 = (bb17.ymin + bb17.ymax) / 2.0
    print(
        "SELECTED: 1 solid for stand body s17  "
        f"bboxY=[{bb17.ymin:.3f},{bb17.ymax:.3f}] yCenter={ycenter17:.3f}  "
        f"bboxSize=[{bb17.xlen:.3f},{bb17.ylen:.3f},{bb17.zlen:.3f}] vol={s17.Volume():.3f}"
    )

    # --- Create rigid translated copy (no trimming/subtraction) ---
    tvec = cq.Vector(0.0, -228.6, 0.0)
    s17_copy = s17.moved(cq.Location(tvec))
    bb_copy = s17_copy.BoundingBox()
    ycenter_copy = (bb_copy.ymin + bb_copy.ymax) / 2.0

    # Congruence check: volume and bbox size must match original
    vol17 = s17.Volume()
    vol_copy = s17_copy.Volume()
    print(
        "VERIFY: congruence (copy vs s17)  "
        f"dVol={vol_copy - vol17:.6f}  "
        f"dSize=[{(bb_copy.xlen-bb17.xlen):.6f},{(bb_copy.ylen-bb17.ylen):.6f},{(bb_copy.zlen-bb17.zlen):.6f}]"
    )

    # Placement self-check per sub-goal
    print(
        "PLACEMENT: translated copy bboxY="
        f"[{bb_copy.ymin:.3f},{bb_copy.ymax:.3f}] (target 50.8..76.2)  "
        f"yCenter={ycenter_copy:.3f} (target ~63.5)  "
        f"dYCenter={ycenter_copy-63.5:.3f}"
    )

    # --- Recompound: keep all pre-existing bodies unchanged; add translated copy as separate body ---
    base_comp = cq.Compound.makeCompound(sols)
    out = cq.Compound.makeCompound(sols + [s17_copy])

    # Isolate added material (should be exactly the new copy since it doesn't overlap)
    try:
        added = out.cut(base_comp)
        bb_added = added.BoundingBox()
        print(
            "ADDED BODY: isolated via out.cut(base)  "
            f"vol={added.Volume():.3f} center={tuple(round(v,3) for v in (added.Center().x, added.Center().y, added.Center().z))}  "
            f"bboxY=[{bb_added.ymin:.3f},{bb_added.ymax:.3f}]"
        )
    except Exception as e:
        print(f"WARN: could not isolate added body via cut: {e}")

    # Confirm original s17 and other solids were not modified (we never booleaned them)
    bb17_post = sols[17].BoundingBox()
    print(
        "CONFIRM: original s17 unchanged (by construction)  "
        f"bboxY=[{bb17_post.ymin:.3f},{bb17_post.ymax:.3f}] vol={sols[17].Volume():.3f}"
    )

    return out