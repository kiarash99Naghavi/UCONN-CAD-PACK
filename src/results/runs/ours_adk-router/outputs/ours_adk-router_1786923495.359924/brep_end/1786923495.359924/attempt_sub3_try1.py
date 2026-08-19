def my_cad_function(args):
    import cadquery as cq

    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input
    base = shape.val() if hasattr(shape, "val") else shape

    solids = base.Solids()
    print(f"SELECTED: {len(solids)} solids from imported STEP")
    for i, s in enumerate(solids):
        bb = s.BoundingBox()
        try:
            v = s.Volume()
        except Exception:
            v = float('nan')
        print(
            f"  solid s{i}: vol={v:.3f}  bbox=({bb.xmin:.3f},{bb.ymin:.3f},{bb.zmin:.3f})..({bb.xmax:.3f},{bb.ymax:.3f},{bb.zmax:.3f})  lens=({bb.xlen:.3f},{bb.ylen:.3f},{bb.zlen:.3f})"
        )

    if len(solids) < 1:
        print("ERROR: No solids found; returning input")
        return shape

    # --- Choose the newly created blade (copy) heuristically: most 'diagonal' blade has largest X span ---
    blade_cands = []
    for i, s in enumerate(solids):
        bb = s.BoundingBox()
        # hub is small (~50mm); blades are hundreds of mm
        if max(bb.xlen, bb.zlen) > 150 and abs(bb.ylen - 12.7) < 2.0:
            blade_cands.append(i)
    print(f"SELECTED: {len(blade_cands)} candidate blade solids (max(xlen,zlen)>150 and ylen~12.7) idx={blade_cands}")

    if not blade_cands:
        print("ERROR: Could not identify any blade solids; returning input")
        return shape

    copy_idx = max(blade_cands, key=lambda k: solids[k].BoundingBox().xlen)
    target = solids[copy_idx]
    bb_t = target.BoundingBox()
    print(
        f"USING: solid s{copy_idx} as target newly-copied blade for long-edge fillets  "
        f"bbox=({bb_t.xmin:.3f},{bb_t.ymin:.3f},{bb_t.zmin:.3f})..({bb_t.xmax:.3f},{bb_t.ymax:.3f},{bb_t.zmax:.3f})  "
        f"lens=({bb_t.xlen:.3f},{bb_t.ylen:.3f},{bb_t.zlen:.3f})"
    )

    # Helpers
    def hc(sh):
        try:
            return sh.hashCode()
        except Exception:
            try:
                return sh.wrapped.HashCode(2147483647)
            except Exception:
                return id(sh)

    def vsub(a, b):
        return cq.Vector(a.x - b.x, a.y - b.y, a.z - b.z)

    def vadd(a, b):
        return cq.Vector(a.x + b.x, a.y + b.y, a.z + b.z)

    def vscale(a, s):
        return cq.Vector(a.x * s, a.y * s, a.z * s)

    def vdot(a, b):
        return a.x * b.x + a.y * b.y + a.z * b.z

    def vlen(a):
        return a.Length

    def vnorm(a):
        L = a.Length
        if L < 1e-9:
            return cq.Vector(0, 0, 1)
        return cq.Vector(a.x / L, a.y / L, a.z / L)

    def dist_point_to_axis(pt, origin, axis_dir):
        v = vsub(pt, origin)
        t = vdot(v, axis_dir)
        proj = vscale(axis_dir, t)
        perp = vsub(v, proj)
        return perp.Length, t

    # --- Determine blade axis from two end faces (small planar faces) ---
    t_faces = target.Faces()
    end_face_cands = []
    for fi, f in enumerate(t_faces):
        if f.geomType() != "PLANE":
            continue
        a = f.Area()
        # end faces in the provided index are ~256 mm^2; accept a band
        if 150.0 <= a <= 400.0:
            c = f.Center()
            end_face_cands.append((fi, a, c, f))

    print(f"SELECTED: {len(end_face_cands)} planar end-face candidates on s{copy_idx} (area 150..400)")
    for k, (fi, a, c, _) in enumerate(end_face_cands[:10]):
        print(f"  end_face_cand[{k}]: local_face_idx={fi} area={a:.3f} center=[{c.x:.3f},{c.y:.3f},{c.z:.3f}]")

    axis_origin = target.Center()
    axis_dir = cq.Vector(0, 0, 1)

    if len(end_face_cands) >= 2:
        # pick the farthest pair by center distance
        best = None
        for i in range(len(end_face_cands)):
            for j in range(i + 1, len(end_face_cands)):
                ci = end_face_cands[i][2]
                cj = end_face_cands[j][2]
                d = vsub(cj, ci).Length
                if best is None or d > best[0]:
                    best = (d, ci, cj)
        L, c1, c2 = best
        axis_origin = vscale(vadd(c1, c2), 0.5)
        axis_dir = vnorm(vsub(c2, c1))
        print(
            "AXIS: derived from farthest end-face centers  "
            f"L~{L:.3f}  origin=[{axis_origin.x:.3f},{axis_origin.y:.3f},{axis_origin.z:.3f}]  "
            f"dir=[{axis_dir.x:.4f},{axis_dir.y:.4f},{axis_dir.z:.4f}]"
        )
    else:
        # fallback: use XZ bbox diagonal direction
        axis_origin = target.Center()
        axis_dir = vnorm(cq.Vector(bb_t.xlen, 0.0, bb_t.zlen))
        print(
            "AXIS: fallback from bbox XZ spans  "
            f"origin=[{axis_origin.x:.3f},{axis_origin.y:.3f},{axis_origin.z:.3f}]  "
            f"dir=[{axis_dir.x:.4f},{axis_dir.y:.4f},{axis_dir.z:.4f}]"
        )

    # --- Broad faces: planar faces with normals ~ +/-Y (blade broad surfaces) ---
    broad_faces = []
    for fi, f in enumerate(t_faces):
        if f.geomType() != "PLANE":
            continue
        try:
            n = f.normalAt()
        except Exception:
            continue
        if abs(n.y) > 0.99:
            broad_faces.append((fi, f.Area(), f.Center(), n, f))

    print(f"SELECTED: {len(broad_faces)} planar broad-face candidates on s{copy_idx} (|normal.y|>0.99)")
    broad_faces.sort(key=lambda t: t[1], reverse=True)
    for k, (fi, a, c, n, _) in enumerate(broad_faces[:12]):
        print(
            f"  broad_face[{k}]: local_face_idx={fi} area={a:.3f} center=[{c.x:.3f},{c.y:.3f},{c.z:.3f}] normal=[{n.x:.3f},{n.y:.3f},{n.z:.3f}]"
        )

    if not broad_faces:
        print("ERROR: No broad faces found; returning input")
        return shape

    # Global edge index map (for printing indices)
    base_edges = base.Edges()
    edge_idx_by_hc = {}
    for ei, e in enumerate(base_edges):
        edge_idx_by_hc[hc(e)] = ei

    # Collect candidate edges from broad faces
    cand_edges = {}
    for _, _, _, _, f in broad_faces:
        for e in f.Edges():
            cand_edges[hc(e)] = e

    cand_list = list(cand_edges.values())
    print(f"SELECTED: {len(cand_list)} unique edges from broad faces on s{copy_idx}")

    # Filter to long, straight, axis-aligned edges near y-extremes (exclude central step), and compute distance-to-axis
    edge_info = []
    for e in cand_list:
        try:
            L = e.Length()
        except Exception:
            continue
        if L < 100.0:
            continue

        try:
            gt = e.geomType()
        except Exception:
            gt = "(unknown)"
        if gt != "LINE":
            continue

        vs = e.Vertices()
        if len(vs) < 2:
            continue
        p1 = vs[0].Center()
        p2 = vs[-1].Center()
        edir = vnorm(vsub(p2, p1))
        align = abs(vdot(edir, axis_dir))
        if align < 0.95:
            continue

        ce = e.Center()
        # Keep only edges on the main blade outer surfaces (y ~ 0 or 12.7), to avoid central crossing profile changes
        if not (abs(ce.y - 0.0) < 0.35 or abs(ce.y - 12.7) < 0.35):
            continue

        d_perp, t_along = dist_point_to_axis(ce, axis_origin, axis_dir)
        edge_info.append((d_perp, L, align, t_along, ce, e, gt))

    edge_info.sort(key=lambda t: (t[0], t[1]), reverse=True)
    print(f"SELECTED: {len(edge_info)} candidate long outer edges (LINE, L>100, aligned to blade axis, y~0/12.7) on s{copy_idx}")
    for k, (d_perp, L, align, t_along, ce, e, gt) in enumerate(edge_info[:20]):
        ei = edge_idx_by_hc.get(hc(e), None)
        print(
            f"  edge_cand[{k}]: global_edge_idx={ei} type={gt} L={L:.3f} align={align:.4f} d_perp={d_perp:.3f} t_along={t_along:.3f} center=[{ce.x:.3f},{ce.y:.3f},{ce.z:.3f}]"
        )

    if not edge_info:
        print("ERROR: No candidate edges found; returning input")
        return shape

    # Select the outermost distance band (includes split segments if the long edge is segmented by central features)
    max_d = edge_info[0][0]
    d_tol = 0.20
    selected = []
    sel_h = set()
    for d_perp, L, align, t_along, ce, e, gt in edge_info:
        if d_perp >= max_d - d_tol:
            h = hc(e)
            if h not in sel_h:
                sel_h.add(h)
                selected.append(e)

    sel_idx = [edge_idx_by_hc.get(hc(e), None) for e in selected]
    print(f"SELECTED: {len(selected)} edges for R1.27 fillet on s{copy_idx} (outermost band max_d={max_d:.3f}, tol={d_tol:.3f}) idx={sel_idx}")
    if len(selected) < 4:
        print("WARNING: Expected at least 4 long outer edges; selection is smaller than expected")

    # --- Apply fillet ---
    r = 1.27
    try:
        edited = target.fillet(r, selected)
        print(f"FILLET: succeeded on {len(selected)} edges with R={r:.3f} mm")
    except Exception as e:
        print(f"ERROR: Fillet failed on target blade s{copy_idx} with R={r:.3f}: {e}")
        return shape

    # --- Verification: find cylindrical faces of radius ~1.27 created by fillet and print achieved radius ---
    try:
        from OCP.BRepAdaptor import BRepAdaptor_Surface
        from OCP.GeomAbs import GeomAbs_Cylinder
    except Exception as e:
        print(f"WARNING: Could not import OCP adaptors for radius verification: {e}")
        BRepAdaptor_Surface = None
        GeomAbs_Cylinder = None

    cyl_rs = []
    cyl_faces = 0
    if BRepAdaptor_Surface is not None:
        for f in edited.Faces():
            if f.geomType() != "CYLINDER":
                continue
            try:
                ad = BRepAdaptor_Surface(f.wrapped, True)
                if GeomAbs_Cylinder is not None and ad.GetType() != GeomAbs_Cylinder:
                    continue
                rad = ad.Cylinder().Radius()
            except Exception:
                continue

            # Only consider likely long fillet cylinders (span along blade length)
            bb = f.BoundingBox()
            if max(bb.xlen, bb.zlen) < 80.0:
                continue

            cyl_faces += 1
            if abs(rad - r) < 0.05:
                cyl_rs.append(rad)

    print(f"VERIFY: found {cyl_faces} long cylindrical faces on edited blade s{copy_idx} (bbox long-dim>80mm)")
    print(f"VERIFY: found {len(cyl_rs)} long cylindrical faces with radius within ±0.05 of {r:.3f} mm")
    if cyl_rs:
        r_avg = sum(cyl_rs) / len(cyl_rs)
        r_min = min(cyl_rs)
        r_max = max(cyl_rs)
        print(f"ACHIEVED RADIUS: avg={r_avg:.4f}  min={r_min:.4f}  max={r_max:.4f}  (target {r:.3f})")
    else:
        print("WARNING: Could not confirm R1.27 numerically; rely on render inspection")

    # --- Recompound with only the target blade replaced; other bodies untouched ---
    out_solids = []
    for i, s in enumerate(solids):
        out_solids.append(edited if i == copy_idx else s)
    out = cq.Compound.makeCompound(out_solids)
    return out