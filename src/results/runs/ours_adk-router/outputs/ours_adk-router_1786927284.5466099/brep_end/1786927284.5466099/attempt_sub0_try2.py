def my_cad_function(args):
    import cadquery as cq

    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input

    base = shape.val() if hasattr(shape, "val") else shape
    solids = base.Solids()
    print(f"INFO: imported solids={len(solids)}")
    if len(solids) != 1:
        print("WARNING: expected exactly 1 solid; will edit solid[0] and re-compound others (if any).")

    bb_before = base.BoundingBox()
    print(f"INFO: bbox BEFORE min={[bb_before.xmin, bb_before.ymin, bb_before.zmin]} max={[bb_before.xmax, bb_before.ymax, bb_before.zmax]}")

    # Resolve target faces by index and verify against the provided geometry index
    faces = base.Faces()
    print(f"INFO: total faces={len(faces)}")

    idxs = [3, 4, 9, 10, 11]
    picked = []
    for i in idxs:
        try:
            f = faces[i]
            c = f.Center()
            print(
                f"SELECTED: 1 face idx={i} for holder-source ref  geomType={f.geomType()} "
                f"center={[round(c.x, 3), round(c.y, 3), round(c.z, 3)]} area={round(f.Area(), 3)}"
            )
            picked.append(f)
        except Exception as e:
            print(f"SELECTED: 0 faces idx={i} (FAILED) for holder-source ref  err={e}")

    # Key faces: #10 outer cylinder r=7, #3 bore cylinder r=5
    f_outer = faces[10]
    f_bore = faces[3]
    bb_outer = f_outer.BoundingBox()
    bb_bore = f_bore.BoundingBox()

    # Estimate radii from bounding boxes (axis is Y, so xlen/zlen ~ 2r)
    r_outer_est = 0.5 * min(bb_outer.xlen, bb_outer.zlen)
    r_bore_est = 0.5 * min(bb_bore.xlen, bb_bore.zlen)
    print(f"INFO: outer cyl face#10 bbox xlen/zlen=({bb_outer.xlen:.3f},{bb_outer.zlen:.3f}) -> r~{r_outer_est:.3f}")
    print(f"INFO: bore  cyl face#3  bbox xlen/zlen=({bb_bore.xlen:.3f},{bb_bore.zlen:.3f}) -> r~{r_bore_est:.3f}")

    # Build a slicing/replacement box centered on the source holder at x=0.
    # IMPORTANT: keep x-halfwidth <= 8mm so that the last copy at x=72 stays within maxX=80.
    margin = 0.8
    x_half_raw = 0.5 * bb_outer.xlen + margin
    allowed_half = bb_before.xmax - 72.0 - 0.1  # keep a small safety margin from maxX=80
    x_half = min(x_half_raw, allowed_half)
    if x_half <= 7.05:
        # ensure we still cover the r=7 boss + tiny tolerance
        x_half = 7.1
    xlen = 2.0 * x_half

    ylen = bb_before.ylen + 2.0
    zlen = bb_before.zlen + 2.0

    src_center = cq.Vector(0.0, 0.5 * (bb_before.ymin + bb_before.ymax), 0.5 * (bb_before.zmin + bb_before.zmax))
    print(
        "INFO: source replacement box params: "
        f"center={[round(src_center.x,3), round(src_center.y,3), round(src_center.z,3)]} "
        f"xlen={xlen:.3f} (x_half={x_half:.3f}, x_half_raw={x_half_raw:.3f}, allowed_half={allowed_half:.3f}) "
        f"ylen={ylen:.3f} zlen={zlen:.3f}"
    )

    src_box = (
        cq.Workplane(cq.Plane(origin=(src_center.x, src_center.y, src_center.z), normal=(0, 0, 1)))
        .box(xlen, ylen, zlen, centered=(True, True, True))
        .val()
    )

    # Extract the source slice (rail segment + complete holder geometry within the box)
    src_solid = solids[0]
    src_chunk = src_solid.intersect(src_box)
    try:
        v = src_chunk.Volume()
    except Exception:
        v = None
    bb_chunk = src_chunk.BoundingBox()
    print(
        "INFO: extracted source chunk: "
        f"volume={None if v is None else round(v,3)} "
        f"bbox min={[round(bb_chunk.xmin,3), round(bb_chunk.ymin,3), round(bb_chunk.zmin,3)]} "
        f"max={[round(bb_chunk.xmax,3), round(bb_chunk.ymax,3), round(bb_chunk.zmax,3)]}"
    )

    # Duplicate at x=24,48,72 by REPLACING the target x-slice with the source chunk
    # (cut by translated box, then fuse translated chunk). This preserves the bore void too.
    targets = [24.0, 48.0, 72.0]

    edited = src_solid
    for dx in targets:
        box_dx = src_box.translate((dx, 0, 0))
        chunk_dx = src_chunk.translate((dx, 0, 0))

        print(f"SELECTED: 1 translated replacement box for dx={dx}  purpose=cut-out target slice")
        edited = edited.cut(box_dx)

        print(f"SELECTED: 1 translated source chunk for dx={dx}  purpose=fuse-in duplicated holder slice")
        edited = edited.fuse(chunk_dx)

    # Re-compound other solids untouched (if any)
    out = edited
    if len(solids) > 1:
        out = cq.Compound.makeCompound([s for i, s in enumerate(solids) if i != 0] + [edited])

    bb_after = out.BoundingBox()
    print(f"INFO: bbox AFTER  min={[bb_after.xmin, bb_after.ymin, bb_after.zmin]} max={[bb_after.xmax, bb_after.ymax, bb_after.zmax]}")
    print(
        "INFO: bbox delta (AFTER-BEFORE) "
        f"dmin={[round(bb_after.xmin-bb_before.xmin,3), round(bb_after.ymin-bb_before.ymin,3), round(bb_after.zmin-bb_before.zmin,3)]} "
        f"dmax={[round(bb_after.xmax-bb_before.xmax,3), round(bb_after.ymax-bb_before.ymax,3), round(bb_after.zmax-bb_before.zmax,3)]}"
    )

    # Placement self-check: find all r~7 full cylindrical faces and report their axis points (x,*,z)
    faces_out = out.Faces()
    cyl_faces = [f for f in faces_out if f.geomType() == "CYLINDER"]
    print(f"INFO: found CYLINDER faces in output: {len(cyl_faces)}")

    r7_axes = []
    r5_axes = []
    for f in cyl_faces:
        bb = f.BoundingBox()
        r_est = 0.5 * min(bb.xlen, bb.zlen)
        axis_pt = (0.5 * (bb.xmin + bb.xmax), 0.5 * (bb.ymin + bb.ymax), 0.5 * (bb.zmin + bb.zmax))
        if abs(r_est - 7.0) < 0.25:
            r7_axes.append(axis_pt)
        elif abs(r_est - 5.0) < 0.25:
            r5_axes.append(axis_pt)

    r7_axes_sorted = sorted(r7_axes, key=lambda p: p[0])
    r5_axes_sorted = sorted(r5_axes, key=lambda p: p[0])

    print("INFO: achieved holder outer-cylinder (r~7) axis points (x,y,z) sorted by x:")
    for p in r7_axes_sorted:
        print(f"  AXIS_R7: {[round(p[0],3), round(p[1],3), round(p[2],3)]}")

    print("INFO: achieved holder bore-cylinder (r~5) axis points (x,y,z) sorted by x:")
    for p in r5_axes_sorted:
        print(f"  AXIS_R5: {[round(p[0],3), round(p[1],3), round(p[2],3)]}")

    expected_x = [0.0, 24.0, 48.0, 72.0]
    got_x = [p[0] for p in r7_axes_sorted]
    if len(got_x) >= 4:
        got_x4 = got_x[:4]
        deltas = [got_x4[i] - expected_x[i] for i in range(4)]
        print(f"INFO: holder x check (r7): expected={expected_x} got={[round(x,3) for x in got_x4]} deltas={[round(d,3) for d in deltas]}")
        # If something is badly off, print a loud warning (we cannot safely re-solve without redoing booleans)
        if any(abs(d) > 1.0 for d in deltas):
            print("WARNING: holder x positions off by >1mm; expected ~[0,24,48,72].")
    else:
        print(f"WARNING: expected 4 r~7 cylinders after duplication, got {len(got_x)}")

    return out