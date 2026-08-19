def my_cad_function(args):
    import cadquery as cq
    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input

    base = shape.val() if hasattr(shape, "val") else shape

    # ---- Sub-goal named numbers (explicit print) ----
    target_y = 146.05
    target_z = 241.30
    target_d = 44.45
    target_r = target_d / 2.0
    print(f"TARGET: inlet port center YZ=({target_y:.2f}, {target_z:.2f}) diameter={target_d:.2f} radius={target_r:.4f} axis=+X")

    solids = base.Solids()
    print(f"SELECTED: {len(solids)} solids in imported shape")
    if len(solids) == 0:
        print("ERROR: No solids found; returning input")
        return shape

    # ---- Identify the solid that actually contains the requested YZ location (robust vs body-tag ambiguity) ----
    # Use a representative x near the housing region seen in the index (-88.9 is mid of the known skin span -111.125..-66.675)
    probe_x = -88.9
    candidates = []
    for i, s in enumerate(solids):
        bb = s.BoundingBox()
        contains = (bb.ymin - 1e-6 <= target_y <= bb.ymax + 1e-6) and (bb.zmin - 1e-6 <= target_z <= bb.zmax + 1e-6) and (bb.xmin - 1e-6 <= probe_x <= bb.xmax + 1e-6)
        print(
            f"SOLID[{i}] bbox: x[{bb.xmin:.3f},{bb.xmax:.3f}] y[{bb.ymin:.3f},{bb.ymax:.3f}] z[{bb.zmin:.3f},{bb.zmax:.3f}] vol={s.Volume():.3f}  contains_targetYZ&x? {contains}"
        )
        if contains:
            candidates.append((i, s, bb, s.Volume()))

    if not candidates:
        print("ERROR: No solid bbox contains target (y,z) and probe_x; cannot place the port safely. Returning input.")
        return shape

    # Prefer the largest-volume candidate (likely the housing)
    candidates.sort(key=lambda t: t[3], reverse=True)
    k, target_solid, target_bb, _ = candidates[0]
    print(f"SELECTED: 1 solid for port cut -> solids[{k}] (largest candidate by volume)")

    # ---- Resolve and report reference faces #5 and #12 from GLOBAL face list (per instructions) ----
    faces_all = base.Faces()
    print(f"INFO: base has {len(faces_all)} faces total")
    ref_face_ids = [5, 12]
    ref_faces = []
    for fid in ref_face_ids:
        if fid < 0 or fid >= len(faces_all):
            print(f"WARNING: face_idx #{fid} out of range; cannot use it")
            continue
        f = faces_all[fid]
        c = f.Center()
        n = f.normalAt()
        a = f.Area()
        print(
            f"SELECTED: 1 face for ref face_idx #{fid} center={[round(c.x,3), round(c.y,3), round(c.z,3)]} area={a:.3f} normal={[round(n.x,3), round(n.y,3), round(n.z,3)]}"
        )
        ref_faces.append((fid, f, c, n))

    # Determine X span from these faces if they look like X-facing skins; else fallback to target solid bbox
    x_start = target_bb.xmin - 50.0
    x_end = target_bb.xmax + 50.0
    used_ref_span = False
    if len(ref_faces) == 2:
        ok = True
        xs = []
        for fid, f, c, n in ref_faces:
            if abs(n.x) < 0.9 or abs(n.y) > 0.2 or abs(n.z) > 0.2:
                ok = False
            xs.append(c.x)
        if ok:
            x0 = min(xs) - 5.0
            x1 = max(xs) + 5.0
            # Only accept if it overlaps the target solid in X
            if x1 >= target_bb.xmin - 1.0 and x0 <= target_bb.xmax + 1.0:
                x_start, x_end = x0, x1
                used_ref_span = True

    print(
        f"INFO: cylinder X-span will be from x={x_start:.3f} to x={x_end:.3f} (len={(x_end-x_start):.3f})  source={'faces#5/#12' if used_ref_span else 'target solid bbox'}"
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

    # ---- Build tool and cut (through-port along +X) ----
    axis_dir = cq.Vector(1, 0, 0)
    tool_len = (x_end - x_start)
    tool_base = cq.Vector(x_start, target_y, target_z)
    tool = cq.Solid.makeCylinder(target_r, tool_len, pnt=tool_base, dir=axis_dir)
    print(
        f"TOOL: cylinder r={target_r:.4f} len={tool_len:.3f} base={[round(tool_base.x,3), round(tool_base.y,3), round(tool_base.z,3)]} dir={[axis_dir.x, axis_dir.y, axis_dir.z]}"
    )

    def evaluate_cut(solid_before, solid_after, label):
        removed = solid_before.cut(solid_after)
        vol = removed.Volume() if removed is not None else 0.0
        if vol < 1e-6:
            print(f"WARNING: {label}: Removed volume is ~0 (vol={vol:.6f}); tool may not have intersected")
            return None
        bb = removed.BoundingBox()
        c = bb.center
        # Cross-section diameter estimate from removed bbox (works for a clean cylindrical cut)
        approx_d = max(bb.ylen, bb.zlen)
        print(
            f"ACHIEVED ({label}): removed_vol={vol:.3f} removed_bbox_center YZ=({c.y:.3f}, {c.z:.3f}) approx_d={approx_d:.3f}"
        )
        print(
            f"DELTA ({label}): dY={c.y-target_y:+.3f} dZ={c.z-target_z:+.3f} dD={approx_d-target_d:+.3f}"
        )
        return (removed, c, approx_d)

    edited = target_solid.cut(tool)
    ev1 = evaluate_cut(target_solid, edited, "attempt1")
    if ev1 is None:
        print("ERROR: Cut failed/no intersection; returning input")
        return shape

    # Correct within same attempt if needed
    _, c1, d1 = ev1
    dy = target_y - c1.y
    dz = target_z - c1.z
    dd = target_d - d1
    need_corr = (abs(dy) > 0.01) or (abs(dz) > 0.01) or (abs(dd) > 0.02)

    if need_corr:
        corr_r = target_r + dd / 2.0
        print(f"CORRECTION: rebuild tool at exact target YZ and adjust radius by {dd/2.0:+.4f} -> r={corr_r:.4f}")
        tool2_base = cq.Vector(x_start, target_y, target_z)
        tool2 = cq.Solid.makeCylinder(corr_r, tool_len, pnt=tool2_base, dir=axis_dir)
        edited = target_solid.cut(tool2)
        ev2 = evaluate_cut(target_solid, edited, "attempt2")
        if ev2 is None:
            print("ERROR: Correction cut failed; returning input")
            return shape

    # Extra diagnostic: look for new circular mouth edges near the target YZ on the edited solid
    new_edges = []
    for e in edited.Edges():
        try:
            if e.geomType() != "CIRCLE":
                continue
            r = e.radius()
            if abs(r - target_r) > 0.05:
                continue
            cc = e.Center()
            if abs(cc.y - target_y) < 0.5 and abs(cc.z - target_z) < 0.5:
                new_edges.append((cc, r))
        except Exception:
            continue
    print(f"SELECTED: {len(new_edges)} circular edges on edited solid matching r≈{target_r:.3f} near target YZ for port-mouth verification")
    for i, (cc, r) in enumerate(new_edges[:10]):
        print(f"  mouth_edge[{i}] center={[round(cc.x,3), round(cc.y,3), round(cc.z,3)]} r={r:.4f} d={2*r:.4f}")

    # ---- Recompound: keep every other body unchanged ----
    out = cq.Compound.makeCompound([s for i, s in enumerate(solids) if i != k] + [edited])
    return out
"
}