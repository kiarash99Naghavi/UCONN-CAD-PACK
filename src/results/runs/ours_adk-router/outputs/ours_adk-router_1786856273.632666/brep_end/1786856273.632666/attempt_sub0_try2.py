def my_cad_function(args):
    import cadquery as cq
    from math import sqrt

    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input
    base = shape.val() if hasattr(shape, "val") else shape

    # --- constants from the prompt (self-check numbers) ---
    Y_FRONT = 3.175
    EDGE0_R = 15.25
    CYL2_R = 15.75
    CHAMFER = 1.0
    print("TARGETS: Y_FRONT=", Y_FRONT, "EDGE0_R=", EDGE0_R, "CYL2_R=", CYL2_R, "CHAMFER=", CHAMFER)

    faces_all = base.Faces()
    edges_all = base.Edges()
    solids_all = base.Solids()
    print("Loaded: solids=", len(solids_all), "faces=", len(faces_all), "edges=", len(edges_all))

    # Resolve indexed entities from the provided geometry index
    edge0 = edges_all[0]
    face496 = faces_all[496]
    face2 = faces_all[2]
    print("Resolved face496 center:", face496.Center(), "geom:", face496.geomType())
    print("Resolved face2   center:", face2.Center(), "geom:", face2.geomType())
    print("Resolved edge0   center (COM):", edge0.Center(), "geom:", edge0.geomType())

    # Helper: circle params from an edge
    def circle_params(edge):
        try:
            from OCP.BRepAdaptor import BRepAdaptor_Curve
            from OCP.GeomAbs import GeomAbs_Circle
            ad = BRepAdaptor_Curve(edge.wrapped)
            if ad.GetType() != GeomAbs_Circle:
                return None
            c = ad.Circle()
            loc = c.Location()
            return (c.Radius(), (loc.X(), loc.Y(), loc.Z()))
        except Exception as ex:
            print("circle_params failed:", ex)
            return None

    cp0 = circle_params(edge0)
    if cp0:
        r0, c0 = cp0
        print("edge0 circle radius=", r0, "circle_center=", c0, "(dy to Y_FRONT)", c0[1] - Y_FRONT)
    else:
        print("edge0 is not a circle per adaptor (geomType was)", edge0.geomType())

    # Find which solid contains edge0 (file has 2 solids)
    solid_idx = None
    for i, s in enumerate(solids_all):
        found = False
        for e in s.Edges():
            if e.isSame(edge0):
                solid_idx = i
                found = True
                break
        if found:
            break
    print("edge0 belongs to solid_idx=", solid_idx)
    if solid_idx is None:
        # Fallback: operate on entire base
        target_solid = base
        other_solids = []
    else:
        target_solid = solids_all[solid_idx]
        other_solids = [s for j, s in enumerate(solids_all) if j != solid_idx]

    # Build adjacency: faces in target_solid incident to edge0
    adj_faces = []
    for f in target_solid.Faces():
        for e in f.Edges():
            if e.isSame(edge0):
                adj_faces.append(f)
                break
    print("Adjacent faces to edge0 in target solid:", len(adj_faces), [f.geomType() for f in adj_faces])
    for k, f in enumerate(adj_faces[:10]):
        c = f.Center()
        print(f"  adj_face[{k}] geom={f.geomType()} center={c}")

    # Identify blend faces to remove (typically torus/bspline) strictly from adjacency to edge0
    blend_faces = []
    for f in adj_faces:
        gt = f.geomType().upper()
        if gt in ("TORUS", "BSPLINE", "BEZIER", "REVOLUTION"):
            c = f.Center()
            # keep it local to the bore mouth region
            if abs(c.y - Y_FRONT) < 2.0 and sqrt(c.x * c.x + c.z * c.z) < 25.0:
                blend_faces.append(f)

    print("Candidate blend faces to defeature:", len(blend_faces), [f.geomType() for f in blend_faces])

    edited_shape = target_solid

    # Remove the fillet/blend by defeaturing (heal)
    defeatured = False
    try:
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Defeaturing
        algo = BRepAlgoAPI_Defeaturing()
        algo.SetShape(target_solid.wrapped)
        for bf in blend_faces:
            algo.AddFace(bf.wrapped)
        algo.Build()
        ok = True
        if hasattr(algo, "IsDone"):
            ok = algo.IsDone()
        print("Defeaturing IsDone:", ok)
        if ok and len(blend_faces) > 0:
            res = cq.Shape.cast(algo.Shape())
            # ensure we have a single solid
            res_solids = res.Solids()
            if len(res_solids) == 1:
                edited_shape = res_solids[0]
            else:
                edited_shape = res
            defeatured = True
        else:
            print("Defeaturing skipped (no blend faces found or not done)")
    except Exception as ex:
        print("Defeaturing failed/unavailable, continuing without it:", ex)

    print("defeatured=", defeatured)

    # Find the *front-side* circular rim edge to chamfer: near origin, circle center y=Y_FRONT,
    # and adjacent to a +Y plane (front face) and a Y-axis cylinder.
    try:
        from OCP.BRepAdaptor import BRepAdaptor_Surface
        from OCP.GeomAbs import GeomAbs_Plane, GeomAbs_Cylinder
    except Exception as ex:
        BRepAdaptor_Surface = None
        GeomAbs_Plane = None
        GeomAbs_Cylinder = None
        print("Surface adaptor import failed (will use weaker filtering):", ex)

    def face_is_front_plane(face):
        if BRepAdaptor_Surface is None:
            c = face.Center()
            return face.geomType().upper() == "PLANE" and abs(c.y - Y_FRONT) < 1e-2
        srf = BRepAdaptor_Surface(face.wrapped)
        if srf.GetType() != GeomAbs_Plane:
            return False
        pln = srf.Plane()
        d = pln.Axis().Direction()
        # normal should be +Y
        return d.Y() > 0.9 and abs(face.Center().y - Y_FRONT) < 1e-2

    def face_is_y_cylinder(face):
        if BRepAdaptor_Surface is None:
            return face.geomType().upper() == "CYLINDER"
        srf = BRepAdaptor_Surface(face.wrapped)
        if srf.GetType() != GeomAbs_Cylinder:
            return False
        cyl = srf.Cylinder()
        d = cyl.Axis().Direction()
        return abs(d.Y()) > 0.9

    # adjacency within edited_shape
    edited_faces = edited_shape.Faces()
    edited_edges = edited_shape.Edges()
    
    def incident_faces(edge):
        out = []
        for f in edited_faces:
            for e in f.Edges():
                if e.isSame(edge):
                    out.append(f)
                    break
        return out

    candidates = []
    for e in edited_edges:
        cp = circle_params(e)
        if not cp:
            continue
        r, cc = cp
        # front-side only by true circle center y
        if abs(cc[1] - Y_FRONT) > 1e-2:
            continue
        # center around origin
        if sqrt(cc[0] * cc[0] + cc[2] * cc[2]) > 1e-2:
            continue
        # must be near the expected rim radius (post-heal likely ~CYL2_R)
        if abs(r - CYL2_R) > 1.2 and abs(r - EDGE0_R) > 1.2:
            continue

        inc = incident_faces(e)
        if len(inc) != 2:
            continue
        ok_plane = any(face_is_front_plane(f) for f in inc)
        ok_cyl = any(face_is_y_cylinder(f) for f in inc)
        if not (ok_plane and ok_cyl):
            continue

        score = abs(r - CYL2_R)
        candidates.append((score, e, r, cc, [f.geomType() for f in inc]))

    print("Front rim chamfer candidates:", len(candidates))
    for i, (score, e, r, cc, ftypes) in enumerate(sorted(candidates, key=lambda t: t[0])[:10]):
        print(f"  cand[{i}] r={r:.4f} center={cc} score|r-15.75|={score:.4f} faces={ftypes}")

    if not candidates:
        # As a last-resort, try using edge0 itself if it is on front side.
        cp = circle_params(edge0)
        if cp:
            r, cc = cp
            if abs(cc[1] - Y_FRONT) < 1e-2:
                print("No healed rim candidates found; falling back to chamfering edge0 directly")
                chosen_edge = edge0
            else:
                raise ValueError("No suitable front-side circular rim edge found to chamfer")
        else:
            raise ValueError("No suitable front-side circular rim edge found to chamfer")
    else:
        chosen_edge = sorted(candidates, key=lambda t: t[0])[0][1]

    cp_ch = circle_params(chosen_edge)
    if cp_ch:
        rch, cch = cp_ch
        print("Chosen edge for chamfer: r=", rch, "circle_center=", cch, "dy=", cch[1] - Y_FRONT)

    # Apply 1mm chamfer on the chosen front rim edge only
    wp = cq.Workplane(obj=edited_shape).newObject([chosen_edge])
    chamfered = wp.chamfer(CHAMFER).val()

    # Rebuild compound with the untouched other solids
    if other_solids:
        out = cq.Compound.makeCompound([chamfered] + other_solids)
    else:
        out = chamfered

    # Placement / locality check: report what we added/removed is only local by comparing before/after
    try:
        removed = target_solid.cut(chamfered)
        added = chamfered.cut(target_solid)
        if hasattr(removed, "Volume"):
            print("Removed vol:", removed.Volume(), "center:", removed.Center())
        if hasattr(added, "Volume"):
            print("Added   vol:", added.Volume(), "center:", added.Center())
        if hasattr(added, "BoundingBox"):
            bb = added.BoundingBox()
            print("Added bbox:", (bb.xmin, bb.ymin, bb.zmin), (bb.xmax, bb.ymax, bb.zmax))
            print("Added bbox dy_to_front:", bb.ymax - Y_FRONT)
    except Exception as ex:
        print("Delta measurement failed (non-fatal):", ex)

    return out