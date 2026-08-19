def my_cad_function(args):
    import cadquery as cq
    import math

    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input
    base = shape.val() if hasattr(shape, "val") else shape

    solids = base.Solids()
    print(f"SELECTED: {len(solids)} solids from imported STEP")
    for i, s in enumerate(solids):
        bb = s.BoundingBox()
        print(
            f"  solid s{i}: vol={s.Volume():.3f}  bbox=({bb.xmin:.3f},{bb.ymin:.3f},{bb.zmin:.3f})..({bb.xmax:.3f},{bb.ymax:.3f},{bb.zmax:.3f})  lens=({bb.xlen:.3f},{bb.ylen:.3f},{bb.zlen:.3f})"
        )

    if not solids:
        print("ERROR: No solids found; returning input")
        return shape

    # --- identify the newly-copied blade body: the long diagonal one (s3 in the geometry index) ---
    blade_idx = None
    cands = []
    for i, s in enumerate(solids):
        bb = s.BoundingBox()
        if abs(bb.ylen - 12.7) < 1.0 and max(bb.xlen, bb.zlen) > 300:
            cands.append((max(bb.xlen, bb.zlen), i))
    cands.sort(reverse=True)
    print(f"SELECTED: {len(cands)} candidate blade solids (ylen~12.7 and max(xlen,zlen)>300) idx={[i for _, i in cands]}")

    # Prefer the one matching the known s3 bbox proportions (xlen~347.6, zlen~202.7)
    best = None
    for _, i in cands:
        bb = solids[i].BoundingBox()
        score = abs(bb.xlen - 347.642) + abs(bb.zlen - 202.709)
        if best is None or score < best[0]:
            best = (score, i)
    if best is not None:
        blade_idx = best[1]

    if blade_idx is None:
        # last resort: max xlen among ylen~12.7
        m = None
        for i, s in enumerate(solids):
            bb = s.BoundingBox()
            if abs(bb.ylen - 12.7) < 2.0:
                if m is None or bb.xlen > m[0]:
                    m = (bb.xlen, i)
        blade_idx = m[1] if m else 0

    target = solids[blade_idx]
    bb_t = target.BoundingBox()
    print(
        f"USING: solid s{blade_idx} as target newly-copied blade for long-edge R1.27 rounding  bbox=({bb_t.xmin:.3f},{bb_t.ymin:.3f},{bb_t.zmin:.3f})..({bb_t.xmax:.3f},{bb_t.ymax:.3f},{bb_t.zmax:.3f})  lens=({bb_t.xlen:.3f},{bb_t.ylen:.3f},{bb_t.zlen:.3f})"
    )

    # Map global edge indices for reporting
    base_edges = base.Edges()
    edge_idx_by_hc = {e.hashCode(): i for i, e in enumerate(base_edges)}

    # --- derive blade axis from the two planar end faces (area ~256.298) ---
    end_faces = []
    for fi, f in enumerate(target.Faces()):
        if f.geomType() != "PLANE":
            continue
        a = f.Area()
        if 240.0 <= a <= 270.0:
            c = f.Center()
            end_faces.append((fi, a, c, f))

    print(f"SELECTED: {len(end_faces)} planar end-face candidates on s{blade_idx} (area 240..270)")
    for k, (fi, a, c, _) in enumerate(end_faces[:10]):
        print(f"  end_face_cand[{k}]: local_face_idx={fi} area={a:.3f} center=[{c.x:.3f},{c.y:.3f},{c.z:.3f}]")

    axis_origin = cq.Vector(0, 0, 0)
    axis_dir = cq.Vector(0, 0, 1)
    if len(end_faces) >= 2:
        # pick farthest pair
        best_pair = None
        for i in range(len(end_faces)):
            for j in range(i + 1, len(end_faces)):
                ci = end_faces[i][2]
                cj = end_faces[j][2]
                d = (cj - ci).Length
                if best_pair is None or d > best_pair[0]:
                    best_pair = (d, ci, cj)
        L, c1, c2 = best_pair
        axis_origin = (c1 + c2) * 0.5
        axis_dir = (c2 - c1).normalized()
        print(
            f"AXIS: derived from farthest end-face centers  L~{L:.3f}  origin=[{axis_origin.x:.3f},{axis_origin.y:.3f},{axis_origin.z:.3f}]  dir=[{axis_dir.x:.4f},{axis_dir.y:.4f},{axis_dir.z:.4f}]"
        )
    else:
        # fallback: use longest bbox axis projected to XZ
        v = cq.Vector(bb_t.xlen, 0, bb_t.zlen)
        axis_dir = v.normalized() if v.Length > 1e-6 else cq.Vector(1, 0, 0)
        axis_origin = target.Center()
        print(
            f"AXIS: fallback from bbox  origin=[{axis_origin.x:.3f},{axis_origin.y:.3f},{axis_origin.z:.3f}]  dir=[{axis_dir.x:.4f},{axis_dir.y:.4f},{axis_dir.z:.4f}]"
        )

    def dist_point_to_axis(P, O, D):
        # D must be normalized
        v = P - O
        t = v.dot(D)
        perp = v - D * t
        return perp.Length, t

    def faces_adjacent_to_edge(solid, edge):
        eh = edge.hashCode()
        adj = []
        for f in solid.Faces():
            for e in f.Edges():
                if e.hashCode() == eh:
                    adj.append(f)
                    break
        return adj

    # --- select broad outer faces at y~0 and y~12.7, and collect candidate long edges from them ---
    broad_faces = []
    for fi, f in enumerate(target.Faces()):
        if f.geomType() != "PLANE":
            continue
        try:
            n = f.normalAt().normalized()
        except Exception:
            continue
        if abs(n.y) < 0.99:
            continue
        c = f.Center()
        if not (abs(c.y - 0.0) < 0.20 or abs(c.y - 12.7) < 0.20):
            continue
        if f.Area() < 500.0:
            continue
        broad_faces.append((fi, f.Area(), c, n, f))

    broad_faces.sort(key=lambda t: t[1], reverse=True)
    print(f"SELECTED: {len(broad_faces)} planar broad outer faces on s{blade_idx} (|normal.y|>0.99 and y~0/12.7 and area>500)")
    for k, (fi, a, c, n, _) in enumerate(broad_faces[:10]):
        print(
            f"  broad_face[{k}]: local_face_idx={fi} area={a:.3f} center=[{c.x:.3f},{c.y:.3f},{c.z:.3f}] normal=[{n.x:.3f},{n.y:.3f},{n.z:.3f}]"
        )

    cand_edges = {}
    for _, _, _, _, f in broad_faces:
        for e in f.Edges():
            cand_edges[e.hashCode()] = e
    cand_list = list(cand_edges.values())
    print(f"SELECTED: {len(cand_list)} unique edges collected from broad outer faces")

    # --- filter to long, axis-aligned, outermost edges that are between two planar faces (broad+side) ---
    edge_info = []
    for e in cand_list:
        try:
            if e.geomType() != "LINE":
                continue
            L = e.Length()
        except Exception:
            continue
        if L < 80.0:
            continue

        ce = e.Center()
        # constrain to top/bottom surfaces so we don't touch central crossing profile inside the stack
        if not (abs(ce.y - 0.0) < 0.35 or abs(ce.y - 12.7) < 0.35):
            continue

        try:
            t = e.tangentAt(0.5).normalized()
        except Exception:
            continue
        align = abs(t.dot(axis_dir))
        if align < 0.95:
            continue

        adj = faces_adjacent_to_edge(target, e)
        if len(adj) != 2:
            continue
        if any(f.geomType() != "PLANE" for f in adj):
            continue
        try:
            n1 = adj[0].normalAt().normalized()
            n2 = adj[1].normalAt().normalized()
        except Exception:
            continue

        # one should be broad (|y|~1), the other should not
        cond = (abs(n1.y) > 0.99 and abs(n2.y) < 0.2) or (abs(n2.y) > 0.99 and abs(n1.y) < 0.2)
        if not cond:
            continue

        d_perp, t_along = dist_point_to_axis(ce, axis_origin, axis_dir)
        edge_info.append((d_perp, L, align, t_along, ce, e, adj, n1, n2))

    edge_info.sort(key=lambda t: (t[0], t[1]), reverse=True)
    print(f"SELECTED: {len(edge_info)} candidate long outer edges (LINE, L>80, aligned to axis, y~0/12.7, planar+planar adjacency)")
    for k, (d_perp, L, align, t_along, ce, e, adj, n1, n2) in enumerate(edge_info[:20]):
        gi = edge_idx_by_hc.get(e.hashCode(), None)
        print(
            f"  edge_cand[{k}]: global_edge_idx={gi} L={L:.3f} align={align:.4f} d_perp={d_perp:.3f} t_along={t_along:.3f} center=[{ce.x:.3f},{ce.y:.3f},{ce.z:.3f}] n1=[{n1.x:.2f},{n1.y:.2f},{n1.z:.2f}] n2=[{n2.x:.2f},{n2.y:.2f},{n2.z:.2f}]"
        )

    # fallback if above was too strict: use the outermost band from previous approach
    if not edge_info:
        edge_info = []
        for e in cand_list:
            try:
                if e.geomType() != "LINE":
                    continue
                L = e.Length()
            except Exception:
                continue
            if L < 80.0:
                continue
            ce = e.Center()
            if not (abs(ce.y - 0.0) < 0.35 or abs(ce.y - 12.7) < 0.35):
                continue
            try:
                t = e.tangentAt(0.5).normalized()
            except Exception:
                continue
            align = abs(t.dot(axis_dir))
            if align < 0.95:
                continue
            d_perp, t_along = dist_point_to_axis(ce, axis_origin, axis_dir)
            edge_info.append((d_perp, L, align, t_along, ce, e, None, None, None))
        edge_info.sort(key=lambda t: (t[0], t[1]), reverse=True)
        print(f"SELECTED: {len(edge_info)} FALLBACK candidate long edges (no adjacency filter)")

    if not edge_info:
        print("ERROR: No candidate long edges found; cannot apply requested rounding. Returning input (no-op risk).")
        return shape

    max_d = edge_info[0][0]
    d_tol = 0.25
    selected_edges = []
    sel_h = set()
    for d_perp, L, align, t_along, ce, e, adj, n1, n2 in edge_info:
        if d_perp >= max_d - d_tol:
            h = e.hashCode()
            if h not in sel_h:
                sel_h.add(h)
                selected_edges.append((e, adj, n1, n2))

    sel_idx = [edge_idx_by_hc.get(e.hashCode(), None) for e, _, _, _ in selected_edges]
    print(f"SELECTED: {len(selected_edges)} edges for R1.27 rounding (outermost band max_d={max_d:.3f}, tol={d_tol:.3f}) idx={sel_idx}")

    # --- Boolean fillet cutters (kernel refused native fillet previously) ---
    r = 1.27
    print(f"TARGET FILLET RADIUS: r={r:.3f} mm")

    BIG = max(bb_t.xlen, bb_t.ylen, bb_t.zlen) * 4.0
    cutters = []

    def plane_xdir_from_normal(n):
        ref = cq.Vector(0, 0, 1) if abs(n.z) < 0.9 else cq.Vector(1, 0, 0)
        xdir = ref.cross(n)
        if xdir.Length < 1e-9:
            xdir = cq.Vector(0, 1, 0).cross(n)
        return xdir.normalized()

    for k, (e, adj, n1, n2) in enumerate(selected_edges):
        try:
            P = e.positionAt(0.5)
            t = e.tangentAt(0.5).normalized()
            L = e.Length()
        except Exception as ex:
            print(f"EDGE[{k}]: skip (failed to read geometry): {ex}")
            continue

        # If adjacency not captured, re-evaluate now
        if adj is None:
            adj = faces_adjacent_to_edge(target, e)
            if len(adj) != 2 or any(f.geomType() != "PLANE" for f in adj):
                print(f"EDGE[{k}]: skip (adjacency not 2 planar faces) adj={len(adj)}")
                continue
            n1 = adj[0].normalAt().normalized()
            n2 = adj[1].normalAt().normalized()

        b = (n1 + n2)
        if b.Length < 1e-9:
            print(f"EDGE[{k}]: skip (bisector nearly zero)")
            continue
        b = b.normalized()

        # Convexity check and possible flip (ensure P - b*eps is inside, P + b*eps is outside)
        eps = 0.05
        try:
            inside_minus = target.isInside(P - b * eps)
            inside_plus = target.isInside(P + b * eps)
        except Exception:
            inside_minus = True
            inside_plus = False

        if not inside_minus and inside_plus:
            b = -b
            inside_minus, inside_plus = inside_plus, inside_minus

        print(
            f"EDGE[{k}]: global_edge_idx={edge_idx_by_hc.get(e.hashCode(), None)}  L={L:.3f}  P=[{P.x:.3f},{P.y:.3f},{P.z:.3f}]  t=[{t.x:.3f},{t.y:.3f},{t.z:.3f}]  "
            f"n1=[{n1.x:.3f},{n1.y:.3f},{n1.z:.3f}] n2=[{n2.x:.3f},{n2.y:.3f},{n2.z:.3f}]  b=[{b.x:.3f},{b.y:.3f},{b.z:.3f}]  inside(P-b*eps)={inside_minus} inside(P+b*eps)={inside_plus}"
        )

        denom = b.dot(n1)
        if abs(denom) < 1e-9:
            denom = b.dot(n2)
        if abs(denom) < 1e-9:
            print(f"EDGE[{k}]: skip (bisector dot normal too small)")
            continue

        C = P - b * (r / denom)

        # slab solids (half-spaces into the part) and a clip prism along the edge to prevent over-cut
        try:
            x1 = plane_xdir_from_normal(n1)
            x2 = plane_xdir_from_normal(n2)
            slab1 = cq.Workplane(cq.Plane(origin=P, normal=n1, xDir=x1)).rect(BIG, BIG).extrude(-r).val()
            slab2 = cq.Workplane(cq.Plane(origin=P, normal=n2, xDir=x2)).rect(BIG, BIG).extrude(-r).val()

            # clip prism along edge tangent
            xclip = b.cross(t)
            if xclip.Length < 1e-9:
                xclip = cq.Vector(0, 1, 0).cross(t)
            if xclip.Length < 1e-9:
                xclip = cq.Vector(1, 0, 0)
            xclip = xclip.normalized()
            h = L + 6 * r
            clip = cq.Workplane(cq.Plane(origin=P, normal=t, xDir=xclip)).rect(8 * r, 8 * r).extrude(h, both=True).val()

            corner = slab1.intersect(slab2).intersect(target).intersect(clip)
            if corner.Volume() < 1e-6:
                print(f"EDGE[{k}]: skip (corner intersect volume ~0)")
                continue

            cyl = cq.Solid.makeCylinder(r, h, C - t * (h / 2.0), t).intersect(clip)
            cutter = corner.cut(cyl)

            if cutter.Volume() < 1e-6:
                print(f"EDGE[{k}]: skip (cutter volume ~0)")
                continue

            cutters.append(cutter)
            bb_cu = cutter.BoundingBox()
            print(
                f"EDGE[{k}]: CUTTER built  cutterVol={cutter.Volume():.3f}  center=[{cutter.Center().x:.3f},{cutter.Center().y:.3f},{cutter.Center().z:.3f}]  "
                f"bbox=({bb_cu.xmin:.3f},{bb_cu.ymin:.3f},{bb_cu.zmin:.3f})..({bb_cu.xmax:.3f},{bb_cu.ymax:.3f},{bb_cu.zmax:.3f})"
            )

        except Exception as ex:
            print(f"EDGE[{k}]: skip (failed to build cutter): {ex}")
            continue

    print(f"SELECTED: {len(cutters)} boolean cutters for R1.27 long-edge rounding")
    if not cutters:
        print("ERROR: No cutters built; cannot apply rounding. Returning input (no-op risk).")
        return shape

    tool = cq.Compound.makeCompound(cutters)

    vol_before = target.Volume()
    try:
        edited = target.cut(tool)
    except Exception as ex:
        print(f"ERROR: Boolean cut failed: {ex}")
        return shape

    vol_after = edited.Volume()
    removed_vol = vol_before - vol_after
    print(f"RESULT: target blade volume before={vol_before:.3f} after={vol_after:.3f} removed={removed_vol:.3f} mm^3")

    if abs(removed_vol) < 1e-6:
        print("WARNING: removed volume ~0 (possible no-op). Still returning edited shape to avoid silent failure.")

    # --- Verify achieved radius by measuring cylindrical faces with radius ~1.27 ---
    achieved = []
    try:
        from OCP.BRepAdaptor import BRepAdaptor_Surface
        from OCP.GeomAbs import GeomAbs_Cylinder

        long_cyl = 0
        for f in edited.Faces():
            if f.geomType() != "CYLINDER":
                continue
            try:
                ad = BRepAdaptor_Surface(f.wrapped, True)
                if ad.GetType() != GeomAbs_Cylinder:
                    continue
                rad = ad.Cylinder().Radius()
            except Exception:
                continue

            bb = f.BoundingBox()
            if max(bb.xlen, bb.zlen) < 60.0:
                continue
            long_cyl += 1
            if abs(rad - r) < 0.05:
                achieved.append(rad)

        print(f"VERIFY: found {long_cyl} long cylindrical faces on edited blade (max(xlen,zlen)>60)")
        print(f"VERIFY: found {len(achieved)} long cylinders with radius within ±0.05 of {r:.3f}")
        if achieved:
            print(
                f"ACHIEVED RADIUS: avg={sum(achieved)/len(achieved):.4f}  min={min(achieved):.4f}  max={max(achieved):.4f}  (target {r:.3f})"
            )
        else:
            print("WARNING: could not numerically confirm R1.27; rely on render inspection")

    except Exception as ex:
        print(f"WARNING: radius verification skipped (OCP adaptor import failed): {ex}")

    # --- Recompound with only the target blade replaced; other bodies untouched ---
    out_solids = [edited if i == blade_idx else s for i, s in enumerate(solids)]
    out = cq.Compound.makeCompound(out_solids)
    return out