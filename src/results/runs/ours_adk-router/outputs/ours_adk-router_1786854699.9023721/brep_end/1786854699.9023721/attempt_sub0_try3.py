def my_cad_function(args):
    import cadquery as cq
    from OCP.gp import gp_Trsf, gp_Pnt
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform

    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input

    base = shape.val() if hasattr(shape, "val") else shape

    # --- Pre-scale verification against provided geometry index ---
    faces = base.Faces()
    print(f"Pre-scale: solids={len(base.Solids())}, faces={len(faces)}, edges={len(base.Edges())}, verts={len(base.Vertices())}")
    bb0 = base.BoundingBox()
    print(f"Pre-scale bbox min=({bb0.xmin:.6g},{bb0.ymin:.6g},{bb0.zmin:.6g}) max=({bb0.xmax:.6g},{bb0.ymax:.6g},{bb0.zmax:.6g}) size=({bb0.xlen:.6g},{bb0.ylen:.6g},{bb0.zlen:.6g})")
    v0 = base.Volume()
    print(f"Pre-scale volume={v0:.12g} mm^3 (expected ~5.47)")

    def _pf(i):
        f = faces[i]
        c = f.Center()
        print(f"Pre-scale face #{i}: area={f.Area():.12g} center=({c.x:.6g},{c.y:.6g},{c.z:.6g})")
        return f

    # As per index: #10 at x=+1, #12 at x=-1, #11 bottom z=-0.75, #8/#9 top z=+0.75
    _pf(10)
    _pf(12)
    _pf(11)
    _pf(8)
    _pf(9)

    # --- True geometry scale about global origin (0,0,0) ---
    scale_factor = 10.0
    trsf = gp_Trsf()
    trsf.SetScale(gp_Pnt(0.0, 0.0, 0.0), scale_factor)
    brep_tr = BRepBuilderAPI_Transform(base.wrapped, trsf, True)  # copy=True
    scaled_wrapped = brep_tr.Shape()
    scaled = cq.Shape.cast(scaled_wrapped)

    # Ensure we keep only one solid
    solids = scaled.Solids()
    print(f"Post-scale: solids={len(solids)} (must be 1)")

    # --- Post-scale explicit validations ---
    bb = scaled.BoundingBox()
    print(f"Post-scale bbox min=({bb.xmin:.6g},{bb.ymin:.6g},{bb.zmin:.6g}) max=({bb.xmax:.6g},{bb.ymax:.6g},{bb.zmax:.6g}) size=({bb.xlen:.6g},{bb.ylen:.6g},{bb.zlen:.6g})")

    v1 = scaled.Volume()
    ratio = (v1 / v0) if v0 != 0 else float('nan')
    print(f"Post-scale volume={v1:.12g} mm^3; volume ratio={ratio:.12g} (expect ~1000x; ~5470 mm^3)")

    sf = scaled.Faces()

    def _ps(i):
        f = sf[i]
        c = f.Center()
        print(f"Post-scale face #{i}: area={f.Area():.12g} center=({c.x:.6g},{c.y:.6g},{c.z:.6g})")
        return f, c

    f10, c10 = _ps(10)
    f12, c12 = _ps(12)
    f11, c11 = _ps(11)
    f8, c8 = _ps(8)
    f9, c9 = _ps(9)

    # Distance between the two largest opposing X-normal planar faces (#10 and #12)
    dist_x_faces = (c10.sub(c12)).Length
    print(f"Check: distance between face#10 and face#12 centers = {dist_x_faces:.6g} mm (expect 20)")
    print(f"Check: face#10 center.x = {c10.x:.6g} (expect +10), face#12 center.x = {c12.x:.6g} (expect -10)")

    # Z-level checks
    print(f"Check: bottom face#11 center.z = {c11.z:.6g} (expect -7.5)")
    print(f"Check: top face#8 center.z = {c8.z:.6g} (expect +7.5)")
    print(f"Check: top face#9 center.z = {c9.z:.6g} (expect +7.5)")

    # Explicit bbox target check
    print("Check: expected bbox approx min[-10,-30,-7.5], max[10,30,7.5]")
    print(f"Check deltas: min dx={bb.xmin + 10:.6g}, dy={bb.ymin + 30:.6g}, dz={bb.zmin + 7.5:.6g}; max dx={bb.xmax - 10:.6g}, dy={bb.ymax - 30:.6g}, dz={bb.zmax - 7.5:.6g}")

    return scaled