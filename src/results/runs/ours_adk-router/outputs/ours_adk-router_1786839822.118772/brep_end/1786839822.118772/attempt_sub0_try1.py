def my_cad_function(args):
    import cadquery as cq
    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input

    base = shape.val() if hasattr(shape, "val") else shape

    # --- Resolve target cylindrical faces (per provided geometry index) ---
    f0 = base.Faces()[0]
    f1 = base.Faces()[1]
    c0 = f0.Center()
    c1 = f1.Center()
    print("Target cylindrical face #0 center:", (c0.x, c0.y, c0.z), "(expected ~ (153.0, 7.5, -30.0))")
    print("Target cylindrical face #1 center:", (c1.x, c1.y, c1.z), "(expected ~ (15.0, 7.5, -30.0))")

    # Also print the referenced circular edges (edge_idx [1,2,4,5]) as a sanity check
    for ei in [1, 2, 4, 5]:
        e = base.Edges()[ei]
        ec = e.Center()
        print(f"Edge #{ei} center:", (ec.x, ec.y, ec.z))

    # --- Numbers named by the sub-goal ---
    r = 7.5
    z0 = -60.0
    z1 = 0.0
    h = z1 - z0  # 60
    pA = (15.0, 7.5)
    pB = (153.0, 7.5)
    print("Requested cylinders: r=", r, "z-span=", (z0, z1), "axes at", pA, "and", pB)

    # --- Build two coaxial cylinders spanning the full part height (flush at z=-60 and z=0) ---
    cylA = cq.Solid.makeCylinder(r, h, cq.Vector(pA[0], pA[1], z0), cq.Vector(0, 0, 1))
    cylB = cq.Solid.makeCylinder(r, h, cq.Vector(pB[0], pB[1], z0), cq.Vector(0, 0, 1))

    # Union them into the existing solid
    out = cq.Workplane(obj=base).union(cylA).union(cylB)

    # --- Placement self-check: isolate the added material and compare to targets ---
    out_solid = out.val()
    added = out_solid.cut(base)
    bb = added.BoundingBox()
    cc = added.Center()
    print("ADDED material center:", (cc.x, cc.y, cc.z))
    print("ADDED material bbox min:", (bb.xmin, bb.ymin, bb.zmin), "max:", (bb.xmax, bb.ymax, bb.zmax))
    print("Check Z flush: zmin delta", bb.zmin - z0, "zmax delta", bb.zmax - z1)

    # Check that the XY centers of each cylinder land correctly by slicing added into two halves by X
    # (simple heuristic: compute centers of two bounding boxes from two separate solids)
    addedA = cylA.cut(base)
    addedB = cylB.cut(base)
    bbA = addedA.BoundingBox(); cA = addedA.Center()
    bbB = addedB.BoundingBox(); cB = addedB.Center()
    print("Added cylA center:", (cA.x, cA.y, cA.z), "expected XY", pA)
    print("Added cylA bbox z:", (bbA.zmin, bbA.zmax), "expected", (z0, z1))
    print("Added cylB center:", (cB.x, cB.y, cB.z), "expected XY", pB)
    print("Added cylB bbox z:", (bbB.zmin, bbB.zmax), "expected", (z0, z1))
    print("XY deltas cylA:", (cA.x - pA[0], cA.y - pA[1]), "cylB:", (cB.x - pB[0], cB.y - pB[1]))

    return out