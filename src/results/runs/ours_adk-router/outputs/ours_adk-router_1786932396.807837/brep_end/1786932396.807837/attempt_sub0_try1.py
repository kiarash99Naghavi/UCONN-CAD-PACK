def my_cad_function(args):
    import cadquery as cq
    import math

    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input

    base = shape.val() if hasattr(shape, "val") else shape

    # --- Resolve and verify target face by global face index ---
    faces = base.Faces()
    print(f"INFO: total faces in imported shape = {len(faces)}")
    if len(faces) <= 68:
        print("ERROR: Face index 68 not present; returning input unchanged")
        return shape

    f68 = faces[68]
    try:
        f68_area = f68.Area()
    except Exception:
        f68_area = None
    f68_c = f68.Center()
    print(f"SELECTED: 1 face for target rear BSPLINE face #68  center={[round(f68_c.x,3), round(f68_c.y,3), round(f68_c.z,3)]} area={None if f68_area is None else round(f68_area,3)}")
    # Index expects: s6 BSPLINE area=107871.616 c=[-150.0, 209.019, 0.331]

    # --- Identify solid s6 robustly (by bbox + volume) ---
    solids = base.Solids()
    print(f"INFO: total solids in imported shape = {len(solids)}")

    target_bbox = {
        "xmin": -297.902,
        "ymin": 9.0,
        "zmin": -0.0,
        "xmax": -2.098,
        "ymax": 409.0,
        "zmax": 320.0,
    }
    target_vol = 20460695.535

    best_i = None
    best_score = 1e99
    for i, s in enumerate(solids):
        bb = s.BoundingBox()
        try:
            vol = s.Volume()
        except Exception:
            vol = None
        # Score combines bbox deltas and volume delta
        score = 0.0
        score += abs(bb.xmin - target_bbox["xmin"]) + abs(bb.xmax - target_bbox["xmax"])
        score += abs(bb.ymin - target_bbox["ymin"]) + abs(bb.ymax - target_bbox["ymax"])
        score += abs(bb.zmin - target_bbox["zmin"]) + abs(bb.zmax - target_bbox["zmax"])
        if vol is not None:
            score += abs(vol - target_vol) / 1e6
        if score < best_score:
            best_score = score
            best_i = i

    if best_i is None:
        print("ERROR: could not locate target solid s6; returning input unchanged")
        return shape

    s6 = solids[best_i]
    bb6 = s6.BoundingBox()
    print(
        "SELECTED: 1 solid for body s6 (by bbox/vol best match) "
        f"solid_index={best_i} score={round(best_score,6)} "
        f"bbox=[{round(bb6.xmin,3)},{round(bb6.ymin,3)},{round(bb6.zmin,3)}]..[{round(bb6.xmax,3)},{round(bb6.ymax,3)},{round(bb6.zmax,3)}] vol={round(s6.Volume(),3)}"
    )

    # --- Parameters from sub-goal ---
    cx, cy = -150.0, 106.902
    sx, sy = 200.0, 100.0
    r = 10.0
    depth = 30.0
    eps = 0.5  # start slightly outside, keep floor at exactly rear_z + depth

    x_min_t, x_max_t = cx - sx / 2.0, cx + sx / 2.0
    y_min_t, y_max_t = cy - sy / 2.0, cy + sy / 2.0

    print("INFO: target numbers (from sub-goal):")
    print(f"  target center = [{cx}, {cy}, ~0.0]")
    print(f"  target mouth X extents = {x_min_t}..{x_max_t}")
    print(f"  target mouth Y extents = {y_min_t}..{y_max_t}")
    print(f"  target corner radius = R{r}")
    print(f"  target depth along +Z = {depth}")

    # --- Measure rear surface Z at the intended pocket center using a probe intersection ---
    # Make a thin cylinder along +Z that spans beyond the solid in Z.
    probe_r = 0.75
    probe_h = (bb6.zmax - bb6.zmin) + 200.0
    probe_z0 = bb6.zmin - 100.0
    cyl = cq.Solid.makeCylinder(probe_r, probe_h, cq.Vector(cx, cy, probe_z0), cq.Vector(0, 0, 1))
    inter = s6.intersect(cyl)
    inter_vol = inter.Volume() if inter is not None else 0.0
    print(f"INFO: probe intersection volume at pocket center = {round(inter_vol,6)}")
    if inter is None or inter_vol < 1e-6:
        print("ERROR: probe did not hit solid; cannot anchor rear Z. Returning input unchanged.")
        return shape

    ibb = inter.BoundingBox()
    rear_z = ibb.zmin
    print(
        "INFO: measured rear surface Z at center (via probe bbox.zmin) = "
        f"{round(rear_z,6)} (probe z-span inside solid: {round(ibb.zmin,6)}..{round(ibb.zmax,6)})"
    )

    z_start = rear_z - eps
    extrude_len = depth + eps
    floor_z_nominal = z_start + extrude_len  # should equal rear_z + depth

    print(f"INFO: sketch plane origin z_start = {round(z_start,6)} (rear_z - eps)")
    print(f"INFO: extrude_len = {round(extrude_len,6)} so nominal floor_z = {round(floor_z_nominal,6)} (should be rear_z+depth={round(rear_z+depth,6)})")

    # --- Build the cutting tool: axis-aligned rounded rectangle extruded along +Z ---
    plane = cq.Plane(origin=(0, 0, z_start), normal=(0, 0, 1), xDir=(1, 0, 0))
    tool_wp = cq.Workplane(plane)
    tool = (
        tool_wp
        .center(cx, cy)
        .rect(sx, sy, centered=True)
        .vertices()
        .fillet(r)
        .extrude(extrude_len)
        .val()
    )

    tbb = tool.BoundingBox()
    print(
        "INFO: tool bbox "
        f"X={round(tbb.xmin,3)}..{round(tbb.xmax,3)} Y={round(tbb.ymin,3)}..{round(tbb.ymax,3)} Z={round(tbb.zmin,3)}..{round(tbb.zmax,3)}"
    )

    # Verify tool's 2D intent (axis alignment inherently correct with xDir=(1,0,0))
    print(
        "VERIFY(tool intent): centerXY="
        f"[{round((tbb.xmin+tbb.xmax)/2,3)}, {round((tbb.ymin+tbb.ymax)/2,3)}] "
        f"sizeXY=[{round(tbb.xmax-tbb.xmin,3)}, {round(tbb.ymax-tbb.ymin,3)}]"
    )

    # --- Cut only on s6, re-compound everything else unchanged ---
    edited_s6 = s6.cut(tool)

    out = cq.Compound.makeCompound([s for i, s in enumerate(solids) if i != best_i] + [edited_s6])

    # --- Self-check: isolate removed material and report achieved placement ---
    removed = s6.cut(edited_s6)
    rem_vol = removed.Volume() if removed is not None else 0.0
    print(f"INFO: removed volume (s6 before - after) = {round(rem_vol,3)} mm^3")
    if removed is None or rem_vol < 1e-6:
        print("ERROR: cut appears to have removed nothing (selection/no intersection). Returning input unchanged.")
        return shape

    rbb = removed.BoundingBox()
    rcenter = removed.Center()
    print(
        "VERIFY(removed): center="
        f"[{round(rcenter.x,3)}, {round(rcenter.y,3)}, {round(rcenter.z,3)}] "
        f"bbox X={round(rbb.xmin,3)}..{round(rbb.xmax,3)} Y={round(rbb.ymin,3)}..{round(rbb.ymax,3)} Z={round(rbb.zmin,3)}..{round(rbb.zmax,3)}"
    )

    # Compare achieved extents to targets
    dx0, dx1 = rbb.xmin - x_min_t, rbb.xmax - x_max_t
    dy0, dy1 = rbb.ymin - y_min_t, rbb.ymax - y_max_t
    print(
        "VERIFY(mouth extents vs target): "
        f"dXmin={round(dx0,3)} dXmax={round(dx1,3)} dYmin={round(dy0,3)} dYmax={round(dy1,3)} (mm)"
    )

    # Find the pocket floor face (planar, normal ~ -Z) near expected floor_z
    floor_faces = []
    for ff in edited_s6.Faces():
        try:
            if ff.geomType() != "PLANE":
                continue
            n = ff.normalAt()
            if abs(n.x) < 1e-3 and abs(n.y) < 1e-3 and (n.z < -0.999):
                c = ff.Center()
                floor_faces.append((ff, c))
        except Exception:
            continue

    print(f"SELECTED: {len(floor_faces)} planar faces with normal ~ -Z on edited s6 (candidate pocket floors)")
    floor_face = None
    if floor_faces:
        # pick nearest to expected floor point
        best = None
        best_d = 1e99
        for ff, c in floor_faces:
            d = math.sqrt((c.x - cx) ** 2 + (c.y - cy) ** 2 + (c.z - floor_z_nominal) ** 2)
            if d < best_d:
                best_d = d
                best = (ff, c)
        floor_face, fc = best
        fbb = floor_face.BoundingBox()
        achieved_floor_z = fc.z
        achieved_depth = achieved_floor_z - rear_z
        print(
            "VERIFY(floor face): center="
            f"[{round(fc.x,3)}, {round(fc.y,3)}, {round(fc.z,3)}] "
            f"bboxZ={round(fbb.zmin,6)}..{round(fbb.zmax,6)}"
        )
        print(
            "VERIFY(depth along +Z): achieved_depth=(floor_z - rear_z)="
            f"{round(achieved_depth,6)} mm; target={depth} mm; delta={round(achieved_depth - depth,6)} mm"
        )
    else:
        print("WARNING: could not reliably locate a planar -Z pocket floor face to verify depth; relying on construction nominal floor_z.")
        print(
            "VERIFY(depth along +Z, nominal): nominal_depth=(rear_z+depth - rear_z)="
            f"{depth} mm; nominal floor_z={round(floor_z_nominal,6)}"
        )

    # Corner radius & alignment: report intended (tool is axis-aligned) and confirm tool was built with fillet r
    print(f"VERIFY(corner radius intent): constructed rounded-rectangle with fillet radius R={r} mm")
    print("VERIFY(profile alignment): sketch plane normal +Z, xDir +X -> straight sides parallel to world X/Y")

    return out