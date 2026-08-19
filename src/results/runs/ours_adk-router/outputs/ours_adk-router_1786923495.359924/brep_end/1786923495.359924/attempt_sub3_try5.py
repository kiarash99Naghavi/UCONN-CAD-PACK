def my_cad_function(args):
    import cadquery as cq

    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input
    base = shape.val() if hasattr(shape, "val") else shape

    # --------------------------
    # Helpers
    # --------------------------
    def v(x, y=None, z=None):
        if y is None:
            return cq.Vector(x[0], x[1], x[2])
        return cq.Vector(float(x), float(y), float(z))

    def unit(vec: cq.Vector) -> cq.Vector:
        L = vec.Length
        if L < 1e-12:
            return cq.Vector(0, 0, 0)
        return vec.multiply(1.0 / L)

    def dist(a: cq.Vector, b: cq.Vector) -> float:
        return (a - b).Length

    def edge_dir_from_endpoints(e: cq.Edge) -> cq.Vector:
        try:
            p0 = e.startPoint()
            p1 = e.endPoint()
        except Exception:
            # fallback through vertices
            vs = e.Vertices()
            p0 = vs[0].Center()
            p1 = vs[-1].Center()
        d = unit(p1 - p0)
        return d

    def face_belongs_to_solid(face: cq.Face, solid: cq.Solid) -> bool:
        # best-effort: check identity against solid's faces
        for sf in solid.Faces():
            try:
                if sf.isSame(face):
                    return True
            except Exception:
                pass
        return False

    def print_solid(i, s, label=""):
        bb = s.BoundingBox()
        try:
            vol = s.Volume()
        except Exception:
            vol = float('nan')
        print(
            f"  solid s{i}{(' ' + label) if label else ''}: vol={vol:.3f}  "
            f"bbox=({bb.xmin:.3f},{bb.ymin:.3f},{bb.zmin:.3f})..({bb.xmax:.3f},{bb.ymax:.3f},{bb.zmax:.3f})  "
            f"lens=({bb.xlen:.3f},{bb.ylen:.3f},{bb.zlen:.3f})"
        )

    def print_face_global(idx, face, named_center=None):
        c = face.Center()
        a = face.Area()
        gt = face.geomType()
        n_txt = "(n/a)"
        if gt == "PLANE":
            n = face.normalAt()
            n_txt = f"[{n.x:.3f},{n.y:.3f},{n.z:.3f}]"
        bb = face.BoundingBox()
        extra = ""
        if named_center is not None:
            dc = dist(c, named_center)
            extra = f"  delta_to_named_center={dc:.3f}"
        print(
            f"SELECTED: 1 face for global face_idx #{idx}  type={gt}  center=[{c.x:.3f},{c.y:.3f},{c.z:.3f}]  "
            f"area={a:.3f}  normal={n_txt}  bbox=({bb.xmin:.3f},{bb.ymin:.3f},{bb.zmin:.3f})..({bb.xmax:.3f},{bb.ymax:.3f},{bb.zmax:.3f}){extra}"
        )

    # --------------------------
    # Select solids and identify s3 by its bbox
    # --------------------------
    solids = base.Solids()
    print(f"SELECTED: {len(solids)} solids from imported STEP")
    for i, s in enumerate(solids):
        print_solid(i, s)

    if len(solids) != 4:
        print("WARNING: Expected 4 solids; continuing but will still only modify the solid matching s3 bbox")

    # s3 bbox target from prompt
    s3_bb_min = v([-173.821, 0.0, -101.355])
    s3_bb_max = v([173.821, 12.7, 101.355])

    def bbox_close(bb, tol=0.25):
        return (
            abs(bb.xmin - s3_bb_min.x) <= tol and abs(bb.ymin - s3_bb_min.y) <= tol and abs(bb.zmin - s3_bb_min.z) <= tol and
            abs(bb.xmax - s3_bb_max.x) <= tol and abs(bb.ymax - s3_bb_max.y) <= tol and abs(bb.zmax - s3_bb_max.z) <= tol
        )

    s3_idx = None
    for i, s in enumerate(solids):
        bb = s.BoundingBox()
        if bbox_close(bb, tol=0.5):
            s3_idx = i
            break

    if s3_idx is None:
        # fallback: choose solid with matching y-range 0..12.7 and biggest xlen (~347.6) and zlen (~202.7)
        cands = []
        for i, s in enumerate(solids):
            bb = s.BoundingBox()
            if abs(bb.ymin - 0.0) < 1.0 and abs(bb.ymax - 12.7) < 1.0:
                cands.append((abs(bb.xlen - 347.642) + abs(bb.zlen - 202.709), i))
        print(f"SELECTED: {len(cands)} candidate solids for s3 by y-range and size")
        if not cands:
            print("ERROR: Could not identify s3; returning input")
            return shape
        cands.sort(key=lambda t: t[0])
        s3_idx = cands[0][1]

    s3 = solids[s3_idx]
    bb3_before = s3.BoundingBox()
    v3_before = s3.Volume()
    print(f"USING: solid s{s3_idx} as s3 (edit target)  bbox_before=({bb3_before.xmin:.3f},{bb3_before.ymin:.3f},{bb3_before.zmin:.3f})..({bb3_before.xmax:.3f},{bb3_before.ymax:.3f},{bb3_before.zmax:.3f})  vol_before={v3_before:.3f}")

    # --------------------------
    # Anchor axis & named broad-face centers
    # --------------------------
    axis = unit(v([0.881, 0.0, -0.473]))
    print(f"ANCHOR: s3 longitudinal axis=[{axis.x:.3f},{axis.y:.3f},{axis.z:.3f}]")

    R = 1.27
    print(f"RADIUS: target corner rounding R={R:.3f} mm (explicit swept-boolean; NO kernel fillet/chamfer)")

    named_centers = [
        v([106.007, 12.7, -56.901]),
        v([106.007, 0.0, -56.901]),
        v([-106.007, 12.7, 56.901]),
        v([-106.007, 0.0, 56.901]),
    ]
    for c in named_centers:
        print(f"ANCHOR FACE CENTER (named): [{c.x:.3f},{c.y:.3f},{c.z:.3f}]")

    # Resolve the 4 planar faces by GLOBAL face indices given in the geometry index
    faces_global = base.Faces()
    print(f"SELECTED: {len(faces_global)} faces on base shape (global face indexing)")

    global_face_indices = [124, 126, 138, 140]
    target_faces = []
    for idx, c_named in zip(global_face_indices, named_centers):
        if idx >= len(faces_global):
            print(f"SELECTED: 0 faces for global face_idx #{idx} (out of range)")
            continue
        f = faces_global[idx]
        print_face_global(idx, f, named_center=c_named)
        ok = face_belongs_to_solid(f, s3)
        print(f"CHECK: global face_idx #{idx} belongs_to_s3={ok}")
        target_faces.append(f)

    print(f"SELECTED: {len(target_faces)} broad planar faces (expected 4) for building paths")
    if len(target_faces) != 4:
        print("ERROR: Did not resolve the 4 required broad planar faces; returning input")
        return shape

    # --------------------------
    # Build 4 swept shaving tools (one path per named broad face)
    # --------------------------
    s3_edges = s3.Edges()
    print(f"INFO: s3 has {len(s3_edges)} edges for local index mapping")

    cutters = []
    selected_paths = []

    trim_each_end = 0.30  # mm; keep shoulder transition & short tip edges intact
    print(f"TRIM: each cutter is shortened by {trim_each_end:.2f} mm at both ends to avoid modifying shoulder/tip faces")

    for fi, f in enumerate(target_faces):
        if f.geomType() != "PLANE":
            print(f"WARNING: target_faces[{fi}] is not planar (type={f.geomType()}); skipping")
            continue

        ow = f.outerWire()
        ews = ow.Edges()
        print(f"SELECTED: {len(ews)} edges from outerWire of target face[{fi}] for path finding")

        n_broad_out = unit(f.normalAt())

        # candidate longitudinal edges: long and parallel to s3 axis
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

            eidx = None
            for k, ee in enumerate(s3_edges):
                try:
                    if ee.isSame(e):
                        eidx = k
                        break
                except Exception:
                    pass

            pm = e.positionAt(0.5)
            cands.append((L, align, e, eidx, pm))

        cands.sort(key=lambda t: (-t[0], -t[1]))
        print(f"SELECTED: {len(cands)} longitudinal candidate edges on face[{fi}] (L>40, |dot(axis)|>0.97)")
        for (L, align, _e, eidx, pm) in cands[:8]:
            print(f"  cand edge local_idx={eidx}  L={L:.3f}  align={align:.4f}  mid=[{pm.x:.3f},{pm.y:.3f},{pm.z:.3f}]")

        if not cands:
            print(f"ERROR: no longitudinal edges found on face[{fi}] -> cannot build cutter")
            continue

        # Use the single longest / best-aligned edge per named face as the path
        L, align, edge_path, eidx, pm = cands[0]
        selected_paths.append((fi, eidx, L, align, pm))
        print(f"SELECTED: 1 edge as path for face[{fi}]  local_edge_idx={eidx}  L={L:.3f}  align={align:.4f}")

        # Determine edge endpoints and local axis direction (should match global axis)
        p0 = edge_path.startPoint()
        p1 = edge_path.endPoint()
        d_edge = unit(p1 - p0)
        if d_edge.dot(axis) < 0:
            # flip so it is generally aligned with +axis
            p0, p1 = p1, p0
            d_edge = d_edge.multiply(-1)

        # Determine which side of the broad face this edge is on:
        # v_side is perpendicular to (axis, broad normal). sign from face center -> edge mid.
        v_side = unit(axis.cross(n_broad_out))
        if v_side.Length < 1e-9:
            print(f"WARNING: v_side near zero on face[{fi}] (axis parallel to normal?) -> cannot build cutter")
            continue

        fc = f.Center()
        sign = 1.0 if (pm - fc).dot(v_side) >= 0 else -1.0
        n_side_out = v_side.multiply(sign)

        inward_broad = n_broad_out.multiply(-1)
        inward_side = n_side_out.multiply(-1)
        offset = inward_broad.add(inward_side).multiply(R)

        # Trim extents to preserve shoulder transition and tip face
        total_len = (p1 - p0).Length
        if total_len <= 2 * trim_each_end + 1e-6:
            print(f"WARNING: path too short after trimming on face[{fi}] (len={total_len:.3f}); skipping cutter")
            continue

        p0t = p0.add(d_edge.multiply(trim_each_end))
        p1t = p1.add(d_edge.multiply(-trim_each_end))
        height = (p1t - p0t).Length

        # Cylinder base center for the cutter
        C0 = p0t.add(offset)

        # Build cutter as an explicit swept solid (a cylinder along the path direction)
        try:
            cyl = cq.Solid.makeCylinder(R, height, pnt=C0, dir=d_edge)
        except Exception as e:
            print(f"WARNING: makeCylinder failed on face[{fi}] cutter: {e}")
            continue

        # Slightly enlarge cutter radius epsilon to ensure robust boolean, without affecting target radius materially
        # (kept very small)
        eps = 0.01
        try:
            cyl = cq.Solid.makeCylinder(R + eps, height, pnt=C0, dir=d_edge)
        except Exception:
            pass

        cutters.append(cyl)
        print(
            f"CUTTER BUILT: face[{fi}] path edge_idx={eidx}  path_len={total_len:.3f} trimmed_len={height:.3f}  "
            f"n_broad_out=[{n_broad_out.x:.3f},{n_broad_out.y:.3f},{n_broad_out.z:.3f}]  "
            f"n_side_out=[{n_side_out.x:.3f},{n_side_out.y:.3f},{n_side_out.z:.3f}]  "
            f"C0=[{C0.x:.3f},{C0.y:.3f},{C0.z:.3f}] dir=[{d_edge.x:.3f},{d_edge.y:.3f},{d_edge.z:.3f}]"
        )

    print(f"SELECTED: {len(selected_paths)} total path edges (expected 4)")
    for (fi, eidx, L, align, pm) in selected_paths:
        print(f"  PATH: face[{fi}] edge_local_idx={eidx}  L={L:.3f}  align={align:.4f}  mid=[{pm.x:.3f},{pm.y:.3f},{pm.z:.3f}]")

    print(f"SELECTED: {len(cutters)} cutters built (expected 4)")
    if len(cutters) != 4:
        print("ERROR: Did not build 4 cutters; returning input (no-op risk)")
        return shape

    # --------------------------
    # Apply cutters to s3 ONLY
    # --------------------------
    s3_edited = s3
    removed_total = 0.0
    for i, tool in enumerate(cutters):
        try:
            vb = s3_edited.Volume()
            s3_edited = s3_edited.cut(tool)
            va = s3_edited.Volume() if hasattr(s3_edited, "Volume") else vb
            dv = vb - va
            removed_total += dv
            print(f"CUT: applied cutter[{i}]  removed_volume={dv:.3f} mm^3")
        except Exception as e:
            print(f"ERROR: cutter[{i}] cut failed: {e}")
            return shape

    print(f"CUT SUMMARY: total_removed_volume={removed_total:.3f} mm^3")
    if abs(removed_total) < 1e-6:
        print("ERROR: Total removed volume ~0 (no-op). Returning input unchanged.")
        return shape

    # Ensure s3_edited is still a single solid; if a compound is returned, keep the largest fused
    try:
        s3_solids_after = s3_edited.Solids()
        if len(s3_solids_after) != 1:
            print(f"WARNING: s3_edited is {len(s3_solids_after)} solids after cut; attempting fuse to keep single body")
            fused = s3_solids_after[0]
            for ss in s3_solids_after[1:]:
                try:
                    fused = fused.fuse(ss)
                except Exception:
                    pass
            s3_edited = fused
            s3_solids_after2 = s3_edited.Solids()
            print(f"CHECK: solids in s3_edited after fuse attempt = {len(s3_solids_after2)}")
    except Exception as e:
        print(f"WARNING: could not analyze/fuse s3_edited solids: {e}")

    bb3_after = s3_edited.BoundingBox()
    v3_after = s3_edited.Volume()
    print(
        f"s3 bbox AFTER: ({bb3_after.xmin:.3f},{bb3_after.ymin:.3f},{bb3_after.zmin:.3f})..({bb3_after.xmax:.3f},{bb3_after.ymax:.3f},{bb3_after.zmax:.3f})"
    )
    print(
        "s3 bbox delta: "
        f"dxmin={bb3_after.xmin - bb3_before.xmin:+.3f}, dxmax={bb3_after.xmax - bb3_before.xmax:+.3f}, "
        f"dymin={bb3_after.ymin - bb3_before.ymin:+.3f}, dymax={bb3_after.ymax - bb3_before.ymax:+.3f}, "
        f"dzmin={bb3_after.zmin - bb3_before.zmin:+.3f}, dzmax={bb3_after.zmax - bb3_before.zmax:+.3f}"
    )
    print(f"s3 volume delta: {v3_after - v3_before:+.3f} mm^3")

    # --------------------------
    # Verification: print measured radii ~1.27 mm from resulting cylindrical faces aligned to blade axis
    # --------------------------
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
                ax = unit(cq.Vector(d.X(), d.Y(), d.Z()))
                align = abs(ax.dot(axis))
                c = f.Center()
                cyl_faces.append((r, align, c))

        print(f"SELECTED: {len(cyl_faces)} cylindrical faces on s3_edited with radius ~ {R:.2f} mm")
        cyl_faces_sorted = sorted(cyl_faces, key=lambda t: -t[1])
        for j, (r, align, c) in enumerate(cyl_faces_sorted[:24]):
            print(
                f"  R_CHECK[{j}]: r={r:.4f} mm  axis_align_to_blade_axis={align:.4f}  face_center=[{c.x:.3f},{c.y:.3f},{c.z:.3f}]"
            )
    except Exception as e:
        print(f"WARNING: could not compute cylinder radii via OCP adaptor: {e}")

    # --------------------------
    # Recompound: preserve s0,s1,s2 UNCHANGED and keep solid order unchanged
    # --------------------------
    vols_before = [s.Volume() for s in solids]
    out_solids = list(solids)  # preserve order
    out_solids[s3_idx] = s3_edited
    vols_after = [out_solids[i].Volume() for i in range(len(out_solids))]

    for i in range(len(out_solids)):
        dv = vols_after[i] - vols_before[i]
        tag = "(edited s3)" if i == s3_idx else "(must be unchanged)"
        print(f"VOLUME CHECK: solid index {i} {tag}  before={vols_before[i]:.3f} after={vols_after[i]:.3f}  delta={dv:+.3f}")

    out = cq.Compound.makeCompound(out_solids)
    return out