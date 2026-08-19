def my_cad_function(args):
    import cadquery as cq
    from math import isfinite

    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input
    base = shape.val() if hasattr(shape, "val") else shape

    solids = list(base.Solids())
    print(f"SELECTED: {len(solids)} solids in imported shape")
    if not solids:
        print("ERROR: No solids found; returning input")
        return shape

    # Choose the edited housing as the largest solid (matches prior kept attempt's edited body)
    vols = []
    for i, s in enumerate(solids):
        try:
            v = s.Volume()
        except Exception:
            v = float('nan')
        vols.append(v)
    housing_i = max(range(len(solids)), key=lambda i: vols[i] if isfinite(vols[i]) else -1.0)
    housing = solids[housing_i]
    hbb = housing.BoundingBox()
    print(f"SELECTED: 1 solid as housing (largest volume) idx={housing_i} vol={vols[housing_i]:.3f} bbox=([{hbb.xmin:.3f},{hbb.ymin:.3f},{hbb.zmin:.3f}]..[{hbb.xmax:.3f},{hbb.ymax:.3f},{hbb.zmax:.3f}])")

    # Targets
    target_y = 146.05
    target_z = -241.30
    target_d = 44.45
    target_r = target_d / 2.0
    print(f"TARGET: relocate outlet port to center YZ=({target_y:.2f}, {target_z:.2f}) diameter={target_d:.2f} (r={target_r:.4f}), axis ~X")

    # --- Helpers to read true circle center/radius even for partial arcs ---
    def circle_data_from_edge(e):
        """Return (centerVector, radius) if edge is circular (including arcs), else (None,None)."""
        try:
            from OCP.BRepAdaptor import BRepAdaptor_Curve
            from OCP.GeomAbs import GeomAbs_Circle
            ad = BRepAdaptor_Curve(e.wrapped)
            if ad.GetType() != GeomAbs_Circle:
                return None, None
            circ = ad.Circle()  # gp_Circ
            loc = circ.Location()  # gp_Pnt
            cen = cq.Vector(loc.X(), loc.Y(), loc.Z())
            rad = float(circ.Radius())
            return cen, rad
        except Exception:
            return None, None

    def collect_circle_edges(sol, r_target, r_tol, z_focus=None, z_tol=None):
        out = []
        for idx, e in enumerate(sol.Edges()):
            cen, rad = circle_data_from_edge(e)
            if cen is None:
                continue
            if abs(rad - r_target) > r_tol:
                continue
            if z_focus is not None and z_tol is not None:
                if abs(cen.z - z_focus) > z_tol:
                    continue
            out.append((idx, e, cen, rad))
        return out

    # We search for the misplaced through-cut by its circular mouth edges r=22.225 near z=-241.30
    r_tol = 0.25
    z_tol = 5.0
    ce_22225 = collect_circle_edges(housing, target_r, r_tol, z_focus=target_z, z_tol=z_tol)
    print(f"SELECTED: {len(ce_22225)} circular edges on housing with r~{target_r:.4f} within tol {r_tol} and |z-{target_z:.2f}|<{z_tol}")

    ce_254 = collect_circle_edges(housing, 25.4, 0.35, z_focus=target_z, z_tol=z_tol)
    print(f"SELECTED: {len(ce_254)} circular edges on housing with r~25.4 within tol 0.35 and |z-{target_z:.2f}|<{z_tol} (counterbore discriminator)")

    # Group r=22.225 edges by YZ (rounded) to find candidate ports
    def yz_key(v, step=0.05):
        return (round(v.y / step) * step, round(v.z / step) * step)

    groups = {}
    for idx, e, cen, rad in ce_22225:
        k = yz_key(cen)
        groups.setdefault(k, []).append((idx, e, cen, rad))

    print(f"SELECTED: {len(groups)} YZ-groups for r~{target_r:.4f} edges (candidate port mouths)")
    # Print groups near expected Y positions (±146) and z near target
    def nearest_254_presence(yz, tol_y=0.6, tol_z=0.6):
        y0, z0 = yz
        for _, _, c, _ in ce_254:
            if abs(c.y - y0) <= tol_y and abs(c.z - z0) <= tol_z:
                return True
        return False

    # Build a sorted list of candidate groups close to z target
    cand = []
    for k, items in groups.items():
        yk, zk = k
        # keep only those very close in Z to the intended z
        if abs(zk - target_z) > 1.0:
            continue
        xs = sorted({round(it[2].x, 3) for it in items})
        has254 = nearest_254_presence(k)
        cand.append((k, items, xs, has254))

    cand.sort(key=lambda t: (abs(t[0][1] - target_z), abs(abs(t[0][0]) - abs(target_y)), -len(t[1])))
    for (yk, zk), items, xs, has254 in cand[:10]:
        print(f"  CANDIDATE-GROUP: YZ=({yk:.3f},{zk:.3f}) edges={len(items)} x_levels={xs} has_r25.4_nearby={has254}")

    # Identify the *source* (misplaced) hole group:
    # Prefer the one near y=-146.05, z=-241.30 WITHOUT nearby r=25.4 (so we don't touch a stepped/legacy port)
    source = None
    best_score = None
    for (yk, zk), items, xs, has254 in cand:
        # Must be at z target
        if abs(zk - target_z) > 0.2:
            continue
        # Candidate near -target_y
        y_score = abs(yk - (-target_y))
        # Also accept if it's simply far from target_y (misplaced)
        far_from_target = abs(yk - target_y)
        # Score: prioritize no counterbore, near -target_y, and not already at target_y
        score = (0 if not has254 else 1, y_score, far_from_target, -len(items))
        if source is None or score < best_score:
            source = ((yk, zk), items, xs, has254)
            best_score = score

    if source is None:
        # Fallback: take the group farthest from target_y at z target
        for (yk, zk), items, xs, has254 in cand:
            if abs(zk - target_z) > 0.2:
                continue
            score = (-abs(yk - target_y), 0 if not has254 else 1, -len(items))
            if source is None or score < best_score:
                source = ((yk, zk), items, xs, has254)
                best_score = score

    if source is None:
        print("WARNING: Found zero candidate YZ-groups near target Z for r=22.225; widening search to all z")
        ce_22225_allz = collect_circle_edges(housing, target_r, r_tol, z_focus=None, z_tol=None)
        print(f"SELECTED: {len(ce_22225_allz)} circular edges on housing with r~{target_r:.4f} within tol {r_tol} (no z filter)")
        # still attempt: assume source at (-146.05,-241.30) and use known x-span from prompt index
        src_y, src_z = -target_y, target_z
        xmin, xmax = -111.125, -66.675
        print(f"FALLBACK-SOURCE: assuming source YZ=({src_y:.2f},{src_z:.2f}) and x-span [{xmin:.3f}..{xmax:.3f}]")
    else:
        (src_y, src_z), items, xs, has254 = source
        # Determine x-span from x-levels observed; if insufficient, fall back to known housing skin planes
        if len(xs) >= 2:
            xmin, xmax = float(min(xs)), float(max(xs))
        else:
            # fallback to typical skin planes seen in index
            xmin, xmax = -111.125, -66.675
        print(f"SELECTED: 1 source port group to relocate  YZ=({src_y:.3f},{src_z:.3f}) edges={len(items)} x_span=[{xmin:.3f}..{xmax:.3f}] has_r25.4_nearby={has254}")
        # Also print the matched edges indices (up to 24)
        sel_edge_idx = [it[0] for it in items]
        print(f"  SOURCE-EDGE-IDX (on housing.Edges()): {sel_edge_idx[:24]}{' (showing 24 of %d)'%len(sel_edge_idx) if len(sel_edge_idx)>24 else ''}")

    # Compute translation required in Y (expected +292.10 if src_y≈-146.05)
    dy = target_y - src_y
    print(f"RELOCATE: source_y={src_y:.3f} -> target_y={target_y:.3f}  dy={dy:+.3f} mm (expected +292.100 if symmetric)")

    # Build a plug to FILL the misplaced through-cut (kept inside the skin-to-skin span)
    xspan = abs(xmax - xmin)
    if xspan < 1.0:
        xspan = 44.45
        xmin = -111.125
        xmax = xmin + xspan
        print(f"WARNING: x-span too small; forcing x-span={xspan:.3f} using xmin={xmin:.3f} xmax={xmax:.3f}")

    dirx = cq.Vector(1, 0, 0) if xmax >= xmin else cq.Vector(-1, 0, 0)
    plug_base = cq.Vector(min(xmin, xmax), src_y, target_z)
    plug_len = xspan
    plug = cq.Solid.makeCylinder(target_r, plug_len, pnt=plug_base, dir=dirx)
    print(f"TOOL(plug): cylinder r={target_r:.4f} len={plug_len:.3f} base={[round(plug_base.x,3), round(plug_base.y,3), round(plug_base.z,3)]} dir={[dirx.x, dirx.y, dirx.z]}")

    # Build the correct through-cut tool at target location (slightly overlong in X)
    cut_base = cq.Vector(min(xmin, xmax) - 5.0, target_y, target_z)
    cut_len = xspan + 10.0
    cut_tool = cq.Solid.makeCylinder(target_r, cut_len, pnt=cut_base, dir=dirx)
    print(f"TOOL(cut):  cylinder r={target_r:.4f} len={cut_len:.3f} base={[round(cut_base.x,3), round(cut_base.y,3), round(cut_base.z,3)]} dir={[dirx.x, dirx.y, dirx.z]}")

    # Apply: fill misplaced, then cut at correct
    housing_filled = housing.fuse(plug)
    housing_out = housing_filled.cut(cut_tool)

    # Self-check: verify port at target location by re-detecting circular edges r=22.225 near target YZ
    def find_port_center(sol, y_expect, z_expect, y_tol=1.0, z_tol=1.0):
        edges = collect_circle_edges(sol, target_r, r_tol, z_focus=z_expect, z_tol=3.0)
        near = []
        for idx, e, cen, rad in edges:
            if abs(cen.y - y_expect) <= y_tol and abs(cen.z - z_expect) <= z_tol:
                near.append((idx, cen, rad))
        return near

    near_target = find_port_center(housing_out, target_y, target_z, y_tol=1.0, z_tol=1.0)
    print(f"SELECTED: {len(near_target)} circular edges near TARGET port (r~{target_r:.4f}, Y~{target_y:.2f}, Z~{target_z:.2f})")

    if near_target:
        ay = sum(c.y for _, c, _ in near_target) / len(near_target)
        az = sum(c.z for _, c, _ in near_target) / len(near_target)
        ar = sum(r for _, _, r in near_target) / len(near_target)
        ad = 2.0 * ar
        print(f"ACHIEVED: port center YZ=({ay:.3f}, {az:.3f}) diameter={ad:.4f}")
        print(f"DELTA:    dY={ay-target_y:+.3f} dZ={az-target_z:+.3f} dD={ad-target_d:+.4f}")
    else:
        print("WARNING: Could not verify target port via nearby circular edges (may be split arcs on non-planar mouths).")

    # Also check that the SOURCE (misplaced) simple-hole signature is reduced (best-effort)
    near_source = find_port_center(housing_out, src_y, target_z, y_tol=1.0, z_tol=1.0)
    print(f"SELECTED: {len(near_source)} circular edges still near SOURCE YZ=({src_y:.2f},{target_z:.2f}) after plug+recut")

    # Recompound: replace only the housing solid
    out_solids = []
    for i, s in enumerate(solids):
        out_solids.append(housing_out if i == housing_i else s)
    out = cq.Compound.makeCompound(out_solids)
    return out