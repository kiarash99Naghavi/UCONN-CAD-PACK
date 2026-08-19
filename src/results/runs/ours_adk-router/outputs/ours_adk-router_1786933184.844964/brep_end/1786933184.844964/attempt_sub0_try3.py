def my_cad_function(args):
    import cadquery as cq
    from math import isfinite

    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input
    base = shape.val() if hasattr(shape, "val") else shape

    sols = base.Solids()
    print(f"INFO: input solids={len(sols)}")
    if len(sols) != 1:
        print("ERROR: expected 1 solid; returning input")
        return shape
    base_solid = sols[0]

    # ------------------------ numbers from the prompt ------------------------
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

    def edge_circle_radius(edge: cq.Edge):
        try:
            from OCP.BRepAdaptor import BRepAdaptor_Curve
            from OCP.GeomAbs import GeomAbs_Circle
            ad = BRepAdaptor_Curve(edge.wrapped)
            if ad.GetType() == GeomAbs_Circle:
                circ = ad.Circle()
                return float(circ.Radius())
        except Exception:
            return None
        return None

    def count_edge_circle_radii(solid: cq.Solid):
        hist = {}
        any_c = 0
        for e in solid.Edges():
            r = edge_circle_radius(e)
            if r is None or (not isfinite(r)):
                continue
            any_c += 1
            rk = round(r, 3)
            hist[rk] = hist.get(rk, 0) + 1
        return any_c, dict(sorted(hist.items(), key=lambda kv: (-kv[0], kv[1])))

    def bbox_tuple(bb):
        return {
            "min": (float(bb.xmin), float(bb.ymin), float(bb.zmin)),
            "max": (float(bb.xmax), float(bb.ymax), float(bb.zmax)),
            "size": (float(bb.xlen), float(bb.ylen), float(bb.zlen)),
        }

    def edge_hash(e: cq.Edge):
        try:
            return int(e.wrapped.HashCode(2147483647))
        except Exception:
            try:
                return int(e.hashCode())
            except Exception:
                return None

    def dist_edge_point(edge: cq.Edge, p: cq.Vector):
        # robust OCC distance
        try:
            from OCP.gp import gp_Pnt
            from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeVertex
            from OCP.BRepExtrema import BRepExtrema_DistShapeShape
            vtx = BRepBuilderAPI_MakeVertex(gp_Pnt(float(p.x), float(p.y), float(p.z))).Vertex()
            dss = BRepExtrema_DistShapeShape(edge.wrapped, vtx)
            dss.Perform()
            if hasattr(dss, "IsDone") and not dss.IsDone():
                return float("inf")
            return float(dss.Value())
        except Exception:
            # fallback: center distance (weak)
            c = edge.Center()
            return (c.sub(p)).Length

    # ------------------------ baseline diagnostics ------------------------
    any_c0, hist0 = count_edge_circle_radii(base_solid)
    bb0 = base_solid.BoundingBox()
    vol0 = base_solid.Volume()
    print(f"INFO: BEFORE volume={vol0:.4f} mm^3")
    print(f"INFO: BEFORE bbox={bbox_tuple(bb0)}")
    print(f"INFO: BEFORE any_circle_edges={any_c0} circle_radii_hist={hist0}")

    # ------------------------ resolve and print the target faces/edges (index sanity) ------------------------
    faces = base_solid.Faces()
    edges = base_solid.Edges()
    print(f"INFO: base_solid faces={len(faces)} edges={len(edges)}")

    remove_faces = []
    for fi in remove_face_idx:
        if fi < 0 or fi >= len(faces):
            print(f"ERROR: face idx {fi} out of range")
            continue
        f = faces[fi]
        try:
            print(f"SELECTED: 1 face for r10-blend removal fi={fi} geom={f.geomType()} area={f.Area():.3f} center={_v3(f.Center())}")
        except Exception as exc:
            print(f"SELECTED: 1 face for r10-blend removal fi={fi} (print failed: {exc})")
        remove_faces.append(f)
    print(f"SELECTED: {len(remove_faces)} faces total for r10-blend removal idx={remove_face_idx}")
    if len(remove_faces) != len(remove_face_idx):
        print("ERROR: did not resolve all required remove faces; returning input")
        return shape

    r10_edges = []
    probe_pts = []
    for ei in r10_edge_idx:
        if ei < 0 or ei >= len(edges):
            print(f"ERROR: edge idx {ei} out of range")
            continue
        e = edges[ei]
        r = edge_circle_radius(e)
        print(f"SELECTED: 1 edge for r10 reference ei={ei} len={e.Length():.3f} center={_v3(e.Center())} r={(r if r is not None else float('nan')):.3f}")
        r10_edges.append(e)
        try:
            sp = e.startPoint()
            ep = e.endPoint()
            probe_pts.append(cq.Vector(sp.x, sp.y, sp.z))
            probe_pts.append(cq.Vector(ep.x, ep.y, ep.z))
        except Exception as exc:
            print(f"WARNING: could not read endpoints for edge ei={ei}: {exc}")
    print(f"SELECTED: {len(r10_edges)} edges total for r10 reference idx={r10_edge_idx}")
    print(f"INFO: probe_pts from r10 arc endpoints count={len(probe_pts)}")

    # ------------------------ defeature: remove the r10 blend faces only ------------------------
    df_shape = None
    try:
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Defeaturing
        from OCP.TopTools import TopTools_ListOfShape

        df = BRepAlgoAPI_Defeaturing()
        # set input
        set_ok = False
        for m in ("SetShape", "SetInShape"):
            if hasattr(df, m):
                try:
                    getattr(df, m)(base_solid.wrapped)
                    print(f"INFO: defeaturing used {m}()")
                    set_ok = True
                    break
                except Exception as exc:
                    print(f"WARNING: defeaturing {m}() failed: {exc}")
        if not set_ok:
            print("ERROR: defeaturing has no usable SetShape/SetInShape; returning input")
            return shape

        faces_wrapped = [f.wrapped for f in remove_faces]
        added = False
        if hasattr(df, "AddFacesToRemove"):
            try:
                lst = TopTools_ListOfShape()
                for fw in faces_wrapped:
                    lst.Append(fw)
                df.AddFacesToRemove(lst)
                print(f"INFO: defeaturing added {len(faces_wrapped)} faces via AddFacesToRemove(list)")
                added = True
            except Exception as exc:
                print(f"WARNING: AddFacesToRemove failed: {exc}")
        if (not added) and hasattr(df, "AddFaceToRemove"):
            try:
                for fw in faces_wrapped:
                    df.AddFaceToRemove(fw)
                print(f"INFO: defeaturing added {len(faces_wrapped)} faces via AddFaceToRemove(each)")
                added = True
            except Exception as exc:
                print(f"WARNING: AddFaceToRemove failed: {exc}")
        if not added:
            print("ERROR: defeaturing could not add faces to remove; returning input")
            return shape

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
            print("ERROR: defeaturing could not be executed; returning input")
            return shape

        done = True
        try:
            done = bool(df.IsDone()) if hasattr(df, "IsDone") else True
        except Exception:
            done = True
        print(f"INFO: defeaturing IsDone={done}")
        if not done:
            print("ERROR: defeaturing reported not done; returning input")
            return shape

        df_shape = cq.Shape.cast(df.Shape())
    except Exception as exc:
        print(f"ERROR: defeaturing failed to run: {exc}")
        return shape

    df_sols = df_shape.Solids()
    print(f"INFO: defeature output solids={len(df_sols)}")
    if len(df_sols) == 0:
        print("ERROR: defeaturing produced no solids; returning input")
        return shape
    if len(df_sols) != 1:
        print(f"WARNING: defeaturing produced {len(df_sols)} solids; using first")
    df_solid = df_sols[0]

    any_c_df, hist_df = count_edge_circle_radii(df_solid)
    bb_df = df_solid.BoundingBox()
    vol_df = df_solid.Volume()
    print(f"INFO: AFTER defeature volume={vol_df:.4f} mm^3  (delta={vol_df-vol0:+.4f})")
    print(f"INFO: AFTER defeature bbox={bbox_tuple(bb_df)}")
    print(f"INFO: AFTER defeature any_circle_edges={any_c_df} circle_radii_hist={hist_df}")

    # ------------------------ select the NEW sharp edges to fillet (r=2) ------------------------
    # Use distance from the defeatured edges to the ENDPOINTS of the old r10 arc edges.
    tol = 0.50  # mm
    min_len = 50.0  # mm (avoid the short ~18mm edges that caused the prior global change)
    cand = []
    df_edges = df_solid.Edges()
    for k, e in enumerate(df_edges):
        try:
            L = float(e.Length())
        except Exception:
            continue
        if L < min_len:
            continue
        # compute hits/minDist to probe points
        md = float("inf")
        hits = 0
        for p in probe_pts:
            d = dist_edge_point(e, p)
            if d < md:
                md = d
            if d <= tol:
                hits += 1
        if hits > 0:
            cand.append((md, -hits, -L, k, e))

    cand.sort(key=lambda t: (t[0], t[1], t[2]))

    sel_edges = []
    sel_hash = set()
    for md, nh, nL, k, e in cand:
        h = edge_hash(e)
        if h is not None and h in sel_hash:
            continue
        # Avoid accidentally picking existing circle edges of very small radius (r~2 blends) by requiring proximity hits only.
        # Also allow curved path edges (could be r50/r63 in-plane), but do not select very small circular edges.
        cr = edge_circle_radius(e)
        if cr is not None and isfinite(cr) and cr < 5.0:
            continue
        if h is not None:
            sel_hash.add(h)
        sel_edges.append(e)

    print(f"SELECTED: {len(sel_edges)} edges by distance-to-old-r10-endpoints for new r={R_NEW} fillet (tol={tol}mm, min_len={min_len}mm)")
    for i, e in enumerate(sel_edges[:40]):
        cr = edge_circle_radius(e)
        crmsg = f" circle_r={cr:.3f}" if (cr is not None and isfinite(cr)) else ""
        print(f"  EDGE_SEL[{i}]: geom={e.geomType()} len={e.Length():.3f} center={_v3(e.Center())}{crmsg} hash={edge_hash(e)}")
    if len(sel_edges) == 0:
        print("WARNING: distance-based selection got 0 edges; FALLBACK to nearest-edge per probe point with length filter")
        wp_df = cq.Workplane(obj=df_solid)
        for i, p in enumerate(probe_pts):
            ev = wp_df.edges(cq.selectors.NearestToPointSelector(p.toTuple())).vals()
            print(f"SELECTED: {len(ev)} edges nearest to probe_pt[{i}]={_v3(p)} (fallback)")
            if not ev:
                continue
            e = ev[0]
            if e.Length() < min_len:
                continue
            h = edge_hash(e)
            if h is not None and h in sel_hash:
                continue
            cr = edge_circle_radius(e)
            if cr is not None and isfinite(cr) and cr < 5.0:
                continue
            if h is not None:
                sel_hash.add(h)
            sel_edges.append(e)
        print(f"SELECTED: {len(sel_edges)} edges after fallback")
        if len(sel_edges) == 0:
            print("ERROR: selected 0 edges for refillet; returning defeatured solid (r10 removed but r2 not applied)")
            return df_solid

    # ------------------------ apply the new r=2 fillet ------------------------
    try:
        out_solid = df_solid.fillet(R_NEW, sel_edges)
        print(f"INFO: fillet applied with r={R_NEW} on {len(sel_edges)} edges")
    except Exception as exc:
        print(f"ERROR: CadQuery fillet failed: {exc}")
        print("ERROR: returning defeatured solid (no r2 reblend)")
        return df_solid

    # ------------------------ verification / self-check ------------------------
    any_c2, hist2 = count_edge_circle_radii(out_solid)
    bb2 = out_solid.BoundingBox()
    vol2 = out_solid.Volume()

    def bb_delta(a, b):
        return {
            "dxmin": float(b.xmin - a.xmin),
            "dymin": float(b.ymin - a.ymin),
            "dzmin": float(b.zmin - a.zmin),
            "dxmax": float(b.xmax - a.xmax),
            "dymax": float(b.ymax - a.ymax),
            "dzmax": float(b.zmax - a.zmax),
        }

    print(f"INFO: AFTER refillet volume={vol2:.4f} mm^3  (delta vs BEFORE={vol2-vol0:+.4f})")
    print(f"INFO: AFTER refillet bbox={bbox_tuple(bb2)}")
    print(f"CHECK: bbox delta (after - before) = {bb_delta(bb0, bb2)}  <-- should be ~0 for a pure blend replacement")
    print(f"INFO: AFTER refillet any_circle_edges={any_c2} circle_radii_hist={hist2}")
    print("EXPECTATION: r=10 circle-edge family should be gone; r=2 should increase; r=50 and r=63 groups should remain present.")

    # Probe nearest circular edge to the original r10 edge centers and report radius
    wp_out = cq.Workplane(obj=out_solid)
    for i, e0 in enumerate(r10_edges):
        p = e0.Center()
        ev = wp_out.edges(cq.selectors.NearestToPointSelector(p.toTuple())).vals()
        if not ev:
            print(f"CHECK: old_r10_center_probe[{i}] no nearest edge")
            continue
        e = ev[0]
        r = edge_circle_radius(e)
        r_msg = f"r={r:.3f}" if (r is not None and isfinite(r)) else "(not a circle)"
        print(f"CHECK: nearest edge to old_r10_edge_center[{i}] -> {r_msg} edge_geom={e.geomType()} len={e.Length():.3f} edge_center={_v3(e.Center())}")

    return out_solid