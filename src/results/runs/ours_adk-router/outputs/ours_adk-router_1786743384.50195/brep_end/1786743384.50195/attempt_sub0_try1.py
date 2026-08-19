def my_cad_function(args):
    import cadquery as cq

    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input
    base = shape.val() if hasattr(shape, "val") else shape

    def bb_info(s):
        bb = s.BoundingBox()
        return (bb.xlen, bb.ylen, bb.zlen, bb.xmin, bb.ymin, bb.zmin, bb.xmax, bb.ymax, bb.zmax)

    # Inventory solids
    solids = list(base.Solids())
    print(f"Loaded base type={type(base).__name__}, solids={len(solids)}, faces={len(base.Faces())}, edges={len(base.Edges())}")
    for i, s in enumerate(solids):
        bb = s.BoundingBox()
        print(f"  solid[{i}] vol={s.Volume():.3f} faces={len(s.Faces())} edges={len(s.Edges())} bb=({bb.xlen:.3f},{bb.ylen:.3f},{bb.zlen:.3f}) c=({s.Center().x:.3f},{s.Center().y:.3f},{s.Center().z:.3f})")

    # Resolve faces by provided indices
    faces = base.Faces()
    if len(faces) <= 43:
        print("ERROR: Not enough faces to resolve indices 42/43")
        return base

    f42 = faces[42]
    f43 = faces[43]
    print("Resolved face #42:", "area=", f42.Area(), "center=", tuple(round(v, 3) for v in f42.Center().toTuple()), "normal=", tuple(round(v, 3) for v in f42.normalAt().toTuple()))
    print("Resolved face #43:", "area=", f43.Area(), "center=", tuple(round(v, 3) for v in f43.Center().toTuple()), "normal=", tuple(round(v, 3) for v in f43.normalAt().toTuple()))

    # Find the solid that contains these faces (by face hash)
    h42 = f42.hashCode()
    h43 = f43.hashCode()

    owner = None
    for s in solids:
        hs = {ff.hashCode() for ff in s.Faces()}
        if (h42 in hs) or (h43 in hs):
            owner = s
            break

    if owner is None:
        print("ERROR: Could not find owning solid for face #42/#43")
        return base

    print("Owner solid:", "vol=", owner.Volume(), "faces=", len(owner.Faces()), "edges=", len(owner.Edges()))

    # Get the matching face objects from the owning solid (to avoid stale/topology mismatch)
    owner_faces = owner.Faces()
    owner_f42 = next((ff for ff in owner_faces if ff.hashCode() == h42), None)
    owner_f43 = next((ff for ff in owner_faces if ff.hashCode() == h43), None)

    # If a face isn't in the owner (e.g. split across solids), just use whichever is present
    seed_faces = [ff for ff in [owner_f42, owner_f43] if ff is not None]
    if not seed_faces:
        print("ERROR: Neither face #42 nor #43 is present in the chosen owner solid")
        return base

    # Build edge->adjacent faces map for the owner solid
    edge_to_faces = {}
    for ff in owner_faces:
        for ee in ff.Edges():
            edge_to_faces.setdefault(ee.hashCode(), []).append(ff)

    # Candidate edges are boundary edges of those planar faces that are still sharp:
    # adjacent faces exactly 2 and both are PLANEs. Choose the longest such edge.
    cand = []
    seen_e = set()
    for sf in seed_faces:
        for e in sf.Edges():
            eh = e.hashCode()
            if eh in seen_e:
                continue
            seen_e.add(eh)
            adj = edge_to_faces.get(eh, [])
            adj_types = [a.geomType() for a in adj]
            is_sharp_plane_plane = (len(adj) == 2 and all(t == "PLANE" for t in adj_types))
            cand.append({
                "edge": e,
                "len": e.Length(),
                "center": e.Center(),
                "geom": e.geomType(),
                "adj_n": len(adj),
                "adj_types": adj_types,
                "sharp": is_sharp_plane_plane,
            })

    # Print a small table of the longest edges on these faces to confirm selection
    cand_sorted = sorted(cand, key=lambda d: d["len"], reverse=True)
    print("Top candidate edges on faces #42/#43 (by length):")
    for i, d in enumerate(cand_sorted[:12]):
        c = d["center"].toTuple()
        print(f"  [{i}] L={d['len']:.3f} geom={d['geom']} adj={d['adj_n']} adj_types={d['adj_types']} sharp_plane_plane={d['sharp']} center=({c[0]:.3f},{c[1]:.3f},{c[2]:.3f})")

    sharp = [d for d in cand_sorted if d["sharp"]]
    # Further ensure we are picking a long perimeter edge, not a short end edge
    # (threshold based on part scale; bbox diagonal is large ~500mm, so 30mm is safely "long" here)
    sharp_long = [d for d in sharp if d["len"] > 30.0]

    if not sharp_long:
        print("ERROR: No sharp long PLANE-PLANE edge found on face #42/#43 boundary")
        return base

    target_edge = sharp_long[0]["edge"]
    tec = target_edge.Center().toTuple()
    print(f"Selected target edge: L={target_edge.Length():.3f} geom={target_edge.geomType()} center=({tec[0]:.3f},{tec[1]:.3f},{tec[2]:.3f})")

    r = 6.35  # mm (0.635 cm)
    print("Applying fillet radius r=", r, "mm")

    # Apply fillet to only that one edge
    edited_owner = owner
    try:
        edited_owner = owner.fillet(r, [target_edge])
        print("Fillet succeeded. valid=", edited_owner.isValid(), "vol:", owner.Volume(), "->", edited_owner.Volume())
    except Exception as ex:
        print("Fillet failed on selected edge:", ex)
        return base

    # Reassemble compound with untouched solids
    untouched = [s for s in solids if s is not owner]
    result = cq.Compound.makeCompound(untouched + [edited_owner]) if len(solids) > 1 else edited_owner

    # Print result summary
    res_solids = list(result.Solids())
    total_vol_before = sum(s.Volume() for s in solids)
    total_vol_after = sum(s.Volume() for s in res_solids)
    bb0 = base.BoundingBox()
    bb1 = result.BoundingBox()
    print(f"Result solids: {len(solids)} -> {len(res_solids)}")
    print(f"Total volume: {total_vol_before:.3f} -> {total_vol_after:.3f} (dV={total_vol_after-total_vol_before:.3f})")
    print(f"BBox before: ({bb0.xlen:.3f},{bb0.ylen:.3f},{bb0.zlen:.3f})  after: ({bb1.xlen:.3f},{bb1.ylen:.3f},{bb1.zlen:.3f})")
    print("Result valid=", result.isValid())

    return result