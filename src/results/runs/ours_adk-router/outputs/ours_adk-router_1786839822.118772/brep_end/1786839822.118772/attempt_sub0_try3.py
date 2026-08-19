def my_cad_function(args):
    import cadquery as cq
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_Cylinder

    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input
    base = shape.val() if hasattr(shape, "val") else shape

    # --- Numbers named in the (possibly-stale) sub-goal ---
    r_target = 7.5
    d_target = 15.0
    z_min = -60.0
    z_max = 0.0
    h_z = z_max - z_min
    p0_xy = (15.0, 7.5)
    p1_xy = (153.0, 7.5)
    # A reference point on each intended axis (mid-Z) for "distance-to-axis" scoring
    t0 = cq.Vector(p0_xy[0], p0_xy[1], 0.5 * (z_min + z_max))
    t1 = cq.Vector(p1_xy[0], p1_xy[1], 0.5 * (z_min + z_max))

    print("SUBGOAL numbers (as given):")
    print(f"  r={r_target} (d={d_target})")
    print(f"  target axis XY: {p0_xy} and {p1_xy}")
    print(f"  requested Z span: z_min={z_min}, z_max={z_max}, h={h_z}")
    print("  reference points for axis-matching:")
    print("    t0=", t0.toTuple())
    print("    t1=", t1.toTuple())

    # --- Helper: extract cylinder axis+radius from a face ---
    def cyl_from_face(face):
        try:
            ad = BRepAdaptor_Surface(face.wrapped, True)
            if ad.GetType() != GeomAbs_Cylinder:
                return None
            c = ad.Cylinder()
            rad = float(c.Radius())
            ax = c.Axis()  # gp_Ax1
            loc = ax.Location()
            direc = ax.Direction()
            p = cq.Vector(float(loc.X()), float(loc.Y()), float(loc.Z()))
            u = cq.Vector(float(direc.X()), float(direc.Y()), float(direc.Z()))
            # Normalize just in case
            if u.Length == 0:
                return None
            u = u.normalized()
            return rad, p, u
        except Exception:
            return None

    def dist_point_to_axis(pt, p0, u_unit):
        # distance = |(pt-p0) x u|
        v = pt.sub(p0)
        return v.cross(u_unit).Length

    # --- Find candidate cylindrical faces of radius ~7.5 and print axis-direction stats ---
    faces = list(base.Faces())
    cyl_faces = []  # (fi, face, rad, p, u)
    for fi, f in enumerate(faces):
        info = cyl_from_face(f)
        if info is None:
            continue
        rad, p, u = info
        if abs(rad - r_target) <= 0.2:  # generous tolerance
            cyl_faces.append((fi, f, rad, p, u))

    print(f"Found {len(cyl_faces)} cylindrical faces with radius ~{r_target} (tol 0.2)")
    # Direction histogram-ish
    nZ = sum(1 for (_, _, _, _, u) in cyl_faces if abs(u.z) > 0.95)
    nY = sum(1 for (_, _, _, _, u) in cyl_faces if abs(u.y) > 0.95)
    nX = sum(1 for (_, _, _, _, u) in cyl_faces if abs(u.x) > 0.95)
    print(f"  axis approx: Z={nZ}, Y={nY}, X={nX} (counts among r~7.5 cyl faces)")

    # --- Pick best matching cylinder faces for each target by distance-to-axis ---
    def best_matches(target_pt, exclude_fis=set()):
        scored = []
        for fi, f, rad, p, u in cyl_faces:
            if fi in exclude_fis:
                continue
            dist = dist_point_to_axis(target_pt, p, u)
            scored.append((dist, fi, f, rad, p, u))
        scored.sort(key=lambda x: x[0])
        return scored

    scored0 = best_matches(t0)
    scored1 = best_matches(t1)

    # Take best for first, then best distinct for second
    if not scored0 or not scored1:
        print("WARNING: Could not find any r~7.5 cylindrical faces; falling back to specified Z-cylinders at given XY.")
        axes = [
            (cq.Vector(p0_xy[0], p0_xy[1], z_min), cq.Vector(0, 0, 1), h_z, "fallback@p0 Z"),
            (cq.Vector(p1_xy[0], p1_xy[1], z_min), cq.Vector(0, 0, 1), h_z, "fallback@p1 Z"),
        ]
    else:
        best0 = scored0[0]
        used = {best0[1]}
        best1 = next((s for s in scored1 if s[1] not in used), scored1[0])

        print("Matched candidate faces (by distance from target point to cylinder axis):")
        for label, best, tgt in [("hole0", best0, t0), ("hole1", best1, t1)]:
            dist, fi, f, rad, p, u = best
            print(f"  {label}: face_idx={fi}, rad={rad:.4f}")
            print(f"    axis p0={p.toTuple()}, u={u.toTuple()}, dist_to_target={dist:.6f}")
            print(f"    target={tgt.toTuple()}")

        # Build cylinders spanning full extent along each detected axis.
        # If axis is ~Z, force exact z_min..z_max for flush ends (per sub-goal).
        axes = []
        for label, best, desired_xy in [("hole0", best0, p0_xy), ("hole1", best1, p1_xy)]:
            dist, fi, f, rad, p, u = best

            if abs(u.z) > 0.95:
                # Force exact Z placement/spanning
                # Keep the detected axis X/Y if it is close; otherwise snap to requested XY.
                x_use = p.x
                y_use = p.y
                # Snap to the named coordinates if within a reasonable tolerance
                if abs(x_use - desired_xy[0]) > 2.0 or abs(y_use - desired_xy[1]) > 2.0:
                    x_use, y_use = desired_xy
                base_pt = cq.Vector(x_use, y_use, z_min)
                dir_pt = cq.Vector(0, 0, 1)
                height = h_z
                tag = f"{label}:forced Z span"
                axes.append((base_pt, dir_pt, height, tag))
            else:
                # Use the cylindrical face's own axial extents from its vertices
                verts = list(f.Vertices())
                if len(verts) == 0:
                    # fallback: use bbox projection of the face center +/- 10mm
                    base_pt = p
                    dir_pt = u
                    height = 10.0
                    tag = f"{label}:fallback short (no vertices)"
                    axes.append((base_pt, dir_pt, height, tag))
                else:
                    ts = []
                    for v in verts:
                        pv = v.Center()
                        vv = cq.Vector(pv.x, pv.y, pv.z)
                        ts.append(vv.sub(p).dot(u))
                    tmin = min(ts)
                    tmax = max(ts)
                    height = float(tmax - tmin)
                    base_pt = p.add(u.multiply(tmin))
                    dir_pt = u
                    tag = f"{label}:detected axis span (non-Z)"
                    axes.append((base_pt, dir_pt, height, tag))

        # Print what we will build
        print("Cylinder rebuild plan (base_pt, dir, height):")
        for base_pt, dir_pt, height, tag in axes:
            print(f"  {tag}: base={base_pt.toTuple()}, dir={dir_pt.toTuple()}, height={height:.6f}")

    # --- Build and fuse the two cylinders (this should extend the previously wrong/partial fill) ---
    cyl_solids = []
    for base_pt, dir_pt, height, tag in axes:
        try:
            s = cq.Solid.makeCylinder(r_target, height, base_pt, dir_pt)
            cyl_solids.append(s)
        except Exception as e:
            print(f"ERROR building cylinder for {tag}: {e}")

    if len(cyl_solids) == 0:
        print("ERROR: no cylinders built; returning base unchanged (should not happen)")
        return base

    cyls = cq.Compound.makeCompound(cyl_solids) if len(cyl_solids) > 1 else cyl_solids[0]
    out = base.fuse(cyls)

    # --- Placement self-check: isolate added material and verify extents ---
    try:
        added = out.cut(base)
        bb = added.BoundingBox()
        c_added = added.Center()
        print("ADDED material checks:")
        print("  added.Center()=", (c_added.x, c_added.y, c_added.z))
        print("  added.BoundingBox()=", bb)
        print(f"  added z extents: min={bb.zmin:.6f}, max={bb.zmax:.6f} (targets {z_min}..{z_max})")
        print(f"    deltas: dzmin={bb.zmin - z_min:.6f}, dzmax={bb.zmax - z_max:.6f}")

        # Per-cylinder bbox checks
        for i, s in enumerate(cyl_solids):
            bbi = s.BoundingBox()
            cx = 0.5 * (bbi.xmin + bbi.xmax)
            cy = 0.5 * (bbi.ymin + bbi.ymax)
            cz = 0.5 * (bbi.zmin + bbi.zmax)
            print(f"  cyl{i} bbox= {bbi}")
            print(f"    cyl{i} bbox center approx=({cx:.6f},{cy:.6f},{cz:.6f})")
            # Compare XY against requested positions (even if actual axis ends up non-Z)
            if i == 0:
                tx, ty = p0_xy
            else:
                tx, ty = p1_xy
            print(f"    cyl{i} XY delta vs named target=({cx - tx:.6f},{cy - ty:.6f})")
            print(f"    cyl{i} z-span=({bbi.zmin:.6f},{bbi.zmax:.6f}) vs named ({z_min},{z_max})")
    except Exception as e:
        print("WARNING: could not compute/print added material checks:", e)

    return out