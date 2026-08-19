def my_cad_function(args):
    import cadquery as cq

    shape = cq.importers.importStep(args["input_file"])
    base = shape.val() if hasattr(shape, "val") else shape

    def solid_fingerprint(s):
        bb = s.BoundingBox()
        return dict(vol=s.Volume(), faces=len(s.Faces()), edges=len(s.Edges()), bb=(bb.xlen, bb.ylen, bb.zlen), c=s.Center())

    solids = list(base.Solids())
    print(f"Loaded base type={type(base).__name__}, solids={len(solids)}, faces={len(base.Faces())}, edges={len(base.Edges())}")
    for i, s in enumerate(solids):
        fp = solid_fingerprint(s)
        c = fp["c"].toTuple()
        bb = fp["bb"]
        print(f"  solid[{i}] vol={fp['vol']:.3f} faces={fp['faces']} edges={fp['edges']} bb=({bb[0]:.3f},{bb[1]:.3f},{bb[2]:.3f}) c=({c[0]:.3f},{c[1]:.3f},{c[2]:.3f})")

    faces = base.Faces()
    if len(faces) <= 43:
        print("ERROR: Not enough faces to resolve indices 42/43")
        return base

    f42 = faces[42]
    f43 = faces[43]
    print("Resolved face #42:", "area=", f42.Area(), "center=", tuple(round(v, 3) for v in f42.Center().toTuple()), "normal=", tuple(round(v, 3) for v in f42.normalAt().toTuple()))
    print("Resolved face #43:", "area=", f43.Area(), "center=", tuple(round(v, 3) for v in f43.Center().toTuple()), "normal=", tuple(round(v, 3) for v in f43.normalAt().toTuple()))

    h42, h43 = f42.hashCode(), f43.hashCode()

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

    owner_faces = owner.Faces()
    owner_f42 = next((ff for ff in owner_faces if ff.hashCode() == h42), None)
    owner_f43 = next((ff for ff in owner_faces if ff.hashCode() == h43), None)
    seed_faces = [ff for ff in [owner_f42, owner_f43] if ff is not None]
    if not seed_faces:
        print("ERROR: Neither face #42 nor #43 is present in the chosen owner solid")
        return base

    # edge adjacency map within owner
    edge_to_faces = {}
    for ff in owner_faces:
        for ee in ff.Edges():
            edge_to_faces.setdefault(ee.hashCode(), []).append(ff)

    def safe_normal_at(face, pt):
        try:
            n = face.normalAt(pt)
        except Exception:
            try:
                n = face.normalAt()
            except Exception:
                return None
        try:
            return n.normalized()
        except Exception:
            return None

    # Candidate long linear edges on the boundaries of face #42/#43
    # We will identify the one that is NOT tangent between its two adjacent faces.
    cand = []
    seen = set()
    for sf in seed_faces:
        for e in sf.Edges():
            eh = e.hashCode()
            if eh in seen:
                continue
            seen.add(eh)
            if e.geomType() != "LINE":
                continue
            L = e.Length()
            if L < 100.0:
                continue
            adj = edge_to_faces.get(eh, [])
            # de-dup adj by hash
            uniq = []
            sh = set()
            for af in adj:
                hh = af.hashCode()
                if hh not in sh:
                    sh.add(hh)
                    uniq.append(af)
            if len(uniq) != 2:
                continue
            p = e.Center()
            n0 = safe_normal_at(uniq[0], p)
            n1 = safe_normal_at(uniq[1], p)
            if n0 is None or n1 is None:
                dot = None
            else:
                dot = abs(n0.dot(n1))
            cand.append(dict(edge=e, L=L, center=p, adj=uniq, adj_types=[a.geomType() for a in uniq], dot=dot, seed_face_hash=sf.hashCode()))

    print(f"Long linear boundary edges found on face#42/#43: {len(cand)}")
    for i, d in enumerate(sorted(cand, key=lambda x: x["L"], reverse=True)[:20]):
        c = d["center"].toTuple()
        dot = d["dot"]
        dot_s = "None" if dot is None else f"{dot:.6f}"
        print(f"  [{i}] L={d['L']:.3f} dot(|n·n|)={dot_s} adj_types={d['adj_types']} center=({c[0]:.3f},{c[1]:.3f},{c[2]:.3f})")

    if not cand:
        print("ERROR: No long linear edges found on face#42/#43 boundaries")
        return base

    # Non-tangent edges have dot significantly less than 1.0.
    non_tangent = [d for d in cand if (d["dot"] is not None and d["dot"] < 0.999)]
    print(f"Non-tangent (sharp) long-edge candidates: {len(non_tangent)}")
    for i, d in enumerate(sorted(non_tangent, key=lambda x: x["dot"])):
        c = d["center"].toTuple()
        print(f"  sharp[{i}] L={d['L']:.3f} dot={d['dot']:.6f} adj_types={d['adj_types']} center=({c[0]:.3f},{c[1]:.3f},{c[2]:.3f})")

    if non_tangent:
        target = min(non_tangent, key=lambda x: x["dot"])
    else:
        # Fallback: pick the longest edge (should be 189.224mm family). This may still fail or do nothing.
        target = max(cand, key=lambda x: x["L"])
        print("WARNING: No clearly non-tangent long edge detected; falling back to longest long edge.")

    target_edge = target["edge"]
    tec = target_edge.Center().toTuple()
    print(f"Selected target edge: L={target_edge.Length():.3f} geom={target_edge.geomType()} dot={target['dot']} adj_types={target['adj_types']} center=({tec[0]:.3f},{tec[1]:.3f},{tec[2]:.3f})")

    r = 6.35  # mm (0.635 cm)
    print(f"Applying fillet radius r={r} mm to exactly 1 edge")

    try:
        edited_owner = owner.fillet(r, [target_edge])
        print("Fillet call returned. valid=", edited_owner.isValid(), "owner vol:", owner.Volume(), "->", edited_owner.Volume())
    except Exception as ex:
        print("ERROR: Fillet failed on selected edge:", ex)
        return base

    untouched = [s for s in solids if s is not owner]
    result = cq.Compound.makeCompound(untouched + [edited_owner]) if len(solids) > 1 else edited_owner

    total_vol_before = sum(s.Volume() for s in solids)
    total_vol_after = sum(s.Volume() for s in result.Solids())
    bb0 = base.BoundingBox()
    bb1 = result.BoundingBox()
    print(f"Result solids: {len(solids)} -> {len(result.Solids())}")
    print(f"Total volume: {total_vol_before:.6f} -> {total_vol_after:.6f} (dV={total_vol_after-total_vol_before:.6f})")
    print(f"BBox before: ({bb0.xlen:.3f},{bb0.ylen:.3f},{bb0.zlen:.3f}) after: ({bb1.xlen:.3f},{bb1.ylen:.3f},{bb1.zlen:.3f})")
    print("Result valid=", result.isValid())

    return result