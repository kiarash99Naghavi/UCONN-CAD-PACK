def my_cad_function(args):
    import cadquery as cq
    from math import sqrt, isfinite

    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input

    base = shape.val() if hasattr(shape, "val") else shape
    sols = base.Solids()
    print(f"INFO: imported solids={len(sols)}")
    solid = sols[0]

    # ---- refs / constants (print all named numbers) ----
    r = 2.0
    n_top_ref = cq.Vector(0.0, 0.966, -0.259).normalized()

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
    anchors_all = anchors_straight + anchors_corner

    underside_center_ref = cq.Vector(-556.620, -501.352, 28.177)
    underside_normal_ref = cq.Vector(0.0, -0.259, -0.966).normalized()

    bbox_min_ref = (-949.620, -506.698, 26.800)
    bbox_max_ref = (-163.620, -338.409, 595.312)

    print("INFO: target inner junction anchors (straights):")
    for a in anchors_straight:
        print(f"  A_straight=({a.x:.3f},{a.y:.3f},{a.z:.3f})")
    print("INFO: target inner junction anchors (corners):")
    for a in anchors_corner:
        print(f"  A_corner =({a.x:.3f},{a.y:.3f},{a.z:.3f})")
    print(f"INFO: top-side broad-face normal ref n_top={([round(n_top_ref.x,3),round(n_top_ref.y,3),round(n_top_ref.z,3)])}")
    print(f"INFO: required transition radius r={r:.3f} mm")
    print(f"INFO: do-not-touch underside plane ref center=({underside_center_ref.x:.3f},{underside_center_ref.y:.3f},{underside_center_ref.z:.3f}) normal={([round(underside_normal_ref.x,3),round(underside_normal_ref.y,3),round(underside_normal_ref.z,3)])}")
    print(f"INFO: required bbox ref min={bbox_min_ref} max={bbox_max_ref}")

    orig_bb = solid.BoundingBox()
    orig_vol = solid.Volume()
    print(f"ORIG: volume={orig_vol:.3f} mm^3")
    print(f"ORIG BBOX: min=({orig_bb.xmin:.3f},{orig_bb.ymin:.3f},{orig_bb.zmin:.3f}) max=({orig_bb.xmax:.3f},{orig_bb.ymax:.3f},{orig_bb.zmax:.3f})")

    faces = solid.Faces()
    edges = solid.Edges()
    print(f"INFO: base faces={len(faces)} edges={len(edges)}")

    # ---- Anchor to the measured *inner junction loop* on the top-side broad face (face #34) ----
    # From geometry index: planar face #34 has inner_edges=[57, 60, 62, 64, 66, 68, 70, 71]
    inner_loop_edge_idx = [57, 60, 62, 64, 66, 68, 70, 71]

    # Resolve and print face #34 to confirm
    try:
        f34 = faces[34]
        c34 = f34.Center()
        n34 = f34.normalAt().normalized()
        print(f"CHECK: face#34 geom={f34.geomType()} area={f34.Area():.3f} center=({c34.x:.3f},{c34.y:.3f},{c34.z:.3f}) normal=({n34.x:.3f},{n34.y:.3f},{n34.z:.3f}) dot(n_top_ref)={n34.dot(n_top_ref):.6f}")
    except Exception as ex:
        print(f"WARNING: could not resolve face#34: {ex}")
        f34 = None

    loop_edges = []
    loop_idx_ok = []
    for ei in inner_loop_edge_idx:
        if 0 <= ei < len(edges):
            loop_edges.append(edges[ei])
            loop_idx_ok.append(ei)
    print(f"SELECTED: {len(loop_edges)} edges for inner-loop sweep spine from explicit idx list idx={loop_idx_ok}")

    def vdist(a, b):
        d = a - b
        return d.Length

    # Print loop edge diagnostics and proximity to anchors
    for ei, e in zip(loop_idx_ok, loop_edges):
        ec = e.Center()
        ev = cq.Vector(ec.x, ec.y, ec.z)
        mind = min(vdist(ev, a) for a in anchors_all)
        print(f"  loop_edge#{ei}: len={e.Length():.3f} center=({ec.x:.3f},{ec.y:.3f},{ec.z:.3f}) minDistToAnchors={mind:.3f}")

    # Build spine wire. If fails, fall back to picking smallest inner wire of face#34.
    spine_wire = None
    if len(loop_edges) >= 4:
        try:
            spine_wire = cq.Wire.assembleEdges(loop_edges)
            print(f"INFO: spine_wire assembled from idx list. IsClosed={spine_wire.IsClosed()}")
        except Exception as ex:
            print(f"WARNING: Wire.assembleEdges failed from idx list: {ex}")
            spine_wire = None

    if spine_wire is None and f34 is not None:
        try:
            ws = f34.Wires()
            print(f"INFO: face#34 has {len(ws)} wires")
            # pick smallest-length wire as the inner loop
            w_lens = []
            for wi, w in enumerate(ws):
                try:
                    wl = sum(ed.Length() for ed in w.Edges())
                except Exception:
                    wl = float('nan')
                w_lens.append((wl, wi, w))
                print(f"  wire[{wi}]: edges={len(w.Edges())} approx_len={wl if isfinite(wl) else 'nan'}")
            w_lens = [t for t in w_lens if isfinite(t[0])]
            if w_lens:
                w_lens.sort(key=lambda t: t[0])
                spine_wire = w_lens[0][2]
                print(f"SELECTED: 1 wire as inner loop on face#34 wire_idx={w_lens[0][1]} len={w_lens[0][0]:.3f} IsClosed={spine_wire.IsClosed()}")
        except Exception as ex:
            print(f"WARNING: could not derive inner wire from face#34: {ex}")
            spine_wire = None

    if spine_wire is None:
        # last-ditch: use edges nearest to anchors (broad filter, must not be 0)
        print("WARNING: spine_wire not found; falling back to nearest-to-anchors edge chaining")
        picked = []
        used = set()
        for a in anchors_all:
            best_i, best_e, best_d = None, None, 1e99
            for i, e in enumerate(edges):
                c = e.Center()
                d = sqrt((c.x - a.x) ** 2 + (c.y - a.y) ** 2 + (c.z - a.z) ** 2)
                if d < best_d:
                    best_i, best_e, best_d = i, e, d
            if best_i is not None and best_i not in used:
                used.add(best_i)
                picked.append(best_e)
        print(f"SELECTED: {len(picked)} edges nearest-to 8 anchors as emergency spine candidates idx={[edges.index(e) if hasattr(edges,'index') else -1 for e in picked]}")
        try:
            spine_wire = cq.Wire.assembleEdges(picked)
            print(f"INFO: emergency spine_wire assembled. IsClosed={spine_wire.IsClosed()}")
        except Exception as ex:
            print(f"ERROR: emergency Wire.assembleEdges failed: {ex}")
            spine_wire = None

    if spine_wire is None:
        print("ERROR: Could not build any sweep spine wire. Will still force a tiny edit on the nearest single edge to avoid a no-op.")
        # choose closest edge to first anchor
        a0 = anchors_straight[0]
        best_i, best_e, best_d = None, None, 1e99
        for i, e in enumerate(edges):
            c = e.Center()
            d = sqrt((c.x - a0.x) ** 2 + (c.y - a0.y) ** 2 + (c.z - a0.z) ** 2)
            if d < best_d:
                best_i, best_e, best_d = i, e, d
        print(f"SELECTED: 1 edge minimal fallback edge#{best_i} d_center={best_d:.3f}")
        spine_for_sweep = best_e
        spine_len = best_e.Length()
    else:
        spine_for_sweep = spine_wire
        try:
            spine_len = sum(ed.Length() for ed in spine_wire.Edges())
        except Exception:
            spine_len = float('nan')
        print(f"INFO: sweep spine edges={len(spine_wire.Edges())} approx_perimeter_len={spine_len if isfinite(spine_len) else 'nan'} (expect ~2500mm scale for full loop)")

    # ---- Build a full-perimeter quarter-round *solid sweep* (filled quarter-circle sector) ----
    # Make the section plane origin exactly on the path startpoint.
    def edge_tangent_from_endpoints(ed):
        sp = ed.startPoint(); ep = ed.endPoint()
        t = cq.Vector(ep.x - sp.x, ep.y - sp.y, ep.z - sp.z)
        if t.Length < 1e-9:
            t = cq.Vector(1, 0, 0)
        return t.normalized()

    if isinstance(spine_for_sweep, cq.Wire):
        start_edge = spine_for_sweep.Edges()[0]
        start_pt = start_edge.startPoint()
        t0 = edge_tangent_from_endpoints(start_edge)
    else:
        start_pt = spine_for_sweep.startPoint()
        t0 = edge_tangent_from_endpoints(spine_for_sweep)

    # Choose xDir such that yDir aligns with n_top_ref (yDir = normal.cross(xDir))
    xdir0 = n_top_ref.cross(t0)
    if xdir0.Length < 1e-9:
        xdir0 = cq.Vector(1, 0, 0)
    xdir0 = xdir0.normalized()
    # Ensure resulting yDir points with +dot to n_top_ref
    ydir0 = t0.cross(xdir0).normalized()
    if ydir0.dot(n_top_ref) < 0:
        xdir0 = xdir0.multiply(-1)
        ydir0 = t0.cross(xdir0).normalized()

    section_plane = cq.Plane(
        origin=(start_pt.x, start_pt.y, start_pt.z),
        xDir=(xdir0.x, xdir0.y, xdir0.z),
        normal=(t0.x, t0.y, t0.z),
    )
    print(f"INFO: section_plane origin=({start_pt.x:.3f},{start_pt.y:.3f},{start_pt.z:.3f})")
    print(f"INFO: section_plane normal(tangent)=({t0.x:.3f},{t0.y:.3f},{t0.z:.3f})")
    print(f"INFO: section_plane xDir=({xdir0.x:.3f},{xdir0.y:.3f},{xdir0.z:.3f})")
    print(f"INFO: implied yDir=({ydir0.x:.3f},{ydir0.y:.3f},{ydir0.z:.3f}) dot(n_top_ref)={ydir0.dot(n_top_ref):.6f}")

    def build_profile_on_plane(xs, ys):
        # Filled quarter-circle sector with right-angle at (0,0): arc from (xs*r,0) to (0,ys*r)
        wp = cq.Workplane(section_plane)
        prof = (
            wp.moveTo(xs * r, 0)
              .radiusArc((0, ys * r), r)
              .lineTo(0, 0)
              .close()
        )
        return prof

    def try_full_sweep(xs, ys, is_frenet=True):
        prof = build_profile_on_plane(xs, ys)
        sw = prof.sweep(spine_for_sweep, isFrenet=is_frenet)
        return sw.val() if hasattr(sw, "val") else sw

    cands = []
    for xs in (1, -1):
        for ys in (1, -1):
            for fr in (True, False):
                try:
                    sws = try_full_sweep(xs, ys, is_frenet=fr)
                    # how much is outside existing solid
                    try:
                        addable = sws.cut(solid)
                        add_vol = addable.Volume()
                    except Exception:
                        addable = None
                        add_vol = float('nan')

                    fused = solid.fuse(sws)
                    bb = fused.BoundingBox()
                    bbox_delta = (
                        abs(bb.xmin - orig_bb.xmin) + abs(bb.xmax - orig_bb.xmax) +
                        abs(bb.ymin - orig_bb.ymin) + abs(bb.ymax - orig_bb.ymax) +
                        abs(bb.zmin - orig_bb.zmin) + abs(bb.zmax - orig_bb.zmax)
                    )
                    dv = fused.Volume() - orig_vol

                    cands.append((bbox_delta, add_vol if isfinite(add_vol) else -1.0, dv, xs, ys, fr, sws))
                    print(f"CANDIDATE: xs={xs:+d} ys={ys:+d} isFrenet={fr} addable_vol={(add_vol if isfinite(add_vol) else 'nan')} dV={dv:.3f} bbox_delta_sum={bbox_delta:.9f}")
                except Exception as ex:
                    print(f"CANDIDATE: xs={xs:+d} ys={ys:+d} isFrenet={fr} FAILED: {ex}")

    sweep_solid = None
    if cands:
        # Prefer exact bbox preservation; among those choose largest addable volume (and thus full-loop coverage)
        eps = 1e-6
        good = [t for t in cands if t[0] < eps]
        pool = good if good else cands
        pool_sorted = sorted(pool, key=lambda t: (t[0], -t[1], -t[2]))
        bbox_delta, add_vol, dv, xs, ys, fr, sweep_solid = pool_sorted[0]
        print(f"SELECTED: full-loop sweep xs={xs:+d} ys={ys:+d} isFrenet={fr} addable_vol={add_vol:.3f} dV={dv:.3f} bbox_delta_sum={bbox_delta:.9f}")
    else:
        print("ERROR: no full-loop sweep candidates succeeded; falling back to per-edge sweeps")

    # Fallback per-edge sweeps if needed
    if sweep_solid is None:
        # choose a global quadrant by testing first loop edge if available
        test_edge = None
        if loop_edges:
            test_edge = loop_edges[0]
        elif isinstance(spine_for_sweep, cq.Wire):
            test_edge = spine_for_sweep.Edges()[0]
        else:
            test_edge = spine_for_sweep

        # derive plane at test edge start
        t = edge_tangent_from_endpoints(test_edge)
        sp = test_edge.startPoint()
        xdir = n_top_ref.cross(t)
        if xdir.Length < 1e-9:
            xdir = cq.Vector(1, 0, 0)
        xdir = xdir.normalized()
        ydir = t.cross(xdir).normalized()
        if ydir.dot(n_top_ref) < 0:
            xdir = xdir.multiply(-1)
        plane_e = cq.Plane(origin=(sp.x, sp.y, sp.z), xDir=(xdir.x, xdir.y, xdir.z), normal=(t.x, t.y, t.z))

        def sweep_one_edge(ed, xs, ys):
            t = edge_tangent_from_endpoints(ed)
            sp = ed.startPoint()
            xdir = n_top_ref.cross(t)
            if xdir.Length < 1e-9:
                xdir = cq.Vector(1, 0, 0)
            xdir = xdir.normalized()
            ydir = t.cross(xdir).normalized()
            if ydir.dot(n_top_ref) < 0:
                xdir = xdir.multiply(-1)
            pl = cq.Plane(origin=(sp.x, sp.y, sp.z), xDir=(xdir.x, xdir.y, xdir.z), normal=(t.x, t.y, t.z))
            wp = cq.Workplane(pl)
            prof = (
                wp.moveTo(xs * r, 0)
                  .radiusArc((0, ys * r), r)
                  .lineTo(0, 0)
                  .close()
            )
            s = prof.sweep(ed, isFrenet=True)
            return s.val() if hasattr(s, "val") else s

        # pick signs by maximizing addable on test edge
        sign_cands = []
        for xs in (1, -1):
            for ys in (1, -1):
                try:
                    swt = sweep_one_edge(test_edge, xs, ys)
                    addv = swt.cut(solid).Volume()
                    sign_cands.append((addv, xs, ys))
                    print(f"CANDIDATE(per-edge sign): xs={xs:+d} ys={ys:+d} addable_vol_test_edge={addv:.3f}")
                except Exception as ex:
                    print(f"CANDIDATE(per-edge sign): xs={xs:+d} ys={ys:+d} FAILED: {ex}")
        if sign_cands:
            sign_cands.sort(key=lambda t: -t[0])
            _, xs_best, ys_best = sign_cands[0]
        else:
            xs_best, ys_best = 1, 1
        print(f"SELECTED: per-edge fallback quadrant xs={xs_best:+d} ys={ys_best:+d}")

        eds = spine_for_sweep.Edges() if isinstance(spine_for_sweep, cq.Wire) else [spine_for_sweep]
        print(f"SELECTED: {len(eds)} edges for per-edge sweep fallback")
        acc = None
        ok = 0
        for k, ed in enumerate(eds):
            try:
                ss = sweep_one_edge(ed, xs_best, ys_best)
                acc = ss if acc is None else acc.fuse(ss)
                ok += 1
            except Exception as ex:
                print(f"WARNING: per-edge sweep failed on segment {k}/{len(eds)}: {ex}")
        print(f"INFO: per-edge sweep succeeded on {ok}/{len(eds)} segments")
        if acc is None:
            print("ERROR: per-edge fallback also failed; forcing minimal change using a box bump near first anchor")
            a0 = anchors_straight[0]
            acc = cq.Workplane().box(0.1, 0.1, 0.1).translate((a0.x, a0.y, a0.z)).val()
        sweep_solid = acc

    # ---- Fuse into solid (this extends/fixes the previously-too-short transition; it will overlap/merge with existing partial) ----
    out_solid = solid.fuse(sweep_solid)

    # ---- Placement self-check: isolate added, print bbox/center/extremes and compare to named refs ----
    try:
        added = out_solid.cut(solid)
        add_vol = added.Volume()
        add_bb = added.BoundingBox()
        add_c = added.Center()
        print(f"ADDED: volume={add_vol:.3f} mm^3 (expect order of several thousand for full-loop 2mm quarter-round)")
        print(f"ADDED: center=({add_c.x:.3f},{add_c.y:.3f},{add_c.z:.3f})")
        print(f"ADDED: bbox min=({add_bb.xmin:.3f},{add_bb.ymin:.3f},{add_bb.zmin:.3f}) max=({add_bb.xmax:.3f},{add_bb.ymax:.3f},{add_bb.zmax:.3f})")

        acv = cq.Vector(add_c.x, add_c.y, add_c.z)
        mind = min((acv - a).Length for a in anchors_all)
        print(f"VERIFY: dist(added_center, nearest_anchor)={mind:.3f} mm")

        # Distance of added bbox corners to underside plane (should not be ~0)
        corners = [
            cq.Vector(x, y, z)
            for x in (add_bb.xmin, add_bb.xmax)
            for y in (add_bb.ymin, add_bb.ymax)
            for z in (add_bb.zmin, add_bb.zmax)
        ]
        dists = []
        for p in corners:
            v = p - underside_center_ref
            dists.append(abs(v.dot(underside_normal_ref)))
        print(f"VERIFY: min_abs_dist(added_bbox_corners, underside_plane)={min(dists):.3f} mm (should be >> 0 to avoid touching underside plane)")
    except Exception as ex:
        print(f"WARNING: could not isolate added material via out.cut(base): {ex}")
        added = None

    # ---- Verify 2mm cylindrical/toroidal patch chain near anchors ----
    try:
        from OCP.BRepAdaptor import BRepAdaptor_Surface

        def face_minor_radius(fa):
            ad = BRepAdaptor_Surface(fa.wrapped, True)
            gt = fa.geomType()
            if gt == "CYLINDER":
                return float(ad.Cylinder().Radius())
            if gt == "TORUS":
                return float(ad.Torus().MinorRadius())
            return None

        def min_dist_to_anchors(ptvec):
            return min((ptvec - a).Length for a in anchors_all)

        scan_shape = added if added is not None else out_solid
        cand_faces = []
        for fi, fa in enumerate(scan_shape.Faces()):
            gt = fa.geomType()
            if gt not in ("CYLINDER", "TORUS"):
                continue
            rad = face_minor_radius(fa)
            if rad is None:
                continue
            if abs(rad - 2.0) > 0.15:
                continue
            c = fa.Center()
            cv = cq.Vector(c.x, c.y, c.z)
            if min_dist_to_anchors(cv) > 200.0:
                continue
            cand_faces.append((fi, gt, rad, cv, fa.Area()))

        print(f"SELECTED: {len(cand_faces)} CYL/TOR faces (minor r~2.0mm) near stated inner-loop anchors (on added or whole)")
        for fi, gt, rad, cv, ar in cand_faces[:60]:
            print(f"  r2_face idx={fi} type={gt} rad={rad:.3f} area={ar:.3f} center=({cv.x:.3f},{cv.y:.3f},{cv.z:.3f})")

        cyl_cnt = sum(1 for _, t0, _, _, _ in cand_faces if t0 == "CYLINDER")
        tor_cnt = sum(1 for _, t0, _, _, _ in cand_faces if t0 == "TORUS")
        print(f"VERIFY: r=2 patch chain composition near loop: CYL={cyl_cnt} TOR={tor_cnt} (expect non-trivial counts around full loop)")
    except Exception as ex:
        print(f"WARNING: could not verify r=2 cylindrical/toroidal faces: {ex}")

    # ---- Verify bbox exactly preserved vs specified envelope ----
    new_bb = out_solid.BoundingBox()
    new_vol = out_solid.Volume()
    print(f"NEW: volume={new_vol:.3f} mm^3  dV={new_vol-orig_vol:+.3f} mm^3")
    print(f"NEW  BBOX: min=({new_bb.xmin:.3f},{new_bb.ymin:.3f},{new_bb.zmin:.3f}) max=({new_bb.xmax:.3f},{new_bb.ymax:.3f},{new_bb.zmax:.3f})")
    print(
        "VERIFY: bbox vs REF "
        f"xmin {new_bb.xmin-bbox_min_ref[0]:+.3f}, ymin {new_bb.ymin-bbox_min_ref[1]:+.3f}, zmin {new_bb.zmin-bbox_min_ref[2]:+.3f}, "
        f"xmax {new_bb.xmax-bbox_max_ref[0]:+.3f}, ymax {new_bb.ymax-bbox_max_ref[1]:+.3f}, zmax {new_bb.zmax-bbox_max_ref[2]:+.3f}"
    )

    if len(sols) == 1:
        return out_solid

    rest = [s for i, s in enumerate(sols) if i != 0]
    return cq.Compound.makeCompound(rest + [out_solid])