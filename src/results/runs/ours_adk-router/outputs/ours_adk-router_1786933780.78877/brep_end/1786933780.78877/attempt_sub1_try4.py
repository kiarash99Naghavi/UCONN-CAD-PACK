def my_cad_function(args):
    import cadquery as cq
    from math import isfinite

    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input

    base = shape.val() if hasattr(shape, "val") else shape
    sols = base.Solids()
    print(f"INFO: imported solids={len(sols)}")
    base_solid = sols[0]

    # --- Reference numbers explicitly named by the sub-goal ---
    top_n_ref = cq.Vector(0.0, 0.966, -0.259).normalized()
    underside_c_ref = cq.Vector(-556.620, -501.352, 28.177)
    underside_n_ref = cq.Vector(0.0, -0.259, -0.966).normalized()
    bbox_min_ref = (-949.620, -506.698, 26.800)
    bbox_max_ref = (-163.620, -338.409, 595.312)

    anchors_straight = [
        cq.Vector(-937.347, -412.697, 307.757),
        cq.Vector(-556.620, -485.355, 36.596),
        cq.Vector(-175.893, -412.697, 307.757),
        cq.Vector(-556.620, -340.040, 578.919),
    ]
    anchors_corner = [
        cq.Vector(-918.918, -480.579, 54.395),
        cq.Vector(-194.322, -480.579, 54.395),
        cq.Vector(-194.322, -344.803, 561.116),
        cq.Vector(-918.918, -344.803, 561.116),
    ]
    print("ANCHORS: straight=", [[round(v.x,3),round(v.y,3),round(v.z,3)] for v in anchors_straight])
    print("ANCHORS: corner =", [[round(v.x,3),round(v.y,3),round(v.z,3)] for v in anchors_corner])
    print(f"ANCHORS: top_normal_ref={[round(top_n_ref.x,3),round(top_n_ref.y,3),round(top_n_ref.z,3)]}")

    orig_bb = base_solid.BoundingBox()
    print(f"ORIG BBOX: min=({orig_bb.xmin:.3f},{orig_bb.ymin:.3f},{orig_bb.zmin:.3f}) max=({orig_bb.xmax:.3f},{orig_bb.ymax:.3f},{orig_bb.zmax:.3f})")

    all_edges = list(base_solid.Edges())
    all_faces = list(base_solid.Faces())
    print(f"INFO: base_solid faces={len(all_faces)} edges={len(all_edges)}")

    # --- Locate underside plane (must remain untouched) by proximity to named center & normal ---
    def vdot(a, b):
        return a.x*b.x + a.y*b.y + a.z*b.z

    underside_face = None
    best = None
    for i, f in enumerate(all_faces):
        if f.geomType() != "PLANE":
            continue
        c = f.Center()
        n = f.normalAt().normalized()
        dn = abs(vdot(n, underside_n_ref))
        dc = (cq.Vector(c.x, c.y, c.z) - underside_c_ref).Length
        score = dc + (1.0 - dn) * 1000.0
        if best is None or score < best[0]:
            best = (score, i, f, dc, dn)
    if best:
        _, fi, underside_face, dc, dn = best
        c = underside_face.Center()
        n = underside_face.normalAt().normalized()
        print(
            f"RESOLVED: underside_plane_face~ center={[round(c.x,3),round(c.y,3),round(c.z,3)]} "
            f"normal={[round(n.x,3),round(n.y,3),round(n.z,3)]} "
            f"delta_center={dc:.3f} dotN={dn:.6f} face_idx_guess={fi}"
        )
    else:
        print("WARNING: could not resolve underside plane face (unexpected); proceeding but will bbox-check")

    # --- Edge selection: pick the intended *inner* junction loop by the given anchors ---
    def shp_hash(sh):
        try:
            return sh.wrapped.HashCode(2147483647)
        except Exception:
            return id(sh)

    edge_hash_to_idx = {shp_hash(e): i for i, e in enumerate(all_edges)}

    line_edges = [e for e in all_edges if e.geomType() == "LINE"]
    circ_edges = [e for e in all_edges if e.geomType() == "CIRCLE"]
    print(f"INFO: candidate LINE edges={len(line_edges)} CIRCLE edges={len(circ_edges)}")

    def nearest_edge_to_point(pt, candidates):
        best_local = None
        for e in candidates:
            c = e.Center()
            d = (cq.Vector(c.x, c.y, c.z) - pt).Length
            if best_local is None or d < best_local[0]:
                best_local = (d, e)
        return best_local

    picked = []
    tol_pick = 25.0

    for k, pt in enumerate(anchors_straight):
        res = nearest_edge_to_point(pt, line_edges)
        if res is None:
            print(f"SELECTED: 0 LINE edges near straight_anchor[{k}] (BUG)")
            continue
        d, e = res
        idx = edge_hash_to_idx.get(shp_hash(e), None)
        ce = e.Center()
        print(
            f"PICK: straight_anchor[{k}] nearest LINE edge idx={idx} dist={d:.3f} "
            f"edge_center=({ce.x:.3f},{ce.y:.3f},{ce.z:.3f}) len={e.Length():.3f}"
        )
        if d <= tol_pick:
            picked.append(e)
        else:
            print(f"WARNING: nearest LINE edge is beyond tol {tol_pick}mm; not picking this anchor")

    for k, pt in enumerate(anchors_corner):
        res = nearest_edge_to_point(pt, circ_edges)
        if res is None:
            print(f"SELECTED: 0 CIRCLE edges near corner_anchor[{k}] (BUG)")
            continue
        d, e = res
        idx = edge_hash_to_idx.get(shp_hash(e), None)
        ce = e.Center()
        print(
            f"PICK: corner_anchor[{k}] nearest CIRCLE edge idx={idx} dist={d:.3f} "
            f"edge_center=({ce.x:.3f},{ce.y:.3f},{ce.z:.3f}) len={e.Length():.3f}"
        )
        if d <= tol_pick:
            picked.append(e)
        else:
            print(f"WARNING: nearest CIRCLE edge is beyond tol {tol_pick}mm; not picking this anchor")

    # Deduplicate
    uniq = []
    seen = set()
    for e in picked:
        h = shp_hash(e)
        if h not in seen:
            seen.add(h)
            uniq.append(e)

    picked_idx = [edge_hash_to_idx.get(shp_hash(e), -1) for e in uniq]
    print(f"SELECTED: {len(uniq)} edges for inner top-side junction spine (anchor-based) idx={picked_idx}")
    for e in uniq:
        i = edge_hash_to_idx.get(shp_hash(e), -1)
        c = e.Center()
        print(f"  spine_edge idx={i} type={e.geomType()} len={e.Length():.3f} center=({c.x:.3f},{c.y:.3f},{c.z:.3f})")

    if len(uniq) < 6:
        print("ERROR: too few spine edges selected; returning original shape to avoid no-op/incorrect edit")
        return shape

    # Assemble wire
    spine_wire = None
    try:
        spine_wire = cq.Wire.assembleEdges(uniq)
        print(f"INFO: assembled spine wire. IsClosed={spine_wire.IsClosed()}")
    except Exception as ex:
        print(f"WARNING: Wire.assembleEdges failed: {ex}")

    if spine_wire is None or not spine_wire.IsClosed():
        # fallback: attempt combine
        try:
            wlist = cq.Wire.combine(uniq)
            print(f"INFO: Wire.combine returned {len(wlist)} wire(s)")
            wclosed = [w for w in wlist if w.IsClosed()]
            spine_wire = wclosed[0] if wclosed else (wlist[0] if wlist else None)
            if spine_wire:
                print(f"INFO: chosen combined wire. IsClosed={spine_wire.IsClosed()}")
        except Exception as ex:
            print(f"ERROR: Wire.combine failed: {ex}")
            spine_wire = None

    if spine_wire is None:
        print("ERROR: could not build spine wire; returning original")
        return shape

    # --- Build adjacency map edge->faces to derive parent junction surfaces ---
    from OCP.TopExp import TopExp
    from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE
    from OCP.TopTools import TopTools_IndexedDataMapOfShapeListOfShape
    from OCP.TopoDS import TopoDS

    edge_face_map = TopTools_IndexedDataMapOfShapeListOfShape()
    TopExp.MapShapesAndAncestors_s(base_solid.wrapped, TopAbs_EDGE, TopAbs_FACE, edge_face_map)

    def faces_of_edge(edge):
        try:
            lst = edge_face_map.FindFromKey(edge.wrapped)
            out = []
            it = lst.Begin()
            while it.More():
                out.append(cq.Face.cast(TopoDS.Face_s(it.Value())))
                it.Next()
            return out
        except Exception:
            return []

    # pick a representative straight/long edge for section orientation
    rep_edge = max([e for e in uniq if e.geomType() == "LINE"], key=lambda ee: ee.Length(), default=uniq[0])
    rep_idx = edge_hash_to_idx.get(shp_hash(rep_edge), -1)
    print(f"INFO: representative edge idx={rep_idx} type={rep_edge.geomType()} len={rep_edge.Length():.3f}")

    # Robust tangent extraction
    def edge_tangent(edge, u=0.5):
        try:
            t = edge.tangentAt(u)
            return cq.Vector(t.x, t.y, t.z).normalized()
        except Exception:
            from OCP.BRepAdaptor import BRepAdaptor_Curve
            from OCP.gp import gp_Vec
            c = BRepAdaptor_Curve(edge.wrapped)
            u1 = c.FirstParameter()
            u2 = c.LastParameter()
            up = u1 + (u2 - u1) * u
            v = gp_Vec()
            c.D1(up, None, v)
            return cq.Vector(v.X(), v.Y(), v.Z()).normalized()

    def edge_point(edge, u=0.5):
        try:
            p = edge.positionAt(u)
            return cq.Vector(p.x, p.y, p.z)
        except Exception:
            c = edge.Center()
            return cq.Vector(c.x, c.y, c.z)

    p0 = edge_point(rep_edge, 0.5)
    t0 = edge_tangent(rep_edge, 0.5)
    print(f"INFO: rep point p0=({p0.x:.3f},{p0.y:.3f},{p0.z:.3f}) tangent={[round(t0.x,3),round(t0.y,3),round(t0.z,3)]}")

    adj_faces = faces_of_edge(rep_edge)
    print(f"SELECTED: {len(adj_faces)} adjacent faces to representative edge for parent-junction derivation")
    for fi, fa in enumerate(adj_faces):
        cc = fa.Center()
        nn = fa.normalAt().normalized()
        print(f"  adj_face[{fi}] type={fa.geomType()} center=({cc.x:.3f},{cc.y:.3f},{cc.z:.3f}) normal={[round(nn.x,3),round(nn.y,3),round(nn.z,3)]}")

    if len(adj_faces) < 2:
        print("ERROR: representative edge does not have 2 adjacent faces; cannot derive tangency; returning original")
        return shape

    # Choose the 'top/ledge' face as the one closest to the stated top broad-face normal
    def top_score(face):
        nn = face.normalAt().normalized()
        return abs(vdot(nn, top_n_ref))

    f_top = max(adj_faces, key=top_score)
    f_other = [f for f in adj_faces if f is not f_top][0]
    n_top = f_top.normalAt().normalized()
    n_other = f_other.normalAt().normalized()
    print(
        "SELECTED: 2 parent faces for quarter-round tangency: "
        f"top_like_dot={abs(vdot(n_top, top_n_ref)):.6f} "
        f"n_top={[round(n_top.x,3),round(n_top.y,3),round(n_top.z,3)]} "
        f"n_other={[round(n_other.x,3),round(n_other.y,3),round(n_other.z,3)]}"
    )

    # --- Decide quadrant: pick signs of in-plane directions so added material is OUTSIDE current solid ---
    r = 2.0  # mm

    def is_inside(solid, pt, tol=1e-6):
        try:
            return solid.isInside(cq.Vector(pt.x, pt.y, pt.z), tol)
        except Exception:
            from OCP.BRepClass3d import BRepClass3d_SolidClassifier
            from OCP.gp import gp_Pnt
            sc = BRepClass3d_SolidClassifier(solid.wrapped, gp_Pnt(pt.x, pt.y, pt.z), tol)
            st = sc.State()
            # 0=TopAbs_IN, 1=OUT, 2=ON, 3=UNKNOWN (varies); treat ON as inside-ish
            return int(st) == 0 or int(st) == 2

    def unit(v):
        if v.Length < 1e-9:
            return v
        return v.normalized()

    # directions in the section plane, perpendicular to edge, lying on each face
    d_top_base = unit(n_top.cross(t0))
    d_oth_base = unit(n_other.cross(t0))

    combos = []
    for s1 in (+1, -1):
        for s2 in (+1, -1):
            d1 = d_top_base.multiply(s1)
            d2 = d_oth_base.multiply(s2)
            test = p0 + (d1 + d2).multiply(0.5 * r)
            inside = is_inside(base_solid, test)
            combos.append((inside, s1, s2, test))
            print(
                f"QUADRANT TRY: s_top={s1:+d} s_other={s2:+d} "
                f"test=({test.x:.3f},{test.y:.3f},{test.z:.3f}) inside={inside}"
            )

    # Prefer a quadrant where the test point is OUTSIDE the current solid (so our sweep is additive into void)
    chosen = next((c for c in combos if c[0] is False), combos[0])
    _, s_top, s_oth, testpt = chosen
    d1 = d_top_base.multiply(s_top)
    d2 = d_oth_base.multiply(s_oth)
    print(
        "SELECTED: quadrant "
        f"s_top={s_top:+d} s_other={s_oth:+d} "
        f"(test point outside solid={not chosen[0]})"
    )

    # Orthonormalize in-plane axes
    xDir = unit(d2)
    # remove x component from d1 to get yDir
    yTmp = d1 - xDir.multiply(vdot(d1, xDir))
    yDir = unit(yTmp)
    if yDir.Length < 1e-9:
        print("ERROR: could not build orthonormal section axes; returning original")
        return shape

    # --- Build the quarter-round *sector* profile at p0 and sweep along spine ---
    sec_plane = cq.Plane(origin=(p0.x, p0.y, p0.z), xDir=(xDir.x, xDir.y, xDir.z), normal=(t0.x, t0.y, t0.z))
    print(
        "INFO: section plane origin="
        f"({p0.x:.3f},{p0.y:.3f},{p0.z:.3f}) "
        f"xDir={[round(xDir.x,3),round(xDir.y,3),round(xDir.z,3)]} "
        f"yDir={[round(yDir.x,3),round(yDir.y,3),round(yDir.z,3)]} "
        f"normal(tangent)={[round(t0.x,3),round(t0.y,3),round(t0.z,3)]}"
    )

    profile_wp = (
        cq.Workplane(sec_plane)
        .moveTo(0, 0)
        .lineTo(r, 0)
        .threePointArc((r, r), (0, r))
        .lineTo(0, 0)
        .close()
    )

    sweep_solid = None
    try:
        sweep_solid = profile_wp.sweep(spine_wire, multisection=False, isFrenet=True)
        sweep_val = sweep_solid.val() if hasattr(sweep_solid, "val") else sweep_solid
        print("INFO: full-loop sweep succeeded")
    except Exception as ex:
        print(f"WARNING: full-loop sweep failed: {ex}")
        sweep_val = None

    # Fallback: sweep per-edge with per-edge quadrant decisions
    if sweep_val is None:
        print("INFO: attempting per-edge sweeps as fallback")
        parts = []
        for e in uniq:
            i = edge_hash_to_idx.get(shp_hash(e), -1)
            pe = edge_point(e, 0.5)
            te = edge_tangent(e, 0.5)
            adj = faces_of_edge(e)
            print(f"  EDGE[{i}] per-edge sweep: adj_faces={len(adj)}")
            if len(adj) < 2:
                continue
            # choose top-like face
            fte = max(adj, key=top_score)
            foe = [f for f in adj if f is not fte][0]
            nte = fte.normalAt().normalized()
            noe = foe.normalAt().normalized()
            dte0 = unit(nte.cross(te))
            doe0 = unit(noe.cross(te))

            # choose signs so test point is outside
            best_combo = None
            for s1 in (+1, -1):
                for s2 in (+1, -1):
                    dte = dte0.multiply(s1)
                    doe = doe0.multiply(s2)
                    test = pe + (dte + doe).multiply(0.5 * r)
                    inside = is_inside(base_solid, test)
                    if best_combo is None:
                        best_combo = (inside, s1, s2, dte, doe)
                    if inside is False:
                        best_combo = (inside, s1, s2, dte, doe)
                        break
                if best_combo and best_combo[0] is False:
                    break

            inside, s1, s2, dte, doe = best_combo
            xD = unit(doe)
            yT = dte - xD.multiply(vdot(dte, xD))
            yD = unit(yT)
            if yD.Length < 1e-9:
                print(f"    SKIP edge[{i}]: could not build section axes")
                continue

            pl = cq.Plane(origin=(pe.x, pe.y, pe.z), xDir=(xD.x, xD.y, xD.z), normal=(te.x, te.y, te.z))
            prof = (
                cq.Workplane(pl)
                .moveTo(0, 0)
                .lineTo(r, 0)
                .threePointArc((r, r), (0, r))
                .lineTo(0, 0)
                .close()
            )
            try:
                sw = prof.sweep(e, multisection=False, isFrenet=True)
                sv = sw.val() if hasattr(sw, "val") else sw
                parts.append(sv)
                print(f"    OK edge[{i}] sweep (quadrant outside={not inside})")
            except Exception as ex:
                print(f"    FAIL edge[{i}] sweep: {ex}")

        print(f"SELECTED: {len(parts)} swept segment solids for fallback")
        if not parts:
            print("ERROR: no sweep solids could be created; returning original")
            return shape
        sweep_val = parts[0]
        for s in parts[1:]:
            sweep_val = sweep_val.fuse(s)

    # --- Fuse the new quarter-round transition into the imported solid ---
    out_solid = base_solid.fuse(sweep_val)

    # --- Self-checks: isolate added material, verify it is local to the upper junction and does not touch underside ---
    added = None
    try:
        added = out_solid.cut(base_solid)
        add_bb = added.BoundingBox()
        add_c = added.Center()
        print(f"ADDED: center=({add_c.x:.3f},{add_c.y:.3f},{add_c.z:.3f})")
        print(f"ADDED: bbox min=({add_bb.xmin:.3f},{add_bb.ymin:.3f},{add_bb.zmin:.3f}) max=({add_bb.xmax:.3f},{add_bb.ymax:.3f},{add_bb.zmax:.3f})")
    except Exception as ex:
        print(f"WARNING: could not isolate added material via cut(out, base): {ex}")

    # Verify underside not altered: ensure added material is not close to underside plane (if we resolved it)
    if added is not None and underside_face is not None:
        cpl = underside_face.Center()
        npl = underside_face.normalAt().normalized()
        def signed_dist_to_underside(pt):
            v = cq.Vector(pt.x - cpl.x, pt.y - cpl.y, pt.z - cpl.z)
            return vdot(v, npl)
        add_c = added.Center()
        sd = signed_dist_to_underside(add_c)
        print(f"VERIFY: added_center signed_dist_to_underside_plane = {sd:.3f} mm (should be far from 0 to avoid underside edits)")

    # --- Verify the new transition is a 2.0mm cylindrical/toroidal patch chain near the named loop ---
    try:
        from OCP.BRepAdaptor import BRepAdaptor_Surface
        def face_radius_info(fa):
            ad = BRepAdaptor_Surface(fa.wrapped, True)
            typ = fa.geomType()
            if typ == "CYLINDER":
                cyl = ad.Cylinder()
                return (typ, float(cyl.Radius()), None)
            if typ == "TORUS":
                tor = ad.Torus()
                return (typ, float(tor.MajorRadius()), float(tor.MinorRadius()))
            return (typ, None, None)

        # collect candidate faces (on the whole part) near anchors
        anchors_all = anchors_straight + anchors_corner
        def min_dist_to_anchors(ptvec):
            return min((ptvec - a).Length for a in anchors_all)

        cand_faces = []
        for fi, fa in enumerate(out_solid.Faces()):
            gt = fa.geomType()
            if gt not in ("CYLINDER", "TORUS"):
                continue
            c = fa.Center()
            cv = cq.Vector(c.x, c.y, c.z)
            if min_dist_to_anchors(cv) > 60.0:
                continue
            typ, r1, r2 = face_radius_info(fa)
            # For torus, minor radius is the blend radius; for cylinder, radius is the blend radius
            rad = r2 if typ == "TORUS" else r1
            if rad is None:
                continue
            if abs(rad - 2.0) <= 0.15:
                cand_faces.append((fi, typ, rad, cv))

        print(f"SELECTED: {len(cand_faces)} faces near inner-loop anchors with radius~2.0mm (CYL/TOR)")
        for fi, typ, rad, cv in cand_faces:
            print(f"  r2_face idx={fi} type={typ} rad={rad:.3f} center=({cv.x:.3f},{cv.y:.3f},{cv.z:.3f})")

        cyl_cnt = sum(1 for _, t, _, _ in cand_faces if t == "CYLINDER")
        tor_cnt = sum(1 for _, t, _, _ in cand_faces if t == "TORUS")
        print(f"VERIFY: radius-2 patch chain composition near loop: CYL={cyl_cnt} TOR={tor_cnt} (expect 4+4 around 4 straights + 4 corners)")
    except Exception as ex:
        print(f"WARNING: could not verify cylindrical/toroidal radius faces: {ex}")

    # --- Verify bounding box remains exactly the specified envelope ---
    new_bb = out_solid.BoundingBox()
    print(f"NEW  BBOX: min=({new_bb.xmin:.3f},{new_bb.ymin:.3f},{new_bb.zmin:.3f}) max=({new_bb.xmax:.3f},{new_bb.ymax:.3f},{new_bb.zmax:.3f})")
    print(
        "VERIFY: bbox vs REF "
        f"xmin {new_bb.xmin-bbox_min_ref[0]:+.3f}, ymin {new_bb.ymin-bbox_min_ref[1]:+.3f}, zmin {new_bb.zmin-bbox_min_ref[2]:+.3f}, "
        f"xmax {new_bb.xmax-bbox_max_ref[0]:+.3f}, ymax {new_bb.ymax-bbox_max_ref[1]:+.3f}, zmax {new_bb.zmax-bbox_max_ref[2]:+.3f}"
    )

    # Return as single solid or re-compounded if necessary
    if len(sols) == 1:
        return out_solid

    rest = [s for i, s in enumerate(sols) if i != 0]
    return cq.Compound.makeCompound(rest + [out_solid])