def my_cad_function(args):
    import cadquery as cq
    from math import isfinite

    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input

    def _v3(p):
        try:
            return (float(p.x), float(p.y), float(p.z))
        except Exception:
            return str(p)

    # ------------------------ helpers: radius queries ------------------------
    def edge_circle_radius(e):
        try:
            from OCP.BRepAdaptor import BRepAdaptor_Curve
            from OCP.GeomAbs import GeomAbs_Circle
            c = BRepAdaptor_Curve(e.wrapped)
            if c.GetType() == GeomAbs_Circle:
                return float(c.Circle().Radius())
        except Exception:
            pass
        return None

    def face_cyl_radius(f):
        try:
            from OCP.BRepAdaptor import BRepAdaptor_Surface
            from OCP.GeomAbs import GeomAbs_Cylinder
            s = BRepAdaptor_Surface(f.wrapped)
            if s.GetType() == GeomAbs_Cylinder:
                return float(s.Cylinder().Radius())
        except Exception:
            pass
        return None

    def count_edge_circle_radii(solid, radii_to_report=(2.0, 10.0, 50.0, 63.0)):
        # Counts by rounding to 0.01mm (enough to bucket)
        counts = {r: 0 for r in radii_to_report}
        all_counts = {}
        any_circle = 0
        for e in solid.Edges():
            r = edge_circle_radius(e)
            if r is None:
                continue
            any_circle += 1
            rr = round(r, 2)
            all_counts[rr] = all_counts.get(rr, 0) + 1
            for rt in radii_to_report:
                if abs(r - rt) < 0.05:
                    counts[rt] += 1
        return any_circle, counts, all_counts

    def count_face_cylinder_radii(solid, radii_to_report=(2.0, 10.0, 50.0, 63.0)):
        counts = {r: 0 for r in radii_to_report}
        all_counts = {}
        for f in solid.Faces():
            r = face_cyl_radius(f)
            if r is None:
                continue
            rr = round(r, 3)
            all_counts[rr] = all_counts.get(rr, 0) + 1
            for rt in radii_to_report:
                if abs(r - rt) < 0.05:
                    counts[rt] += 1
        return counts, all_counts

    # ------------------------ resolve base solid ------------------------
    base = shape.val() if hasattr(shape, "val") else shape
    sols = base.Solids()
    print(f"INFO: input solids={len(sols)}")
    if len(sols) != 1:
        print("ERROR: expected exactly 1 solid; returning input")
        return shape
    base_solid = sols[0]

    # ------------------------ list numbers named by sub-goal ------------------------
    R_OLD = 10.0
    R_NEW = 2.0
    remove_face_idx = [35, 36, 37, 38, 39, 40, 41, 42]
    r10_edge_idx = [95, 97, 98, 100, 101, 102, 103, 104]
    print(f"NUMBERS: R_OLD={R_OLD}mm, R_NEW={R_NEW}mm")
    print(f"NUMBERS: remove_face_idx={remove_face_idx}")
    print(f"NUMBERS: r10_edge_idx={r10_edge_idx}")

    # ------------------------ pre-check counts before edit ------------------------
    any_c0, counts0, all_counts0 = count_edge_circle_radii(base_solid)
    cyl_counts0, cyl_all0 = count_face_cylinder_radii(base_solid)
    print(f"INFO: BEFORE any_circle_edges={any_c0} counts(target radii)={counts0}")
    print(f"INFO: BEFORE circle radii histogram (rounded) keys={sorted(all_counts0.keys())[:20]}{'...' if len(all_counts0)>20 else ''}")
    print(f"INFO: BEFORE cylinder-face radii counts(target)={cyl_counts0}")

    # ------------------------ select faces to defeature (remove r=10 blend family) ------------------------
    faces = base_solid.Faces()
    remove_faces = []
    probe_pts = []
    for fi in remove_face_idx:
        if fi < 0 or fi >= len(faces):
            print(f"ERROR: face index {fi} out of range (nFaces={len(faces)})")
            continue
        f = faces[fi]
        gt = None
        try:
            gt = f.geomType()
        except Exception:
            gt = "(geomType unavailable)"
        c = f.Center()
        a = None
        try:
            a = float(f.Area())
        except Exception:
            a = float('nan')
        r = face_cyl_radius(f)
        rmsg = f" cyl_r={r:.3f}" if (r is not None and isfinite(r)) else ""
        print(f"SELECTED: 1 face for r10-blend removal fi={fi} geom={gt} area={a:.3f} center={_v3(c)}{rmsg}")
        remove_faces.append(f)
        probe_pts.append(cq.Vector(c.x, c.y, c.z))
    print(f"SELECTED: {len(remove_faces)} faces total for r10-blend removal idx={remove_face_idx}")
    if len(remove_faces) == 0:
        print("ERROR: selected 0 faces to remove; returning input")
        return shape

    # ------------------------ also collect edge-based probe points (given edge_idx loop) ------------------------
    edges = base_solid.Edges()
    edge_probe_pts = []
    got_edge_idxs = []
    for ei in r10_edge_idx:
        if ei < 0 or ei >= len(edges):
            print(f"ERROR: edge index {ei} out of range (nEdges={len(edges)})")
            continue
        e = edges[ei]
        ec = e.Center()
        er = edge_circle_radius(e)
        ermsg = f" r={er:.3f}" if (er is not None and isfinite(er)) else ""
        print(f"SELECTED: 1 edge for r10-boundary reference ei={ei} len={e.Length():.3f} center={_v3(ec)}{ermsg}")
        edge_probe_pts.append(cq.Vector(ec.x, ec.y, ec.z))
        got_edge_idxs.append(ei)
    print(f"SELECTED: {len(edge_probe_pts)} edges total for r10-boundary reference idx={got_edge_idxs}")

    # Combine probe points (faces + edges) for robust edge picking after defeature
    probe_pts = probe_pts + edge_probe_pts
    print(f"INFO: probe points total={len(probe_pts)} (face-centers + edge-centers)")

    # ------------------------ defeature: remove the r=10 blend faces ------------------------
    try:
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Defeaturing
        from OCP.TopTools import TopTools_ListOfShape
    except Exception as exc:
        print(f"ERROR: cannot import defeaturing API: {exc}")
        return shape

    df = BRepAlgoAPI_Defeaturing(base_solid.wrapped)

    # Add faces to remove (handle API variants)
    faces_wrapped = [f.wrapped for f in remove_faces]
    if hasattr(df, "AddFacesToRemove"):
        lst = TopTools_ListOfShape()
        for fw in faces_wrapped:
            lst.Append(fw)
        df.AddFacesToRemove(lst)
        print(f"INFO: defeaturing added {len(faces_wrapped)} faces via AddFacesToRemove(TopTools_ListOfShape)")
    elif hasattr(df, "AddFaceToRemove"):
        for fw in faces_wrapped:
            df.AddFaceToRemove(fw)
        print(f"INFO: defeaturing added {len(faces_wrapped)} faces via AddFaceToRemove(each)")
    else:
        print("ERROR: BRepAlgoAPI_Defeaturing has no AddFace(s)ToRemove method in this build; returning input")
        return shape

    # IMPORTANT: Do NOT call Perform() (not available in this environment). Use Build() only.
    if not hasattr(df, "Build"):
        print("ERROR: BRepAlgoAPI_Defeaturing has no Build() in this build; returning input")
        return shape
    df.Build()
    print("INFO: defeaturing called Build()")

    try:
        done = bool(df.IsDone())
    except Exception:
        done = True
    print(f"INFO: defeaturing IsDone={done}")
    if not done:
        print("ERROR: defeaturing reported not done; returning input")
        return shape

    try:
        df_shape = cq.Shape.cast(df.Shape())
    except Exception as exc:
        print(f"ERROR: could not read defeature result shape: {exc}")
        return shape

    df_sols = df_shape.Solids()
    print(f"INFO: defeature output solids={len(df_sols)}")
    if len(df_sols) == 0:
        print("ERROR: defeaturing produced no solids; returning input")
        return shape
    if len(df_sols) != 1:
        print(f"WARNING: defeaturing produced {len(df_sols)} solids; using first")
    df_solid = df_sols[0]

    any_c1, counts1, all_counts1 = count_edge_circle_radii(df_solid)
    cyl_counts1, cyl_all1 = count_face_cylinder_radii(df_solid)
    print(f"INFO: AFTER defeature any_circle_edges={any_c1} counts(target radii)={counts1}")
    print(f"INFO: AFTER defeature cylinder-face radii counts(target)={cyl_counts1}")

    # ------------------------ select edges to fillet to r=2 (near former r=10 blend) ------------------------
    wp_df = cq.Workplane(obj=df_solid)
    sel_edges = []
    sel_hash = set()

    def edge_hash(e):
        try:
            return int(e.wrapped.HashCode(2147483647))
        except Exception:
            try:
                return int(e.hashCode())
            except Exception:
                return None

    for i, p in enumerate(probe_pts):
        w = wp_df.edges(cq.selectors.NearestToPointSelector(p.toTuple()))
        ev = w.vals()
        print(f"SELECTED: {len(ev)} edges nearest to probe_pt[{i}]={_v3(p)} for refillet")
        if len(ev) == 0:
            continue
        e = ev[0]
        h = edge_hash(e)
        if h is not None and h in sel_hash:
            continue
        if h is not None:
            sel_hash.add(h)
        gt = None
        try:
            gt = e.geomType()
        except Exception:
            gt = "(geomType unavailable)"
        cr = edge_circle_radius(e)
        crmsg = f" circle_r={cr:.3f}" if (cr is not None and isfinite(cr)) else ""
        print(f"  EDGE_PICK: geom={gt} len={e.Length():.3f} center={_v3(e.Center())}{crmsg} hash={h}")
        sel_edges.append(e)

    print(f"SELECTED: {len(sel_edges)} unique edges for new r={R_NEW} fillet")
    if len(sel_edges) == 0:
        print("ERROR: selected 0 edges for refillet; returning defeatured solid (no fillet step applied)")
        return df_solid

    # ------------------------ apply the new r=2 fillet ------------------------
    try:
        out_solid = df_solid.fillet(R_NEW, sel_edges)
        print(f"INFO: fillet applied with r={R_NEW} on {len(sel_edges)} edges")
    except Exception as exc:
        print(f"ERROR: CadQuery fillet failed: {exc}")
        # return defeature result (partial progress) rather than crashing
        return df_solid

    # ------------------------ verification prints ------------------------
    any_c2, counts2, all_counts2 = count_edge_circle_radii(out_solid)
    cyl_counts2, cyl_all2 = count_face_cylinder_radii(out_solid)
    print(f"INFO: AFTER refillet any_circle_edges={any_c2} counts(target radii)={counts2}")
    print(f"INFO: AFTER refillet cylinder-face radii counts(target)={cyl_counts2}")
    print("EXPECTATION: r=10 circle-edge count should drop (ideally to 0 for that family) and r=2 count should increase; r=50 and r=63 counts should remain unchanged.")

    # Probe nearest edge to the original r10 edge centers and report if we now see r=2 nearby
    wp_out = cq.Workplane(obj=out_solid)
    for i, p in enumerate(edge_probe_pts):
        ev = wp_out.edges(cq.selectors.NearestToPointSelector(p.toTuple())).vals()
        if len(ev) == 0:
            print(f"CHECK: edge_probe[{i}] no nearest edge found")
            continue
        e = ev[0]
        r = edge_circle_radius(e)
        r_msg = f"r={r:.3f}" if (r is not None and isfinite(r)) else "(not a circle)"
        print(f"CHECK: nearest edge to old_r10_edge_center[{i}] -> {r_msg} edge_center={_v3(e.Center())}")

    return out_solid