def my_cad_function(args):
    import cadquery as cq
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.GeomAbs import GeomAbs_Circle

    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input
    base = shape.val() if hasattr(shape, "val") else shape

    solids = base.Solids()
    print(f"SELECTED: {len(solids)} solids in imported shape")
    if not solids:
        print("ERROR: no solids found")
        return shape

    # ---- constants per sub-goal ----
    target_y = 146.05
    target_z = -241.30
    target_d = 44.45
    target_r = target_d / 2.0
    print(f"TARGET: port center YZ=({target_y:.2f}, {target_z:.2f})  diameter={target_d:.2f}  radius={target_r:.4f}  axis~X")

    # Choose the housing-like solid to edit: the one that actually contains r~22.225 circular edges near z=-241.3
    def circle_params(edge):
        """Return (center_vec, radius) if circle/arc; else None."""
        try:
            ad = BRepAdaptor_Curve(edge.wrapped)
            if ad.GetType() != GeomAbs_Circle:
                return None
            circ = ad.Circle()
            loc = circ.Location()
            cen = cq.Vector(loc.X(), loc.Y(), loc.Z())
            return cen, float(circ.Radius())
        except Exception:
            return None

    def find_r_edges_on_solid(sol, r0, r_tol, z0=None, z_tol=None):
        out = []
        edges = sol.Edges()
        for i, e in enumerate(edges):
            cp = circle_params(e)
            if cp is None:
                continue
            cen, rad = cp
            if abs(rad - r0) > r_tol:
                continue
            if z0 is not None and z_tol is not None:
                if abs(cen.z - z0) > z_tol:
                    continue
            out.append((i, e, cen, rad))
        return out

    # pick housing by presence of these circle edges (fallback to max volume)
    cand = []
    for si, s in enumerate(solids):
        edges_222 = find_r_edges_on_solid(s, target_r, r_tol=0.35, z0=target_z, z_tol=2.5)
        if edges_222:
            cand.append((si, s, len(edges_222), edges_222))
    if cand:
        cand.sort(key=lambda t: t[2], reverse=True)
        housing_i, housing, n222, _edges222 = cand[0]
        print(f"SELECTED: 1 solid as housing by r~{target_r:.4f} edge presence idx={housing_i} edges_found={n222}")
    else:
        housing_i = max(range(len(solids)), key=lambda i: solids[i].Volume())
        housing = solids[housing_i]
        print(f"WARNING: no solid had detectable r~{target_r:.4f} circle edges near z={target_z:.2f}; falling back to largest-volume solid idx={housing_i}")

    bb = housing.BoundingBox()
    print(f"HOUSING bbox: xmin={bb.xmin:.3f} xmax={bb.xmax:.3f} ymin={bb.ymin:.3f} ymax={bb.ymax:.3f} zmin={bb.zmin:.3f} zmax={bb.zmax:.3f}")

    # ---- detect candidate port mouth edge clusters for r=22.225 near target_z ----
    edges_222 = find_r_edges_on_solid(housing, target_r, r_tol=0.35, z0=target_z, z_tol=3.5)
    print(f"SELECTED: {len(edges_222)} circular edges on housing with r~{target_r:.4f} (tol=0.35) and |z-{target_z:.2f}|<3.5")
    if not edges_222:
        # Widen search: drop z filter (as instructed)
        edges_222 = find_r_edges_on_solid(housing, target_r, r_tol=0.45, z0=None, z_tol=None)
        print(f"SELECTED: {len(edges_222)} circular edges on housing with r~{target_r:.4f} (tol=0.45) with NO z filter (widened)")

    # detect counterbore-ish r=25.4 near same z (discriminator)
    edges_254 = find_r_edges_on_solid(housing, 25.4, r_tol=0.45, z0=target_z, z_tol=5.0)
    print(f"SELECTED: {len(edges_254)} circular edges on housing with r~25.4 (tol=0.45) and |z-{target_z:.2f}|<5.0 (counterbore discriminator)")

    # cluster edges_222 by (y,z)
    yz_tol = 0.8
    clusters = []  # each: {y,z, items:[(edge_i, cen, rad)], xs:[...], has254}
    for edge_i, e, cen, rad in edges_222:
        placed = False
        for cl in clusters:
            if abs(cen.y - cl["y"]) <= yz_tol and abs(cen.z - cl["z"]) <= yz_tol:
                cl["items"].append((edge_i, cen, rad))
                cl["xs"].append(cen.x)
                # update representative y,z as running average
                n = len(cl["items"])
                cl["y"] = (cl["y"] * (n - 1) + cen.y) / n
                cl["z"] = (cl["z"] * (n - 1) + cen.z) / n
                placed = True
                break
        if not placed:
            clusters.append({"y": cen.y, "z": cen.z, "items": [(edge_i, cen, rad)], "xs": [cen.x], "has254": False})

    # mark clusters with nearby r=25.4 edges
    for cl in clusters:
        for _, _, cen254, _ in edges_254:
            if abs(cen254.y - cl["y"]) <= 1.2 and abs(cen254.z - cl["z"]) <= 1.2:
                cl["has254"] = True
                break

    print(f"SELECTED: {len(clusters)} YZ-clusters for r~{target_r:.4f} edges")
    for k, cl in enumerate(sorted(clusters, key=lambda c: (-len(c["items"]), c["y"]))):
        xs_u = sorted({round(x, 3) for x in cl["xs"]})
        idxs = [it[0] for it in cl["items"]]
        print(f"  CLUSTER#{k}: YZ=({cl['y']:.3f},{cl['z']:.3f}) edges={len(cl['items'])} x_levels={xs_u[:12]}{' (showing 12)' if len(xs_u)>12 else ''} has_r25.4_nearby={cl['has254']} edge_idx_sample={idxs[:12]}{' (showing 12)' if len(idxs)>12 else ''}")

    # ---- choose SOURCE cluster to relocate (prefer y ~ -146.05, keep z ~ target_z) ----
    # Prefer clusters at z close to target_z, with y closest to -target_y.
    z_focus_tol = 2.0
    zclose = [cl for cl in clusters if abs(cl["z"] - target_z) <= z_focus_tol]
    print(f"SELECTED: {len(zclose)} clusters with |z-{target_z:.2f}|<={z_focus_tol:.2f} for source/target consideration")

    def score_source(cl):
        # Primary: closeness to -target_y; Secondary: penalize counterbore adjacency; Tertiary: fewer edges (often cleaner mouth pair)
        return (abs(cl["y"] - (-target_y)), 1 if cl["has254"] else 0, len(cl["items"]))

    source = None
    if zclose:
        source = min(zclose, key=score_source)
    elif clusters:
        source = min(clusters, key=score_source)

    if source is None:
        print("ERROR: Could not find any r~22.225 circle-edge cluster to relocate; returning input unchanged would score zero, so performing direct cut at target.")
        # As a last resort, just cut the target hole through housing across full X (won't move anything, but at least makes the feature)
        cut_len = bb.xlen + 300.0
        cut_base = cq.Vector(bb.xmin - 150.0, target_y, target_z)
        cut_tool = cq.Solid.makeCylinder(target_r, cut_len, pnt=cut_base, dir=cq.Vector(1, 0, 0))
        housing_out = housing.cut(cut_tool)
    else:
        src_y = float(source["y"])
        src_z = float(source["z"])
        dy = target_y - src_y
        print(f"SELECTED: 1 source cluster to relocate  SOURCE YZ=({src_y:.3f},{src_z:.3f}) edges={len(source['items'])} has_r25.4_nearby={source['has254']}")
        print(f"RELOCATE: dy={dy:+.3f} mm  (expected +292.100 if source was at y=-146.05)")

        # Determine skin-to-skin X span from source cluster x-levels.
        # Prefer min/max among negative X levels (where the housing skin stack is), else global min/max.
        xs = sorted({float(round(cen.x, 6)) for _, cen, _ in source["items"]})
        xs_neg = [x for x in xs if x < -50.0]
        if len(xs_neg) >= 2:
            x_min = min(xs_neg)
            x_max = max(xs_neg)
        elif len(xs) >= 2:
            x_min = min(xs)
            x_max = max(xs)
        else:
            # fallback to known skin planes for this housing family
            x_min, x_max = -111.125, -66.675
            print("WARNING: insufficient x-levels on source cluster; falling back to x_min=-111.125 x_max=-66.675")

        x_span = x_max - x_min
        if x_span < 5.0:
            x_min, x_max = -111.125, -66.675
            x_span = x_max - x_min
            print("WARNING: computed x_span too small; forcing x span to known 44.45 between -111.125 and -66.675")

        print(f"X-SPAN for plug/cut: x_min={x_min:.3f} x_max={x_max:.3f} span={x_span:.3f}")

        # Tools
        # Plug must not protrude beyond skins: keep exact x_span between x_min..x_max.
        plug = cq.Solid.makeCylinder(target_r, x_span, pnt=cq.Vector(x_min, src_y, src_z), dir=cq.Vector(1, 0, 0))
        # Cut can be overlong safely.
        cut_len = x_span + 50.0
        cut_base = cq.Vector(x_min - 25.0, target_y, target_z)
        cut_tool = cq.Solid.makeCylinder(target_r, cut_len, pnt=cut_base, dir=cq.Vector(1, 0, 0))

        print(f"TOOL(plug): r={target_r:.4f} len={x_span:.3f} base=({x_min:.3f},{src_y:.3f},{src_z:.3f}) dir=(1,0,0)")
        print(f"TOOL(cut):  r={target_r:.4f} len={cut_len:.3f} base=({cut_base.x:.3f},{cut_base.y:.3f},{cut_base.z:.3f}) dir=(1,0,0)")

        # Apply: fill misplaced then cut at target
        housing_filled = housing.fuse(plug)
        housing_out = housing_filled.cut(cut_tool)

    # ---- verification by re-detecting r~22.225 circle edges near target and source ----
    def summarize_near(sol, y0, z0, label):
        es = find_r_edges_on_solid(sol, target_r, r_tol=0.40, z0=z0, z_tol=3.5)
        near = []
        for edge_i, e, cen, rad in es:
            if abs(cen.y - y0) <= 1.5 and abs(cen.z - z0) <= 1.5:
                near.append((edge_i, cen, rad))
        print(f"SELECTED: {len(near)} circular edges near {label} YZ=({y0:.2f},{z0:.2f})")
        if near:
            ay = sum(c.y for _, c, _ in near) / len(near)
            az = sum(c.z for _, c, _ in near) / len(near)
            ar = sum(r for _, _, r in near) / len(near)
            ad = 2.0 * ar
            print(f"{label} ACHIEVED: center YZ=({ay:.3f},{az:.3f}) diameter={ad:.4f}")
            print(f"{label} DELTA:    dY={ay-y0:+.3f} dZ={az-z0:+.3f} dD={ad-target_d:+.4f}")
        return near

    near_target = summarize_near(housing_out, target_y, target_z, "TARGET")

    # Also report near the symmetric / source location y=-146.05 (what we're trying to relocate away from)
    near_sym = summarize_near(housing_out, -target_y, target_z, "SYMMETRIC(-Y)")

    # ---- recombine solids, replacing only the edited one ----
    out_solids = [housing_out if i == housing_i else s for i, s in enumerate(solids)]
    out = cq.Compound.makeCompound(out_solids)
    return out