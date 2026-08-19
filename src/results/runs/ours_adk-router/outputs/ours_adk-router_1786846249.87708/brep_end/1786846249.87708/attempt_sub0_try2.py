def my_cad_function(args):
    import cadquery as cq
    import math

    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input
    base = shape.val() if hasattr(shape, "val") else shape

    def bb_str(bb):
        return f"([{bb.xmin:.3f},{bb.ymin:.3f},{bb.zmin:.3f}]..[{bb.xmax:.3f},{bb.ymax:.3f},{bb.zmax:.3f}])"

    # --- Resolve and print target faces by index (sanity vs provided geometry index) ---
    def _pface(i, name):
        f = base.Faces()[i]
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

    # --- Target grids (explicit) ---
    x_list = [-34.757, -9.7685, 22.7315]
    z_list = [-10.746, 0.504, 11.754]

    # Platform thickness extents from prompt
    lower_y_outer, lower_y_inner, lower_y_mid = 7.753, -4.247, 1.753
    upper_y_outer, upper_y_inner, upper_y_mid = 50.606, 38.606, 44.606

    print("Verification targets (from prompt):")
    print(f"  LOWER: y_inner={lower_y_inner}, y_outer={lower_y_outer}, y_mid={lower_y_mid}")
    print(f"  UPPER: y_inner={upper_y_inner}, y_outer={upper_y_outer}, y_mid={upper_y_mid}")
    print(f"  X targets={x_list}")
    print(f"  Z targets={z_list}")

    # --- Existing circular features (for proximity avoidance heuristic) ---
    existing_centers = []
    for e in base.Edges():
        try:
            if e.geomType() != "CIRCLE":
                continue
            r = e.radius()
            # only consider likely-through or hole edges in this part
            if not (abs(r - 2.5) < 0.35 or abs(r - 2.6) < 0.35 or abs(r - 2.4) < 0.35 or abs(r - 2.2) < 0.35):
                continue
            c = e.Center()  # ok for full circles; used only for distance heuristic
            existing_centers.append((c.x, c.y, c.z, r))
        except Exception:
            continue
    # de-dup (coarse)
    dedup = {}
    for ex, ey, ez, er in existing_centers:
        k = (round(ex, 3), round(ey, 3), round(ez, 3), round(er, 3))
        dedup[k] = (ex, ey, ez, er)
    existing_centers = list(dedup.values())
    print(f"Existing candidate circular-edge centers collected: {len(existing_centers)}")

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

    def build_platform_points(y_mid, platform_tag):
        pts = []
        shifts = 0
        for x in x_list:
            for z in z_list:
                z_use = z
                d, item = nearest_existing_dist_xz(x, y_mid, z)
                # If too close to an existing circular feature, shift ±5mm in Z toward z=0.504
                # Threshold chosen to avoid overlap with other Ø~5 features (2.5+2.6 radii + small clearance)
                if d is not None and d < 6.0:
                    target_center_z = 0.504
                    dz = -5.0 if z_use > target_center_z else 5.0
                    z_use = z_use + dz
                    shifts += 1
                    print(
                        f"{platform_tag}: shifting hole at x={x:.3f}, z={z:.3f} by {dz:+.1f}mm to z={z_use:.3f} due to proximity d={d:.3f} to existing {item}"
                    )
                pts.append((x, y_mid, z_use))
        print(f"{platform_tag}: total points={len(pts)}, shifted={shifts}")
        for (x, y, z) in pts:
            # compare to nearest target in the list
            dx = min(abs(x - xt) for xt in x_list)
            dz = min(abs(z - zt) for zt in z_list)
            print(f"  {platform_tag} hole center (x,y,z)={[round(x,3), round(y,3), round(z,3)]}  (min |dx|={dx:.3f}, min |dz|={dz:.3f})")
        return pts

    def make_cyl_cut(points_xyz, y_mid, y_inner, y_outer, radius=2.5, margin=1.0):
        # Cylinder axis: +Y, extend beyond both faces by margin
        height = (y_outer - y_inner) + 2.0 * margin
        y_base = y_mid - height / 2.0
        cyls = []
        for (x, _, z) in points_xyz:
            cyl = cq.Solid.makeCylinder(radius, height, cq.Vector(x, y_base, z), cq.Vector(0, 1, 0))
            bb = cyl.BoundingBox()
            # piercing check: cylinder ymin should be <= y_inner, ymax should be >= y_outer
            dy_low = bb.ymin - y_inner
            dy_high = bb.ymax - y_outer
            print(
                f"  cut cyl @({x:.3f},{y_mid:.3f},{z:.3f}) r={radius:.3f} => y_range=[{bb.ymin:.3f},{bb.ymax:.3f}] vs target [{y_inner:.3f},{y_outer:.3f}] (dy_low={dy_low:.3f}, dy_high={dy_high:.3f})"
            )
            cyls.append(cyl)
        return cq.Compound.makeCompound(cyls)

    solids = list(base.Solids())
    print(f"Imported solids: {len(solids)}")
    if len(solids) < 6:
        print("ERROR: Expected at least 6 solids to access #4 and #5")
        return shape

    s4 = solids[4]
    s5 = solids[5]
    bb4 = s4.BoundingBox()
    bb5 = s5.BoundingBox()
    print(f"Solid#4 bbox: {bb_str(bb4)}")
    print(f"Solid#5 bbox: {bb_str(bb5)}")

    # Build hole center grids (with optional Z shifts)
    lower_pts = build_platform_points(lower_y_mid, "LOWER")
    upper_pts = build_platform_points(upper_y_mid, "UPPER")

    # Build cutting tool compounds
    cut4 = make_cyl_cut(lower_pts, lower_y_mid, lower_y_inner, lower_y_outer, radius=2.5, margin=1.0)
    cut5 = make_cyl_cut(upper_pts, upper_y_mid, upper_y_inner, upper_y_outer, radius=2.5, margin=1.0)

    # Perform cuts on the two platform solids only
    s4_new = s4.cut(cut4)
    s5_new = s5.cut(cut5)

    # Self-check removed material per-platform
    try:
        removed4 = s4.cut(s4_new)
        bb = removed4.BoundingBox()
        print(f"Removed4 volume={removed4.Volume():.3f} mm^3 bbox={bb_str(bb)}")
        print(f"Removed4 y_range=[{bb.ymin:.3f},{bb.ymax:.3f}] expected to cover [{lower_y_inner:.3f},{lower_y_outer:.3f}]")
    except Exception as ex:
        print(f"WARNING: could not compute removed4: {ex}")

    try:
        removed5 = s5.cut(s5_new)
        bb = removed5.BoundingBox()
        print(f"Removed5 volume={removed5.Volume():.3f} mm^3 bbox={bb_str(bb)}")
        print(f"Removed5 y_range=[{bb.ymin:.3f},{bb.ymax:.3f}] expected to cover [{upper_y_inner:.3f},{upper_y_outer:.3f}]")
    except Exception as ex:
        print(f"WARNING: could not compute removed5: {ex}")

    # Reassemble compound with unchanged other solids
    new_solids = solids[:]
    new_solids[4] = s4_new
    new_solids[5] = s5_new
    out = cq.Compound.makeCompound(new_solids)

    # Global removed material sanity check
    try:
        removed = base.cut(out)
        bb = removed.BoundingBox()
        rc = removed.Center()
        print(f"TOTAL removed volume={removed.Volume():.3f} mm^3")
        print(f"TOTAL removed bbox={bb_str(bb)}")
        print(f"TOTAL removed center={[round(rc.x,3), round(rc.y,3), round(rc.z,3)]}")
    except Exception as ex:
        print(f"WARNING: could not compute total removed material via base.cut(out): {ex}")

    return out