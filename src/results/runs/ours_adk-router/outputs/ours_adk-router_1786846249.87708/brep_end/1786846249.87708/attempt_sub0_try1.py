def my_cad_function(args):
    import cadquery as cq
    import math

    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input
    base = shape.val() if hasattr(shape, "val") else shape

    # --- Resolve and print the reference faces to verify indexing matches the provided geometry index ---
    faces = list(base.Faces())
    def _pface(i, name):
        f = faces[i]
        c = f.Center()
        try:
            a = f.Area()
        except Exception:
            a = None
        print(f"{name}: face #{i} center={[round(c.x,3), round(c.y,3), round(c.z,3)]} area={None if a is None else round(a,3)}")
        return f

    f64  = _pface(64,  "lower outward face (expected y=7.753)")
    f67  = _pface(67,  "lower inward face (expected y=-4.247)")
    f103 = _pface(103, "upper outward face (expected y=50.606)")
    f106 = _pface(106, "upper inward face (expected y=38.606)")

    # Target grids
    x_list = [-34.757, -9.7685, 22.7315]
    z_list = [-10.746, 0.504, 11.754]

    # Platform thickness extents from prompt
    lower_y_outer, lower_y_inner, lower_y_mid = 7.753, -4.247, 1.753
    upper_y_outer, upper_y_inner, upper_y_mid = 50.606, 38.606, 44.606

    # Build a simple list of existing circular-edge centers (approx) to detect close conflicts.
    # NOTE: Edge.Center() is exact for full circles; good enough for a proximity heuristic.
    existing_centers = []
    for e in base.Edges():
        try:
            if e.geomType() != "CIRCLE":
                continue
            r = e.radius()
            if not (abs(r - 2.5) < 0.25 or abs(r - 2.6) < 0.25 or abs(r - 2.4) < 0.25):
                continue
            c = e.Center()
            key = (round(c.x, 3), round(c.y, 3), round(c.z, 3), round(r, 3))
            existing_centers.append(key)
        except Exception:
            continue
    # de-dup
    existing_centers = list(dict.fromkeys(existing_centers))
    print(f"Existing candidate circle-edge centers collected: {len(existing_centers)}")

    def nearest_existing_dist_xz(x, ymid, z, ytol=1.5):
        best = None
        best_item = None
        for (ex, ey, ez, er) in existing_centers:
            if abs(ey - ymid) > ytol:
                continue
            d = math.hypot(ex - x, ez - z)
            if best is None or d < best:
                best = d
                best_item = (ex, ey, ez, er)
        return best, best_item

    def make_cyl_cut(points_xyz, y_mid, y_inner, y_outer, radius=2.5, margin=1.0):
        # Cylinder axis: +Y, extend beyond both faces by margin
        height = (y_outer - y_inner) + 2.0 * margin
        y_base = y_mid - height / 2.0
        cyls = []
        for (x, y, z) in points_xyz:
            cyl = cq.Solid.makeCylinder(radius, height, cq.Vector(x, y_base, z), cq.Vector(0, 1, 0))
            bb = cyl.BoundingBox()
            print(
                f"  cut cyl @({x:.3f},{y:.3f},{z:.3f}) r={radius} => y_range=[{bb.ymin:.3f},{bb.ymax:.3f}] (target pierce {y_inner:.3f}..{y_outer:.3f})"
            )
            cyls.append(cyl)
        return cq.Compound.makeCompound(cyls)

    def build_platform_points(y_mid, platform_tag):
        pts = []
        shifts = 0
        for x in x_list:
            for z in z_list:
                z_use = z
                d, item = nearest_existing_dist_xz(x, y_mid, z)
                # If too close to an existing circular feature, shift ±5mm in Z toward z=0.504
                if d is not None and d < 6.0:
                    target_center_z = 0.504
                    dz = -5.0 if z_use > target_center_z else 5.0
                    z_use = z_use + dz
                    shifts += 1
                    print(
                        f"{platform_tag}: shifting hole at x={x:.3f}, z={z:.3f} by {dz:+.1f}mm to z={z_use:.3f} بسبب proximity d={d:.3f} to existing {item}"
                    )
                pts.append((x, y_mid, z_use))
        print(f"{platform_tag}: total points={len(pts)}, shifted={shifts}")
        for p in pts:
            print(f"  {platform_tag} hole center (x,y,z)={[round(p[0],3), round(p[1],3), round(p[2],3)]}")
        return pts

    solids = list(base.Solids())
    print(f"Imported solids: {len(solids)}")
    if len(solids) < 6:
        print("ERROR: Expected at least 6 solids to access #4 and #5")
        return shape

    s4 = solids[4]
    s5 = solids[5]
    print(f"Solid#4 bbox: {s4.BoundingBox().toTuple()}")
    print(f"Solid#5 bbox: {s5.BoundingBox().toTuple()}")

    # Build hole center grids (with optional Z shifts)
    lower_pts = build_platform_points(lower_y_mid, "LOWER")
    upper_pts = build_platform_points(upper_y_mid, "UPPER")

    # Build cutting tool compounds
    cut4 = make_cyl_cut(lower_pts, lower_y_mid, lower_y_inner, lower_y_outer, radius=2.5, margin=1.0)
    cut5 = make_cyl_cut(upper_pts, upper_y_mid, upper_y_inner, upper_y_outer, radius=2.5, margin=1.0)

    # Perform cuts on the two platform solids only
    s4_new = s4.cut(cut4)
    s5_new = s5.cut(cut5)

    # Reassemble compound with unchanged other solids
    new_solids = solids[:]
    new_solids[4] = s4_new
    new_solids[5] = s5_new
    out = cq.Compound.makeCompound(new_solids)

    # --- Self-check: removed material isolation and rough placement checks ---
    try:
        removed = base.cut(out)  # material removed by this edit
        bb = removed.BoundingBox()
        print(f"Removed material volume={removed.Volume():.3f} mm^3")
        print(f"Removed material bbox=({bb.xmin:.3f},{bb.ymin:.3f},{bb.zmin:.3f})..({bb.xmax:.3f},{bb.ymax:.3f},{bb.zmax:.3f})")
        rc = removed.Center()
        print(f"Removed material center={[round(rc.x,3), round(rc.y,3), round(rc.z,3)]}")
        # Expected y ranges should lie within the two platform thickness zones
        print(f"Expected LOWER pierce y={lower_y_inner}..{lower_y_outer}, UPPER pierce y={upper_y_inner}..{upper_y_outer}")
    except Exception as ex:
        print(f"WARNING: could not compute removed material via base.cut(out): {ex}")

    # Verify that cut cylinders extend beyond the outward/inward faces by margin (pierce guarantee)
    print("Verification targets:")
    print(f"  LOWER mid-y={lower_y_mid}, face#64 y={lower_y_outer}, face#67 y={lower_y_inner}")
    print(f"  UPPER mid-y={upper_y_mid}, face#103 y={upper_y_outer}, face#106 y={upper_y_inner}")
    print(f"  X targets={x_list}")
    print(f"  Z targets={z_list} (with possible ±5mm shifts toward z=0.504 if proximity detected)")

    return out