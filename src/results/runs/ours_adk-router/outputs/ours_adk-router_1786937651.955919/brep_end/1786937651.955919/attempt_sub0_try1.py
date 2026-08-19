def my_cad_function(args):
    import cadquery as cq
    import math

    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input
    base = shape.val() if hasattr(shape, "val") else shape

    # --- Named numbers from the sub-goal (print them) ---
    target_edge_idx = 2124
    main_r = 3.45
    axis_pt = (0.0, -40.0)  # cylinder axis passes through (0,-40,*)
    z_lower = 7.5
    chamfer_axial = 0.20
    chamfer_radial = 0.25
    expected_bbox_min = (-3.45, -43.45, 0.4)
    expected_bbox_max = (3.45, -36.55, 21.0)
    print("SUBGOAL numbers:")
    print(f"  target_edge_idx={target_edge_idx}")
    print(f"  main cylinder r={main_r} at axis through x,y={axis_pt}, z~{z_lower}")
    print(f"  chamfer target ~{chamfer_axial} mm axially by ~{chamfer_radial} mm radially")
    print(f"  expected s3 bbox {expected_bbox_min} .. {expected_bbox_max}")

    sols = base.Solids()
    print(f"INFO: imported solids={len(sols)}")
    for i, s in enumerate(sols):
        bb = s.BoundingBox()
        print(f"  solid s{i} bbox=({bb.xmin:.3f},{bb.ymin:.3f},{bb.zmin:.3f})..({bb.xmax:.3f},{bb.ymax:.3f},{bb.zmax:.3f})")

    # Find s3 by bbox match (per prompt)
    def bbox_close(bb, mn, mx, tol=0.05):
        return (
            abs(bb.xmin - mn[0]) <= tol and abs(bb.ymin - mn[1]) <= tol and abs(bb.zmin - mn[2]) <= tol and
            abs(bb.xmax - mx[0]) <= tol and abs(bb.ymax - mx[1]) <= tol and abs(bb.zmax - mx[2]) <= tol
        )

    s3_idx = None
    for i, s in enumerate(sols):
        if bbox_close(s.BoundingBox(), expected_bbox_min, expected_bbox_max, tol=0.05):
            s3_idx = i
            break
    if s3_idx is None:
        # fallback: closest by bbox center to (0,-40,~10)
        target_c = cq.Vector(0, -40, 10)
        best = None
        for i, s in enumerate(sols):
            c = s.BoundingBox().center
            d = (cq.Vector(c.x, c.y, c.z) - target_c).Length
            best = (d, i) if best is None or d < best[0] else best
        s3_idx = best[1]
        print(f"WARNING: exact s3 bbox not found; falling back to closest solid by bbox center => s{s3_idx}")
    else:
        print(f"INFO: identified s3 as solid index {s3_idx}")

    s3 = sols[s3_idx]
    bb_before = s3.BoundingBox()
    print(f"INFO: s3 bbox BEFORE=({bb_before.xmin:.3f},{bb_before.ymin:.3f},{bb_before.zmin:.3f})..({bb_before.xmax:.3f},{bb_before.ymax:.3f},{bb_before.zmax:.3f})")

    # Resolve the global edge by index and verify it matches the index description
    all_edges = base.Edges()
    print(f"INFO: base edges count={len(all_edges)}")
    if target_edge_idx < 0 or target_edge_idx >= len(all_edges):
        print(f"SELECTED: 0 edges for lower chamfer (edge_idx {target_edge_idx} out of range)")
        return shape

    e_global = all_edges[target_edge_idx]
    try:
        ec = e_global.Center()
        elen = e_global.Length()
    except Exception as ex:
        ec = None
        elen = None
        print(f"WARNING: failed to measure global edge {target_edge_idx}: {ex}")
    print(f"INFO: resolved global edge[{target_edge_idx}] center={list(ec.toTuple()) if ec else None} length={elen}")

    # Map the global edge to the corresponding edge inside s3
    def is_same_edge(e1, e2):
        try:
            return e1.wrapped.IsSame(e2.wrapped)
        except Exception:
            try:
                return e1.isSame(e2)
            except Exception:
                return False

    s3_edges = s3.Edges()
    e_on_s3 = None
    for e in s3_edges:
        if is_same_edge(e, e_global):
            e_on_s3 = e
            break

    # Fallback: find by geometry (circle at (0,-40,z~7.5) r~3.45)
    if e_on_s3 is None:
        cand = []
        for e in s3_edges:
            try:
                r = e.radius()
                c = e.arcCenter()
                # full circle check via length ~ 2*pi*r
                L = e.Length()
                full = abs(L - 2 * math.pi * r) < 0.5
                if (
                    abs(r - main_r) < 0.05 and
                    abs(c.x - axis_pt[0]) < 0.1 and abs(c.y - axis_pt[1]) < 0.1 and
                    abs(c.z - z_lower) < 0.2 and
                    full
                ):
                    cand.append((abs(c.z - z_lower), e, r, c, L))
            except Exception:
                pass
        cand.sort(key=lambda t: t[0])
        if cand:
            _, e_on_s3, r, c, L = cand[0]
            print(f"WARNING: global edge mapping failed; fallback selected edge on s3 by geom r={r:.4f}, center=({c.x:.4f},{c.y:.4f},{c.z:.4f}), length={L:.4f}")

    if e_on_s3 is None:
        print("SELECTED: 0 edges for the lower rim chamfer on s3 (FAILED to locate edge)")
        return shape

    # Confirm it's a 360° circle-ish edge
    try:
        r_sel = e_on_s3.radius()
        c_sel = e_on_s3.arcCenter()
        L_sel = e_on_s3.Length()
        print(f"INFO: selected s3 edge radius={r_sel:.4f}, arcCenter=({c_sel.x:.4f},{c_sel.y:.4f},{c_sel.z:.4f}), length={L_sel:.4f}, expected circumference~{2*math.pi*r_sel:.4f}")
    except Exception as ex:
        print(f"WARNING: could not fully characterize selected edge on s3: {ex}")

    print(f"SELECTED: 1 edge for lower insertion chamfer on s3   idx=[{target_edge_idx}]")

    # Apply chamfer on s3 only; keep all other solids untouched
    # We want ~0.25 mm radial on the shoulder plane, ~0.20 mm axial along the cylinder.
    # CadQuery chamfer(d1, d2, edges) assigns d1/d2 to the two adjacent faces (order OCC-dependent).
    # We pick (radial, axial) per the sub-goal.
    try:
        s3_edited = s3.chamfer(chamfer_radial, chamfer_axial, [e_on_s3])
        print(f"INFO: chamfer applied on s3 with d1={chamfer_radial}, d2={chamfer_axial}")
    except Exception as ex:
        print(f"ERROR: chamfer failed with d1={chamfer_radial}, d2={chamfer_axial}: {ex}")
        return shape

    # Self-check: compute removed material and its location
    try:
        removed = s3.cut(s3_edited)
        bb_rem = removed.BoundingBox()
        c_rem = removed.Center()
        print("SELF-CHECK: removed material (s3 - s3_edited):")
        print(f"  removed center={list(c_rem.toTuple())}")
        print(f"  removed bbox=({bb_rem.xmin:.4f},{bb_rem.ymin:.4f},{bb_rem.zmin:.4f})..({bb_rem.xmax:.4f},{bb_rem.ymax:.4f},{bb_rem.zmax:.4f})")
        print(f"  removed z-range vs target z~{z_lower}: zmin={bb_rem.zmin:.4f} (dz={bb_rem.zmin - z_lower:+.4f}), zmax={bb_rem.zmax:.4f} (dz={bb_rem.zmax - z_lower:+.4f})")
    except Exception as ex:
        print(f"WARNING: could not compute removed material solid: {ex}")

    # Verify s3 bbox remains unchanged (per requirement)
    bb_after = s3_edited.BoundingBox()
    print(f"INFO: s3 bbox AFTER =({bb_after.xmin:.3f},{bb_after.ymin:.3f},{bb_after.zmin:.3f})..({bb_after.xmax:.3f},{bb_after.ymax:.3f},{bb_after.zmax:.3f})")
    print("SELF-CHECK: s3 bbox deltas (AFTER - EXPECTED):")
    print(f"  dxmin={bb_after.xmin - expected_bbox_min[0]:+.4f}, dymin={bb_after.ymin - expected_bbox_min[1]:+.4f}, dzmin={bb_after.zmin - expected_bbox_min[2]:+.4f}")
    print(f"  dxmax={bb_after.xmax - expected_bbox_max[0]:+.4f}, dymax={bb_after.ymax - expected_bbox_max[1]:+.4f}, dzmax={bb_after.zmax - expected_bbox_max[2]:+.4f}")

    # Diagnostic: find new circular edges near the chamfer location to confirm a 360° lead-in exists
    near = []
    for e in s3_edited.Edges():
        try:
            r = e.radius()
            c = e.arcCenter()
            if abs(c.x - axis_pt[0]) < 0.15 and abs(c.y - axis_pt[1]) < 0.15 and abs(c.z - z_lower) < 0.35:
                L = e.Length()
                full = abs(L - 2 * math.pi * r) < 0.5
                if full:
                    near.append((c.z, r, L))
        except Exception:
            pass
    near.sort()
    print(f"SELECTED: {len(near)} full-circle edges near (0,-40,z~{z_lower}) on edited s3 (diagnostic)")
    for (cz, r, L) in near[:10]:
        print(f"  circle @ z={cz:.4f} r={r:.4f} L={L:.4f}")

    # Recompound with all other solids untouched
    new_sols = list(sols)
    new_sols[s3_idx] = s3_edited
    out = cq.Compound.makeCompound(new_sols)
    return out