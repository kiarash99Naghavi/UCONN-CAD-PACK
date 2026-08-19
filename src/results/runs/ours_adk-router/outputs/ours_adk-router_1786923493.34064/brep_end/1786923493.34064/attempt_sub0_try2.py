def my_cad_function(args):
    import cadquery as cq
    import math

    # OCP helpers to read true cylinder axis/center
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_Cylinder

    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input
    base = shape.val() if hasattr(shape, "val") else shape

    solids = base.Solids()
    print(f"SELECTED: {len(solids)} solids in imported shape")
    if len(solids) != 1:
        raise ValueError(f"Expected 1 solid, found {len(solids)}")

    solid = solids[0]
    bb0 = solid.BoundingBox()
    vol_in = solid.Volume()
    print(f"BASE(now): volume={vol_in:.3f} mm^3")
    print(
        "BASE(now): bbox "
        f"min=({bb0.xmin:.3f},{bb0.ymin:.3f},{bb0.zmin:.3f}) "
        f"max=({bb0.xmax:.3f},{bb0.ymax:.3f},{bb0.zmax:.3f}) "
        f"size=({bb0.xlen:.3f},{bb0.ylen:.3f},{bb0.zlen:.3f})"
    )

    # Targets (absolute)
    xs = [-50.0, -34.67, -19.33, -4.0, 11.33, 26.67, 42.0]
    y0 = 0.0
    d = 5.0
    r = d / 2.0
    axis = cq.Vector(0.0, 0.0, 1.0)

    print("PATTERN TARGETS (mm):")
    for i, x in enumerate(xs):
        print(f"  hole[{i}] center=({x:.3f},{y0:.3f}) d={d:.3f} axis=(0,0,1)")

    # Build a robust through-all cutter along world Z, spanning well beyond bbox
    height = bb0.zlen + 200.0
    z_base = bb0.zmin - 100.0
    print(f"TOOL PLAN: cylinder r={r:.3f} h={height:.3f} z_base={z_base:.3f}")

    cyls = []
    for i, x in enumerate(xs):
        cyl = cq.Solid.makeCylinder(r, height, cq.Vector(x, y0, z_base), axis)
        cyls.append(cyl)
        cbb = cyl.BoundingBox()
        print(
            f"TOOL: cyl[{i}] centerXY=({x:.3f},{y0:.3f}) r={r:.3f} "
            f"bboxZ=[{cbb.zmin:.3f},{cbb.zmax:.3f}]"
        )

    # Distinctness check (targets)
    uniq = {(round(x, 3), round(y0, 3)) for x in xs}
    print(f"CHECK: distinct TARGET centers = {len(uniq)} of {len(xs)}")
    if len(uniq) != len(xs):
        raise ValueError("Duplicate target hole centers detected")

    # Diagnose which instances are still under-cut in the CURRENT (already-edited) input:
    # extra_removed ~= how much material is still present inside the ideal cutter volume.
    print("DIAG: per-hole additional removable volume if re-cut at target (mm^3):")
    per_extra = []
    for i, cyl in enumerate(cyls):
        try:
            v_after = solid.cut(cyl).Volume()
            extra = vol_in - v_after
        except Exception as e:
            extra = None
            print(f"  hole[{i}] ERROR evaluating additional removal: {e}")
        per_extra.append(extra)
        if extra is not None:
            print(f"  hole[{i}] x={xs[i]:.2f} extra_removed_if_recut={extra:.3f}")

    tool = cq.Compound.makeCompound(cyls)

    # Apply the corrective cut (re-cut same feature locations; should only deepen/complete holes)
    edited = solid.cut(tool)

    bb1 = edited.BoundingBox()
    vol_out = edited.Volume()

    # Report volume removal vs the ORIGINAL (given)
    original_vol = 16217.422
    removed_total_vs_orig = original_vol - vol_out
    removed_pct_vs_orig = (removed_total_vs_orig / original_vol * 100.0) if original_vol > 0 else 0.0

    removed_vs_in = vol_in - vol_out
    print(f"RESULT: volume={vol_out:.3f} mm^3")
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

    # --- Verification: find the 7 cylindrical hole-wall faces (r~2.5) and print achieved centers/diameters ---
    faces = edited.Faces()
    cyl_faces = []
    for fi, f in enumerate(faces):
        if f.geomType() != "CYLINDER":
            continue
        try:
            ad = BRepAdaptor_Surface(f.wrapped, True)
            if ad.GetType() != GeomAbs_Cylinder:
                continue
            cyl = ad.Cylinder()
            rad = float(cyl.Radius())
            ax = cyl.Axis()
            loc = ax.Location()
            direc = ax.Direction()
            # Axis direction check: near world Z
            dz = float(direc.Z())
            if abs(abs(dz) - 1.0) > 0.02:
                continue
            if abs(rad - r) > 0.05:
                continue
            cyl_faces.append(
                {
                    "fi": fi,
                    "r": rad,
                    "d": 2.0 * rad,
                    "x": float(loc.X()),
                    "y": float(loc.Y()),
                    "z": float(loc.Z()),
                    "dz": dz,
                }
            )
        except Exception:
            continue

    idxs = [c["fi"] for c in cyl_faces]
    print(f"SELECTED: {len(cyl_faces)} cylindrical faces r~{r:.3f} with axis ~Z for hole-wall verification idx={idxs}")

    # Sort by x to compare to target pattern
    cyl_faces_sorted = sorted(cyl_faces, key=lambda c: c["x"])

    print("ACHIEVED HOLE AXES (from cylinder surface geometry):")
    for i, c in enumerate(cyl_faces_sorted):
        print(
            f"  found[{i}] face_idx={c['fi']} centerXY≈({c['x']:.3f},{c['y']:.3f}) "
            f"d={c['d']:.3f} axisZ={c['dz']:.4f}"
        )

    # Map found holes to nearest targets
    targets = [(x, y0) for x in xs]
    used = set()
    assignments = []
    for c in cyl_faces_sorted:
        best = None
        best_j = None
        for j, (tx, ty) in enumerate(targets):
            if j in used:
                continue
            dist = abs(c["x"] - tx) + abs(c["y"] - ty)
            if best is None or dist < best:
                best = dist
                best_j = j
        if best_j is not None:
            used.add(best_j)
            assignments.append((best_j, c))

    print("VERIFY: found-to-target mapping (delta in mm):")
    for j, c in sorted(assignments, key=lambda t: t[0]):
        tx, ty = targets[j]
        dx = c["x"] - tx
        dy = c["y"] - ty
        dd = c["d"] - d
        print(f"  target[{j}] ({tx:.2f},{ty:.2f}) -> found face_idx={c['fi']} dXY=({dx:.3f},{dy:.3f}) dd={dd:.3f}")

    missing = [j for j in range(len(xs)) if j not in used]
    if missing:
        print(f"WARN: missing cylinder hole-wall faces for targets idx={missing} (will rely on the re-cut to have created them)")

    # Distinctness check on achieved centers
    if len(cyl_faces_sorted) >= 2:
        min_sep = 1e9
        for i in range(len(cyl_faces_sorted)):
            for j in range(i + 1, len(cyl_faces_sorted)):
                dx = cyl_faces_sorted[i]["x"] - cyl_faces_sorted[j]["x"]
                dy = cyl_faces_sorted[i]["y"] - cyl_faces_sorted[j]["y"]
                sep = math.hypot(dx, dy)
                min_sep = min(min_sep, sep)
        print(f"CHECK: achieved hole center min XY separation = {min_sep:.3f} mm")

    # Sanity vs requested 10-15% removal of original
    if not (10.0 <= removed_pct_vs_orig <= 15.0):
        print("WARN: total removed volume percent is outside requested ~10-15% range; indicates some holes may still break out / not fully remove intended volume")

    return edited