def my_cad_function(args):
    import cadquery as cq
    import math

    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input
    base = shape.val() if hasattr(shape, "val") else shape

    solids = base.Solids()
    print(f"SELECTED: {len(solids)} solids in imported shape")
    if len(solids) != 1:
        raise ValueError(f"Expected 1 solid, found {len(solids)}")

    solid = solids[0]
    bb0 = solid.BoundingBox()
    vol0 = solid.Volume()
    print(f"BASE: volume={vol0:.3f} mm^3")
    print(f"BASE: bbox min=({bb0.xmin:.3f},{bb0.ymin:.3f},{bb0.zmin:.3f}) max=({bb0.xmax:.3f},{bb0.ymax:.3f},{bb0.zmax:.3f}) size=({bb0.xlen:.3f},{bb0.ylen:.3f},{bb0.zlen:.3f})")

    faces = base.Faces()
    print(f"SELECTED: {len(faces)} faces on base shape for face_idx resolution")
    f5 = faces[5]
    f11 = faces[11]
    c5 = f5.Center()
    c11 = f11.Center()
    try:
        n5 = f5.normalAt()
    except Exception as e:
        n5 = None
        print(f"WARN: face #5 normalAt() failed: {e}")
    try:
        n11 = f11.normalAt()
    except Exception as e:
        n11 = None
        print(f"WARN: face #11 normalAt() failed: {e}")
    print(f"SELECTED: 1 face for reference face #5  area={f5.Area():.3f} center=({c5.x:.3f},{c5.y:.3f},{c5.z:.3f}) normal={None if n5 is None else (round(n5.x,3),round(n5.y,3),round(n5.z,3))}")
    print(f"SELECTED: 1 face for reference face #11 area={f11.Area():.3f} center=({c11.x:.3f},{c11.y:.3f},{c11.z:.3f}) normal={None if n11 is None else (round(n11.x,3),round(n11.y,3),round(n11.z,3))}")

    # Target pattern
    xs = [-50.0, -34.67, -19.33, -4.0, 11.33, 26.67, 42.0]
    y0 = 0.0
    d = 5.0
    r = d / 2.0
    axis = cq.Vector(0.0, 0.0, 1.0)

    print("PATTERN TARGETS (mm):")
    for i, x in enumerate(xs):
        print(f"  hole[{i}] center=({x:.3f},{y0:.3f}) d={d:.3f} axis=(0,0,1)")

    # Build long cylinders to guarantee through-cut between the two Z-facing sides
    height = bb0.zlen + 10.0
    z_base = bb0.zmin - 5.0

    cyls = []
    for i, x in enumerate(xs):
        cyl = cq.Solid.makeCylinder(r, height, cq.Vector(x, y0, z_base), axis)
        cyls.append(cyl)
        cbb = cyl.BoundingBox()
        print(
            f"TOOL: cyl[{i}] r={r:.3f} h={height:.3f} base=({x:.3f},{y0:.3f},{z_base:.3f}) "
            f"bboxZ=[{cbb.zmin:.3f},{cbb.zmax:.3f}]"
        )

    # Ensure distinct centers
    uniq = {(round(x, 3), round(y0, 3)) for x in xs}
    print(f"CHECK: distinct centers = {len(uniq)} of {len(xs)}")
    if len(uniq) != len(xs):
        raise ValueError("Duplicate hole centers detected")

    # Inside-ness heuristic: intersection volume should be close to expected (pi r^2 * nominal thickness)
    # Note: thickness can vary slightly; we just catch gross breakouts.
    est_nom = math.pi * r * r * bb0.zlen
    print(f"CHECK: nominal per-hole volume estimate (pi*r^2*bbox.zlen) = {est_nom:.3f} mm^3")
    for i, cyl in enumerate(cyls):
        inter = solid.intersect(cyl)
        v = inter.Volume() if inter is not None else 0.0
        ratio = (v / est_nom) if est_nom > 0 else 0.0
        print(f"CHECK: cyl[{i}] intersect volume={v:.3f} mm^3 ratio_vs_nom={ratio:.3f}")
        if ratio < 0.80:
            print(f"WARN: cyl[{i}] appears to break out of the arm perimeter (low intersection volume)")

    tool = cq.Compound.makeCompound(cyls)

    # Apply cut
    edited = solid.cut(tool)

    bb1 = edited.BoundingBox()
    vol1 = edited.Volume()
    removed = vol0 - vol1
    removed_pct = (removed / vol0 * 100.0) if vol0 > 0 else 0.0

    print(f"RESULT: volume={vol1:.3f} mm^3")
    print(f"RESULT: removed={removed:.3f} mm^3 ({removed_pct:.2f}% of original {vol0:.3f} mm^3)")

    print(
        "BBOX CHECK:\n"
        f"  before min=({bb0.xmin:.3f},{bb0.ymin:.3f},{bb0.zmin:.3f}) max=({bb0.xmax:.3f},{bb0.ymax:.3f},{bb0.zmax:.3f})\n"
        f"  after  min=({bb1.xmin:.3f},{bb1.ymin:.3f},{bb1.zmin:.3f}) max=({bb1.xmax:.3f},{bb1.ymax:.3f},{bb1.zmax:.3f})\n"
        f"  delta min=({(bb1.xmin-bb0.xmin):.6f},{(bb1.ymin-bb0.ymin):.6f},{(bb1.zmin-bb0.zmin):.6f}) "
        f"delta max=({(bb1.xmax-bb0.xmax):.6f},{(bb1.ymax-bb0.ymax):.6f},{(bb1.zmax-bb0.zmax):.6f})"
    )

    # Verify we created 7 distinct through-openings: look for new circular rim edges of radius ~2.5
    edges = edited.Edges()
    circ = []
    for ei, e in enumerate(edges):
        if e.geomType() == "CIRCLE":
            try:
                er = e.radius()
            except Exception:
                er = None
            if er is not None and abs(er - r) < 0.05:
                cc = e.Center()  # for full circles, centroid==true center
                circ.append((ei, er, cc))

    print(f"SELECTED: {len(circ)} circular edges with r~{r:.3f} (candidate new hole rims)")
    if len(circ) > 0:
        for (ei, er, cc) in circ[:30]:
            print(f"  rimEdge[{ei}] r={er:.3f} center~({cc.x:.3f},{cc.y:.3f},{cc.z:.3f})")
        if len(circ) > 30:
            print("  (showing 30 of N)")

    # Per-target hit check near x,y
    tol_xy = 0.75
    for i, x in enumerate(xs):
        hits = [(ei, er, cc) for (ei, er, cc) in circ if abs(cc.x - x) < tol_xy and abs(cc.y - y0) < tol_xy]
        zs = sorted([h[2].z for h in hits])
        print(f"VERIFY: target hole[{i}] at x={x:.2f},y={y0:.2f} -> rim hits={len(hits)} z_centers={['%.3f'%z for z in zs[:6]]}{'...' if len(zs)>6 else ''}")

    # Sanity vs requested 10-15% removal
    if not (10.0 <= removed_pct <= 15.0):
        print("WARN: removed volume percent is outside requested ~10-15% range")

    return edited