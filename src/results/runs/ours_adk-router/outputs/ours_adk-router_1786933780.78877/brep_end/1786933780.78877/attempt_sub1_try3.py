def my_cad_function(args):
    import cadquery as cq

    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input

    base = shape.val() if hasattr(shape, "val") else shape
    sols = base.Solids()
    print(f"INFO: imported solids={len(sols)}")
    if len(sols) < 1:
        print("ERROR: no solids found -> returning original shape")
        return shape

    s0 = sols[0]
    orig_bb = s0.BoundingBox()
    print(
        f"ORIG BBOX: min=({orig_bb.xmin:.3f},{orig_bb.ymin:.3f},{orig_bb.zmin:.3f}) "
        f"max=({orig_bb.xmax:.3f},{orig_bb.ymax:.3f},{orig_bb.zmax:.3f})"
    )

    # --- Resolve face #12 (bottom reference plane for SD) ---
    faces0 = s0.Faces()
    edges0 = s0.Edges()
    print(f"INFO: solid[0] faces={len(faces0)} edges={len(edges0)}")

    try:
        f12 = faces0[12]
    except Exception as ex:
        print(f"ERROR: cannot access face#12: {ex} -> returning original shape")
        return shape

    c12 = f12.Center()
    n12 = f12.normalAt().normalized()
    a12 = f12.Area()
    print(
        "RESOLVED: face#12 "
        f"center=({c12.x:.3f},{c12.y:.3f},{c12.z:.3f}) area={a12:.3f} "
        f"normal=({n12.x:.3f},{n12.y:.3f},{n12.z:.3f})"
    )

    fillet_r = 2.0
    ledge_thickness = 5.0
    print(f"INFO: target fillet radius={fillet_r:.3f} mm")
    print(f"INFO: expected ledge thickness reference={ledge_thickness:.3f} mm (junction plane sd ~ -5)")

    def sd_to_f12_plane(p):
        # signed distance along n12 from face#12 plane (positive along n12)
        v = cq.Vector(p.x - c12.x, p.y - c12.y, p.z - c12.z)
        return n12.dot(v)

    # --- Find the ledge TOP planar face(s): normal opposite to face#12, at sd ~ -5 ---
    planar = []
    for fi, f in enumerate(faces0):
        if f.geomType() != "PLANE":
            continue
        nn = f.normalAt().normalized()
        dot = nn.dot(n12)
        cc = f.Center()
        sd = sd_to_f12_plane(cc)
        planar.append((fi, f, nn, dot, sd, f.Area(), cc))

    print(f"INFO: planar faces found={len(planar)}")

    # Candidate ledge-top faces: dot ~ -1 (opposite) and sd ~ -5
    cand = [(fi, f, nn, dot, sd, area, cc) for (fi, f, nn, dot, sd, area, cc) in planar if dot < -0.999 and abs(sd + ledge_thickness) < 3.0]
    cand.sort(key=lambda t: abs(t[4] + ledge_thickness))

    print(f"SELECTED: {len(cand)} planar faces as ledge-top candidates (dot<-0.999, sd≈-5±3mm)")
    for k, (fi, f, nn, dot, sd, area, cc) in enumerate(cand[:20]):
        print(
            f"  ledgeTopCand[{k}]: face_idx={fi} area={area:.3f} "
            f"center=({cc.x:.3f},{cc.y:.3f},{cc.z:.3f}) dot={dot:.6f} sd={sd:.3f}"
        )
    if len(cand) > 20:
        print(f"  ... showing 20 of {len(cand)}")

    if not cand:
        # Fail loudly but do not no-op silently
        print("ERROR: no ledge-top planar face found. Cannot target junction edge loop -> returning original shape")
        return shape

    # Use all candidates (in case the ledge top got split into multiple coplanar faces)
    ledge_top_faces = [t[1] for t in cand]

    # --- Get OUTER wire edges from ledge-top face(s): this is the junction loop to fillet ---
    # Exclude inner wire (opening) edges by ONLY taking OuterWire().
    junction_edges_map = {}
    ehash_to_idx = {e.hashCode(): i for i, e in enumerate(edges0)}

    for f in ledge_top_faces:
        try:
            ow = f.OuterWire()
        except Exception as ex:
            print(f"WARNING: could not get OuterWire() for a ledge-top candidate face: {ex}")
            continue
        for e in ow.Edges():
            junction_edges_map[e.hashCode()] = e

    junction_edges = list(junction_edges_map.values())
    junction_idx = [ehash_to_idx.get(e.hashCode(), None) for e in junction_edges]
    junction_idx_sorted = [i for i in sorted([i for i in junction_idx if i is not None])]

    print(f"SELECTED: {len(junction_edges)} edges from ledge-top OUTER wire(s) for the upper junction fillet idx={junction_idx_sorted}")
    for i, e in enumerate(junction_edges[:50]):
        mp = e.positionAt(0.5)
        sdmp = sd_to_f12_plane(mp)
        print(
            f"  jEdge[{i}]: edge_idx={ehash_to_idx.get(e.hashCode(),-1)} len={e.Length():.3f} "
            f"mid=({mp.x:.3f},{mp.y:.3f},{mp.z:.3f}) sd_mid={sdmp:.3f}"
        )
    if len(junction_edges) > 50:
        print(f"  ... showing 50 of {len(junction_edges)}")

    if not junction_edges:
        print("ERROR: junction edge list is empty -> returning original shape")
        return shape

    # --- Helper: radius verification via OCC surface adaptor ---
    def count_r2_faces_in_sd_band(solid, sd_min, sd_max, r=2.0, tol=0.2):
        try:
            from OCP.BRepAdaptor import BRepAdaptor_Surface
            from OCP.GeomAbs import GeomAbs_Cylinder, GeomAbs_Torus
        except Exception as ex:
            print(f"WARNING: cannot import BRepAdaptor_Surface for verification: {ex}")
            return []
        hits = []
        for fi, f in enumerate(solid.Faces()):
            c = f.Center()
            sdv = sd_to_f12_plane(c)
            if sdv < sd_min or sdv > sd_max:
                continue
            ad = BRepAdaptor_Surface(f.wrapped)
            st = ad.GetType()
            rad = None
            if int(st) == int(GeomAbs_Cylinder):
                rad = ad.Cylinder().Radius()
                stname = "CYLINDER"
            elif int(st) == int(GeomAbs_Torus):
                rad = ad.Torus().MinorRadius()
                stname = "TORUS"
            else:
                continue
            if abs(rad - r) <= tol:
                hits.append((fi, stname, float(rad), c, float(sdv)))
        return hits

    pre_bottom_r2 = count_r2_faces_in_sd_band(s0, -0.75, 0.75, r=fillet_r)
    pre_junc_r2 = count_r2_faces_in_sd_band(s0, -7.5, -2.5, r=fillet_r)
    print(f"VERIFY(pre): radius~{fillet_r:.3f} faces in bottom band sd[-0.75,+0.75] = {len(pre_bottom_r2)}")
    print(f"VERIFY(pre): radius~{fillet_r:.3f} faces in junction band sd[-7.5,-2.5] = {len(pre_junc_r2)}")

    # --- Apply 2mm fillet to the selected junction edge loop ---
    out = s0
    applied = 0
    try:
        out = s0.fillet(fillet_r, edgeList=junction_edges)
        applied = len(junction_edges)
        print(f"INFO: fillet({fillet_r:.3f}mm) applied in one op to {applied} edges")
    except Exception as ex:
        print(f"ERROR: fillet failed on full edge set: {ex}")
        print("ATTEMPT: applying fillet progressively (one edge at a time; re-select nearest edge by midpoint)")
        out = s0
        # Store target midpoints to find closest surviving edge after each fillet
        targets = []
        for e in junction_edges:
            try:
                mp = e.positionAt(0.5)
            except Exception:
                continue
            targets.append((cq.Vector(mp.x, mp.y, mp.z), e.Length(), ehash_to_idx.get(e.hashCode(), -1)))

        for k, (mp0, elen, eidx0) in enumerate(targets):
            cur_edges = out.Edges()
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
                print(f"  PROGRESS[{k}]: no edge found near target edge_idx={eidx0}")
                continue
            try:
                out = out.fillet(fillet_r, edgeList=[best])
                applied += 1
                print(f"  PROGRESS[{k}]: success target edge_idx={eidx0} nearest_d={best_d:.3f} applied={applied}")
            except Exception as ex2:
                print(f"  PROGRESS[{k}]: fail target edge_idx={eidx0} nearest_d={best_d:.3f}: {ex2}")

    if applied == 0:
        print("ERROR: no fillet applied (applied==0) -> returning original shape")
        return shape

    # --- Verify outer bounding box unchanged ---
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

    # --- Verify: new radius~2 faces appear around the upper junction, and bottom edges remain sharp ---
    post_bottom_r2 = count_r2_faces_in_sd_band(out, -0.75, 0.75, r=fillet_r)
    post_junc_r2 = count_r2_faces_in_sd_band(out, -7.5, -2.5, r=fillet_r)

    print(f"VERIFY(post): radius~{fillet_r:.3f} faces in bottom band sd[-0.75,+0.75] = {len(post_bottom_r2)} (delta {len(post_bottom_r2)-len(pre_bottom_r2):+d})")
    print(f"VERIFY(post): radius~{fillet_r:.3f} faces in junction band sd[-7.5,-2.5] = {len(post_junc_r2)} (delta {len(post_junc_r2)-len(pre_junc_r2):+d})")

    for i, (fi, stname, rad, c, sdv) in enumerate(post_junc_r2[:40]):
        print(f"  r2_junction_face[{i}]: face_idx={fi} type={stname} rad={rad:.4f} center=({c.x:.3f},{c.y:.3f},{c.z:.3f}) sd={sdv:.3f}")
    if len(post_junc_r2) > 40:
        print(f"  ... showing 40 of {len(post_junc_r2)}")

    print(f"RESULT: fillet edges requested={len(junction_edges)} applied={applied}")

    # Return as single solid or re-compounded if necessary
    if len(sols) == 1:
        return out
    rest = [s for i, s in enumerate(sols) if i != 0]
    return cq.Compound.makeCompound(rest + [out])