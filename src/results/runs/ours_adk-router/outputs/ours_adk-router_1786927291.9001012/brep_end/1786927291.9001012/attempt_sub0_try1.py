def my_cad_function(args):
    import cadquery as cq

    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input
    base = shape.val() if hasattr(shape, "val") else shape

    # --- Sanity: resolve the referenced global faces/edges on the imported (unmodified) shape ---
    faces = base.Faces()
    edges = base.Edges()
    print(f"MODEL: faces={len(faces)} edges={len(edges)} solids={len(base.Solids())}")

    ref_face_ids = [1264, 1267, 1262]
    for fid in ref_face_ids:
        try:
            f = faces[fid]
            c = f.Center()
            print(
                f"REF CHECK: face#{fid} center={[round(c.x,3), round(c.y,3), round(c.z,3)]} area={round(f.Area(),3)}"
            )
        except Exception as e:
            print(f"REF CHECK FAILED: face#{fid} error={e}")

    ref_edge_ids = [3482, 3483, 3485, 3486, 3487, 3495, 3496, 3502, 3509]
    for eid in ref_edge_ids:
        try:
            e = edges[eid]
            bb = e.BoundingBox()
            cc = e.Center()
            print(
                f"REF CHECK: edge#{eid} centroid={[round(cc.x,3), round(cc.y,3), round(cc.z,3)]} "
                f"bboxZ=[{round(bb.zmin,3)}..{round(bb.zmax,3)}]"
            )
        except Exception as ex:
            print(f"REF CHECK FAILED: edge#{eid} error={ex}")

    # --- Pick solid s8 by bbox center near (40,-60) and zmax near 21 ---
    sols = base.Solids()
    print(f"SELECTED: {len(sols)} solids in model")

    target_xy = (40.0, -60.0)
    best_i = None
    best_score = 1e9
    for i, s in enumerate(sols):
        bb = s.BoundingBox()
        cx = 0.5 * (bb.xmin + bb.xmax)
        cy = 0.5 * (bb.ymin + bb.ymax)
        cz = 0.5 * (bb.zmin + bb.zmax)
        # score favors closeness to expected center and expected z-span
        score = abs(cx - target_xy[0]) + abs(cy - target_xy[1]) + 0.2 * abs(bb.zmax - 21.0) + 0.1 * abs(bb.zmin - 0.0)
        print(
            f"  SOLID[{i}] bbox=([{round(bb.xmin,3)},{round(bb.ymin,3)},{round(bb.zmin,3)}].."
            f"[{round(bb.xmax,3)},{round(bb.ymax,3)},{round(bb.zmax,3)}]) center={[round(cx,3),round(cy,3),round(cz,3)]} score={round(score,4)}"
        )
        if score < best_score:
            best_score = score
            best_i = i

    if best_i is None:
        print("SELECTED: 0 solids for s8 (unexpected) -- returning input")
        return shape

    s8 = sols[best_i]
    bb8 = s8.BoundingBox()
    c8x = 0.5 * (bb8.xmin + bb8.xmax)
    c8y = 0.5 * (bb8.ymin + bb8.ymax)
    print(
        f"SELECTED: 1 solid for s8 => SOLID[{best_i}] centerXY={[round(c8x,3), round(c8y,3)]} "
        f"bboxZ=[{round(bb8.zmin,3)}..{round(bb8.zmax,3)}]"
    )

    # --- Remove a 1.0mm axial slice within the upper threaded region, then translate the top down by 1.0mm ---
    # Keep geometry at/below thread start (~z=6.5) unchanged by placing the removed slice at z=19..20.
    z0 = 19.0
    dz = 200.0
    dx = 100.0
    dy = 100.0

    # Box covering everything above z0 (z0..z0+dz) to CUT away for the lower piece
    box_above = (
        cq.Workplane(cq.Plane(origin=(c8x, c8y, z0 + dz / 2.0), normal=(0, 0, 1)))
        .box(dx, dy, dz, centered=(True, True, True))
        .val()
    )
    # Box covering everything below z0+1 (z0+1-dz..z0+1) to CUT away for the upper piece
    box_below = (
        cq.Workplane(cq.Plane(origin=(c8x, c8y, (z0 + 1.0) - dz / 2.0), normal=(0, 0, 1)))
        .box(dx, dy, dz, centered=(True, True, True))
        .val()
    )

    lower = s8.cut(box_above)
    upper = s8.cut(box_below)

    bbL = lower.BoundingBox()
    bbU = upper.BoundingBox()
    print(
        f"SLICE: lower bboxZ=[{round(bbL.zmin,3)}..{round(bbL.zmax,3)}] (should end at ~{z0})"
    )
    print(
        f"SLICE: upper bboxZ=[{round(bbU.zmin,3)}..{round(bbU.zmax,3)}] (should start at ~{z0+1.0})"
    )

    upper_moved = upper.translate((0, 0, -1.0))
    bbUm = upper_moved.BoundingBox()
    print(
        f"MOVE:  upper_moved bboxZ=[{round(bbUm.zmin,3)}..{round(bbUm.zmax,3)}] (expected shift -1.0)"
    )

    edited = lower.fuse(upper_moved)
    try:
        edited = edited.clean()
    except Exception as e:
        print(f"NOTE: clean() unavailable/failed: {e}")

    # --- Verification ---
    bbE = edited.BoundingBox()
    cEx = 0.5 * (bbE.xmin + bbE.xmax)
    cEy = 0.5 * (bbE.ymin + bbE.ymax)
    print(
        "VERIFY s8: "
        f"centerXY={[round(cEx,3), round(cEy,3)]} (target [40.0,-60.0], d=[{round(cEx-40.0,3)},{round(cEy+60.0,3)}]) "
        f"zmin={round(bbE.zmin,3)} (target 0.0, d={round(bbE.zmin-0.0,3)}) "
        f"zmax={round(bbE.zmax,3)} (target 20.0, d={round(bbE.zmax-20.0,3)})"
    )
    nsolE = len(edited.Solids())
    print(f"VERIFY s8: edited solids count={nsolE} (expect 1)")

    # --- Recompound with other solids untouched ---
    new_sols = []
    for i, s in enumerate(sols):
        new_sols.append(edited if i == best_i else s)

    out = cq.Compound.makeCompound(new_sols)
    return out