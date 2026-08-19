def my_cad_function(args):
    import cadquery as cq
    from OCP.gp import gp_Trsf, gp_Pnt
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform

    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input

    base = shape.val() if hasattr(shape, "val") else shape

    # --- Pre-scale sanity (must remain in mm; do not change any unit settings) ---
    bb0 = base.BoundingBox()
    vol0 = base.Volume()
    print("[pre] bbox min/max:", [bb0.xmin, bb0.ymin, bb0.zmin], [bb0.xmax, bb0.ymax, bb0.zmax])
    print("[pre] bbox size   :", [bb0.xlen, bb0.ylen, bb0.zlen])
    print("[pre] volume      :", vol0)

    # --- True geometric scale: uniform 10x about (0,0,0), baked into B-Rep ---
    s = 10.0
    tr = gp_Trsf()
    tr.SetScale(gp_Pnt(0, 0, 0), s)

    # copy=True ensures the result is an independent transformed shape (baked)
    xf = BRepBuilderAPI_Transform(base.wrapped, tr, True)
    xf.Build()
    scaled = cq.Shape.cast(xf.Shape())

    # --- Verification prints (geometric coordinates, mm) ---
    bb = scaled.BoundingBox()
    vol = scaled.Volume()
    print("[post] bbox min/max:", [bb.xmin, bb.ymin, bb.zmin], [bb.xmax, bb.ymax, bb.zmax])
    print("[post] bbox size   :", [bb.xlen, bb.ylen, bb.zlen])
    print("[post] volume      :", vol, " (expect ~", vol0 * (s ** 3), ")")

    # Find largest opposing planar faces with normals ~ +/-X
    x_faces = []
    zmin_faces = []
    zmax_faces = []
    for f in scaled.Faces():
        try:
            n = f.normalAt()
        except Exception:
            continue
        a = f.Area()
        c = f.Center()
        # normals approximately +/-X
        if abs(n.x) > 0.99 and abs(n.y) < 0.05 and abs(n.z) < 0.05:
            x_faces.append((a, n.x, c.x, c.toTuple(), f))
        # normals approximately +/-Z
        if abs(n.z) > 0.99 and abs(n.x) < 0.05 and abs(n.y) < 0.05:
            if n.z < 0:
                zmin_faces.append((a, c.z, c.toTuple(), f))
            else:
                zmax_faces.append((a, c.z, c.toTuple(), f))

    x_faces.sort(key=lambda t: t[0], reverse=True)
    if len(x_faces) >= 2:
        # pick best +X and -X by area
        pos = sorted([t for t in x_faces if t[1] > 0], key=lambda t: t[0], reverse=True)
        neg = sorted([t for t in x_faces if t[1] < 0], key=lambda t: t[0], reverse=True)
        if pos and neg:
            ax, nx, xpx, cpos, _ = pos[0]
            ax2, nx2, xnx, cneg, _ = neg[0]
            print(f"[check] largest +X face: area={ax:.6f}, center.x={xpx:.6f} (target +10.0), center={cpos}")
            print(f"[check] largest -X face: area={ax2:.6f}, center.x={xnx:.6f} (target -10.0), center={cneg}")
            print(f"[check] X distance: {xpx - xnx:.6f} (target 20.0)")
        else:
            print("[check] Could not find both +X and -X planar faces")
    else:
        print("[check] Not enough +/-X planar faces detected")

    if zmin_faces:
        zmin_faces.sort(key=lambda t: t[0], reverse=True)
        a, cz, ctr, _ = zmin_faces[0]
        print(f"[check] largest -Z (bottom) face: area={a:.6f}, center.z={cz:.6f} (target -7.5), center={ctr}")
    else:
        print("[check] No -Z planar faces detected")

    if zmax_faces:
        zmax_faces.sort(key=lambda t: t[0], reverse=True)
        a, cz, ctr, _ = zmax_faces[0]
        print(f"[check] largest +Z (top) face: area={a:.6f}, center.z={cz:.6f} (target +7.5), center={ctr}")
    else:
        print("[check] No +Z planar faces detected")

    # Return as a single-body workplane
    return cq.Workplane(obj=scaled)