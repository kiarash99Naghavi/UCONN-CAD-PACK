def my_cad_function(args):
    import cadquery as cq

    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input

    base = shape.val() if hasattr(shape, "val") else shape
    solids = base.Solids()
    print(f"SELECTED: {len(solids)} solids in imported shape")
    if not solids:
        print("ERROR: No solids found; returning input")
        return shape

    # ---- Sub-goal explicit numbers ----
    target_y = 146.05
    target_z = 241.30
    target_d = 44.45
    target_r = target_d / 2.0
    print(f"TARGET: inlet port center YZ=({target_y:.2f}, {target_z:.2f}) diameter={target_d:.2f} radius={target_r:.4f}")

    # ---- Choose the owning solid (bbox containment is reliable across multi-body STEP) ----
    candidates = []
    for i, s in enumerate(solids):
        bb = s.BoundingBox()
        contains_yz = (bb.ymin - 1e-6 <= target_y <= bb.ymax + 1e-6) and (bb.zmin - 1e-6 <= target_z <= bb.zmax + 1e-6)
        v = s.Volume()
        print(
            f"SOLID[{i}]: vol={v:.3f} bbox=([{" ".join([f'{bb.xmin:.3f}', f'{bb.ymin:.3f}', f'{bb.zmin:.3f}'])}]..[{" ".join([f'{bb.xmax:.3f}', f'{bb.ymax:.3f}', f'{bb.zmax:.3f}'])}]) contains_targetYZ={contains_yz}"
        )
        if contains_yz:
            candidates.append((i, s, bb, v))

    if not candidates:
        print("ERROR: No solid bbox contains the target (y,z). Returning input unchanged.")
        return shape

    candidates.sort(key=lambda t: t[3], reverse=True)
    k, target_solid, target_bb, _ = candidates[0]
    print(f"SELECTED: 1 solid for port cut -> solids[{k}] (largest bbox-containing candidate)")
    print(
        f"SELECTED_SOLID_BBOX: xmin={target_bb.xmin:.3f} xmax={target_bb.xmax:.3f} | ymin={target_bb.ymin:.3f} ymax={target_bb.ymax:.3f} | zmin={target_bb.zmin:.3f} zmax={target_bb.zmax:.3f}"
    )

    # ---- Resolve and report reference faces #5 and #12 from GLOBAL face list (as instructed) ----
    faces_all = base.Faces()
    print(f"INFO: base has {len(faces_all)} faces total")
    ref_face_ids = [5, 12]
    ref_faces = []
    for fid in ref_face_ids:
        if fid < 0 or fid >= len(faces_all):
            print(f"SELECTED: 0 faces for ref face_idx #{fid} (out of range)")
            continue
        f = faces_all[fid]
        c = f.Center()
        n = f.normalAt()
        a = f.Area()
        print(
            f"SELECTED: 1 face for ref face_idx #{fid} center={[round(c.x,3), round(c.y,3), round(c.z,3)]} area={a:.3f} normal={[round(n.x,3), round(n.y,3), round(n.z,3)]}"
        )
        ref_faces.append((fid, f, c, n))

    # Determine X-span from those faces if they look like opposing X-facing skins and are near the target solid
    x_start = target_bb.xmin - 50.0
    x_end = target_bb.xmax + 50.0
    used_ref_span = False
    if len(ref_faces) == 2:
        ok = True
        xs = []
        for fid, f, c, n in ref_faces:
            # must be roughly X-facing
            if abs(n.x) < 0.9 or abs(n.y) > 0.2 or abs(n.z) > 0.2:
                ok = False
            # must lie near selected solid in X, and near target YZ
            if not (target_bb.xmin - 5.0 <= c.x <= target_bb.xmax + 5.0):
                ok = False
            xs.append(c.x)
        if ok:
            x0 = min(xs) - 5.0
            x1 = max(xs) + 5.0
            # Only accept if it meaningfully spans the solid
            if (x1 - x0) > 1.0 and x1 >= target_bb.xmin - 1.0 and x0 <= target_bb.xmax + 1.0:
                x_start, x_end = x0, x1
                used_ref_span = True

    print(
        f"INFO: cylinder X-span x={x_start:.3f}..{x_end:.3f} (len={(x_end-x_start):.3f}) source={'faces#5/#12' if used_ref_span else 'target solid bbox'}"
    )

    # ---- Resolve the existing matching port mouth edges edge_idx [68,95] from GLOBAL edge list and print ----
    edges_all = base.Edges()
    print(f"INFO: base has {len(edges_all)} edges total")
    ref_edge_ids = [68, 95]
    print(f"SELECTED: {len(ref_edge_ids)} edges for existing port mouth reference idx={ref_edge_ids}")
    for eid in ref_edge_ids:
        if eid < 0 or eid >= len(edges_all):
            print(f"  WARNING: edge_idx {eid} out of range")
            continue
        e = edges_all[eid]
        c = e.Center()
        try:
            gt = e.geomType()
        except Exception:
            gt = "<geomType unavailable>"
        try:
            r = e.radius()
        except Exception:
            r = None
        msg = f"  edge#{eid}: geomType={gt} center={[round(c.x,3), round(c.y,3), round(c.z,3)]}"
        if r is not None:
            msg += f" radius={r:.4f} dia={2*r:.4f}"
        print(msg)

    # ---- Build tool and cut (through-port along measured X direction) ----
    axis_dir = cq.Vector(1, 0, 0)
    tool_len = (x_end - x_start)
    tool_base = cq.Vector(x_start, target_y, target_z)
    tool = cq.Solid.makeCylinder(target_r, tool_len, pnt=tool_base, dir=axis_dir)
    print(
        f"TOOL: cylinder r={target_r:.4f} len={tool_len:.3f} base={[round(tool_base.x,3), round(tool_base.y,3), round(tool_base.z,3)]} dir={[axis_dir.x, axis_dir.y, axis_dir.z]}"
    )

    def find_mouth_edges(solid, y, z, r, yz_tol=0.5, r_tol=0.05):
        mouths = []
        for e in solid.Edges():
            try:
                if e.geomType() != "CIRCLE":
                    continue
                er = e.radius()
                if abs(er - r) > r_tol:
                    continue
                cc = e.Center()  # for circular edges this is the true center
                if abs(cc.y - y) <= yz_tol and abs(cc.z - z) <= yz_tol:
                    mouths.append((cc, er))
            except Exception:
                continue
        return mouths

    def evaluate_cut(solid_before, solid_after, label):
        removed = solid_before.cut(solid_after)
        vol = removed.Volume() if removed is not None else 0.0
        if vol < 1e-6:
            print(f"WARNING: {label}: Removed volume is ~0 (vol={vol:.6f}); tool may not have intersected")
            return None

        # Prefer exact mouth edge centers/radii
        mouths = find_mouth_edges(solid_after, target_y, target_z, target_r)
        print(f"SELECTED: {len(mouths)} circular mouth edges on edited solid near target YZ with r≈{target_r:.4f} ({label})")
        for i, (cc, er) in enumerate(mouths[:10]):
            print(f"  mouth_edge[{i}] center={[round(cc.x,3), round(cc.y,3), round(cc.z,3)]} r={er:.4f} d={2*er:.4f}")

        if len(mouths) >= 1:
            ay = sum(cc.y for cc, _ in mouths) / len(mouths)
            az = sum(cc.z for cc, _ in mouths) / len(mouths)
            ad = 2.0 * (sum(er for _, er in mouths) / len(mouths))
            print(f"ACHIEVED ({label}): port_center_YZ=({ay:.3f}, {az:.3f}) diameter={ad:.3f} (from mouth edges)")
            print(f"DELTA ({label}): dY={ay-target_y:+.3f} dZ={az-target_z:+.3f} dD={ad-target_d:+.3f}")
            return (removed, (ay, az, ad))

        # Fallback: bbox estimate of removed region
        bb = removed.BoundingBox()
        c = bb.center
        approx_d = max(bb.ylen, bb.zlen)
        print(f"ACHIEVED ({label}): removed_vol={vol:.3f} removed_bbox_center YZ=({c.y:.3f}, {c.z:.3f}) approx_d={approx_d:.3f} (fallback)")
        print(f"DELTA ({label}): dY={c.y-target_y:+.3f} dZ={c.z-target_z:+.3f} dD={approx_d-target_d:+.3f}")
        return (removed, (c.y, c.z, approx_d))

    edited = target_solid.cut(tool)
    ev1 = evaluate_cut(target_solid, edited, "attempt1")
    if ev1 is None:
        print("ERROR: Cut failed/no intersection; returning input")
        return shape

    _, (ay1, az1, ad1) = ev1
    dy = target_y - ay1
    dz = target_z - az1
    dd = target_d - ad1
    need_corr = (abs(dy) > 0.01) or (abs(dz) > 0.01) or (abs(dd) > 0.02)

    if need_corr:
        corr_r = target_r + dd / 2.0
        corr_y = target_y
        corr_z = target_z
        print(
            f"CORRECTION: rebuild tool at YZ=({corr_y:.2f},{corr_z:.2f}) adjust radius by {dd/2.0:+.4f} -> r={corr_r:.4f}"
        )
        tool2_base = cq.Vector(x_start, corr_y, corr_z)
        tool2 = cq.Solid.makeCylinder(corr_r, tool_len, pnt=tool2_base, dir=axis_dir)
        edited2 = target_solid.cut(tool2)
        ev2 = evaluate_cut(target_solid, edited2, "attempt2")
        if ev2 is None:
            print("ERROR: Correction cut failed; returning input")
            return shape
        edited = edited2

    # ---- Recompound: keep every other body unchanged ----
    out = cq.Compound.makeCompound([s for i, s in enumerate(solids) if i != k] + [edited])
    return out