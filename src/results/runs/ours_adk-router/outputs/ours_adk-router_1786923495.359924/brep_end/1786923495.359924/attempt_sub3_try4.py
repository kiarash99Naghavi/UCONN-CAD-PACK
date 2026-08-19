def my_cad_function(args):
    import cadquery as cq
    from math import sqrt

    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input
    base = shape.val() if hasattr(shape, "val") else shape

    sols = base.Solids()
    print(f"SELECTED: {len(sols)} solids from imported STEP")
    for i, s in enumerate(sols):
        bb = s.BoundingBox()
        print(
            f"  solid s{i}: vol={s.Volume():.3f}  bbox=({bb.xmin:.3f},{bb.ymin:.3f},{bb.zmin:.3f})..({bb.xmax:.3f},{bb.ymax:.3f},{bb.zmax:.3f})  lens=({bb.xlen:.3f},{bb.ylen:.3f},{bb.zlen:.3f})"
        )

    if len(sols) != 4:
        print("WARNING: expected 4 solids per index; continuing anyway")

    # --- Identify s3 by bbox (given in prompt) ---
    def close(a, b, tol):
        return abs(a - b) <= tol

    s3_idx = None
    for i, s in enumerate(sols):
        bb = s.BoundingBox()
        if (
            close(bb.xmin, -173.821, 2.0) and close(bb.xmax, 173.821, 2.0)
            and close(bb.ymin, 0.0, 0.5) and close(bb.ymax, 12.7, 0.5)
            and close(bb.zmin, -101.355, 2.0) and close(bb.zmax, 101.355, 2.0)
        ):
            s3_idx = i
            break

    if s3_idx is None:
        # fallback: xlen~347.6, ylen~12.7, zlen~202.7
        cands = []
        for i, s in enumerate(sols):
            bb = s.BoundingBox()
            if abs(bb.ylen - 12.7) < 1.0 and bb.xlen > 300 and bb.zlen > 150:
                cands.append((abs(bb.xlen - 347.642) + abs(bb.zlen - 202.71), i))
        print(f"SELECTED: {len(cands)} candidate solids for s3 by overall span")
        if not cands:
            print("ERROR: could not identify s3; returning input")
            return shape
        cands.sort()
        s3_idx = cands[0][1]

    s3 = sols[s3_idx]
    bb3_before = s3.BoundingBox()
    print(
        f"USING: solid s{s3_idx} as s3 for edit  bbox_before=({bb3_before.xmin:.3f},{bb3_before.ymin:.3f},{bb3_before.zmin:.3f})..({bb3_before.xmax:.3f},{bb3_before.ymax:.3f},{bb3_before.zmax:.3f})"
    )

    # --- Named constants / anchors from prompt ---
    axis = cq.Vector(0.881, 0.0, -0.473)
    axis = axis.multiply(1.0 / axis.Length)
    R = 1.27
    face_centers_named = [
        cq.Vector(106.007, 12.7, -56.901),
        cq.Vector(106.007, 0.0, -56.901),
        cq.Vector(-106.007, 12.7, 56.901),
        cq.Vector(-106.007, 0.0, 56.901),
    ]
    print(f"ANCHOR: s3 longitudinal axis=[{axis.x:.3f},{axis.y:.3f},{axis.z:.3f}]")
    print(f"RADIUS: target corner rounding R={R:.3f} mm (explicit boolean via swept cylinders)")
    for p in face_centers_named:
        print(f"ANCHOR FACE CENTER (named): [{p.x:.3f},{p.y:.3f},{p.z:.3f}]")

    # --- Resolve faces by GLOBAL indices first (as instructed), then validate/fallback to s3-local search ---
    faces_global = base.Faces()
    print(f"SELECTED: {len(faces_global)} faces on base shape (global indexing)")

    global_face_indices = [124, 126, 138, 140]

    def face_info(f):
        c = f.Center()
        gt = f.geomType()
        a = f.Area()
        n_txt = "(n/a)"
        if gt == "PLANE":
            n = f.normalAt()
            n_txt = f"[{n.x:.3f},{n.y:.3f},{n.z:.3f}]"
        return c, gt, a, n_txt

    faces_seed = []
    for gi, named_c in zip(global_face_indices, face_centers_named):
        if gi >= len(faces_global):
            print(f"SELECTED: 0 faces for global face_idx #{gi} (out of range)")
            faces_seed.append(None)
            continue
        f = faces_global[gi]
        c, gt, a, n_txt = face_info(f)
        d = (c - named_c).Length
        print(
            f"SELECTED: 1 face for global face_idx #{gi}  type={gt} center=[{c.x:.3f},{c.y:.3f},{c.z:.3f}] area={a:.3f} normal={n_txt}  delta_to_named_center={d:.3f}"
        )
        faces_seed.append(f)

    # Validate they are planar +/-Y and near the given centers; otherwise search on s3
    def find_s3_face_near(center_target, y_sign, tol_xyz=1.0):
        # y_sign: +1 for normal +Y outward, -1 for normal -Y outward
        best = None
        best_d = 1e9
        for f in s3.Faces():
            if f.geomType() != "PLANE":
                continue
            n = f.normalAt()
            if abs(n.x) > 1e-3 or abs(n.z) > 1e-3:
                continue
            if (y_sign > 0 and n.y < 0.9) or (y_sign < 0 and n.y > -0.9):
                continue
            c = f.Center()
            d = (c - center_target).Length
            if d < best_d:
                best = f
                best_d = d
        if best is not None and best_d <= tol_xyz:
            return best, best_d
        return None, best_d

    target_faces = []
    for i, (f_seed, named_c) in enumerate(zip(faces_seed, face_centers_named)):
        y_sign = 1 if named_c.y > 6.0 else -1
        ok = False
        if f_seed is not None and f_seed.geomType() == "PLANE":
            c = f_seed.Center()
            n = f_seed.normalAt()
            d = (c - named_c).Length
            if d <= 1.0 and abs(n.x) < 1e-3 and abs(n.z) < 1e-3 and ((y_sign > 0 and n.y > 0.9) or (y_sign < 0 and n.y < -0.9)):
                ok = True
        if ok:
            target_faces.append(f_seed)
            print(f"SELECTED: 1 planar s3 broad face (from global index) near named center #{i}  y_sign={y_sign}")
        else:
            f_fb, d_fb = find_s3_face_near(named_c, y_sign=y_sign, tol_xyz=2.0)
            if f_fb is None:
                print(f"SELECTED: 0 planar s3 broad faces near named center #{i}; cannot proceed safely")
                return shape
            c, gt, a, n_txt = face_info(f_fb)
            print(
                f"SELECTED: 1 planar s3 broad face (fallback search) for named center #{i}  center=[{c.x:.3f},{c.y:.3f},{c.z:.3f}] area={a:.3f} normal={n_txt} delta_to_named_center={d_fb:.3f}"
            )
            target_faces.append(f_fb)

    # --- For each face: identify longitudinal boundary paths (edges parallel to blade axis) on the OUTER wire ---
    # We will build explicit swept-circle cutters (cylinders) offset by R in the inward (broad) and inward (side) directions.
    # This realizes an exact R arc on the remaining surface without invoking kernel fillet.

    s3_center = s3.Center()
    print(f"s3 CENTER: [{s3_center.x:.3f},{s3_center.y:.3f},{s3_center.z:.3f}]")

    # To avoid shoulder/tip changes, keep a setback from the edge endpoints.
    end_setback = 1.0  # mm
    over = 0.25        # mm extra length on cutter for robust boolean, still within setback-protected span

    cutters = []
    selected_paths = []

    def unit(v):
        if v.Length < 1e-12:
            return cq.Vector(0, 0, 0)
        return v.multiply(1.0 / v.Length)

    def edge_dir_from_endpoints(e):
        p0 = e.startPoint()
        p1 = e.endPoint()
        return unit(p1 - p0)

    def build_cutter_for_edge(face, edge, label=""):
        # Determine trimmed span along the edge
        L = edge.Length()
        if L < 10.0:
            return None
        sb = min(end_setback, 0.10 * L)
        t0 = sb / L
        t1 = 1.0 - sb / L
        if t1 <= t0:
            return None

        P0 = edge.positionAt(t0)
        P1 = edge.positionAt(t1)
        Pm = edge.positionAt(0.5)

        # Use the named blade axis as sweep direction
        # Ensure direction from P0 to P1 aligns with axis for consistent length computation
        if (P1 - P0).dot(axis) < 0:
            P0, P1 = P1, P0

        n_out = face.normalAt()  # outward from solid (by convention)
        n_in = unit(-n_out)

        # Side direction candidate (perpendicular to axis and broad normal)
        b0 = unit(axis.cross(n_in))
        if b0.Length < 1e-9:
            return None

        # Choose side inward (+/-b0) by looking toward solid center from the edge
        sign = 1.0 if (s3_center - Pm).dot(b0) > 0 else -1.0
        side_in = b0.multiply(sign)

        offset = n_in.multiply(R).add(side_in.multiply(R))

        C0 = P0.add(offset)
        C1 = P1.add(offset)

        height = abs((C1 - C0).dot(axis))
        if height < 5.0:
            return None

        # Build a swept-circle cutter: cylinder of radius R along the axis direction
        # Start slightly before C0 and end slightly after C1 (within setback region)
        Cstart = C0.add(axis.multiply(-over))
        h = height + 2 * over

        cyl = cq.Solid.makeCylinder(R, h, pnt=Cstart, dir=axis)

        # Print placement checks
        print(
            f"CUTTER BUILT: {label}  edge_len={L:.3f} trimmed_span={height:.3f}  "
            f"n_out=[{n_out.x:.3f},{n_out.y:.3f},{n_out.z:.3f}] n_in=[{n_in.x:.3f},{n_in.y:.3f},{n_in.z:.3f}] "
            f"side_in=[{side_in.x:.3f},{side_in.y:.3f},{side_in.z:.3f}]  "
            f"C0=[{C0.x:.3f},{C0.y:.3f},{C0.z:.3f}] C1=[{C1.x:.3f},{C1.y:.3f},{C1.z:.3f}]"
        )
        return cyl

    # Precompute s3 local edges list for index reporting
    s3_edges = s3.Edges()

    for fi, f in enumerate(target_faces):
        ow = f.outerWire()
        ews = ow.Edges()
        print(f"SELECTED: {len(ews)} edges from outerWire of target face[{fi}]")

        # Candidate longitudinal edges: long and parallel to axis
        cands = []
        for e in ews:
            try:
                L = e.Length()
            except Exception:
                continue
            if L < 40.0:
                continue
            d = edge_dir_from_endpoints(e)
            align = abs(d.dot(axis))
            if align < 0.97:
                continue
            # determine which s3 edge index this is (best-effort)
            eidx = None
            for k, ee in enumerate(s3_edges):
                if ee.isSame(e):
                    eidx = k
                    break
            pm = e.positionAt(0.5)
            cands.append((L, align, e, eidx, pm))

        cands.sort(key=lambda t: (-t[0], -t[1]))
        print(f"SELECTED: {len(cands)} longitudinal candidate edges on face[{fi}] (L>40, |dot(axis)|>0.97)")
        for (L, align, e, eidx, pm) in cands[:8]:
            print(
                f"  cand edge local_idx={eidx}  L={L:.3f}  align={align:.4f}  mid=[{pm.x:.3f},{pm.y:.3f},{pm.z:.3f}]"
            )

        if not cands:
            print(f"WARNING: no longitudinal edges found on face[{fi}] -> cannot build cutter(s) from this face")
            continue

        # The prompt language says 'four paths from outer boundaries' (one per face). We will use
        # ONLY the single longest/most-aligned longitudinal edge per face as that 'path'.
        # This avoids accidentally touching any inner/secondary longitudinal edges.
        L, align, edge_path, eidx, pm = cands[0]
        selected_paths.append((fi, eidx, L, align, pm))
        print(f"SELECTED: 1 edge as path for face[{fi}]  local_edge_idx={eidx}  L={L:.3f} align={align:.4f}")

        cutter = build_cutter_for_edge(f, edge_path, label=f"face[{fi}] path edge_idx={eidx}")
        if cutter is not None:
            cutters.append(cutter)

    print(f"SELECTED: {len(selected_paths)} total path edges (1 per named face)")
    for (fi, eidx, L, align, pm) in selected_paths:
        print(f"  PATH: face[{fi}] edge_local_idx={eidx}  L={L:.3f}  align={align:.4f}  mid=[{pm.x:.3f},{pm.y:.3f},{pm.z:.3f}]")

    print(f"SELECTED: {len(cutters)} cutters built")
    if not cutters:
        print("ERROR: No cutters built; returning input (no-op risk)")
        return shape

    # --- Apply cutters to s3 only (explicit boolean shave, no fillet/chamfer ops) ---
    s3_edited = s3
    total_removed = None
    for i, tool in enumerate(cutters):
        try:
            before = s3_edited.Volume()
            s3_edited = s3_edited.cut(tool)
            after = s3_edited.Volume()
            dv = before - after
            print(f"CUT: applied cutter[{i}]  removed_volume={dv:.3f} mm^3")
            if total_removed is None:
                total_removed = dv
            else:
                total_removed += dv
        except Exception as e:
            print(f"WARNING: cutter[{i}] boolean cut failed: {e}")

    if total_removed is None or abs(total_removed) < 1e-6:
        print("ERROR: Total removed volume ~0 (no-op risk). Returning input unchanged.")
        return shape

    bb3_after = s3_edited.BoundingBox()
    print(
        f"s3 bbox AFTER: ({bb3_after.xmin:.3f},{bb3_after.ymin:.3f},{bb3_after.zmin:.3f})..({bb3_after.xmax:.3f},{bb3_after.ymax:.3f},{bb3_after.zmax:.3f})"
    )
    print(
        "s3 bbox delta: "
        f"dxmin={bb3_after.xmin - bb3_before.xmin:+.3f}, dxmax={bb3_after.xmax - bb3_before.xmax:+.3f}, "
        f"dymin={bb3_after.ymin - bb3_before.ymin:+.3f}, dymax={bb3_after.ymax - bb3_before.ymax:+.3f}, "
        f"dzmin={bb3_after.zmin - bb3_before.zmin:+.3f}, dzmax={bb3_after.zmax - bb3_before.zmax:+.3f}"
    )

    # --- Verification: print measured radii for the resulting cylindrical surfaces ~R=1.27 ---
    # We will scan s3_edited faces and extract cylinder radii via OCP adaptor.
    try:
        from OCP.BRepAdaptor import BRepAdaptor_Surface
        from OCP.GeomAbs import GeomAbs_Cylinder

        cyl_faces = []
        for f in s3_edited.Faces():
            if f.geomType() != "CYLINDER":
                continue
            ad = BRepAdaptor_Surface(f.wrapped, True)
            if ad.GetType() != GeomAbs_Cylinder:
                continue
            cyl = ad.Cylinder()
            r = float(cyl.Radius())
            if abs(r - R) <= 0.05:
                d = cyl.Axis().Direction()
                ax = cq.Vector(d.X(), d.Y(), d.Z())
                ax = unit(ax)
                align = abs(ax.dot(axis))
                c = f.Center()
                cyl_faces.append((r, align, c, f))

        print(f"SELECTED: {len(cyl_faces)} cylindrical faces on s3_edited with radius ~ {R:.2f} mm")
        for j, (r, align, c, _) in enumerate(sorted(cyl_faces, key=lambda t: -t[1])[:12]):
            print(
                f"  R_CHECK[{j}]: r={r:.4f} mm  axis_align_to_blade_axis={align:.4f}  face_center=[{c.x:.3f},{c.y:.3f},{c.z:.3f}]"
            )
    except Exception as e:
        print(f"WARNING: could not compute cylinder radii via OCP adaptor: {e}")

    # --- Recompound: replace only s3; preserve s0,s1,s2 unchanged ---
    out_solids = []
    for i, s in enumerate(sols):
        out_solids.append(s3_edited if i == s3_idx else s)
    out = cq.Compound.makeCompound(out_solids)
    return out