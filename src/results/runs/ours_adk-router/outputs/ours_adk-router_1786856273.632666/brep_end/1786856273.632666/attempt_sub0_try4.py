def my_cad_function(args):
    import cadquery as cq

    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input
    base = shape.val() if hasattr(shape, "val") else shape

    def bb_tuple(s):
        bb = s.BoundingBox()
        return (bb.xmin, bb.ymin, bb.zmin, bb.xmax, bb.ymax, bb.zmax)

    def circle_info(edge):
        try:
            if edge.geomType() != "CIRCLE":
                return None
            ad = edge._geomAdaptor()
            c = ad.Circle()
            p = c.Location()
            center = (p.X(), p.Y(), p.Z())
            r = c.Radius()
            return center, r
        except Exception as e:
            print("circle_info failed:", e)
            return None

    def cyl_info(face):
        try:
            if face.geomType() != "CYLINDER":
                return None
            ad = face._geomAdaptor()
            cy = ad.Cylinder()
            r = cy.Radius()
            ax = cy.Axis().Direction()
            loc = cy.Location()
            return (loc.X(), loc.Y(), loc.Z()), (ax.X(), ax.Y(), ax.Z()), r
        except Exception as e:
            print("cyl_info failed:", e)
            return None

    # --- Resolve and print the anchor entities from the WHOLE imported shape (per provided index) ---
    try:
        f1 = base.Faces()[1]
        print("ANCHOR face#1 area", f1.Area(), "center", tuple(f1.Center().toTuple()))
    except Exception as e:
        print("Could not resolve base.Faces()[1]", e)

    try:
        f2 = base.Faces()[2]
        ci = cyl_info(f2)
        print("ANCHOR face#2 geom", f2.geomType(), "cyl_info", ci, "center", tuple(f2.Center().toTuple()))
    except Exception as e:
        print("Could not resolve base.Faces()[2]", e)

    for ei in [0, 2]:
        try:
            e = base.Edges()[ei]
            info = circle_info(e)
            print(f"ANCHOR edge#{ei} geom", e.geomType(), "circle_info", info, "edge_center", tuple(e.Center().toTuple()))
        except Exception as ex:
            print(f"Could not resolve base.Edges()[{ei}]", ex)

    # --- Isolate solids ---
    solids = base.Solids()
    print("Imported solids:", len(solids))
    for i, s in enumerate(solids):
        try:
            print(f"  solid[{i}] vol={s.Volume():.3f} bb={bb_tuple(s)} center={tuple(s.Center().toTuple())}")
        except Exception as e:
            print(f"  solid[{i}] info failed:", e)

    if len(solids) < 2:
        print("ERROR: expected 2 solids")
        return shape

    # pick SOLID #0 as the smallest volume (hub)
    idx0 = min(range(len(solids)), key=lambda i: solids[i].Volume())
    idx1 = [i for i in range(len(solids)) if i != idx0][0]
    solid0 = solids[idx0]
    solid1 = solids[idx1]
    print("Chosen SOLID#0 index:", idx0, "SOLID#1 index:", idx1)

    # --- Chamfer cut tool (ANNULAR: big cylinder minus inner frustum) ---
    # Named numbers from sub-goal
    y0 = 2.175
    y1 = 3.175
    h = y1 - y0  # 1.0
    r_cyl = 15.75
    r_top = 14.75

    print("Chamfer tool params:", {"y0": y0, "y1": y1, "h": h, "r_cyl": r_cyl, "r_top": r_top})

    p0 = cq.Vector(0, y0, 0)
    axis = cq.Vector(0, 1, 0)

    # Outer bounding cylinder for the annular removal region
    outer_cyl = cq.Solid.makeCylinder(30.0, h, pnt=p0, dir=axis)
    inner_frustum = cq.Solid.makeCone(r_cyl, r_top, h, pnt=p0, dir=axis)
    chamfer_tool = outer_cyl.cut(inner_frustum)

    bb_tool = chamfer_tool.BoundingBox()
    print("Chamfer tool bb:", (bb_tool.xmin, bb_tool.ymin, bb_tool.zmin, bb_tool.xmax, bb_tool.ymax, bb_tool.zmax))
    print("Chamfer tool y-min/y-max deltas:", bb_tool.ymin - y0, bb_tool.ymax - y1)

    # --- Apply only to SOLID #0 ---
    solid0_mod = solid0.cut(chamfer_tool)

    # Self-check: what was removed from SOLID #0
    removed = solid0.cut(solid0_mod)
    try:
        bb_rem = removed.BoundingBox()
        print("Removed volume:", removed.Volume())
        print("Removed bb:", (bb_rem.xmin, bb_rem.ymin, bb_rem.zmin, bb_rem.xmax, bb_rem.ymax, bb_rem.zmax))
        print("Removed y-extents vs [2.175,3.175]:", bb_rem.ymin, bb_rem.ymax)
    except Exception as e:
        print("Removed-shape check failed:", e)

    # ensure SOLID #0 modification stayed one solid
    try:
        sm = solid0_mod.Solids()
        print("SOLID#0_mod solids count:", len(sm))
    except Exception as e:
        print("SOLID#0_mod solids count check failed:", e)

    # --- Recombine: leave SOLID #1 untouched ---
    out = cq.Compound.makeCompound([solid0_mod, solid1])
    print("Output compound solids:", len(out.Solids()))

    return out