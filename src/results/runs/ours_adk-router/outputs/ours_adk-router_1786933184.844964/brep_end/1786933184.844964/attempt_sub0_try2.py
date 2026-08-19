def my_cad_function(args):
    import cadquery as cq
    from math import isfinite

    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input
    base = shape.val() if hasattr(shape, "val") else shape
    sols = base.Solids()
    print(f"INFO: input solids={len(sols)}")
    if len(sols) != 1:
        print("ERROR: expected exactly 1 solid; returning input")
        return shape
    base_solid = sols[0]

    # ------------------------ numbers named by sub-goal ------------------------
    R_OLD = 10.0
    R_NEW = 2.0
    remove_face_idx = [35, 36, 37, 38, 39, 40, 41, 42]
    r10_edge_idx = [95, 97, 98, 100, 101, 102, 103, 104]
    print(f"NUMBERS: R_OLD={R_OLD}mm, R_NEW={R_NEW}mm")
    print(f"NUMBERS: remove_face_idx={remove_face_idx}")
    print(f"NUMBERS: r10_edge_idx={r10_edge_idx}")

    # ------------------------ helpers ------------------------
    def _v3(v):
        return (float(v.x), float(v.y), float(v.z))

    def edge_circle_radius(e):
        # CadQuery Edge.radius() works for circles/arcs; otherwise throws
        try:
            r = float(e.radius())
            if isfinite(r):
                return r
        except Exception:
            pass
        # fallback via adaptor when possible
        try:
            ad = e._geomAdaptor()
            # 1 == GeomAbs_CurveType.GeomAbs_Circle in OCC, but avoid enums
            if hasattr(ad, "GetType") and int(ad.GetType()) == 1 and hasattr(ad, "Circle"):
                return float(ad.Circle().Radius())
        except Exception:
            pass
        return None

    def face_cylinder_radius(f):
        try:
            if str(f.geomType()).upper() == "CYLINDER":
                return float(f.radius())
        except Exception:
            pass
        return None

    def count_edge_circle_radii(solid):
        # returns histogram by rounded radius
        hist = {}
        any_c = 0
        for e in solid.Edges():
            r = edge_circle_radius(e)
            if r is None or not isfinite(r):
                continue
            any_c += 1
            rr = round(r, 3)
            hist[rr] = hist.get(rr, 0) + 1
        return any_c, hist

    def count_cyl_face_radii(solid):
        hist = {}
        for f in solid.Faces():
            r = face_cylinder_radius(f)
            if r is None or not isfinite(r):
                continue
            rr = round(r, 3)
            hist[rr] = hist.get(rr, 0) + 1
        return hist

    any_c0, hist0 = count_edge_circle_radii(base_solid)
    cyl0 = count_cyl_face_radii(base_solid)
    print(f"INFO: BEFORE any_circle_edges={any_c0} circle_radii_hist(keys)={sorted(hist0.keys())}")
    print(f"INFO: BEFORE circle_radii_hist={hist0}")
    print(f"INFO: BEFORE cylinder_face_radii_hist={cyl0}")

    # ------------------------ resolve faces/edges by index and print checks ------------------------
    faces = base_solid.Faces()
    edges = base_solid.Edges()
    print(f"INFO: base_solid faces={len(faces)} edges={len(edges)}")

    remove_faces = []
    probe_pts = []
    for fi in remove_face_idx:
        if fi < 0 or fi >= len(faces):
            print(f"ERROR: face index {fi} out of range")
            continue
        f = faces[fi]
        gt = None
        try:
            gt = str(f.geomType())
        except Exception:
            gt = "(geomType unavailable)"
        area = None
        try:
            area = float(f.Area())
        except Exception:
            area = float("nan")
        c = f.Center()
        msg = f"SELECTED: 1 face for r10-blend removal fi={fi} geom={gt} area={area:.3f} center={_v3(c)}"
        fr = face_cylinder_radius(f)
        if fr is not None:
            msg += f" cyl_r={fr:.3f}"
        print(msg)
        remove_faces.append(f)
        probe_pts.append(cq.Vector(c.x, c.y, c.z))

    print(f"SELECTED: {len(remove_faces)} faces total for r10-blend removal idx={[f for f in remove_face_idx if 0 <= f < len(faces)]}")
    if len(remove_faces) == 0:
        print("ERROR: selected 0 faces to remove; returning input")
        return shape

    edge_probe_pts = []
    got_edge_idxs = []
    for ei in r10_edge_idx:
        if ei < 0 or ei >= len(edges):
            print(f"ERROR: edge index {ei} out of range")
            continue
        e = edges[ei]
        ec = e.Center()
        er = edge_circle_radius(e)
        ermsg = f" r={er:.3f}" if (er is not None and isfinite(er)) else ""
        print(f"SELECTED: 1 edge for r10-boundary reference ei={ei} len={e.Length():.3f} center={_v3(ec)}{ermsg}")
        edge_probe_pts.append(cq.Vector(ec.x, ec.y, ec.z))
        got_edge_idxs.append(ei)
    print(f"SELECTED: {len(edge_probe_pts)} edges total for r10-boundary reference idx={got_edge_idxs}")

    probe_pts = probe_pts + edge_probe_pts
    print(f"INFO: probe points total={len(probe_pts)} (face-centers + edge-centers)")

    # ------------------------ defeature: remove the r=10 blend faces ------------------------
    # Diagnosis: previous attempt crashed because this environment's OCP binding exposes
    # BRepAlgoAPI_Defeaturing() with a *default constructor only*; passing a TopoDS_Solid
    # to __init__ raises a TypeError. We must construct it with no args then SetShape/SetInShape.
    try:
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Defeaturing
        from OCP.TopTools import TopTools_ListOfShape
    except Exception as exc:
        print(f"ERROR: cannot import defeaturing API: {exc}")
        return shape

    df = None
    used_ctor_arg = False
    try:
        df = BRepAlgoAPI_Defeaturing(base_solid.wrapped)
        used_ctor_arg = True
        print("INFO: defeaturing constructed with shape argument")
    except TypeError as exc:
        print(f"INFO: defeaturing ctor with arg failed (expected in this build): {exc}")
        try:
            df = BRepAlgoAPI_Defeaturing()
            print("INFO: defeaturing constructed with default ctor")
        except Exception as exc2:
            print(f"ERROR: defeaturing default ctor failed: {exc2}")
            return shape

    if not used_ctor_arg:
        # try the various setter names
        set_ok = False
        for m in ("SetShape", "SetInShape", "SetBaseShape"):
            if hasattr(df, m):
                try:
                    getattr(df, m)(base_solid.wrapped)
                    print(f"INFO: defeaturing set input shape using {m}()")
                    set_ok = True
                    break
                except Exception as exc:
                    print(f"WARNING: defeaturing {m}() failed: {exc}")
        if not set_ok:
            print("ERROR: defeaturing has no usable SetShape/SetInShape method; returning input")
            return shape

    faces_wrapped = [f.wrapped for f in remove_faces]

    added_faces = False
    if hasattr(df, "AddFacesToRemove"):
        try:
            lst = TopTools_ListOfShape()
            for fw in faces_wrapped:
                lst.Append(fw)
            df.AddFacesToRemove(lst)
            print(f"INFO: defeaturing added {len(faces_wrapped)} faces via AddFacesToRemove(TopTools_ListOfShape)")
            added_faces = True
        except Exception as exc:
            print(f"WARNING: AddFacesToRemove failed: {exc}")

    if not added_faces and hasattr(df, "AddFaceToRemove"):
        try:
            for fw in faces_wrapped:
                df.AddFaceToRemove(fw)
            print(f"INFO: defeaturing added {len(faces_wrapped)} faces via AddFaceToRemove(each)")
            added_faces = True
        except Exception as exc:
            print(f"WARNING: AddFaceToRemove failed: {exc}")

    if not added_faces:
        print("ERROR: defeaturing could not add faces to remove (no compatible API); returning input")
        return shape

    # execute
    built = False
    for m in ("Build", "Perform"):
        if hasattr(df, m):
            try:
                getattr(df, m)()
                print(f"INFO: defeaturing called {m}()")
                built = True
                break
            except Exception as exc:
                print(f"WARNING: defeaturing {m}() failed: {exc}")
    if not built:
        print("ERROR: defeaturing has no working Build/Perform; returning input")
        return shape

    try:
        done = bool(df.IsDone()) if hasattr(df, "IsDone") else True
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

    any_c1, hist1 = count_edge_circle_radii(df_solid)
    cyl1 = count_cyl_face_radii(df_solid)
    print(f"INFO: AFTER defeature any_circle_edges={any_c1} circle_radii_hist={hist1}")
    print(f"INFO: AFTER defeature cylinder_face_radii_hist={cyl1}")

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
        ev = wp_df.edges(cq.selectors.NearestToPointSelector(p.toTuple())).vals()
        print(f"SELECTED: {len(ev)} edges nearest to probe_pt[{i}]={_v3(p)} for refillet")
        if len(ev) == 0:
            continue
        e = ev[0]
        h = edge_hash(e)
        if h is not None and h in sel_hash:
            continue

        # Filter out the in-plane loop corner families (r=50, r=63) and other big circles
        cr = edge_circle_radius(e)
        if cr is not None and isfinite(cr) and cr > 5.0:
            print(f"  EDGE_SKIP: picked a circular edge with r={cr:.3f} (>5mm); skipping")
            continue

        if h is not None:
            sel_hash.add(h)

        gt = None
        try:
            gt = str(e.geomType())
        except Exception:
            gt = "(geomType unavailable)"
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
        return df_solid

    # ------------------------ verification prints ------------------------
    any_c2, hist2 = count_edge_circle_radii(out_solid)
    cyl2 = count_cyl_face_radii(out_solid)
    print(f"INFO: AFTER refillet any_circle_edges={any_c2} circle_radii_hist={hist2}")
    print(f"INFO: AFTER refillet cylinder_face_radii_hist={cyl2}")
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