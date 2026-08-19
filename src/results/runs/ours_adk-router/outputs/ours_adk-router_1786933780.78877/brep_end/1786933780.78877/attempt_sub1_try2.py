def my_cad_function(args):
    import cadquery as cq
    import math
    from math import isfinite

    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input

    base = shape.val() if hasattr(shape, "val") else shape
    sols = base.Solids()
    print(f"INFO: imported solids={len(sols)}")
    if not sols:
        print("ERROR: no solids found -> returning original")
        return shape

    s0 = sols[0]
    orig_bb = s0.BoundingBox()
    print(
        f"ORIG BBOX: min=({orig_bb.xmin:.3f},{orig_bb.ymin:.3f},{orig_bb.zmin:.3f}) "
        f"max=({orig_bb.xmax:.3f},{orig_bb.ymax:.3f},{orig_bb.zmax:.3f})"
    )

    # --- Hard references from the provided geometry index (do not guess) ---
    # face #12 from index: center [-556.62, -501.352, 28.177], normal [-0.0, -0.259, -0.966]
    c12_ref = cq.Vector(-556.62, -501.352, 28.177)
    n12_ref = cq.Vector(0.0, -0.259, -0.966).normalized()
    print(
        "REFS: face#12 (index) center="
        f"({c12_ref.x:.3f},{c12_ref.y:.3f},{c12_ref.z:.3f}) normal="
        f"({n12_ref.x:.3f},{n12_ref.y:.3f},{n12_ref.z:.3f})"
    )

    fillet_r = 2.0
    ledge_thickness = 5.0
    print(f"INFO: target fillet radius={fillet_r:.3f} mm")
    print(f"INFO: expected ledge thickness reference={ledge_thickness:.3f} mm")

    faces = s0.Faces()
    edges0 = s0.Edges()
    print(f"INFO: solid faces={len(faces)} edges={len(edges0)}")

    # --- Find the actual bottom reference plane corresponding to face #12 (by center proximity + normal) ---
    planar = [(i, f) for i, f in enumerate(faces) if f.geomType() == "PLANE"]
    print(f"INFO: planar faces in current solid={len(planar)}")

    bottom_cands = []
    for i, f in planar:
        n = f.normalAt().normalized()
        dot = n.dot(n12_ref)
        if dot > 0.999:
            c = f.Center()
            dist = cq.Vector(c.x - c12_ref.x, c.y - c12_ref.y, c.z - c12_ref.z).Length
            bottom_cands.append((dist, f.Area(), i, f, c, n, dot))

    print(f"SELECTED: {len(bottom_cands)} planar faces matching face#12 normal direction (dot>0.999) for bottom reference candidates")
    for k, (dist, area, i, _, c, n, dot) in enumerate(sorted(bottom_cands, key=lambda t: t[0])[:10]):
        print(
            f"  cand[{k}]: face_idx={i} dist_to_ref_center={dist:.3f} area={area:.3f} "
            f"center=({c.x:.3f},{c.y:.3f},{c.z:.3f}) normal=({n.x:.3f},{n.y:.3f},{n.z:.3f}) dot_ref={dot:.6f}"
        )
    if not bottom_cands:
        print("ERROR: could not find any planar face with face#12 normal direction -> returning original")
        return shape

    # choose the closest center to the indexed face#12 center
    dist_b, area_b, idx_b, f_bottom, c_bottom, n_bottom, dot_b = min(bottom_cands, key=lambda t: t[0])
    print(
        f"RESOLVED: bottom reference face face_idx={idx_b} dist_to_index_center={dist_b:.3f} "
        f"area={area_b:.3f} center=({c_bottom.x:.3f},{c_bottom.y:.3f},{c_bottom.z:.3f}) "
        f"normal=({n_bottom.x:.3f},{n_bottom.y:.3f},{n_bottom.z:.3f})"
    )

    def sd_to_bottom_plane(p: cq.Vector) -> float:
        return n_bottom.dot(cq.Vector(p.x - c_bottom.x, p.y - c_bottom.y, p.z - c_bottom.z))

    # --- Find ledge top face(s): planar, same normal direction, at sd ~= -5mm ---
    top_tol = 2.0  # generous; the previous attempt died due to over-tight tolerance
    top_faces = []
    for i, f in planar:
        n = f.normalAt().normalized()
        if n.dot(n_bottom) > 0.999:
            c = f.Center()
            sdv = sd_to_bottom_plane(c)
            if abs(sdv + ledge_thickness) <= top_tol:
                wcnt = len(list(f.Wires()))
                top_faces.append((abs(sdv + ledge_thickness), -wcnt, -f.Area(), i, f, c, sdv))

    print(f"SELECTED: {len(top_faces)} planar faces as ledge-top candidates (sd≈-5mm ±{top_tol}mm, normal aligned)")
    for k, (err, _wcnt, _areaN, i, f, c, sdv) in enumerate(sorted(top_faces)[:10]):
        print(
            f"  top_cand[{k}]: face_idx={i} area={f.Area():.3f} wires={len(list(f.Wires()))} "
            f"center=({c.x:.3f},{c.y:.3f},{c.z:.3f}) sd_to_bottom={sdv:.3f} err_to_-5={err:.3f}"
        )

    if not top_faces:
        # Fallback: if exact top faces are merged/split weirdly, still proceed by selecting edges in an sd band.
        print("WARNING: no ledge-top planar faces found; will fall back to selecting junction edges by sd band only")
        f_top_faces = []
    else:
        # Use all candidate top faces; this avoids topology surprises (split ring, etc.)
        f_top_faces = [t[4] for t in top_faces]

    # --- Build edge->face adjacency map (by edge hash) ---
    edge_to_faces = {}
    for fi, f in enumerate(faces):
        for e in f.Edges():
            edge_to_faces.setdefault(e.hashCode(), []).append(fi)

    # map edge hash to current edge index for diagnostic prints
    ehash_to_idx = {e.hashCode(): i for i, e in enumerate(edges0)}

    def face_sd_span(face):
        bb = face.BoundingBox()
        corners = [
            cq.Vector(bb.xmin, bb.ymin, bb.zmin),
            cq.Vector(bb.xmin, bb.ymin, bb.zmax),
            cq.Vector(bb.xmin, bb.ymax, bb.zmin),
            cq.Vector(bb.xmin, bb.ymax, bb.zmax),
            cq.Vector(bb.xmax, bb.ymin, bb.zmin),
            cq.Vector(bb.xmax, bb.ymin, bb.zmax),
            cq.Vector(bb.xmax, bb.ymax, bb.zmin),
            cq.Vector(bb.xmax, bb.ymax, bb.zmax),
        ]
        sds = [sd_to_bottom_plane(p) for p in corners]
        return (min(sds), max(sds))

    # --- Select junction edges: top-edge loop where ledge meets the *existing* inner wall above ---
    # Heuristic: edges lying on sd≈-5 band, belonging to top face(s), and adjacent to a face that extends
    # much further into the body than the 5mm ledge (sd_min << -5). This rejects the short inner-step wall.
    junction = {}

    # If we have top faces, pick edges from them (preferred). Otherwise, scan all edges by sd band.
    if f_top_faces:
        candidate_edges = []
        for tf in f_top_faces:
            candidate_edges.extend(list(tf.Edges()))
        # unique
        candidate_edges = list({e.hashCode(): e for e in candidate_edges}.values())
        print(f"SELECTED: {len(candidate_edges)} unique edges from ledge-top face candidates (pre-filter)")
    else:
        candidate_edges = list(edges0)
        print(f"SELECTED: {len(candidate_edges)} edges from whole solid for sd-band fallback (pre-filter)")

    sd_edge_tol = 1.0
    tall_face_threshold = -20.0  # existing inner wall should extend far above; the new step wall spans only ~0..-5
    for e in candidate_edges:
        try:
            mp = e.positionAt(0.5)
        except Exception:
            continue
        sdmp = sd_to_bottom_plane(mp)
        if abs(sdmp + ledge_thickness) > sd_edge_tol:
            continue

        adj_fis = edge_to_faces.get(e.hashCode(), [])
        if len(adj_fis) < 2:
            continue

        # Determine if one adjacent face is a ledge-top face (planar aligned at sd≈-5)
        has_top_face = False
        for fi in adj_fis:
            f = faces[fi]
            if f.geomType() == "PLANE":
                n = f.normalAt().normalized()
                if n.dot(n_bottom) > 0.999:
                    sdv = sd_to_bottom_plane(f.Center())
                    if abs(sdv + ledge_thickness) <= top_tol:
                        has_top_face = True
                        break
        if not has_top_face and f_top_faces:
            # when we have explicit top faces, require the adjacency to confirm
            continue

        # Check the *other* face(s) spans: we want the edge that meets the existing tall inner wall (sd_min < -20)
        tall_adj = False
        for fi in adj_fis:
            f = faces[fi]
            if f.geomType() == "PLANE":
                # ignore ledge top plane itself
                n = f.normalAt().normalized()
                if n.dot(n_bottom) > 0.999 and abs(sd_to_bottom_plane(f.Center()) + ledge_thickness) <= top_tol:
                    continue
            mn, mx = face_sd_span(f)
            if mn < tall_face_threshold:
                tall_adj = True
                break

        if tall_adj:
            junction[e.hashCode()] = e

    junction_edges = list(junction.values())
    junction_idx = [ehash_to_idx.get(e.hashCode(), None) for e in junction_edges]
    print(
        f"SELECTED: {len(junction_edges)} edges for the upper junction fillet (sd≈-5±{sd_edge_tol}mm, tall-adj) "
        f"idx={junction_idx}"
    )
    for e in junction_edges[:20]:
        mp = e.positionAt(0.5)
        print(
            f"  edge idx={ehash_to_idx.get(e.hashCode(), -1)} len={e.Length():.3f} "
            f"mp=({mp.x:.3f},{mp.y:.3f},{mp.z:.3f}) sd_mp={sd_to_bottom_plane(mp):.3f}"
        )
    if len(junction_edges) > 20:
        print(f"  ... showing 20 of {len(junction_edges)}")

    # Fallback if nothing matched: relax tall threshold (but still avoid bottom edges)
    if not junction_edges:
        tall_face_threshold2 = -8.0
        print(f"WARNING: junction edge selection empty; relaxing tall-adj threshold to mn<{tall_face_threshold2}mm")
        for e in candidate_edges:
            try:
                mp = e.positionAt(0.5)
            except Exception:
                continue
            sdmp = sd_to_bottom_plane(mp)
            if abs(sdmp + ledge_thickness) > sd_edge_tol:
                continue
            adj_fis = edge_to_faces.get(e.hashCode(), [])
            if len(adj_fis) < 2:
                continue
            tall_adj = False
            for fi in adj_fis:
                f = faces[fi]
                mn, mx = face_sd_span(f)
                if mn < tall_face_threshold2:
                    tall_adj = True
                    break
            if tall_adj:
                junction[e.hashCode()] = e
        junction_edges = list(junction.values())
        junction_idx = [ehash_to_idx.get(e.hashCode(), None) for e in junction_edges]
        print(f"SELECTED: {len(junction_edges)} edges for upper junction fillet after relaxed threshold idx={junction_idx}")

    # --- Apply the fillet (never return original silently) ---
    out = s0
    applied = 0
    if junction_edges:
        try:
            out = s0.fillet(fillet_r, edgeList=junction_edges)
            applied = len(junction_edges)
            print(f"INFO: fillet({fillet_r:.3f}mm) applied in one operation to {applied} edges")
        except Exception as ex:
            print(f"ERROR: fillet failed on full edge set: {ex}")
            print("ATTEMPT: applying fillet progressively, one edge at a time (re-finding nearest edge each step)")
            out = s0
            targets = [(e.positionAt(0.5), e.Length(), ehash_to_idx.get(e.hashCode(), -1)) for e in junction_edges]
            for k, (mp0, elen, eidx0) in enumerate(targets):
                cur_edges = out.Edges()
                # find nearest current edge by midpoint proximity
                best = None
                best_d = 1e99
                for ce in cur_edges:
                    try:
                        mp = ce.positionAt(0.5)
                    except Exception:
                        continue
                    d = cq.Vector(mp.x - mp0.x, mp.y - mp0.y, mp.z - mp0.z).Length
                    if d < best_d:
                        best_d = d
                        best = ce
                if best is None:
                    print(f"  PROGRESS[{k}]: could not find any edge near target edge_idx={eidx0}")
                    continue
                try:
                    out = out.fillet(fillet_r, edgeList=[best])
                    applied += 1
                    print(f"  PROGRESS[{k}]: success on target edge_idx={eidx0} nearest_d={best_d:.3f} applied={applied}")
                except Exception as ex2:
                    print(f"  PROGRESS[{k}]: failed on target edge_idx={eidx0} nearest_d={best_d:.3f}: {ex2}")
                    continue
    else:
        print("ERROR: no junction edges selected -> no fillet applied (would be a no-op)")

    # --- Verify bbox unchanged (outer bounding box must not change) ---
    new_bb = out.BoundingBox()
    print(
        f"NEW  BBOX: min=({new_bb.xmin:.3f},{new_bb.ymin:.3f},{new_bb.zmin:.3f}) "
        f"max=({new_bb.xmax:.3f},{new_bb.ymax:.3f},{new_bb.zmax:.3f})"
    )
    print(
        "VERIFY: bbox delta "
        f"xmin {new_bb.xmin-orig_bb.xmin:+.3f}, xmax {new_bb.xmax-orig_bb.xmax:+.3f}, "
        f"ymin {new_bb.ymin-orig_bb.ymin:+.3f}, ymax {new_bb.ymax-orig_bb.ymax:+.3f}, "
        f"zmin {new_bb.zmin-orig_bb.zmin:+.3f}, zmax {new_bb.zmax-orig_bb.zmax:+.3f}"
    )

    # --- Verify: report radius~2 blend faces near the junction, and confirm none near the bottom perimeter ---
    # We look for CYLINDER/TORUS faces with radius close to 2mm and center in sd bands.
    try:
        from OCP.BRepAdaptor import BRepAdaptor_Surface
        from OCP.GeomAbs import GeomAbs_Cylinder, GeomAbs_Torus

        def collect_r2_faces_in_sd_band(solid, sd_min, sd_max, r=2.0, tol=0.15):
            hits = []
            for fi, f in enumerate(solid.Faces()):
                c = f.Center()
                sdv = sd_to_bottom_plane(c)
                if sdv < sd_min or sdv > sd_max:
                    continue
                ad = BRepAdaptor_Surface(f.wrapped)
                st = ad.GetType()
                rad = None
                if st == GeomAbs_Cylinder:
                    rad = ad.Cylinder().Radius()
                elif st == GeomAbs_Torus:
                    rad = ad.Torus().MinorRadius()
                if rad is None:
                    continue
                if abs(rad - r) <= tol:
                    hits.append((fi, st, rad, c, sdv))
            return hits

        # Bottom band near sd=0 should remain sharp (no new r=2 there due to this operation)
        bottom_band = collect_r2_faces_in_sd_band(out, -0.75, 0.75, r=fillet_r)
        print(f"VERIFY: radius~{fillet_r:.3f} faces with center sd in [-0.75,+0.75]mm (bottom band) = {len(bottom_band)}")

        # Junction band around sd≈-5 should contain the new fillet surfaces
        junction_band = collect_r2_faces_in_sd_band(out, -9.0, -1.0, r=fillet_r)
        print(f"VERIFY: radius~{fillet_r:.3f} faces with center sd in [-9.0,-1.0]mm (upper junction band) = {len(junction_band)}")
        for i, (fi, st, rad, c, sdv) in enumerate(junction_band[:30]):
            stname = "CYLINDER" if int(st) == int(GeomAbs_Cylinder) else ("TORUS" if int(st) == int(GeomAbs_Torus) else str(st))
            print(f"  r2_face[{i}]: face_idx={fi} type={stname} rad={rad:.4f} center=({c.x:.3f},{c.y:.3f},{c.z:.3f}) sd={sdv:.3f}")
        if len(junction_band) > 30:
            print(f"  ... showing 30 of {len(junction_band)}")

    except Exception as ex:
        print(f"WARNING: could not run radius verification via BRepAdaptor_Surface: {ex}")

    print(f"RESULT: fillet edges applied (requested) = {applied}")

    # Return as single solid or re-compounded if necessary
    if len(sols) == 1:
        return out

    rest = [s for i, s in enumerate(sols) if i != 0]
    return cq.Compound.makeCompound(rest + [out])