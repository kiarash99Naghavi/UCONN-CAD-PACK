def my_cad_function(args):
    import cadquery as cq

    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input

    base = shape.val() if hasattr(shape, "val") else shape
    solids = base.Solids()
    print(f"INPUT: solids={len(solids)} faces={len(base.Faces())} edges={len(base.Edges())} verts={len(base.Vertices())}")
    bb0 = base.BoundingBox()
    print(
        "INPUT BBOX:",
        f"xmin={bb0.xmin:.6f} xmax={bb0.xmax:.6f} ymin={bb0.ymin:.6f} ymax={bb0.ymax:.6f} zmin={bb0.zmin:.6f} zmax={bb0.zmax:.6f}",
    )

    if len(solids) != 1:
        print("ERROR: Expected exactly 1 solid; returning input unchanged")
        return base

    s0 = solids[0]

    # Resolve and print some face/edge sanity checks against the provided index
    try:
        f0 = base.Faces()[0]
        f14 = base.Faces()[14]
        print(
            "CHECK face#0:",
            f"area={f0.Area():.6f}",
            f"center={[round(c, 6) for c in f0.Center().toTuple()]}",
        )
        print(
            "CHECK face#14:",
            f"area={f14.Area():.6f}",
            f"center={[round(c, 6) for c in f14.Center().toTuple()]}",
        )
    except Exception as e:
        print("WARN: Face index sanity check failed:", repr(e))

    edges0 = s0.Edges()
    print(f"SOLID0: faces={len(s0.Faces())} edges={len(edges0)} verts={len(s0.Vertices())}")

    # Sub-goal: fillet all 36 ORIGINAL boundary edges, including edge_idx [0,2,19,21]
    target_edge_indices = list(range(len(edges0)))
    print(f"SELECTED: {len(target_edge_indices)} edges for global boundary fillet (original-only) idx={target_edge_indices}")

    for i in [0, 2, 19, 21]:
        if i < len(edges0):
            e = edges0[i]
            try:
                c = e.Center()
                print(
                    f"CHECK edge_idx[{i}]: length={e.Length():.6f} center={[round(c.x,6), round(c.y,6), round(c.z,6)]}"
                )
            except Exception as ex:
                print(f"CHECK edge_idx[{i}]: (failed to measure)", repr(ex))
        else:
            print(f"CHECK edge_idx[{i}]: OUT OF RANGE (edges={len(edges0)})")

    selected_edges = [edges0[i] for i in target_edge_indices]

    r = 0.2
    try:
        out_solid = s0.fillet(r, selected_edges)
        print(f"FILLET: applied radius={r} to {len(selected_edges)} edges in a single operation (non-recursive)")
    except Exception as e:
        print("ERROR: Fillet operation failed; returning input unchanged:", repr(e))
        return base

    out_solids = out_solid.Solids()
    print(f"OUTPUT: solids={len(out_solids)} faces={len(out_solid.Faces())} edges={len(out_solid.Edges())} verts={len(out_solid.Vertices())}")

    bb1 = out_solid.BoundingBox()
    print(
        "OUTPUT BBOX:",
        f"xmin={bb1.xmin:.6f} xmax={bb1.xmax:.6f} ymin={bb1.ymin:.6f} ymax={bb1.ymax:.6f} zmin={bb1.zmin:.6f} zmax={bb1.zmax:.6f}",
    )

    # Verify bbox against required values
    tgt = {
        "xmin": -1.0,
        "xmax": 1.0,
        "ymin": -3.0,
        "ymax": 3.0,
        "zmin": -0.75,
        "zmax": 0.75,
    }
    deltas = {
        "xmin": bb1.xmin - tgt["xmin"],
        "xmax": bb1.xmax - tgt["xmax"],
        "ymin": bb1.ymin - tgt["ymin"],
        "ymax": bb1.ymax - tgt["ymax"],
        "zmin": bb1.zmin - tgt["zmin"],
        "zmax": bb1.zmax - tgt["zmax"],
    }
    print(
        "BBOX DELTAS vs target:",
        {k: round(v, 9) for k, v in deltas.items()},
    )

    if len(out_solids) != 1:
        print("ERROR: Result is not exactly one solid; returning input unchanged")
        return base

    # Preserve compound structure if needed
    if base.ShapeType() == "Compound" and len(solids) == 1:
        return out_solid

    return out_solid