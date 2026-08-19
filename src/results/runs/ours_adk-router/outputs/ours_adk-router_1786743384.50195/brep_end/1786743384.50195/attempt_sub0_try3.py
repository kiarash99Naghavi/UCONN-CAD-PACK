def my_cad_function(args):
    import cadquery as cq

    base = cq.importers.importStep(args["input_file"])
    base = base.val() if hasattr(base, "val") else base

    solids = base.Solids()
    print(f"Loaded base type={type(base).__name__}, solids={len(solids)}, faces={len(base.Faces())}, edges={len(base.Edges())}")

    def solid_fp(s):
        bb = s.BoundingBox()
        return dict(vol=s.Volume(), faces=len(s.Faces()), edges=len(s.Edges()), bb=(bb.xlen, bb.ylen, bb.zlen), c=s.Center())

    for i, s in enumerate(solids):
        fp = solid_fp(s)
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

    # Build edge->adjacent faces map (within owner)
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

    # Collect boundary edges of faces #42/#43 and score for "sharp and long".
    # Exclude edges adjacent to CYLINDER faces (already radiused tangency edges).
    cand = []
    seen = set()
    for sf in seed_faces:
        for e in sf.Edges():
            eh = e.hashCode()
            if eh in seen:
                continue
            seen.add(eh)

            # skip pure circular/elliptic edges (holes/end rounds) for this goal
            gt = e.geomType()
            if gt in ("CIRCLE", "ELLIPSE"):
                continue

            L = e.Length()
            if L < 30.0:
                continue

            adj = edge_to_faces.get(eh, [])
            # unique adjacent faces
            uniq, sh = [], set()
            for af in adj:
                hh = af.hashCode()
                if hh not in sh:
                    sh.add(hh)
                    uniq.append(af)
            if len(uniq) != 2:
                continue

            adj_types = [a.geomType() for a in uniq]
            if "CYLINDER" in adj_types:
                # very likely already a blend tangency edge
                continue

            p = e.Center()
            n0 = safe_normal_at(uniq[0], p)
            n1 = safe_normal_at(uniq[1], p)
            if n0 is None or n1 is None:
                continue
            dot = abs(n0.dot(n1))

            # only sharp edges
            if dot > 0.999:
                continue

            cand.append(dict(edge=e, L=L, center=p, adj=uniq, adj_types=adj_types, dot=dot, seed_face_hash=sf.hashCode(), geom=gt))

    print(f"Sharp (non-tangent) candidate edges on face#42/#43 boundaries: {len(cand)}")
    for i, d in enumerate(sorted(cand, key=lambda x: x["L"], reverse=True)[:25]):
        c = d["center"].toTuple()
        print(f"  cand[{i}] L={d['L']:.3f} geom={d['geom']} dot={d['dot']:.6f} adj_types={d['adj_types']} center=({c[0]:.3f},{c[1]:.3f},{c[2]:.3f})")

    r = 6.35  # mm (0.635 cm)
    print(f"Requested fillet radius r={r} mm")

    edited_owner = None
    picked = None

    # Try longer edges first (the "long perimeter edge")
    for d in sorted(cand, key=lambda x: x["L"], reverse=True):
        e = d["edge"]
        try:
            out = owner.fillet(r, [e])
            dv = out.Volume() - owner.Volume()
            if out is not None and out.isValid() and abs(dv) > 1e-6:
                edited_owner = out
                picked = d
                print("SUCCESS: filleted 1 edge.")
                print(f"  picked L={d['L']:.3f} geom={d['geom']} dot={d['dot']:.6f} adj_types={d['adj_types']}")
                cc = d["center"].toTuple()
                print(f"  picked center=({cc[0]:.3f},{cc[1]:.3f},{cc[2]:.3f})")
                print(f"  owner vol {owner.Volume():.6f} -> {out.Volume():.6f} (dV={dv:.6f})")
                break
            else:
                print(f"Fillet returned but rejected (valid={getattr(out,'isValid',lambda:None)() if out else None}, dV={dv if out else None}) on edge L={d['L']:.3f}")
        except Exception as ex:
            print(f"Fillet failed on candidate edge L={d['L']:.3f} geom={d['geom']} center={tuple(round(v,3) for v in d['center'].toTuple())}: {ex}")

    # Last-resort fallback: try ANY sharp edge in owner near the seed face planes (still 1 edge only)
    if edited_owner is None:
        print("WARNING: No candidate on face#42/#43 boundaries succeeded. Falling back to nearest sharp long edge in owner.")
        seed_pts = [sf.Center() for sf in seed_faces]

        def pt_dist(a, b):
            return (a.sub(b)).Length

        # collect sharp edges in owner
        all_cand = []
        # rebuild adjacency (already have edge_to_faces)
        for e in owner.Edges():
            gt = e.geomType()
            if gt in ("CIRCLE", "ELLIPSE"):
                continue
            L = e.Length()
            if L < 30.0:
                continue
            adj = edge_to_faces.get(e.hashCode(), [])
            uniq, sh = [], set()
            for af in adj:
                hh = af.hashCode()
                if hh not in sh:
                    sh.add(hh)
                    uniq.append(af)
            if len(uniq) != 2:
                continue
            adj_types = [a.geomType() for a in uniq]
            if "CYLINDER" in adj_types:
                continue
            p = e.Center()
            n0 = safe_normal_at(uniq[0], p)
            n1 = safe_normal_at(uniq[1], p)
            if n0 is None or n1 is None:
                continue
            dot = abs(n0.dot(n1))
            if dot > 0.999:
                continue
            # distance to closest seed face center
            dmin = min((p - sp).Length for sp in seed_pts)
            all_cand.append((dmin, -L, dict(edge=e, L=L, center=p, adj_types=adj_types, dot=dot, geom=gt)))

        all_cand.sort()
        print(f"Fallback sharp-edge pool size: {len(all_cand)}")
        for i, (dmin, negL, d) in enumerate(all_cand[:15]):
            c = d['center'].toTuple()
            print(f"  fb[{i}] dist={dmin:.3f} L={d['L']:.3f} geom={d['geom']} dot={d['dot']:.6f} adj_types={d['adj_types']} center=({c[0]:.3f},{c[1]:.3f},{c[2]:.3f})")

        for dmin, negL, d in all_cand:
            try:
                out = owner.fillet(r, [d['edge']])
                dv = out.Volume() - owner.Volume()
                if out is not None and out.isValid() and abs(dv) > 1e-6:
                    edited_owner = out
                    picked = d
                    print("SUCCESS (fallback): filleted 1 edge.")
                    print(f"  picked dist={dmin:.3f} L={d['L']:.3f} geom={d['geom']} dot={d['dot']:.6f} adj_types={d['adj_types']}")
                    cc = d['center'].toTuple()
                    print(f"  picked center=({cc[0]:.3f},{cc[1]:.3f},{cc[2]:.3f})")
                    print(f"  owner vol {owner.Volume():.6f} -> {out.Volume():.6f} (dV={dv:.6f})")
                    break
            except Exception as ex:
                pass

    if edited_owner is None:
        print("ERROR: Could not apply the requested fillet anywhere (no change will be returned).")
        # Return base anyway to avoid crash, but this will be a no-op.
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