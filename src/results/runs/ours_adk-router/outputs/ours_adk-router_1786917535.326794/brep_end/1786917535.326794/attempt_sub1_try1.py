def my_cad_function(args):
    import cadquery as cq

    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input
    base = shape.val() if hasattr(shape, "val") else shape

    # --- Resolve and report the referenced face (#11) ---
    faces = base.Faces()
    print(f"INFO: base has {len(faces)} faces")
    f11 = faces[11]
    c11 = f11.Center()
    n11 = f11.normalAt()
    print(
        "SELECTED: 1 face for reference face #11 "
        f"center=({c11.x:.3f},{c11.y:.3f},{c11.z:.3f}) normal=({n11.x:.3f},{n11.y:.3f},{n11.z:.3f}) area={f11.Area():.3f}"
    )

    # --- Parameters from sub-goal (explicit absolute anchors) ---
    hole_d = 0.5
    hole_r = hole_d / 2.0
    # flange thickness region to cut
    z_bot = -0.75
    z_top = -0.25
    flange_thk = z_top - z_bot  # 0.5

    # hole centers (absolute)
    centers_xy = [(2.4, 4.4), (2.4, -4.4), (-2.4, 4.4), (-2.4, -4.4)]

    # hole axis: normal to bottom face, stated as [0,0,-1]
    axis = cq.Vector(0.0, 0.0, -1.0)

    print(
        "INFO: target hole numbers: "
        f"D={hole_d} (r={hole_r}); z_bot={z_bot}, z_top={z_top}, flange_thk={flange_thk}; "
        f"axis=({axis.x:.1f},{axis.y:.1f},{axis.z:.1f}); centers_xy={centers_xy}"
    )

    # --- Build 4 cylindrical cut tools (limited to flange thickness; allow extra only BELOW z_bot) ---
    # Start on z_top and extrude downward; do NOT go above z_top to avoid cutting into the upper body.
    extra_below = 0.05
    tool_h = flange_thk + extra_below  # 0.55 -> z range [-0.25 .. -0.80]
    cylinders = []
    for (x, y) in centers_xy:
        p0 = cq.Vector(x, y, z_top)  # cylinder starts at top of flange
        cyl = cq.Solid.makeCylinder(hole_r, tool_h, p0, axis)
        cylinders.append(cyl)

    print(f"SELECTED: {len(cylinders)} solids for hole cut tools (cylinders)")

    tool = cq.Compound.makeCompound(cylinders)
    bb_tool = tool.BoundingBox()
    print(
        "CHECK: hole tool compound bbox "
        f"x=[{bb_tool.xmin:.3f},{bb_tool.xmax:.3f}] y=[{bb_tool.ymin:.3f},{bb_tool.ymax:.3f}] z=[{bb_tool.zmin:.3f},{bb_tool.zmax:.3f}] (must have zmax<=-0.250)"
    )
    print(f"CHECK: tool zmax - z_top = {bb_tool.zmax - z_top:.6f} (should be 0.0)")

    # --- Cut ---
    out = base.cut(tool)

    # --- Verify achieved hole rims by finding circular edges of radius ~0.25 near z=-0.75/-0.25 ---
    circ_edges = []
    for i, e in enumerate(out.Edges()):
        try:
            if e.geomType() != "CIRCLE":
                continue
            r = e.radius()
            if abs(r - hole_r) > 0.02:
                continue
            ce = e.Center()  # for full circles, centroid is the true center
            # Only consider rims near the flange faces
            if abs(ce.z - z_top) < 0.05 or abs(ce.z - z_bot) < 0.05:
                circ_edges.append((i, e, r, ce))
        except Exception:
            continue

    print(f"SELECTED: {len(circ_edges)} circular edges (r~{hole_r}) for hole-rim verification")
    if len(circ_edges) > 0:
        idxs = [t[0] for t in circ_edges]
        print(f"INFO: verification circle edge indices={idxs}")

    # Group found circles by (x,y) rounded
    def rkey(v, nd=3):
        return (round(v.x, nd), round(v.y, nd))

    found = {}
    for (ei, e, r, ce) in circ_edges:
        k = rkey(ce, 3)
        found.setdefault(k, []).append((ei, r, ce))

    # Report achieved centers and diameters per requested targets
    tol_xy = 0.05
    for (x, y) in centers_xy:
        k = (round(x, 3), round(y, 3))
        # Find nearest key in found dict
        nearest_k = None
        nearest_d = 1e9
        for fk in found.keys():
            dx = fk[0] - k[0]
            dy = fk[1] - k[1]
            d = (dx * dx + dy * dy) ** 0.5
            if d < nearest_d:
                nearest_d = d
                nearest_k = fk

        if nearest_k is None or nearest_d > 10:
            print(f"CHECK: HOLE target center=({x:.3f},{y:.3f})mm -> NO matching circular rims found")
            continue

        rims = found.get(nearest_k, [])
        # Take representative rim (prefer z near z_top)
        rims_sorted = sorted(rims, key=lambda t: abs(t[2].z - z_top))
        rep = rims_sorted[0]
        rep_center = rep[2]
        rep_diam = 2.0 * rep[1]
        dx = rep_center.x - x
        dy = rep_center.y - y
        print(
            "CHECK: HOLE achieved "
            f"target=({x:.3f},{y:.3f})mm achieved=({rep_center.x:.3f},{rep_center.y:.3f},{rep_center.z:.3f})mm "
            f"D={rep_diam:.3f}mm  dxy=({dx:.3f},{dy:.3f})"
        )

        if abs(dx) > tol_xy or abs(dy) > tol_xy:
            # Correct within same attempt by re-cutting from original base with correct absolute coords (should not happen)
            print("WARNING: hole center mismatch beyond tolerance; re-importing and re-cutting with explicit absolute coords")
            shape2 = cq.importers.importStep(args["input_file"])
            base2 = shape2.val() if hasattr(shape2, "val") else shape2
            cylinders2 = []
            for (xx, yy) in centers_xy:
                p0 = cq.Vector(xx, yy, z_top)
                cylinders2.append(cq.Solid.makeCylinder(hole_r, tool_h, p0, axis))
            tool2 = cq.Compound.makeCompound(cylinders2)
            out = base2.cut(tool2)
            break

    return out