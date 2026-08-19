def my_cad_function(args):
    import cadquery as cq

    # ALWAYS load the input
    shape = cq.importers.importStep(args["input_file"])
    base = shape.val() if hasattr(shape, "val") else shape

    # ---- constants from the prompt (absolute anchors) ----
    rim_r10_arc_centers = [
        (-226.62, -348.409, 583.515),
        (-173.62, -362.126, 532.32),
        (-173.62, -481.183, 87.995),
        (-226.62, -494.9, 36.8),
        (-886.62, -494.9, 36.8),
        (-939.62, -481.183, 87.995),
        (-939.62, -362.126, 532.32),
        (-886.62, -348.409, 583.515),
    ]
    bbox_min = (-949.62, -506.698, 26.8)
    bbox_max = (-163.62, -338.409, 595.312)

    print("ANCHORS: rim r=10 arc centers:", rim_r10_arc_centers)
    print("ANCHORS: expected bbox min", bbox_min)
    print("ANCHORS: expected bbox max", bbox_max)

    # ---- helpers ----
    def v3(t):
        return cq.Vector(float(t[0]), float(t[1]), float(t[2]))

    def dot(a, b):
        return float(a.x * b.x + a.y * b.y + a.z * b.z)

    def pick_planar_face(solid, c_target, n_target, label, c_tol=60.0, dot_min=0.97):
        c_target = v3(c_target)
        n_target = v3(n_target).normalized()
        best = None
        best_score = 1e99
        for f in solid.Faces():
            try:
                if f.geomType() != "PLANE":
                    continue
                c = f.Center()
                n = f.normalAt().normalized()
                d = (c - c_target).Length
                ad = abs(dot(n, n_target))
                if d > c_tol or ad < dot_min:
                    continue
                # score: distance dominates, then angular misalignment
                score = d + (1.0 - ad) * 100.0
                if score < best_score:
                    best = f
                    best_score = score
            except Exception:
                continue
        if best is None:
            print(f"SELECTED: 0 planar faces for {label} (target c={list(c_target)}, n={list(n_target)})")
            return None
        print(
            f"SELECTED: 1 planar face for {label}  area={best.Area():.3f}  c={[round(x,3) for x in best.Center().toTuple()]}  n={[round(x,6) for x in best.normalAt().toTuple()]}"
        )
        return best

    def shared_edges(fa, fb):
        ea = fa.Edges()
        eb = fb.Edges()
        out = []
        for e1 in ea:
            for e2 in eb:
                try:
                    if e1.isSame(e2):
                        out.append(e1)
                        break
                except Exception:
                    pass
        return out

    def face_geom_info(face):
        # For QA prints: detect CYLINDER/TORUS and radii through OCP
        try:
            from OCP.BRep import BRep_Tool
            from OCP.GeomAdaptor import GeomAdaptor_Surface
            from OCP.GeomAbs import GeomAbs_Cylinder, GeomAbs_Torus

            surf = BRep_Tool.Surface_s(face.wrapped)
            ad = GeomAdaptor_Surface(surf)
            st = ad.GetType()
            if st == GeomAbs_Cylinder:
                cyl = ad.Cylinder()
                return ("CYLINDER", float(cyl.Radius()))
            if st == GeomAbs_Torus:
                tor = ad.Torus()
                return ("TORUS", (float(tor.MajorRadius()), float(tor.MinorRadius())))
            return (face.geomType(), None)
        except Exception:
            return (face.geomType(), None)

    # ---- step 1: select the 8 faces to remove (4 r=10 quarter-cyls + 4 r=10 torus corners) by index ----
    # per geometry index:
    # r=10 cylinders face_idx=[36, 38, 40, 42]
    # torus corner faces are #35,#37,#39,#41 (largest torus patches at the rim)
    faces_to_remove_idx = [35, 36, 37, 38, 39, 40, 41, 42]

    all_faces = base.Faces()
    if max(faces_to_remove_idx) >= len(all_faces):
        print(
            "ERROR: face indices out of range for input. Face count=",
            len(all_faces),
            "max idx=",
            max(faces_to_remove_idx),
        )
        return shape

    faces_to_remove = []
    for i in faces_to_remove_idx:
        f = all_faces[i]
        gtype, rad = face_geom_info(f)
        print(
            f"RESOLVED: face #{i} geom={gtype} rad={rad} area={f.Area():.3f} center={[round(x,3) for x in f.Center().toTuple()]}"
        )
        faces_to_remove.append(f)
    print(f"SELECTED: {len(faces_to_remove)} faces for defeaturing (remove r=10 rim family) idx={faces_to_remove_idx}")

    # ---- step 2: defeature (remove selected faces; extend adjacent support faces to sharp intersections) ----
    try:
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Defeaturing

        df = BRepAlgoAPI_Defeaturing(base.wrapped)
        # FIXED API: AddFaceToRemove exists here; AddFace does NOT.
        for f in faces_to_remove:
            df.AddFaceToRemove(f.wrapped)
        df.Build()
        deb_any = cq.Shape.cast(df.Shape())
        deb_solids = deb_any.Solids()
        print(f"Defeaturing: produced shape type={deb_any.ShapeType()} solids={len(deb_solids)} faces={len(deb_any.Faces())}")
        if len(deb_solids) != 1:
            print("ERROR: defeaturing did not produce exactly one solid; returning original.")
            return shape
        deb = deb_solids[0]
    except Exception as ex:
        print("Defeaturing: FAILED:", ex)
        return shape

    # ---- step 3: re-identify support planes by measured center+normal ----
    # (these are unchanged primary surfaces; must not be offset)
    f12 = pick_planar_face(deb, (-556.62, -431.314, 312.746), (-0.0, -0.966, 0.259), "support plane #12")
    f9 = pick_planar_face(deb, (-556.62, -494.124, 39.698), (0.0, 0.259, 0.966), "support plane #9")
    f45 = pick_planar_face(deb, (-556.62, -349.185, 580.617), (-0.0, -0.259, -0.966), "support plane #45")
    f11 = pick_planar_face(deb, (-176.62, -421.655, 310.157), (-1.0, 0.0, -0.0), "support plane #11")
    f33 = pick_planar_face(deb, (-936.62, -421.655, 310.157), (1.0, -0.0, 0.0), "support plane #33")

    if any(f is None for f in [f12, f9, f45, f11, f33]):
        print("ERROR: could not re-identify all support planes after defeaturing. Returning original.")
        return shape

    # ---- step 4: select the sharp intersection edges between the support plane #12 and the four other support planes ----
    e_sel = []
    for fb, name in [(f9, "#12-#9"), (f45, "#12-#45"), (f11, "#12-#11"), (f33, "#12-#33")]:
        es = shared_edges(f12, fb)
        es2 = [e for e in es if e.Length() > 5.0]
        print(f"SELECTED: {len(es2)} edges for sharp rim intersection {name}")
        e_sel.extend(es2)

    # de-duplicate edges by geometric sameness
    uniq = []
    for e in e_sel:
        if not any(e.isSame(u) for u in uniq):
            uniq.append(e)
    e_sel = uniq
    print(f"SELECTED: {len(e_sel)} unique edges for candidate rim blend")

    # proximity gate to the r=10 arc-center loop region (broad, only to avoid unrelated intersections)
    arc_centers_v = [v3(p) for p in rim_r10_arc_centers]
    gate = []
    for e in e_sel:
        c = e.Center()
        dmin = min((c - p).Length for p in arc_centers_v)
        if dmin < 250.0:
            gate.append(e)
    e_sel = gate
    print(f"SELECTED: {len(e_sel)} edges after proximity gate to r=10 arc-center loop (d<250mm)")

    if len(e_sel) == 0:
        print("SELECTED: 0 edges for r=2 rim fillet -> NO-OP would result. Returning original.")
        return shape

    # ---- step 5: apply r=2 fillet on those sharp edges (creates analytic cylinders + torus at corners) ----
    out = deb
    try:
        out = out.fillet(2.0, edgeList=e_sel)
        print(f"Fillet: OK (r=2.0) on {len(e_sel)} edges")
    except Exception as ex:
        print("Fillet: bulk FAILED:", ex)
        # try per-edge to salvage partial success
        ok = 0
        for k, e in enumerate(e_sel):
            try:
                out = out.fillet(2.0, edgeList=[e])
                ok += 1
                print(f"Fillet: per-edge OK ({k+1}/{len(e_sel)})")
            except Exception as ex2:
                print(f"Fillet: per-edge FAILED ({k+1}/{len(e_sel)}):", ex2)
        if ok == 0:
            print("Fillet: no edges succeeded -> returning original")
            return shape

    # ---- placement / delta self-checks ----
    try:
        added = out.cut(base)
        removed = base.cut(out)
        if added.Volume() > 1e-6:
            bb = added.BoundingBox()
            print(
                "DELTA added: vol=",
                round(added.Volume(), 6),
                "center=",
                [round(x, 3) for x in added.Center().toTuple()],
                "bb=",
                [round(bb.xmin, 3), round(bb.ymin, 3), round(bb.zmin, 3), round(bb.xmax, 3), round(bb.ymax, 3), round(bb.zmax, 3)],
            )
        else:
            print("DELTA added: ~0")
        if removed.Volume() > 1e-6:
            bb = removed.BoundingBox()
            print(
                "DELTA removed: vol=",
                round(removed.Volume(), 6),
                "center=",
                [round(x, 3) for x in removed.Center().toTuple()],
                "bb=",
                [round(bb.xmin, 3), round(bb.ymin, 3), round(bb.zmin, 3), round(bb.xmax, 3), round(bb.ymax, 3), round(bb.zmax, 3)],
            )
        else:
            print("DELTA removed: ~0")
    except Exception as ex:
        print("DELTA isolation: FAILED:", ex)

    # bbox verification
    bb = out.BoundingBox()
    got_min = (bb.xmin, bb.ymin, bb.zmin)
    got_max = (bb.xmax, bb.ymax, bb.zmax)
    print("RESULT bbox min", [round(x, 6) for x in got_min], "delta", [round(got_min[i] - bbox_min[i], 6) for i in range(3)])
    print("RESULT bbox max", [round(x, 6) for x in got_max], "delta", [round(got_max[i] - bbox_max[i], 6) for i in range(3)])

    # QA: count remaining r=10 cylinders and r=10-minor tori
    cyl10 = []
    cyl2 = []
    tor_minor10 = []
    tor_minor2 = []
    for i, f in enumerate(out.Faces()):
        ftype, rad = face_geom_info(f)
        if ftype == "CYLINDER" and rad is not None:
            if abs(rad - 10.0) < 1e-2:
                cyl10.append(i)
            if abs(rad - 2.0) < 1e-2:
                cyl2.append(i)
        if ftype == "TORUS" and isinstance(rad, tuple):
            R, r = rad
            if abs(r - 10.0) < 1e-2:
                tor_minor10.append(i)
            if abs(r - 2.0) < 1e-2:
                tor_minor2.append(i)

    print(f"CHECK: cylindrical faces r≈10.0 count={len(cyl10)} idx={cyl10}")
    print(f"CHECK: cylindrical faces r≈2.0  count={len(cyl2)} idx={cyl2}")
    print(f"CHECK: torus faces minor r≈10.0 count={len(tor_minor10)} idx={tor_minor10}")
    print(f"CHECK: torus faces minor r≈2.0  count={len(tor_minor2)} idx={tor_minor2}")

    return out