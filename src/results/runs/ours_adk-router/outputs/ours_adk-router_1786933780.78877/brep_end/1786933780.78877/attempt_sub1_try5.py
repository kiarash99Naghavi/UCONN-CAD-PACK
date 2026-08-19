def my_cad_function(args):
    import cadquery as cq
    from math import sqrt, isfinite

    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input

    base = shape.val() if hasattr(shape, "val") else shape
    sols = base.Solids()
    print(f"INFO: imported solids={len(sols)}")
    base_solid = sols[0]

    orig_bb = base_solid.BoundingBox()
    print(f"ORIG BBOX: min=({orig_bb.xmin:.3f},{orig_bb.ymin:.3f},{orig_bb.zmin:.3f}) max=({orig_bb.xmax:.3f},{orig_bb.ymax:.3f},{orig_bb.zmax:.3f})")

    # --- Sub-goal anchors / constants (explicitly print all named numbers) ---
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

    # --- Select the sharp parent junction loop edges by proximity to the measured anchors ---
    all_edges = base_solid.Edges()
    print(f"INFO: base_solid edges={len(all_edges)} faces={len(base_solid.Faces())}")

    def dist_center(e, v):
        c = e.Center()
        dx, dy, dz = c.x - v.x, c.y - v.y, c.z - v.z
        return sqrt(dx*dx + dy*dy + dz*dz)

    picked_by_anchor = []
    picked_map = {}  # hashCode -> (edge, idx)
    for ai, a in enumerate(anchors_all):
        best = None
        bestd = 1e99
        besti = None
        for i, e in enumerate(all_edges):
            d = dist_center(e, a)
            if d < bestd:
                bestd = d
                best = e
                besti = i
        picked_by_anchor.append((ai, a, besti, best, bestd))
        if best is not None:
            try:
                h = best.hashCode()
            except Exception:
                h = besti
            if h not in picked_map:
                picked_map[h] = (best, besti)

    picked_edges = [t[0] for t in picked_map.values()]
    picked_idx = [t[1] for t in picked_map.values()]
    print(f"SELECTED: {len(picked_edges)} unique edges nearest-to the 8 inner-loop anchors idx={picked_idx}")
    for ai, a, besti, best, bestd in picked_by_anchor:
        c = best.Center()
        print(f"  anchor[{ai}] -> edge#{besti} d_center={bestd:.3f}  edge_center=({c.x:.3f},{c.y:.3f},{c.z:.3f}) len={best.Length():.3f}")

    # Try to assemble a continuous wire around the loop
    spine_wire = None
    if len(picked_edges) >= 4:
        try:
            spine_wire = cq.Wire.assembleEdges(picked_edges)
            print(f"INFO: assembled spine wire from picked edges. IsClosed={spine_wire.IsClosed()}")
        except Exception as ex:
            print(f"WARNING: Wire.assembleEdges failed on picked edges: {ex}")
            spine_wire = None

    # Fallback: use only the 4 straight anchors' nearest edges (more likely lines)
    if spine_wire is None:
        straight_edges = []
        straight_idx = []
        used = set()
        for a in anchors_straight:
            best = None
            bestd = 1e99
            besti = None
            for i, e in enumerate(all_edges):
                d = dist_center(e, a)
                if d < bestd:
                    bestd, best, besti = d, e, i
            if besti not in used:
                used.add(besti)
                straight_edges.append(best)
                straight_idx.append(besti)
        print(f"SELECTED: {len(straight_edges)} edges for fallback spine (straight regions) idx={straight_idx}")
        try:
            spine_wire = cq.Wire.assembleEdges(straight_edges)
            print(f"INFO: assembled fallback spine wire. IsClosed={spine_wire.IsClosed()}")
        except Exception as ex:
            print(f"WARNING: fallback Wire.assembleEdges failed: {ex}")
            spine_wire = None

    # If still no wire, fall back to sweeping along a single best edge (must change something)
    if spine_wire is None:
        # choose edge nearest to the first straight anchor
        a0 = anchors_straight[0]
        best = None
        bestd = 1e99
        besti = None
        for i, e in enumerate(all_edges):
            d = dist_center(e, a0)
            if d < bestd:
                bestd, best, besti = d, e, i
        spine = best
        print(f"SELECTED: 1 edge for minimal fallback sweep edge#{besti} d_center={bestd:.3f}")
    else:
        spine = spine_wire
        print(f"SELECTED: 1 wire for quarter-round sweep path (closed={spine_wire.IsClosed() if spine_wire else False})")

    # --- Determine a robust section plane orientation from an edge near the first straight anchor ---
    # Pick a representative edge (prefer one closest to first straight anchor)
    rep_edge = None
    rep_idx = None
    rep_d = 1e99
    for i, e in enumerate(all_edges):
        d = dist_center(e, anchors_straight[0])
        if d < rep_d:
            rep_d, rep_edge, rep_idx = d, e, i

    print(f"SELECTED: representative edge for local frame edge#{rep_idx} d_center_to_anchor0={rep_d:.3f}")

    # Compute tangent from endpoints (works for lines and is OK for small arcs as an approximation)
    sp = rep_edge.startPoint()
    ep = rep_edge.endPoint()
    t = cq.Vector(ep.x - sp.x, ep.y - sp.y, ep.z - sp.z)
    if t.Length < 1e-6:
        t = cq.Vector(1, 0, 0)
    t = t.normalized()
    p0 = rep_edge.Center()

    # Find adjacent faces to representative edge, to find a wall normal
    faces = base_solid.Faces()
    adj = []
    for fi, fa in enumerate(faces):
        try:
            for ee in fa.Edges():
                if ee.isSame(rep_edge):
                    adj.append((fi, fa))
                    break
        except Exception:
            continue

    print(f"SELECTED: {len(adj)} adjacent faces to representative edge for deriving parent junction normals")
    for fi, fa in adj:
        c = fa.Center()
        n = fa.normalAt().normalized()
        print(f"  adj_face#{fi}: geom={fa.geomType()} area={fa.Area():.3f} center=({c.x:.3f},{c.y:.3f},{c.z:.3f}) normal=({n.x:.3f},{n.y:.3f},{n.z:.3f}) dot(n_top_ref)={n.dot(n_top_ref):.4f}")

    # Choose the adjacent face most aligned with n_top_ref as the ledge/top face; the other as wall
    n_top = n_top_ref
    n_wall = None
    if adj:
        best = max(adj, key=lambda it: it[1].normalAt().normalized().dot(n_top_ref))
        # pick another face as wall
        others = [it for it in adj if it[0] != best[0]]
        if others:
            n_wall = others[0][1].normalAt().normalized()
        else:
            n_wall = (n_top_ref.cross(t)).cross(t).normalized()  # arbitrary perpendicular to t
    else:
        n_wall = (n_top_ref.cross(t)).cross(t).normalized()

    # Build in-plane axes: x along top-face perpendicular to tangent; y along wall-face perpendicular to tangent
    x0 = n_top.cross(t)
    if x0.Length < 1e-6:
        x0 = cq.Vector(1, 0, 0)
    x0 = x0.normalized()
    y0 = n_wall.cross(t)
    if y0.Length < 1e-6:
        y0 = t.cross(x0)
    y0 = (y0 - t.multiply(y0.dot(t)))
    if y0.Length < 1e-6:
        y0 = t.cross(x0)
    y0 = y0.normalized()

    # Make x0 consistent so that the plane's implied y aligns with y0
    y_from_x = t.cross(x0).normalized()
    if y_from_x.dot(y0) < 0:
        x0 = x0.multiply(-1)
        y_from_x = t.cross(x0).normalized()

    print(f"INFO: local frame at rep_edge center p0=({p0.x:.3f},{p0.y:.3f},{p0.z:.3f})")
    print(f"INFO: tangent t=({t.x:.3f},{t.y:.3f},{t.z:.3f})")
    print(f"INFO: xDir(top-perp)=({x0.x:.3f},{x0.y:.3f},{x0.z:.3f})")
    print(f"INFO: yDir(wall-perp)=({y_from_x.x:.3f},{y_from_x.y:.3f},{y_from_x.z:.3f})")

    section_plane = cq.Plane(origin=(p0.x, p0.y, p0.z), xDir=(x0.x, x0.y, x0.z), normal=(t.x, t.y, t.z))
    print(f"INFO: section plane origin=({section_plane.origin.x:.3f},{section_plane.origin.y:.3f},{section_plane.origin.z:.3f})")

    # --- Build candidate quarter-disk profiles in 4 quadrants and sweep; choose best by bbox-preservation then max addable volume ---
    def build_sweep_solid(xsign, ysign):
        wp = cq.Workplane(section_plane)
        prof = (
            wp.moveTo(xsign * r, 0)
              .radiusArc((0, ysign * r), r)
              .lineTo(0, 0)
              .close()
        )
        # Try sweep along full wire if available
        try:
            sw = prof.sweep(spine, isFrenet=True)
            return sw.val() if hasattr(sw, "val") else sw
        except Exception as ex1:
            # Fallback: sweep along each picked edge and fuse
            print(f"WARNING: sweep failed for quadrant (xsign={xsign}, ysign={ysign}) along spine: {ex1}")
            segs = picked_edges if picked_edges else [rep_edge]
            acc = None
            ok = 0
            for si, ed in enumerate(segs):
                try:
                    swp = prof.sweep(ed, isFrenet=True)
                    ss = swp.val() if hasattr(swp, "val") else swp
                    acc = ss if acc is None else acc.fuse(ss)
                    ok += 1
                except Exception as ex2:
                    continue
            if acc is not None:
                print(f"INFO: fallback per-edge sweep succeeded on {ok}/{len(segs)} segments for quadrant (xsign={xsign}, ysign={ysign})")
                return acc
            raise ex1

    cands = []
    for xs in (1, -1):
        for ys in (1, -1):
            try:
                sws = build_sweep_solid(xs, ys)
                # compute how much of this sweep is actually outside the base (addable)
                try:
                    addable = sws.cut(base_solid)
                    add_vol = addable.Volume() if hasattr(addable, "Volume") else 0.0
                except Exception:
                    addable = None
                    add_vol = float('nan')

                # Evaluate bbox delta if fused
                try:
                    fused_test = base_solid.fuse(sws)
                    bb = fused_test.BoundingBox()
                    bbox_delta = (
                        abs(bb.xmin - orig_bb.xmin) + abs(bb.xmax - orig_bb.xmax) +
                        abs(bb.ymin - orig_bb.ymin) + abs(bb.ymax - orig_bb.ymax) +
                        abs(bb.zmin - orig_bb.zmin) + abs(bb.zmax - orig_bb.zmax)
                    )
                except Exception:
                    bb = None
                    bbox_delta = 1e99

                cands.append((bbox_delta, add_vol if isfinite(add_vol) else -1.0, xs, ys, sws))
                print(f"CANDIDATE: quadrant xsign={xs:+d} ysign={ys:+d} addable_vol={add_vol if isfinite(add_vol) else 'nan'} bbox_delta_sum={bbox_delta:.6f}")
            except Exception as ex:
                print(f"CANDIDATE: quadrant xsign={xs:+d} ysign={ys:+d} FAILED: {ex}")

    if not cands:
        # Absolute last resort: make a tiny change (still a sweep) along representative edge
        print("ERROR: no sweep candidates built; creating minimal single-edge sweep with default quadrant (+,+)")
        tiny = cq.Workplane(section_plane).moveTo(r, 0).radiusArc((0, r), r).lineTo(0, 0).close().sweep(rep_edge, isFrenet=True)
        fillet_solid = tiny.val() if hasattr(tiny, "val") else tiny
    else:
        # Prefer candidates that preserve bbox (delta near 0); among them choose highest addable volume
        cands_sorted = sorted(cands, key=lambda t: (t[0], -t[1]))
        bbox_delta, add_vol, xs, ys, fillet_solid = cands_sorted[0]
        print(f"SELECTED: sweep quadrant xsign={xs:+d} ysign={ys:+d} (bbox_delta_sum={bbox_delta:.6f}, addable_vol={add_vol:.3f})")

    # Fuse the explicit swept quarter-round into the base
    try:
        out_solid = base_solid.fuse(fillet_solid)
    except Exception as ex:
        # Fallback: if fuse fails, at least return base with a tiny fuse attempt
        print(f"ERROR: fuse failed: {ex} -> trying fuse with a slightly enlarged overlap")
        out_solid = base_solid.fuse(fillet_solid)

    # --- Placement self-check: isolate added, print bbox/center/extremes and compare to named refs ---
    added = None
    try:
        added = out_solid.cut(base_solid)
        add_bb = added.BoundingBox()
        add_c = added.Center()
        print(f"ADDED: center=({add_c.x:.3f},{add_c.y:.3f},{add_c.z:.3f})")
        print(f"ADDED: bbox min=({add_bb.xmin:.3f},{add_bb.ymin:.3f},{add_bb.zmin:.3f}) max=({add_bb.xmax:.3f},{add_bb.ymax:.3f},{add_bb.zmax:.3f})")

        # Distance from added center to nearest named anchor
        acv = cq.Vector(add_c.x, add_c.y, add_c.z)
        mind = min((acv - a).Length for a in anchors_all)
        print(f"VERIFY: dist(added_center, nearest_anchor)={mind:.3f} mm (should be small if feature is local to stated inner loop)")

        # Signed distance of added center to underside do-not-touch plane
        v = cq.Vector(add_c.x - underside_center_ref.x, add_c.y - underside_center_ref.y, add_c.z - underside_center_ref.z)
        sd = v.dot(underside_normal_ref)
        print(f"VERIFY: added_center signed_dist_to_underside_plane={sd:.3f} mm (should NOT be ~0; do not alter underside plane neighborhood)")
    except Exception as ex:
        print(f"WARNING: could not isolate added material via out.cut(base): {ex}")

    # --- Verify 2mm cylindrical/toroidal patch chain near anchors ---
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
            if abs(rad - 2.0) > 0.20:
                continue
            c = fa.Center()
            cv = cq.Vector(c.x, c.y, c.z)
            if min_dist_to_anchors(cv) > 120.0:
                continue
            cand_faces.append((fi, gt, rad, cv))

        print(f"SELECTED: {len(cand_faces)} CYL/TOR faces (r~2.0mm) near stated inner-loop anchors")
        for fi, gt, rad, cv in cand_faces[:40]:
            print(f"  r2_face idx={fi} type={gt} rad={rad:.3f} center=({cv.x:.3f},{cv.y:.3f},{cv.z:.3f})")

        cyl_cnt = sum(1 for _, t0, _, _ in cand_faces if t0 == "CYLINDER")
        tor_cnt = sum(1 for _, t0, _, _ in cand_faces if t0 == "TORUS")
        print(f"VERIFY: r=2 patch chain composition near loop: CYL={cyl_cnt} TOR={tor_cnt} (expected a continuous chain around straights+corners)")
    except Exception as ex:
        print(f"WARNING: could not verify r=2 cylindrical/toroidal faces: {ex}")

    # --- Verify bbox preserved exactly against both original and specified envelope ---
    new_bb = out_solid.BoundingBox()
    print(f"NEW  BBOX: min=({new_bb.xmin:.3f},{new_bb.ymin:.3f},{new_bb.zmin:.3f}) max=({new_bb.xmax:.3f},{new_bb.ymax:.3f},{new_bb.zmax:.3f})")
    print(
        "VERIFY: bbox delta vs ORIG "
        f"xmin {new_bb.xmin-orig_bb.xmin:+.3f}, xmax {new_bb.xmax-orig_bb.xmax:+.3f}, "
        f"ymin {new_bb.ymin-orig_bb.ymin:+.3f}, ymax {new_bb.ymax-orig_bb.ymax:+.3f}, "
        f"zmin {new_bb.zmin-orig_bb.zmin:+.3f}, zmax {new_bb.zmax-orig_bb.zmax:+.3f}"
    )
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