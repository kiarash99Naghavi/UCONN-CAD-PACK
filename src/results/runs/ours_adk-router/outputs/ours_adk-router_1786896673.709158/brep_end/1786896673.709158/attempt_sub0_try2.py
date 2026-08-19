def my_cad_function(args):
    import cadquery as cq
    from cadquery import selectors as sel

    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input
    base = shape.val() if hasattr(shape, "val") else shape

    # ----------------------------
    # Helpers
    # ----------------------------
    def _key(e):
        try:
            return e.hashCode()
        except Exception:
            return id(e)

    def _vtu(v):
        try:
            return (float(v.x), float(v.y), float(v.z))
        except Exception:
            try:
                return v.toTuple()
            except Exception:
                return tuple(v)

    print("Base: solids=", len(base.Solids()), "faces=", len(base.Faces()), "edges=", len(base.Edges()))

    # ----------------------------
    # Resolve targets by index (from provided geometry index)
    # ----------------------------
    faces = base.Faces()
    edges = base.Edges()

    target_face_idx = 1
    seed_edge_idx = [25, 26, 27, 29]
    fillet_r = 2.0

    if target_face_idx >= len(faces):
        print("ERROR: target_face_idx out of range:", target_face_idx, "/", len(faces))
        # do something minimal (but not a no-op): attempt fillet on the two r=7.032 edges by nearest point
        out = base
        for ei in [26, 27]:
            if ei < len(edges):
                pt = _vtu(edges[ei].Center())
                try:
                    out = cq.Workplane(obj=out).edges(sel.NearestToPointSelector(pt)).fillet(fillet_r).val()
                    print("Fallback fillet succeeded on edge idx", ei)
                except Exception as e:
                    print("Fallback fillet failed on edge idx", ei, "err=", repr(e))
        return out

    target_face = faces[target_face_idx]
    print("Resolved target face #1:")
    try:
        print("  area=", float(target_face.Area()), "center=", _vtu(target_face.Center()))
    except Exception as e:
        print("  could not print area/center:", repr(e))
    try:
        n = target_face.normalAt()  # IMPORTANT: no (u,v) args
        print("  normalAt()=", _vtu(n))
    except Exception as e:
        print("  could not print normalAt():", repr(e))

    seed_edges = []
    for i in seed_edge_idx:
        if i < len(edges):
            seed_edges.append(edges[i])
    print("Seed edges resolved:", [(i, edges[i].geomType() if i < len(edges) else None) for i in seed_edge_idx])
    for i in seed_edge_idx:
        if i < len(edges):
            e = edges[i]
            try:
                print(f"  seed edge_idx={i} type={e.geomType()} len={e.Length():.3f} center={_vtu(e.Center())}")
            except Exception as ex:
                print("  seed edge print failed for", i, repr(ex))

    if not seed_edges:
        print("ERROR: No seed edges resolved; attempting broad nearest-point fillet near expected slot center.")
        # center from index for r=7.032 arcs: [27.325, 22.101, 51.269]
        guess_pt = (27.325, 22.101, 51.269)
        try:
            return cq.Workplane(obj=base).edges(sel.NearestToPointSelector(guess_pt)).fillet(fillet_r).val()
        except Exception as e:
            print("Broad nearest-point fillet failed:", repr(e))
            return base

    seed_keys = set(_key(e) for e in seed_edges)

    # ----------------------------
    # Identify which solid owns the seed edges (by hash match count)
    # ----------------------------
    solids = list(base.Solids())
    best_si = 0
    best_match = -1
    for si, s in enumerate(solids):
        try:
            skeys = set(_key(e) for e in s.Edges())
            match = len(seed_keys.intersection(skeys))
        except Exception as e:
            print("  could not score solid", si, "err=", repr(e))
            match = 0
        print(f"Solid[{si}] seed-edge match count={match}")
        if match > best_match:
            best_match = match
            best_si = si

    target_solid = solids[best_si]
    print("Chosen target solid idx=", best_si, "match_count=", best_match, "vol=", float(target_solid.Volume()))

    # ----------------------------
    # Find the perimeter loop wire on face #1 that contains the seed edges
    # ----------------------------
    wires = list(target_face.Wires())
    print("Target face wires:", len(wires))
    best_wire = None
    best_wscore = -1
    best_wire_edges = []

    for wi, w in enumerate(wires):
        w_edges = list(w.Edges())
        w_keys = set(_key(we) for we in w_edges)
        score = len(seed_keys.intersection(w_keys))
        print(f"  Wire[{wi}] edge_count={len(w_edges)} seed_match_count={score}")
        if score > best_wscore:
            best_wscore = score
            best_wire = w
            best_wire_edges = w_edges

    if best_wire is None or best_wscore <= 0:
        print("WARNING: No wire on face #1 matched seed edges. Falling back to filleting only the seed edges.")
        best_wire_edges = seed_edges

    # Deduplicate loop edges
    uniq = {}
    for e in best_wire_edges:
        uniq[_key(e)] = e
    loop_edges = list(uniq.values())

    print("Perimeter/target edge set selected:", len(loop_edges), "(wire_score=", best_wscore, ")")
    for k, e in enumerate(loop_edges[:30]):
        try:
            print(f"  LoopEdge[{k}] type={e.geomType()} len={e.Length():.3f} center={_vtu(e.Center())}")
        except Exception as ex:
            print("  LoopEdge print failed:", repr(ex))

    # ----------------------------
    # Apply fillet on the chosen solid, then recombine with the other solid (wheel)
    # ----------------------------
    bb_before = target_solid.BoundingBox()
    vol_before = float(target_solid.Volume())
    print("Target solid bbox BEFORE:", (bb_before.xmin, bb_before.ymin, bb_before.zmin), "..", (bb_before.xmax, bb_before.ymax, bb_before.zmax))
    print("Target solid vol  BEFORE:", vol_before)

    filleted_solid = None
    try:
        filleted_solid = cq.Workplane(obj=target_solid).newObject(loop_edges).fillet(fillet_r).val()
        print("One-shot fillet succeeded on selected edge set.")
    except Exception as e:
        print("One-shot fillet FAILED; attempting sequential per-edge fillets. Error:", repr(e))
        filleted_solid = target_solid
        applied = 0
        for j, oe in enumerate(loop_edges):
            pt = _vtu(oe.Center())
            try:
                filleted_solid = cq.Workplane(obj=filleted_solid).edges(sel.NearestToPointSelector(pt)).fillet(fillet_r).val()
                applied += 1
                print(f"  Sequential fillet ok for edge#{j} near point {pt}")
            except Exception as e2:
                print(f"  Sequential fillet FAILED for edge#{j} near point {pt}. Error:", repr(e2))
        print("Sequential applied count:", applied, "/", len(loop_edges))

        # If literally nothing applied, try at least one seed-edge nearest point
        if applied == 0 and seed_edges:
            pt = _vtu(seed_edges[0].Center())
            try:
                filleted_solid = cq.Workplane(obj=filleted_solid).edges(sel.NearestToPointSelector(pt)).fillet(fillet_r).val()
                print("Last-resort: filleted one seed edge near", pt)
            except Exception as e3:
                print("Last-resort single-edge fillet also failed:", repr(e3))

    bb_after = filleted_solid.BoundingBox()
    vol_after = float(filleted_solid.Volume())
    print("Target solid bbox AFTER :", (bb_after.xmin, bb_after.ymin, bb_after.zmin), "..", (bb_after.xmax, bb_after.ymax, bb_after.zmax))
    print("Target solid vol  AFTER :", vol_after)
    print("Target solid vol  delta :", vol_after - vol_before)

    # Difference self-check (material removed by fillet)
    try:
        removed = target_solid.cut(filleted_solid)
        bb_removed = removed.BoundingBox()
        print("Removed (before - after) center:", _vtu(removed.Center()))
        print("Removed bbox:", (bb_removed.xmin, bb_removed.ymin, bb_removed.zmin), "..", (bb_removed.xmax, bb_removed.ymax, bb_removed.zmax))
    except Exception as e:
        print("Could not compute removed region for self-check:", repr(e))

    other_solids = [s for si, s in enumerate(solids) if si != best_si]
    if other_solids:
        out = cq.Compound.makeCompound([filleted_solid] + other_solids)
        print("Returned a Compound with solids:", 1 + len(other_solids))
    else:
        out = filleted_solid
        print("Returned single filleted solid.")

    bb_base = base.BoundingBox()
    bb_out = out.BoundingBox()
    print("Global bbox BEFORE:", (bb_base.xmin, bb_base.ymin, bb_base.zmin), "..", (bb_base.xmax, bb_base.ymax, bb_base.zmax))
    print("Global bbox AFTER :", (bb_out.xmin, bb_out.ymin, bb_out.zmin), "..", (bb_out.xmax, bb_out.ymax, bb_out.zmax))
    print("Global bbox delta :",
          (bb_out.xmin - bb_base.xmin, bb_out.ymin - bb_base.ymin, bb_out.zmin - bb_base.zmin), "..",
          (bb_out.xmax - bb_base.xmax, bb_out.ymax - bb_base.ymax, bb_out.zmax - bb_base.zmax))

    return out