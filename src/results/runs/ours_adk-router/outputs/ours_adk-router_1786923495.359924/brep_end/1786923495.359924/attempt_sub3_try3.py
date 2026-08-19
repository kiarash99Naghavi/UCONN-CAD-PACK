def my_cad_function(args):
    import cadquery as cq

    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input
    base = shape.val() if hasattr(shape, "val") else shape

    solids = base.Solids()
    print(f"SELECTED: {len(solids)} solids from imported STEP")
    for i, s in enumerate(solids):
        bb = s.BoundingBox()
        print(
            f"  solid s{i}: vol={s.Volume():.3f}  bbox=({bb.xmin:.3f},{bb.ymin:.3f},{bb.zmin:.3f})..({bb.xmax:.3f},{bb.ymax:.3f},{bb.zmax:.3f})  lens=({bb.xlen:.3f},{bb.ylen:.3f},{bb.zlen:.3f})"
        )

    if len(solids) < 1:
        print("ERROR: no solids; returning input")
        return shape

    # Global edge index mapping for prints
    all_edges = base.Edges()
    edge_idx_by_hc = {e.hashCode(): i for i, e in enumerate(all_edges)}
    print(f"INDEX: {len(all_edges)} global edges mapped by hashCode")

    # --- pick the newly-copied blade body (expected: s3) ---
    # Heuristic: a blade is thin in Y (~12.7), long in XZ; new one in this dataset has the largest xlen among those.
    blade_cands = []
    for i, s in enumerate(solids):
        bb = s.BoundingBox()
        if abs(bb.ylen - 12.7) < 0.75 and bb.xlen > 250:  # excludes s1 (xlen=25.4)
            blade_cands.append((bb.xlen, bb.zlen, i))
    print(f"SELECTED: {len(blade_cands)} candidate diagonal blades (ylen~12.7 & xlen>250) idx={[t[2] for t in blade_cands]}")
    if not blade_cands:
        print("ERROR: could not find target blade; returning input")
        return shape

    blade_cands.sort(reverse=True)  # max xlen first
    blade_idx = blade_cands[0][2]
    target = solids[blade_idx]
    bb_t = target.BoundingBox()
    print(
        f"USING: solid s{blade_idx} as target newly-copied blade for long-edge R1.27 fillets  "
        f"bbox=({bb_t.xmin:.3f},{bb_t.ymin:.3f},{bb_t.zmin:.3f})..({bb_t.xmax:.3f},{bb_t.ymax:.3f},{bb_t.zmax:.3f})  lens=({bb_t.xlen:.3f},{bb_t.ylen:.3f},{bb_t.zlen:.3f})"
    )

    # --- derive blade axis from its two planar end faces (area ~256.298 in the geometry index) ---
    end_faces = []
    for fi, f in enumerate(target.Faces()):
        if f.geomType() != "PLANE":
            continue
        a = f.Area()
        if 240.0 <= a <= 270.0:
            c = f.Center()
            end_faces.append((fi, a, c, f))
    print(f"SELECTED: {len(end_faces)} planar end-face candidates on s{blade_idx} (area 240..270)")
    for k, (fi, a, c, _) in enumerate(end_faces[:8]):
        print(f"  end_face_cand[{k}]: local_face_idx={fi} area={a:.3f} center=[{c.x:.3f},{c.y:.3f},{c.z:.3f}]")

    if len(end_faces) < 2:
        print("ERROR: need 2 end faces to derive axis; returning input")
        return shape

    # pick the farthest pair by center distance
    best = None
    for i in range(len(end_faces)):
        for j in range(i + 1, len(end_faces)):
            ci = end_faces[i][2]
            cj = end_faces[j][2]
            d = (cj - ci).Length
            if best is None or d > best[0]:
                best = (d, end_faces[i], end_faces[j])

    L_axis, efi, efj = best
    c1 = efi[2]
    c2 = efj[2]
    axis = (c2 - c1)
    if axis.Length < 1e-9:
        print("ERROR: derived axis is degenerate; returning input")
        return shape
    axis = axis.normalized()
    origin = (c1 + c2) * 0.5
    print(
        f"AXIS: derived from farthest end-face centers  L~{L_axis:.3f}  "
        f"origin=[{origin.x:.3f},{origin.y:.3f},{origin.z:.3f}]  dir=[{axis.x:.4f},{axis.y:.4f},{axis.z:.4f}]"
    )

    # --- select long outer blade edges to fillet ---
    r = 1.27
    print(f"TARGET FILLET RADIUS: r={r:.3f} mm")

    def edge_mid(e):
        # Edge.Center() is fine; for lines it's midpoint.
        return e.Center()

    def edge_tangent_mid(e):
        try:
            return e.tangentAt(0.5).normalized()
        except Exception:
            # fallback for weird parameterization
            return e.tangentAt(e.paramAt(0.0 + 1e-6)).normalized()

    def dist_to_axis(P):
        v = P - origin
        u = v.dot(axis)
        rad = (v - axis * u).Length
        return u, rad

    edges = target.Edges()

    def pick_edges(y_tol, u_exclude, d_min, align_min=0.995, L_min=80.0, require_y=True):
        sel = []
        mids = []
        for e in edges:
            if e.geomType() != "LINE":
                continue
            L = e.Length()
            if L < L_min:
                continue
            P = edge_mid(e)
            t = edge_tangent_mid(e)
            if abs(t.dot(axis)) < align_min:
                continue
            u, d = dist_to_axis(P)
            if abs(u) < u_exclude:
                continue
            if d < d_min:
                continue
            if require_y:
                if not (abs(P.y - 0.0) <= y_tol or abs(P.y - 12.7) <= y_tol):
                    continue
            sel.append(e)
            mids.append(P)
        return sel, mids

    # primary strict selection, then relax progressively
    attempts = [
        (0.35, 25.0, 8.5, True),
        (0.50, 15.0, 8.0, True),
        (0.75,  5.0, 7.5, True),
        (0.75,  0.0, 7.0, True),
        (1.50,  0.0, 7.0, False),
    ]

    selected = []
    midpoints = []
    used = None
    for (y_tol, u_ex, dmin, reqy) in attempts:
        sel, mids = pick_edges(y_tol=y_tol, u_exclude=u_ex, d_min=dmin, require_y=reqy)
        print(
            f"SELECTED: {len(sel)} candidate long edges with params: y_tol={y_tol:.2f}, u_exclude={u_ex:.1f}, d_min={dmin:.1f}, require_y={reqy}"
        )
        if len(sel) >= 4:
            selected, midpoints, used = sel, mids, (y_tol, u_ex, dmin, reqy)
            break

    if not selected:
        # last-ditch: anything long+aligned (still excludes end edges), no distance filter
        sel = []
        mids = []
        for e in edges:
            if e.geomType() != "LINE":
                continue
            if e.Length() < 120.0:
                continue
            P = edge_mid(e)
            t = edge_tangent_mid(e)
            if abs(t.dot(axis)) < 0.995:
                continue
            sel.append(e)
            mids.append(P)
        selected, midpoints, used = sel, mids, (None, None, None, None)
        print(f"SELECTED: {len(selected)} LAST-DITCH long+aligned edges (no y/u/d filters)")

    # Report selection with global edge indices and y-side counts
    idxs = [edge_idx_by_hc.get(e.hashCode(), None) for e in selected]
    y0 = 0
    y12 = 0
    for P in midpoints:
        if abs(P.y - 0.0) <= 0.75:
            y0 += 1
        if abs(P.y - 12.7) <= 0.75:
            y12 += 1
    print(f"SELECTED: {len(selected)} edges for R1.27 long-edge fillet  idx={idxs}")
    print(f"CHECK: selected-edge midpoint y-side distribution: near y~0 => {y0}, near y~12.7 => {y12}")

    if len(selected) == 0:
        # Must not silently no-op; but we also must not damage part randomly.
        print("ERROR: selected 0 edges; cannot proceed without risking wrong fillets. Returning input (no-op).")
        return shape

    # --- apply fillet ---
    vol_before = target.Volume()
    edited = None
    try:
        edited = target.fillet(r, selected)
        print(f"FILLET: applied in one operation on {len(selected)} edges")
    except Exception as ex:
        print(f"WARNING: bulk fillet failed: {ex}")
        # iterative fillet by nearest-to-point; keep partial successes
        wp = cq.Workplane(cq.Plane.XY()).newObject([target])
        ok_pts = 0
        for k, P in enumerate(midpoints):
            try:
                wp = wp.edges(cq.selectors.NearestToPointSelector((P.x, P.y, P.z))).fillet(r)
                ok_pts += 1
                print(f"FILLET: success at midpoint[{k}] P=[{P.x:.3f},{P.y:.3f},{P.z:.3f}]")
            except Exception as ex2:
                print(f"FILLET: failed at midpoint[{k}] P=[{P.x:.3f},{P.y:.3f},{P.z:.3f}]  err={ex2}")
        edited = wp.val()
        print(f"FILLET: iterative complete, successes={ok_pts}/{len(midpoints)}")

    vol_after = edited.Volume()
    removed = vol_before - vol_after
    print(f"RESULT: target blade volume before={vol_before:.3f} after={vol_after:.3f} removed={removed:.3f} mm^3")

    # --- verify achieved radius by inspecting cylindrical faces near r=1.27 ---
    try:
        from OCP.BRepAdaptor import BRepAdaptor_Surface
        from OCP.GeomAbs import GeomAbs_Cylinder

        radii = []
        long_cyl = 0
        for f in edited.Faces():
            if f.geomType() != "CYLINDER":
                continue
            ad = BRepAdaptor_Surface(f.wrapped, True)
            if ad.GetType() != GeomAbs_Cylinder:
                continue
            rad = float(ad.Cylinder().Radius())
            bb = f.BoundingBox()
            # long fillet faces should be extended along the blade length in XZ
            if max(bb.xlen, bb.zlen) > 60.0:
                long_cyl += 1
                if abs(rad - r) <= 0.06:
                    radii.append(rad)

        print(f"VERIFY: found {long_cyl} long cylindrical faces (max(xlen,zlen)>60) on edited blade")
        if radii:
            avg = sum(radii) / len(radii)
            print(
                f"ACHIEVED RADIUS: avg={avg:.4f}  min={min(radii):.4f}  max={max(radii):.4f}  n={len(radii)}  (target {r:.3f})"
            )
        else:
            print(f"WARNING: could not numerically confirm R{r:.3f} cylinders; rely on render inspection")
    except Exception as ex:
        print(f"WARNING: radius verification skipped/failed: {ex}")

    # --- Recompound with only the target blade replaced; other bodies untouched ---
    out_solids = [edited if i == blade_idx else s for i, s in enumerate(solids)]
    out = cq.Compound.makeCompound(out_solids)
    return out