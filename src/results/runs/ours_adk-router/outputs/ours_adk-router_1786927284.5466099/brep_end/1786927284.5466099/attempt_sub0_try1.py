def my_cad_function(args):
    import cadquery as cq
    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input

    base = shape.val() if hasattr(shape, "val") else shape
    sols = base.Solids()
    print(f"SELECTED: {len(sols)} solids in input")
    if len(sols) != 1:
        print("WARNING: expected exactly 1 solid; proceeding with solid[0]")
    s0 = sols[0]

    # --- Resolve index-referenced faces for sanity ---
    faces = base.Faces()
    edges = base.Edges()
    print(f"INFO: base has {len(faces)} faces, {len(edges)} edges")

    def f_info(i, label):
        try:
            f = faces[i]
            c = f.Center()
            print(f"SELECTED: 1 face for {label}  face_idx=[{i}]  geom={f.geomType()}  center={[round(c.x,3), round(c.y,3), round(c.z,3)]}  area={round(f.Area(),3)}")
            return f
        except Exception as e:
            print(f"SELECTED: 0 faces for {label} (failed to resolve face {i}): {e}")
            return None

    f10 = f_info(10, "outer r=7 cylinder wall (should be blue)")
    f3  = f_info(3,  "inner r=5 bore wall (should be red)")
    f4  = f_info(4,  "bore bottom/opening face #4")
    f11 = f_info(11, "bore mouth/opening face #11")
    f9  = f_info(9,  "adjoining BSPLINE base face #9")

    print("TARGET NUMBERS:")
    print("  outer CYL r=7 axis~Y center~[0,29,4] spans Y 24..34")
    print("  bore  CYL r=5 axis~Y center~[0,29.5,4] spans Y 25..34 (blind)")
    print("  copies at x = 24, 48, 72 (keep original at x=0), all z=4, same Y extents")

    # --- Extract a local chunk that contains the full holder material (incl. spline blend) ---
    # Include entire Z extent of holder (r=7 around z=4 -> z -3..11 per bbox), and enough Y to include the spline blend.
    # Keep X narrow so we only capture local plate section + holder.
    cx, cy, cz = 0.0, 22.5, 4.0
    xlen, ylen, zlen = 30.0, 34.0, 20.0
    box = cq.Solid.makeBox(
        xlen, ylen, zlen,
        cq.Vector(cx - xlen/2.0, cy - ylen/2.0, cz - zlen/2.0)
    )
    print(f"INFO: chunk extraction box center={[cx,cy,cz]} size={[xlen,ylen,zlen]}")

    chunk = s0.intersect(box)
    try:
        bb = chunk.BoundingBox()
        cc = chunk.Center()
        print(f"SELECTED: 1 shape for holder-chunk (intersection)  bbox=([x {bb.xmin:.3f}..{bb.xmax:.3f}], [y {bb.ymin:.3f}..{bb.ymax:.3f}], [z {bb.zmin:.3f}..{bb.zmax:.3f}])  center={[round(cc.x,3), round(cc.y,3), round(cc.z,3)]}")
    except Exception as e:
        print(f"SELECTED: 0 shape for holder-chunk (intersection failed/empty?): {e}")

    # --- Fuse 3 translated copies of the chunk (adds material only; does NOT create bores) ---
    out = s0
    desired_x = [0.0, 24.0, 48.0, 72.0]
    dxs = [24.0, 48.0, 72.0]

    for dx in dxs:
        prev = out
        moved = chunk.translate((dx, 0, 0))
        out = out.fuse(moved)
        # placement self-check for this fuse step
        added_step = out.cut(prev)
        try:
            bb = added_step.BoundingBox()
            cc = added_step.Center()
            print(f"INFO: after fusing chunk at dx={dx:.3f}, added material bbox=([x {bb.xmin:.3f}..{bb.xmax:.3f}], [y {bb.ymin:.3f}..{bb.ymax:.3f}], [z {bb.zmin:.3f}..{bb.zmax:.3f}]) center={[round(cc.x,3), round(cc.y,3), round(cc.z,3)]}")
        except Exception as e:
            print(f"INFO: after fusing chunk at dx={dx:.3f}, could not compute added material diagnostics: {e}")

    # --- Cut the r=5 bore at new locations (blind: y=34 down to y=25) ---
    # Build tool starting slightly outside the y=34 mouth, ending exactly at y=25.
    eps = 0.0001
    bore_len = 9.0 + eps  # start at 34+eps, end at 25.0

    for x in [24.0, 48.0, 72.0]:
        plane = cq.Plane(origin=(x, 34.0 + eps, 4.0), normal=(0, 1, 0), xDir=(1, 0, 0))
        print(f"INFO: bore cut plane origin={[x, 34.0+eps, 4.0]} normal=[0,1,0] (extrude -Y {bore_len:.4f} to y=25.0)")
        tool = cq.Workplane(plane).circle(5.0).extrude(-bore_len).val()

        prev = out
        out = out.cut(tool)
        removed_step = prev.cut(out)
        try:
            bb = removed_step.BoundingBox()
            cc = removed_step.Center()
            print(f"INFO: after cutting bore at x={x:.3f}, removed material bbox=([x {bb.xmin:.3f}..{bb.xmax:.3f}], [y {bb.ymin:.3f}..{bb.ymax:.3f}], [z {bb.zmin:.3f}..{bb.zmax:.3f}]) center={[round(cc.x,3), round(cc.y,3), round(cc.z,3)]}")
        except Exception as e:
            print(f"INFO: after cutting bore at x={x:.3f}, could not compute removed material diagnostics: {e}")

    # --- Global added/removed diagnostics ---
    added = out.cut(s0)
    removed = s0.cut(out)
    try:
        bb = added.BoundingBox()
        cc = added.Center()
        print(f"SELF-CHECK: TOTAL added material center={[round(cc.x,3), round(cc.y,3), round(cc.z,3)]} bbox=([x {bb.xmin:.3f}..{bb.xmax:.3f}], [y {bb.ymin:.3f}..{bb.ymax:.3f}], [z {bb.zmin:.3f}..{bb.zmax:.3f}])")
    except Exception as e:
        print(f"SELF-CHECK: TOTAL added material could not be measured (maybe empty): {e}")
    try:
        bb = removed.BoundingBox()
        cc = removed.Center()
        print(f"SELF-CHECK: TOTAL removed material center={[round(cc.x,3), round(cc.y,3), round(cc.z,3)]} bbox=([x {bb.xmin:.3f}..{bb.xmax:.3f}], [y {bb.ymin:.3f}..{bb.ymax:.3f}], [z {bb.zmin:.3f}..{bb.zmax:.3f}])")
    except Exception as e:
        print(f"SELF-CHECK: TOTAL removed material could not be measured (maybe empty): {e}")

    # --- Achieved holder-axis coordinate check (from CYLINDER face centers near bore y,z) ---
    cyl_faces = [f for f in out.Faces() if f.geomType() == "CYLINDER"]
    print(f"SELECTED: {len(cyl_faces)} faces for cylinder-scan (post-edit)")

    bore_candidates = []
    for f in cyl_faces:
        c = f.Center()
        # bore wall centers should be near y=29.5, z=4; accept loose tolerance.
        if abs(c.z - 4.0) < 0.75 and abs(c.y - 29.5) < 1.25:
            bore_candidates.append(c)

    print(f"SELECTED: {len(bore_candidates)} cylinder faces as bore-wall candidates (center near y~29.5 z~4)")
    bore_xs = sorted([c.x for c in bore_candidates])
    # Cluster by rounding to nearest mm to report distinct holders
    bore_xs_rounded = sorted(set([round(x, 1) for x in bore_xs]))
    print(f"ACHIEVED: bore-axis approx centers (x values from candidate cyl-face centers) = {bore_xs_rounded} (expect ~[0,24,48,72])")

    # If detection failed, still print the intended coordinates; do not move geometry silently.
    if len(bore_xs_rounded) < 4:
        print(f"WARNING: could not robustly detect 4 bore cylinders; intended holder axes are {[ (x, '*', 4.0) for x in desired_x ]}")

    # Simple numeric check
    if len(bore_xs_rounded) >= 4:
        # Compare the four smallest (in case extra candidates)
        xs = bore_xs_rounded[:4]
        deltas = [xs[i] - desired_x[i] for i in range(4)]
        print(f"SELF-CHECK: holder-axis x deltas vs target [0,24,48,72] = {[round(d,3) for d in deltas]}")

    return out