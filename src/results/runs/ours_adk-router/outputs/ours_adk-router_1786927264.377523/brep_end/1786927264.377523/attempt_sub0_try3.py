def my_cad_function(args):
    import cadquery as cq

    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input
    base = shape.val() if hasattr(shape, "val") else shape

    def vfmt(v):
        return f"({v.x:.3f},{v.y:.3f},{v.z:.3f})"

    # Numbers named by the sub-goal
    z_from = -450.0
    z_to = -445.0
    print(f"NUMBERS: face#34 Z from {z_from} -> {z_to} (delta {z_to - z_from:+.3f} mm)")

    sols = base.Solids()
    print(f"INFO: imported solids={len(sols)}")
    if len(sols) < 1:
        print("SELECTED: 0 solids (ERROR)")
        return shape

    solid = sols[0]
    bb0 = solid.BoundingBox()
    print(f"INFO: base bbox min=({bb0.xmin:.3f},{bb0.ymin:.3f},{bb0.zmin:.3f}) max=({bb0.xmax:.3f},{bb0.ymax:.3f},{bb0.zmax:.3f})")

    faces = solid.Faces()
    print(f"INFO: solid faces={len(faces)}")

    # Resolve faces by index per instructions (diagnostic / anchoring only)
    try:
        f12 = faces[12]
        f34 = faces[34]
        c12, c34 = f12.Center(), f34.Center()
        n12, n34 = f12.normalAt(), f34.normalAt()
        print(
            "INFO: resolved face #12 "
            f"area={f12.Area():.3f} center={vfmt(c12)} normal={vfmt(n12)}"
        )
        print(
            "INFO: resolved face #34 "
            f"area={f34.Area():.3f} center={vfmt(c34)} normal={vfmt(n34)}"
        )
        print("SELECTED: 2 faces for Z-level reference/correction   idx=[12, 34]")
    except Exception as e:
        print(f"ERROR: could not access face indices 12 and/or 34: {e}")
        return shape

    # QA feedback indicates some geometry still exists at Z=-450 after trimming only face #34.
    # Robust fix: remove ALL material below Z=-445 (half-space cut) which should only affect the protruding step region.
    # Build a large box whose top is exactly at z_to; cutting it removes any material with z < z_to.
    xmid = (bb0.xmin + bb0.xmax) * 0.5
    ymid = (bb0.ymin + bb0.ymax) * 0.5
    big = 2000.0
    h = 2000.0
    box_center = (xmid, ymid, z_to - h * 0.5)
    print(f"INFO: building half-space cut box: size=({big},{big},{h}) center={box_center} so box zmax={z_to:.3f}")

    tool_wp = cq.Workplane(cq.Plane.XY()).box(big, big, h, centered=(True, True, True)).translate(box_center)
    tool = tool_wp.val()
    tbb = tool.BoundingBox()
    print("SELECTED: 1 solid tool for trimming any geometry below Z=-445 (global half-space box)")
    print(f"CHECK: tool bbox zmin={tbb.zmin:.3f} zmax={tbb.zmax:.3f} (expect zmax={z_to:.3f})")

    try:
        edited = solid.cut(tool)
    except Exception as e:
        print(f"ERROR: boolean cut failed: {e}")
        return shape

    # Self-check: isolate removed material
    removed = solid.cut(edited)
    rbb = removed.BoundingBox()
    ebb = edited.BoundingBox()

    print(f"CHECK: removed volume={removed.Volume():.3f} mm^3")
    print(
        "CHECK: removed bbox "
        f"min=({rbb.xmin:.3f},{rbb.ymin:.3f},{rbb.zmin:.3f}) max=({rbb.xmax:.3f},{rbb.ymax:.3f},{rbb.zmax:.3f}) "
        f"(expect removed z-range includes {z_from:.3f}..{z_to:.3f})"
    )
    print(
        "CHECK: edited bbox "
        f"min=({ebb.xmin:.3f},{ebb.ymin:.3f},{ebb.zmin:.3f}) max=({ebb.xmax:.3f},{ebb.ymax:.3f},{ebb.zmax:.3f})"
    )
    print(f"VERIFY: minZ before={bb0.zmin:.3f}, after={ebb.zmin:.3f}, target={z_to:.3f}")
    print(f"VERIFY: edited zmin delta vs target = {ebb.zmin - z_to:.6f} mm")

    # Extra explicit min-Z check by sampling all vertices (helps catch tiny leftovers)
    try:
        vz = [v.Z for v in edited.Vertices()]
        if vz:
            vzmin = min(vz)
            print(f"VERIFY: edited vertex minZ={vzmin:.6f} (target {z_to:.6f})  delta={vzmin - z_to:+.6f} mm")
        else:
            print("VERIFY: edited has 0 vertices (unexpected)")
    except Exception as e:
        print(f"WARN: could not compute vertex minZ: {e}")

    if len(sols) == 1:
        return edited

    out = cq.Compound.makeCompound([s for i, s in enumerate(sols) if i != 0] + [edited])
    return out