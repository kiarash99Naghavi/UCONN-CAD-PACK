def my_cad_function(args):
    import cadquery as cq
    import math

    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input
    base = shape.val() if hasattr(shape, "val") else shape

    # --- Resolve and verify the indexed target face (#68) ---
    faces = base.Faces()
    print(f"INFO: total faces in imported shape = {len(faces)}")
    if len(faces) <= 68:
        print("ERROR: face list shorter than 69; cannot resolve face #68. Returning input unchanged.")
        return shape
    f68 = faces[68]
    try:
        c68 = f68.Center()
        a68 = f68.Area()
        print(
            "SELECTED: 1 face for target rear BSPLINE face #68 "
            f" center=[{round(c68.x,3)}, {round(c68.y,3)}, {round(c68.z,3)}] area={round(a68,3)} geom={f68.geomType()}"
        )
    except Exception as e:
        print(f"ERROR: could not measure face #68: {e}. Returning input unchanged.")
        return shape

    # --- Find body s6 among solids by matching the provided bbox+volume ---
    solids = base.Solids()
    print(f"INFO: total solids in imported shape = {len(solids)}")

    target_bb = (-297.902, 9.0, 0.0, -2.098, 409.0, 320.0)
    target_vol = 20460695.535

    best_i = None
    best_score = 1e99
    for i, s in enumerate(solids):
        bb = s.BoundingBox()
        v = s.Volume()
        # Normalize terms to be dimensionless-ish
        db = (
            abs(bb.xmin - target_bb[0]) + abs(bb.ymin - target_bb[1]) + abs(bb.zmin - target_bb[2]) +
            abs(bb.xmax - target_bb[3]) + abs(bb.ymax - target_bb[4]) + abs(bb.zmax - target_bb[5])
        )
        dv = abs(v - target_vol) / max(target_vol, 1.0)
        score = (db / 1000.0) + dv
        if score < best_score:
            best_score = score
            best_i = i

    if best_i is None:
        print("ERROR: could not find any solid to edit. Returning input unchanged.")
        return shape

    s6 = solids[best_i]
    bb6 = s6.BoundingBox()
    print(
        "SELECTED: 1 solid for body s6 (by bbox/vol best match) "
        f"solid_index={best_i} score={round(best_score,6)} "
        f"bbox=[{round(bb6.xmin,3)},{round(bb6.ymin,3)},{round(bb6.zmin,3)}]..[{round(bb6.xmax,3)},{round(bb6.ymax,3)},{round(bb6.zmax,3)}] "
        f"vol={round(s6.Volume(),3)}"
    )

    # --- Target pocket numbers (from sub-goal) ---
    cx, cy = -150.0, 106.902
    sx, sy = 200.0, 100.0
    r = 10.0
    depth = 30.0
    x_min_t, x_max_t = cx - sx / 2.0, cx + sx / 2.0
    y_min_t, y_max_t = cy - sy / 2.0, cy + sy / 2.0

    print("INFO: target numbers (from sub-goal):")
    print(f"  target center = [{cx}, {cy}, ~0.0]")
    print(f"  target mouth X extents = {x_min_t}..{x_max_t}")
    print(f"  target mouth Y extents = {y_min_t}..{y_max_t}")
    print(f"  target corner radius = R{r}")
    print(f"  target depth along +Z = {depth}")

    # --- Measure rear (-Z side) surface Z at multiple points within the rounded-rect region ---
    # Use points that are safely INSIDE the filleted outline.
    pts = []
    xm, xM = x_min_t + r, x_max_t - r
    ym, yM = y_min_t + r, y_max_t - r
    pts.append((cx, cy, "center"))
    pts.append((xm, cy, "mid-left"))
    pts.append((xM, cy, "mid-right"))
    pts.append((cx, ym, "mid-bottom"))
    pts.append((cx, yM, "mid-top"))
    pts.append((xm, ym, "in-corner-LL"))
    pts.append((xm, yM, "in-corner-LU"))
    pts.append((xM, ym, "in-corner-RL"))
    pts.append((xM, yM, "in-corner-RU"))

    probe_r = 0.75
    probe_h = (bb6.zmax - bb6.zmin) + 400.0
    probe_z0 = bb6.zmin - 200.0

    rear_z_samples = []
    print(f"INFO: probing rear surface Z with {len(pts)} +Z cylinders (r={probe_r}):")
    for (px, py, tag) in pts:
        cyl = cq.Solid.makeCylinder(probe_r, probe_h, cq.Vector(px, py, probe_z0), cq.Vector(0, 0, 1))
        inter = s6.intersect(cyl)
        vol = inter.Volume() if inter is not None else 0.0
        if inter is None or vol < 1e-8:
            print(f"  PROBE[{tag}] at ({px},{py}): NO HIT (vol={vol})")
            continue
        ibb = inter.BoundingBox()
        rear_z = ibb.zmin  # entry at -Z side
        rear_z_samples.append(rear_z)
        print(
            f"  PROBE[{tag}] at ({round(px,3)},{round(py,3)}): "
            f"rear_z={round(rear_z,6)}  (intersect z-span {round(ibb.zmin,6)}..{round(ibb.zmax,6)}) vol={round(vol,6)}"
        )

    if len(rear_z_samples) < 3:
        print("ERROR: insufficient probe hits to anchor rear Z reliably; returning input unchanged.")
        return shape

    min_rear_z = min(rear_z_samples)
    max_rear_z = max(rear_z_samples)
    print(f"INFO: rear surface Z samples: min_rear_z={round(min_rear_z,6)} max_rear_z={round(max_rear_z,6)} spread={round(max_rear_z-min_rear_z,6)}")

    # Tool start and length (ensure mouth fully opens even if rear surface varies in Z)
    eps = 0.5
    z_start = min_rear_z - eps
    extrude_len = (max_rear_z - min_rear_z) + depth + eps
    floor_z_ref = max_rear_z + depth

    print(f"INFO: sketch plane origin z_start = {round(z_start,6)} (min_rear_z - eps)")
    print(f"INFO: extrude_len = {round(extrude_len,6)} so tool top Z ~ {round(z_start+extrude_len,6)} (aiming for floor at max_rear_z+depth={round(floor_z_ref,6)})")

    # --- Build the cutting tool: axis-aligned rounded rectangle extruded along +Z ---
    plane = cq.Plane(origin=(0, 0, z_start), normal=(0, 0, 1), xDir=(1, 0, 0))
    print(f"INFO: tool sketch plane origin=(0,0,{round(z_start,6)}) normal=+Z xDir=+X")

    # IMPORTANT FIX vs previous attempt:
    # Use Sketch fillet (2D) rather than Workplane.fillet (3D) to avoid findSolid() error.
    tool_wp = cq.Workplane(plane).center(cx, cy)
    tool = (
        tool_wp
        .sketch()
        .rect(sx, sy)
        .vertices()
        .fillet(r)
        .finalize()
        .extrude(extrude_len)
        .val()
    )

    tbb = tool.BoundingBox()
    print(
        "INFO: tool bbox "
        f"X={round(tbb.xmin,3)}..{round(tbb.xmax,3)} Y={round(tbb.ymin,3)}..{round(tbb.ymax,3)} Z={round(tbb.zmin,3)}..{round(tbb.zmax,3)}"
    )
    print(
        "VERIFY(tool intent): centerXY="
        f"[{round((tbb.xmin+tbb.xmax)/2,3)}, {round((tbb.ymin+tbb.ymax)/2,3)}] "
        f"sizeXY=[{round(tbb.xmax-tbb.xmin,3)}, {round(tbb.ymax-tbb.ymin,3)}]"
    )

    # Verify corner radius on tool by finding cylindrical faces of ~R10 aligned to Z
    cyl10 = []
    for ff in tool.Faces():
        if ff.geomType() != "CYLINDER":
            continue
        try:
            rad = ff.radius()
            if abs(rad - r) > 0.2:
                continue
            # axis direction for cylinder face
            ax = ff._geomAdaptor().Axis().Direction()
            az = abs(ax.Z())
            if az < 0.98:
                continue
            cyl10.append(rad)
        except Exception:
            continue
    print(f"SELECTED: {len(cyl10)} cylindrical tool faces with radius ~R{r} aligned to Z (expect 4). radii={[(round(x,3)) for x in cyl10]}")

    # --- Cut only on s6, re-compound everything else unchanged ---
    edited_s6 = s6.cut(tool)
    out = cq.Compound.makeCompound([s for i, s in enumerate(solids) if i != best_i] + [edited_s6])

    # --- Self-check: isolate removed material and report achieved placement ---
    removed = s6.cut(edited_s6)
    rem_vol = removed.Volume() if removed is not None else 0.0
    print(f"INFO: removed volume (s6 before - after) = {round(rem_vol,3)} mm^3")
    if removed is None or rem_vol < 1e-6:
        print("ERROR: cut appears to have removed nothing (tool did not intersect / wrong body). Returning input unchanged.")
        return shape

    rbb = removed.BoundingBox()
    rc = removed.Center()
    print(
        "VERIFY(removed): center="
        f"[{round(rc.x,3)}, {round(rc.y,3)}, {round(rc.z,3)}] "
        f"bbox X={round(rbb.xmin,3)}..{round(rbb.xmax,3)} Y={round(rbb.ymin,3)}..{round(rbb.ymax,3)} Z={round(rbb.zmin,3)}..{round(rbb.zmax,3)}"
    )

    # Achieved mouth extents in X/Y should match targets
    dx0, dx1 = rbb.xmin - x_min_t, rbb.xmax - x_max_t
    dy0, dy1 = rbb.ymin - y_min_t, rbb.ymax - y_max_t
    print(
        "VERIFY(mouth extents vs target): "
        f"Xmin={round(rbb.xmin,3)} (d={round(dx0,3)}), Xmax={round(rbb.xmax,3)} (d={round(dx1,3)}), "
        f"Ymin={round(rbb.ymin,3)} (d={round(dy0,3)}), Ymax={round(rbb.ymax,3)} (d={round(dy1,3)})"
    )

    achieved_cx = (rbb.xmin + rbb.xmax) / 2.0
    achieved_cy = (rbb.ymin + rbb.ymax) / 2.0
    print(
        "VERIFY(centerXY vs target): "
        f"achieved=[{round(achieved_cx,3)}, {round(achieved_cy,3)}] "
        f"target=[{cx}, {cy}] delta=[{round(achieved_cx-cx,3)}, {round(achieved_cy-cy,3)}]"
    )

    # Depth along +Z: measure from highest sampled rear Z (max_rear_z) to removed zmax
    achieved_depth_at_max = rbb.zmax - max_rear_z
    print(
        "VERIFY(+Z depth): "
        f"achieved_depth_at_max_rear=(removed.zmax - max_rear_z)={round(achieved_depth_at_max,6)}; "
        f"target={depth}; delta={round(achieved_depth_at_max-depth,6)}"
    )
    print(
        "VERIFY(profile alignment): sketch plane normal +Z, xDir +X -> straight sides parallel to world X/Y"
    )
    print(f"VERIFY(corner radius intent): rounded-rectangle constructed with Sketch fillet radius R={r} mm")

    # If anything is clearly off by >1mm, refuse to return silently-wrong geometry.
    tol = 1.0
    if any(abs(d) > tol for d in [dx0, dx1, dy0, dy1, achieved_cx - cx, achieved_cy - cy, achieved_depth_at_max - depth]):
        print("ERROR: verification exceeded 1mm tolerance on at least one metric; returning input unchanged.")
        return shape

    return out