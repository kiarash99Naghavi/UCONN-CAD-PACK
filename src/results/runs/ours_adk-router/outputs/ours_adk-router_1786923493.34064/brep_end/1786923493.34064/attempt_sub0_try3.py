def my_cad_function(args):
    import cadquery as cq
    import math
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_Cylinder

    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input
    base = shape.val() if hasattr(shape, "val") else shape

    # --- Resolve the sole solid ---
    solids = base.Solids()
    print(f"SELECTED: {len(solids)} solids in imported shape")
    if len(solids) != 1:
        raise ValueError(f"Expected 1 solid, found {len(solids)}")
    solid = solids[0]

    bb0 = solid.BoundingBox()
    vol0 = solid.Volume()
    print(f"BASE(now): volume={vol0:.3f} mm^3")
    print(
        f"BASE(now): bbox min=({bb0.xmin:.3f},{bb0.ymin:.3f},{bb0.zmin:.3f}) "
        f"max=({bb0.xmax:.3f},{bb0.ymax:.3f},{bb0.zmax:.3f}) "
        f"size=({bb0.xlen:.3f},{bb0.ylen:.3f},{bb0.zlen:.3f})"
    )

    # --- Targets ---
    xs = [-50.0, -34.67, -19.33, -4.0, 11.33, 26.67, 42.0]
    y0 = 0.0
    d = 5.0
    r = d / 2.0
    original_vol = 16217.422

    print("PATTERN TARGETS (mm):")
    for i, x in enumerate(xs):
        print(f"  hole[{i}] centerXY=({x:.3f},{y0:.3f}) d={d:.3f} axis=normal_to_large_±Z_planes")

    # --- Find the two large opposing Z-facing planar faces (robust: by |normal.z| and area) ---
    faces = solid.Faces()
    print(f"SELECTED: {len(faces)} faces on solid for planar-face search")

    plane_cands = []
    for fi, f in enumerate(faces):
        if f.geomType() != "PLANE":
            continue
        try:
            n = f.normalAt()
        except Exception:
            continue
        plane_cands.append((fi, f, n, f.Area(), f.Center()))

    print(f"SELECTED: {len(plane_cands)} planar faces as candidates for large ±Z planes")

    top_cands = [(fi, f, n, a, c) for (fi, f, n, a, c) in plane_cands if n.z > 0.7]
    bot_cands = [(fi, f, n, a, c) for (fi, f, n, a, c) in plane_cands if n.z < -0.7]
    print(f"SELECTED: {len(top_cands)} planar faces with n.z>0.7 (top-face candidates)")
    print(f"SELECTED: {len(bot_cands)} planar faces with n.z<-0.7 (bottom-face candidates)")

    if not top_cands or not bot_cands:
        # Fallback: use world Z axis as given, but still do *something* (slightly larger depth probe cut)
        print("WARN: Could not robustly find both ±Z planar faces; falling back to axis=(0,0,1)")
        axis_dir = cq.Vector(0.0, 0.0, 1.0)
        # Use bbox to place
        z_base = bb0.zmin - 50.0
        height = bb0.zlen + 100.0
        cyls = []
        for i, x in enumerate(xs):
            cyl = cq.Solid.makeCylinder(r, height, cq.Vector(x, y0, z_base), axis_dir)
            cyls.append(cyl)
            cbb = cyl.BoundingBox()
            print(f"TOOL(fallback): cyl[{i}] centerXY=({x:.3f},{y0:.3f}) r={r:.3f} bboxZ=[{cbb.zmin:.3f},{cbb.zmax:.3f}]")
        tool = cq.Compound.makeCompound(cyls)
        edited = solid.cut(tool)
    else:
        # Pick the largest-area top and bottom candidates
        top_fi, top_f, top_n, top_a, top_c = sorted(top_cands, key=lambda t: t[3], reverse=True)[0]
        bot_fi, bot_f, bot_n, bot_a, bot_c = sorted(bot_cands, key=lambda t: t[3], reverse=True)[0]

        print(
            f"SELECTED: 1 top planar face idx={top_fi} area={top_a:.3f} "
            f"center=({top_c.x:.3f},{top_c.y:.3f},{top_c.z:.3f}) normal=({top_n.x:.4f},{top_n.y:.4f},{top_n.z:.4f})"
        )
        print(
            f"SELECTED: 1 bottom planar face idx={bot_fi} area={bot_a:.3f} "
            f"center=({bot_c.x:.3f},{bot_c.y:.3f},{bot_c.z:.3f}) normal=({bot_n.x:.4f},{bot_n.y:.4f},{bot_n.z:.4f})"
        )

        # Drill direction: into the body from the top face
        n_top = cq.Vector(top_n.x, top_n.y, top_n.z).normalized()
        drill_dir = n_top.multiply(-1.0)  # into solid
        print(
            "DRILL AXIS (derived): "
            f"drill_dir=({drill_dir.x:.6f},{drill_dir.y:.6f},{drill_dir.z:.6f}) "
            f"(this is -normal(top_face))"
        )

        # Helper: z on a plane at given (x,y). Plane defined by (p0=top_c, n=top_n)
        def z_on_plane_at_xy(p0, n, x, y):
            # n.x*(x-x0) + n.y*(y-y0) + n.z*(z-z0) = 0
            if abs(n.z) < 1e-9:
                return None
            return p0.z - (n.x * (x - p0.x) + n.y * (y - p0.y)) / n.z

        # Build cutting cylinders: ensure they start outside the top face and extend beyond bottom
        margin_out = 50.0
        height = 250.0
        cyls = []
        print(f"TOOL PLAN: r={r:.3f} height={height:.3f} margin_out_along_top_normal={margin_out:.3f}")

        for i, x in enumerate(xs):
            z_entry = z_on_plane_at_xy(top_c, top_n, x, y0)
            if z_entry is None:
                # Fallback: use bbox mid if plane equation fails
                z_entry = 0.5 * (bb0.zmin + bb0.zmax)
                print(f"WARN: hole[{i}] top-plane evaluation failed; using z_entry={z_entry:.3f}")
            p_entry = cq.Vector(x, y0, z_entry)
            base_p = p_entry + n_top.multiply(margin_out)  # outside, on outward side

            cyl = cq.Solid.makeCylinder(r, height, base_p, drill_dir)
            cyls.append(cyl)
            cbb = cyl.BoundingBox()
            print(
                f"TOOL: cyl[{i}] entry@top≈({p_entry.x:.3f},{p_entry.y:.3f},{p_entry.z:.3f}) "
                f"base=({base_p.x:.3f},{base_p.y:.3f},{base_p.z:.3f}) "
                f"dir=({drill_dir.x:.4f},{drill_dir.y:.4f},{drill_dir.z:.4f}) "
                f"bboxZ=[{cbb.zmin:.3f},{cbb.zmax:.3f}]"
            )

        tool = cq.Compound.makeCompound(cyls)

        # Apply cut (this is the correction: re-cut along the correct normal direction)
        edited = solid.cut(tool)

    # --- Reporting: volume, bbox ---
    bb1 = edited.BoundingBox()
    vol1 = edited.Volume()
    removed_vs_in = vol0 - vol1
    removed_total_vs_orig = original_vol - vol1
    removed_pct_vs_orig = (removed_total_vs_orig / original_vol * 100.0) if original_vol > 0 else 0.0

    print(f"RESULT: volume={vol1:.3f} mm^3")
    print(f"RESULT: additional removed THIS STEP = {removed_vs_in:.3f} mm^3")
    print(
        f"RESULT: total removed vs original {original_vol:.3f} = {removed_total_vs_orig:.3f} mm^3 "
        f"({removed_pct_vs_orig:.3f}% )"
    )

    print(
        "BBOX CHECK:\n"
        f"  before min=({bb0.xmin:.3f},{bb0.ymin:.3f},{bb0.zmin:.3f}) max=({bb0.xmax:.3f},{bb0.ymax:.3f},{bb0.zmax:.3f})\n"
        f"  after  min=({bb1.xmin:.3f},{bb1.ymin:.3f},{bb1.zmin:.3f}) max=({bb1.xmax:.3f},{bb1.ymax:.3f},{bb1.zmax:.3f})\n"
        f"  delta min=({(bb1.xmin-bb0.xmin):.6f},{(bb1.ymin-bb0.ymin):.6f},{(bb1.zmin-bb0.zmin):.6f}) "
        f"delta max=({(bb1.xmax-bb0.xmax):.6f},{(bb1.ymax-bb0.ymax):.6f},{(bb1.zmax-bb0.zmax):.6f})"
    )

    # --- Verification: find cylindrical faces for the (corrected) holes by radius and print achieved centers/diameters ---
    # Widen match: accept axes close to either world Z or derived drill_dir; also accept partial trimming.
    faces1 = edited.Faces()
    hole_cyl_faces = []
    for fi, f in enumerate(faces1):
        if f.geomType() != "CYLINDER":
            continue
        try:
            ad = BRepAdaptor_Surface(f.wrapped, True)
            if ad.GetType() != GeomAbs_Cylinder:
                continue
            cyl = ad.Cylinder()
            rad = float(cyl.Radius())
            if abs(rad - r) > 0.06:
                continue
            ax = cyl.Axis()
            loc = ax.Location()
            direc = ax.Direction()
            v = cq.Vector(float(direc.X()), float(direc.Y()), float(direc.Z())).normalized()
            hole_cyl_faces.append(
                {
                    "fi": fi,
                    "r": rad,
                    "d": 2.0 * rad,
                    "p0": cq.Vector(float(loc.X()), float(loc.Y()), float(loc.Z())),
                    "v": v,
                }
            )
        except Exception:
            continue

    print(f"SELECTED: {len(hole_cyl_faces)} cylindrical faces with r~{r:.3f} for hole-wall verification")

    # For each found cylinder, compute closest point to each target point at zmid, assign nearest
    zmid = 0.5 * (bb0.zmin + bb0.zmax)
    targets = [cq.Vector(x, y0, zmid) for x in xs]

    # Score cylinders by their closest approach in XY to targets
    def closest_point_on_line(p0, v, pt):
        # returns p0 + v*t where t minimizes |(p0+v*t)-pt|
        t = v.dot(pt - p0)
        return p0 + v.multiply(t)

    used_cyl = set()
    assignments = []  # (target_idx, cyl_idx, closest_pt, dx, dy)

    for ti, tpt in enumerate(targets):
        best = None
        best_ci = None
        best_p = None
        for ci, c in enumerate(hole_cyl_faces):
            if ci in used_cyl:
                continue
            p = closest_point_on_line(c["p0"], c["v"], tpt)
            dx = p.x - tpt.x
            dy = p.y - tpt.y
            dist = math.hypot(dx, dy)
            if best is None or dist < best:
                best = dist
                best_ci = ci
                best_p = p
        if best_ci is not None:
            used_cyl.add(best_ci)
            c = hole_cyl_faces[best_ci]
            assignments.append((ti, best_ci, best_p, c))

    # Print achieved centers/diameters (achieved center is closest point to target at zmid)
    print("ACHIEVED HOLE CENTERS (closest point on cylinder axis to target @ zmid):")
    for (ti, ci, p, c) in sorted(assignments, key=lambda t: t[0]):
        dd = c["d"] - d
        dx = p.x - xs[ti]
        dy = p.y - y0
        v = c["v"]
        print(
            f"  target[{ti}] ({xs[ti]:.2f},{y0:.2f}) -> face_idx={c['fi']} "
            f"achieved≈({p.x:.3f},{p.y:.3f}) d={c['d']:.3f} "
            f"dXY=({dx:.3f},{dy:.3f}) dd={dd:.3f} axis≈({v.x:.4f},{v.y:.4f},{v.z:.4f})"
        )

    missing = [i for i in range(len(xs)) if i not in {a[0] for a in assignments}]
    if missing:
        print(f"WARN: missing hole-wall cylinder faces for targets idx={missing}")

    # Distinctness check on achieved centers
    achieved_pts = [a[2] for a in sorted(assignments, key=lambda t: t[0])]
    if len(achieved_pts) >= 2:
        min_sep = 1e9
        for i in range(len(achieved_pts)):
            for j in range(i + 1, len(achieved_pts)):
                min_sep = min(min_sep, math.hypot(achieved_pts[i].x - achieved_pts[j].x, achieved_pts[i].y - achieved_pts[j].y))
        print(f"CHECK: achieved hole center min XY separation = {min_sep:.3f} mm")

    # Breakout heuristic: cylindrical face boundary should have mostly circular edges; flag non-circles
    # (Widened match: do not require circular mouths; just detect obvious breakouts)
    noncircle_flags = 0
    for c in hole_cyl_faces:
        f = faces1[c["fi"]]
        es = f.Edges()
        types = [e.geomType() for e in es]
        noncirc = [t for t in types if t != "CIRCLE"]
        if noncirc:
            noncircle_flags += 1
            print(f"WARN: hole wall face_idx={c['fi']} has non-circular boundary edges types={sorted(set(types))} (possible breakout/overlap)")
    print(f"CHECK: {noncircle_flags} / {len(hole_cyl_faces)} hole-wall cylinder faces show non-circular boundary edges")

    # Sanity vs requested 10-15% removal of original
    if not (10.0 <= removed_pct_vs_orig <= 15.0):
        print("WARN: total removed volume percent is outside requested ~10-15% range")

    return edited