def my_cad_function(args):
    import cadquery as cq

    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input
    base = shape.val() if hasattr(shape, "val") else shape

    solids = list(base.Solids())
    print(f"Imported solids: {len(solids)}")
    if len(solids) != 2:
        print("WARNING: expected exactly 2 solids; proceeding with available solids")

    solid0 = solids[0]
    solid1 = solids[1] if len(solids) > 1 else None

    def bb_info(s):
        bb = s.BoundingBox()
        return (bb.xmin, bb.ymin, bb.zmin, bb.xmax, bb.ymax, bb.zmax)

    print("SOLID#0 pre: vol=", solid0.Volume(), " bb=", bb_info(solid0))
    if solid1:
        print("SOLID#1 pre: vol=", solid1.Volume(), " bb=", bb_info(solid1))

    # Named constants from the sub-goal
    R_out = 15.75
    Y_front = 3.175
    chamfer = 1.0
    Y_back = Y_front - chamfer
    R_front = R_out - chamfer
    print("Chamfer spec: R_out=", R_out, "Y_front=", Y_front, "Y_back=", Y_back, "R_front=", R_front)

    # Build revolved wedge cutter from triangle in (X=radius, Y=axial) on the world XY plane at Z=0
    # Triangle vertices: A(2.175, 15.75), B(3.175, 15.75), C(3.175, 14.75) in (Y,R)
    # Converted to (X,Y): A(R_out, Y_back), B(R_out, Y_front), C(R_front, Y_front)
    tri_pts = [(R_out, Y_back), (R_out, Y_front), (R_front, Y_front)]
    cutter_wp = (
        cq.Workplane("XY")
        .polyline(tri_pts)
        .close()
        .revolve(360, (0, 0, 0), (0, 1, 0))
    )
    cutter = cutter_wp.val()

    bb_c = cutter.BoundingBox()
    print(
        "Cutter: vol=", cutter.Volume(),
        " bb=", (bb_c.xmin, bb_c.ymin, bb_c.zmin, bb_c.xmax, bb_c.ymax, bb_c.zmax)
    )
    print("Cutter Y extents (should be 2.175..3.175):", bb_c.ymin, bb_c.ymax)
    print("Cutter R extents (X/Z max should be 15.75):", bb_c.xmax, bb_c.zmax)

    # Strictly local CUT on SOLID #0 only
    solid0_cut = solid0.cut(cutter)

    print("SOLID#0 post: vol=", solid0_cut.Volume(), " bb=", bb_info(solid0_cut))
    if solid1:
        print("SOLID#1 post (unchanged reference): vol=", solid1.Volume(), " bb=", bb_info(solid1))

    out = cq.Compound.makeCompound([solid0_cut] + ([solid1] if solid1 else []))
    return out