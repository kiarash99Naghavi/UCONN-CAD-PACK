def my_cad_function(args):
    import cadquery as cq
    from cadquery import selectors as sel

    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input
    base = shape.val() if hasattr(shape, "val") else shape

    # --- Numbers explicitly named by the sub-goal ---
    fillet_r = 2.0
    seed_edge_indices = [25, 26, 27, 29]
    print("Requested fillet radius:", fillet_r)
    print("Seed edge indices:", seed_edge_indices)
    print("Seed circular families mentioned: r=3.0 (edge_idx 25,29) and r=7.032 (edge_idx 26,27)")

    # --- Resolve face #1 and verify basic measurements ---
    face_idx = 1
    f1 = base.Faces()[face_idx]
    print("Resolved face #1: geomType=", f1.geomType(), "area=", f1.Area(), "center=", f1.Center())

    # --- Resolve seed edges from the whole imported shape index ---
    all_edges = base.Edges()
    seed_edges = []
    for i in seed_edge_indices:
        e = all_edges[i]
        seed_edges.append(e)
        try:
            vs = e.Vertices()
            vpts = [v.toTuple() for v in vs]
        except Exception:
            vpts = []
        print(f"Edge[{i}]: type={e.geomType()} len={e.Length():.3f} center={e.Center()} verts={vpts}")

    def _is_same(a, b):
        try:
            return a.isSame(b)
        except Exception:
            return False

    # --- Find which SOLID contains the seed edges (file has 2 solids) ---
    solids = list(base.Solids())
    print("Solids in imported shape:", len(solids))
    solid_match_counts = []
    for si, s in enumerate(solids):
        sedges = list(s.Edges())
        m = 0
        for se in seed_edges:
            if any(_is_same(se, ee) for ee in sedges):
                m += 1
        solid_match_counts.append(m)
        bb = s.BoundingBox()
        print(f"Solid[{si}] match_count={m} vol={s.Volume():.3f} bbox=({bb.xmin:.3f},{bb.ymin:.3f},{bb.zmin:.3f})..({bb.xmax:.3f},{bb.ymax:.3f},{bb.zmax:.3f})")

    target_solid_idx = max(range(len(solids)), key=lambda k: solid_match_counts[k])
    target_solid = solids[target_solid_idx]
    print("Target solid chosen:", target_solid_idx, "with seed-edge matches:", solid_match_counts[target_solid_idx])

    # --- Find the specific wire (edge loop) on face #1 that contains the seed edges ---
    wires = list(f1.Wires())
    print("Face #1 wire count:", len(wires))
    wire_scores = []
    for wi, w in enumerate(wires):
        wedges = list(w.Edges())
        score = 0
        for se in seed_edges:
            if any(_is_same(se, we) for we in wedges):
                score += 1
        wire_scores.append(score)
        print(f"  Wire[{wi}] edge_count={len(wedges)} seed_match_count={score}")

    if not wires:
        print("ERROR: Face #1 has no wires; no fillet applied.")
        return shape

    best_wire_idx = max(range(len(wires)), key=lambda k: wire_scores[k])
    best_wire = wires[best_wire_idx]
    best_wire_edges = list(best_wire.Edges())
    print("Best wire index:", best_wire_idx, "with seed matches:", wire_scores[best_wire_idx], "wire edge_count:", len(best_wire_edges))

    # --- Map best-wire edges to edges of the target solid (so fillet is applied on that solid) ---
    target_edges = list(target_solid.Edges())
    mapped_edges = []
    unmapped = 0
    for we in best_wire_edges:
        match = None
        for te in target_edges:
            if _is_same(we, te):
                match = te
                break
        if match is None:
            unmapped += 1
        else:
            mapped_edges.append(match)

    print("Mapped edges to target solid:", len(mapped_edges), "/", len(best_wire_edges), "unmapped:", unmapped)
    if len(mapped_edges) == 0:
        print("ERROR: Could not map any perimeter edges onto the target solid; no fillet applied.")
        return shape

    # --- Fillet operation ---
    bb_before = target_solid.BoundingBox()
    print("Target solid bbox BEFORE:", (bb_before.xmin, bb_before.ymin, bb_before.zmin), "..", (bb_before.xmax, bb_before.ymax, bb_before.zmax))

    filleted_solid = None
    try:
        filleted_solid = cq.Workplane(obj=target_solid).newObject(mapped_edges).fillet(fillet_r).val()
        print("One-shot fillet succeeded on mapped perimeter loop edges.")
    except Exception as e:
        print("One-shot fillet FAILED; attempting nearest-point per-edge fallback. Error:", repr(e))
        # Fallback: iterative fillet using nearest-to-point selection (topology changes each step)
        filleted_solid = target_solid
        for j, oe in enumerate(mapped_edges):
            pt = oe.Center()
            try:
                filleted_solid = cq.Workplane(obj=filleted_solid).edges(sel.NearestToPointSelector(pt.toTuple())).fillet(fillet_r).val()
                print(f"  Fallback fillet ok for edge#{j} near point {pt}")
            except Exception as e2:
                print(f"  Fallback fillet FAILED for edge#{j} near point {pt}. Error:", repr(e2))

    bb_after = filleted_solid.BoundingBox()
    print("Target solid bbox AFTER:", (bb_after.xmin, bb_after.ymin, bb_after.zmin), "..", (bb_after.xmax, bb_after.ymax, bb_after.zmax))
    print("Target solid bbox delta:", (bb_after.xmin - bb_before.xmin, bb_after.ymin - bb_before.ymin, bb_after.zmin - bb_before.zmin), "..",
          (bb_after.xmax - bb_before.xmax, bb_after.ymax - bb_before.ymax, bb_after.zmax - bb_before.zmax))

    # --- Recombine solids without moving them ---
    other_solids = [s for si, s in enumerate(solids) if si != target_solid_idx]
    if other_solids:
        out = cq.Compound.makeCompound([filleted_solid] + other_solids)
        print("Returned a Compound with solids:", 1 + len(other_solids))
    else:
        out = filleted_solid
        print("Returned single filleted solid.")

    # Global bbox sanity check (should not expand)
    bb_base = base.BoundingBox()
    bb_out = out.BoundingBox()
    print("Global bbox BEFORE:", (bb_base.xmin, bb_base.ymin, bb_base.zmin), "..", (bb_base.xmax, bb_base.ymax, bb_base.zmax))
    print("Global bbox AFTER :", (bb_out.xmin, bb_out.ymin, bb_out.zmin), "..", (bb_out.xmax, bb_out.ymax, bb_out.zmax))
    print("Global bbox delta :", (bb_out.xmin - bb_base.xmin, bb_out.ymin - bb_base.ymin, bb_out.zmin - bb_base.zmin), "..",
          (bb_out.xmax - bb_base.xmax, bb_out.ymax - bb_base.ymax, bb_out.zmax - bb_base.zmax))

    return out