def my_cad_function(args):
    import cadquery as cq
    from math import sqrt, pi

    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input
    base = shape.val() if hasattr(shape, "val") else shape

    Y_FRONT = 3.175
    EDGE0_R = 15.25
    CHAMFER = 1.0

    print("TARGETS: Y_FRONT=", Y_FRONT, "EDGE0_R=", EDGE0_R, "CHAMFER=", CHAMFER)
    print("Loaded: solids=", len(base.Solids()), "faces=", len(base.Faces()), "edges=", len(base.Edges()))

    # --- OCP helpers ---
    try:
        from OCP.BRepAdaptor import BRepAdaptor_Curve
        from OCP.GeomAbs import GeomAbs_Circle
    except Exception as ex:
        print("OCP adaptor import failed; cannot proceed reliably:", ex)
        raise

    def circle_params(edge):
        """Return (r, C, n) where C is true circle center, n is circle axis direction."""
        try:
            ad = BRepAdaptor_Curve(edge.wrapped)
            if int(ad.GetType()) != int(GeomAbs_Circle):
                return None
            circ = ad.Circle()
            loc = circ.Location()
            axd = circ.Axis().Direction()
            C = cq.Vector(loc.X(), loc.Y(), loc.Z())
            n = cq.Vector(axd.X(), axd.Y(), axd.Z()).normalized()
            r = float(circ.Radius())
            return r, C, n
        except Exception:
            return None

    def solid_has_face(solid, face):
        for f in solid.Faces():
            if f.isSame(face):
                return True
        return False

    def solid_has_edge(solid, edge):
        for e in solid.Edges():
            if e.isSame(edge):
                return True
        return False

    def incident_faces(solid, edge):
        inc = []
        for f in solid.Faces():
            for ee in f.Edges():
                if ee.isSame(edge):
                    inc.append(f)
                    break
        return inc

    # --- resolve required indexed entities and print sanity ---
    faces_all = base.Faces()
    edges_all = base.Edges()

    face496 = faces_all[496]
    print("Resolved face496 center:", face496.Center(), "geom:", face496.geomType(), "area:", face496.Area(), "normalAt:", face496.normalAt())

    # Identify which solid contains the front face #496
    solids = list(base.Solids())
    front_solid_idx = None
    for i, s in enumerate(solids):
        if solid_has_face(s, face496):
            front_solid_idx = i
            break
    if front_solid_idx is None:
        raise ValueError("Could not find a solid containing face #496")

    front_solid = solids[front_solid_idx]
    print("front_solid_idx=", front_solid_idx, "front_solid_vol=", front_solid.Volume(), "bbox=", front_solid.BoundingBox().xmin, front_solid.BoundingBox().ymin, front_solid.BoundingBox().zmin, "..", front_solid.BoundingBox().xmax, front_solid.BoundingBox().ymax, front_solid.BoundingBox().zmax)

    # --- Find the bore-mouth rim edge on the FRONT face (do NOT trust edge index 0 if it lands on other solid) ---
    rim_candidates = []
    for e in front_solid.Edges():
        cp = circle_params(e)
        if not cp:
            continue
        r, C, n = cp
        if abs(C.y - Y_FRONT) > 5e-3:
            continue
        if sqrt(C.x * C.x + C.z * C.z) > 5e-2:
            continue
        if abs(r - EDGE0_R) > 5e-2:
            continue
        inc = incident_faces(front_solid, e)
        if any(f.isSame(face496) for f in inc):
            rim_candidates.append((abs(r - EDGE0_R), e, r, C, n, [f.geomType() for f in inc]))

    print("rim_candidates on front_solid incident to face496:", len(rim_candidates))
    for i, (_, e, r, C, n, ftypes) in enumerate(sorted(rim_candidates, key=lambda t: t[0])[:10]):
        print(f"  cand[{i}] r={r:.6f} C=({C.x:.6f},{C.y:.6f},{C.z:.6f}) n=({n.x:.3f},{n.y:.3f},{n.z:.3f}) faces={ftypes}")

    if not rim_candidates:
        # fallback: try global edge index 0 only if it actually lies on face496
        edge0 = edges_all[0]
        cp0 = circle_params(edge0)
        print("Fallback edge0 circle_params:", cp0)
        if cp0 is None:
            raise ValueError("No circular rim edge found (even fallback edge0 not circular)")
        if not solid_has_edge(front_solid, edge0):
            raise ValueError("No rim edge found on front solid: edge0 not on front_solid")
        inc0 = incident_faces(front_solid, edge0)
        if not any(f.isSame(face496) for f in inc0):
            raise ValueError("No rim edge found on face496: edge0 not incident to face496")
        rim_edge = edge0
        rim_r, rim_C, rim_n = cp0
    else:
        rim_edge = sorted(rim_candidates, key=lambda t: t[0])[0][1]
        rim_r, rim_C, rim_n = circle_params(rim_edge)

    print("Chosen rim edge: r=", rim_r, "C=", (rim_C.x, rim_C.y, rim_C.z), "dy=", rim_C.y - Y_FRONT)

    # --- Remove the existing fillet/blend: defeature non-planar adjacent face(s) around the rim ---
    inc_faces = incident_faces(front_solid, rim_edge)
    print("Incident faces to rim edge:", len(inc_faces), [(f.geomType(), (f.Center().x, f.Center().y, f.Center().z)) for f in inc_faces])

    blend_faces = []
    for f in inc_faces:
        if f.isSame(face496):
            continue
        gt = f.geomType().upper()
        # Treat any non-planar, non-cylinder adjacent face as the blend to remove
        if gt not in ("PLANE", "CYLINDER"):
            blend_faces.append(f)

    print("Blend faces to defeature:", len(blend_faces), [f.geomType() for f in blend_faces])

    healed = front_solid
    defeatured = False
    if blend_faces:
        try:
            from OCP.BRepAlgoAPI import BRepAlgoAPI_Defeaturing
            algo = BRepAlgoAPI_Defeaturing()
            algo.SetShape(front_solid.wrapped)
            for bf in blend_faces:
                algo.AddFace(bf.wrapped)
            algo.Build()
            ok = algo.IsDone() if hasattr(algo, "IsDone") else True
            print("Defeaturing IsDone:", ok)
            if ok:
                res = cq.Shape.cast(algo.Shape())
                res_solids = list(res.Solids())
                print("Defeature result solids:", len(res_solids), "vols:", [s.Volume() for s in res_solids])
                if len(res_solids) == 1:
                    healed = res_solids[0]
                elif len(res_solids) > 1:
                    healed = sorted(res_solids, key=lambda s: s.Volume(), reverse=True)[0]
                defeatured = True
        except Exception as ex:
            print("Defeaturing failed/unavailable; continuing without it:", ex)

    print("defeatured=", defeatured)

    # --- Find (post-heal) front plane on the healed solid (normal +Y at y=Y_FRONT) ---
    healed_front_planes = []
    for f in healed.Faces():
        if f.geomType().upper() != "PLANE":
            continue
        c = f.Center()
        if abs(c.y - Y_FRONT) > 5e-3:
            continue
        nrm = f.normalAt().normalized()
        if nrm.y < 0.9:
            continue
        healed_front_planes.append((f.Area(), f))

    print("healed_front_planes candidates:", len(healed_front_planes))
    if not healed_front_planes:
        raise ValueError("Could not find front planar face on healed solid")

    healed_front_face = sorted(healed_front_planes, key=lambda t: t[0], reverse=True)[0][1]
    print("Chosen healed_front_face center:", healed_front_face.Center(), "area:", healed_front_face.Area(), "normal:", healed_front_face.normalAt())

    # --- Find the rim edge again (sharp edge) on the healed solid, then apply chamfer by boolean cone-ring cut ---
    sharp_candidates = []
    for e in healed.Edges():
        cp = circle_params(e)
        if not cp:
            continue
        r, C, n = cp
        if abs(C.y - Y_FRONT) > 5e-3:
            continue
        if sqrt(C.x * C.x + C.z * C.z) > 5e-2:
            continue
        inc = incident_faces(healed, e)
        if not any(f.isSame(healed_front_face) for f in inc):
            continue
        # require adjacency to a y-axis cylinder to ensure it's the bore rim
        ok_cyl = False
        for f in inc:
            if f.geomType().upper() == "CYLINDER":
                # quick axis test: sample two normals around; or just accept cylinder here
                ok_cyl = True
        if not ok_cyl:
            continue
        sharp_candidates.append((abs(r - EDGE0_R), e, r, C, n, [f.geomType() for f in inc]))

    print("sharp rim candidates (post-heal):", len(sharp_candidates))
    for i, (_, e, r, C, n, ftypes) in enumerate(sorted(sharp_candidates, key=lambda t: t[0])[:10]):
        print(f"  sharp[{i}] r={r:.6f} C=({C.x:.6f},{C.y:.6f},{C.z:.6f}) faces={ftypes}")

    if not sharp_candidates:
        # If defeaturing didn't happen or didn't create cylinder adjacency, still proceed using the original rim edge on healed solid
        print("WARNING: No sharp rim candidate found; falling back to first circle at center on front")
        fallback = []
        for e in healed.Edges():
            cp = circle_params(e)
            if not cp:
                continue
            r, C, n = cp
            if abs(C.y - Y_FRONT) < 5e-3 and sqrt(C.x * C.x + C.z * C.z) < 5e-2:
                fallback.append((abs(r - EDGE0_R), e, r, C, n))
        if not fallback:
            raise ValueError("No usable front center circle edge exists for chamfer")
        chamfer_edge = sorted(fallback, key=lambda t: t[0])[0][1]
        r0, C0, n0 = circle_params(chamfer_edge)
    else:
        chamfer_edge = sorted(sharp_candidates, key=lambda t: t[0])[0][1]
        r0, C0, n0 = circle_params(chamfer_edge)

    print("Chamfer target edge: r=", r0, "C=", (C0.x, C0.y, C0.z), "axis n=", (n0.x, n0.y, n0.z))

    # Determine whether this is a hole entrance or a boss rim, and the "into material" direction
    pm = chamfer_edge.positionAt(0.5)
    radial = (pm - C0).normalized()

    def probe(d, rr):
        # d is +/- n0; rr is radial distance from center in the circle plane
        p = C0 + d * (CHAMFER * 0.5) + radial * rr
        return healed.isInside(p)

    into = n0 if probe(n0, r0 - 0.05) else (-n0 if probe(-n0, r0 - 0.05) else None)
    if into is None:
        # hole entrance case (material outside r)
        into = n0 if probe(n0, r0 + 0.05) else -n0

    boss = not probe(into, r0 + 0.05)
    print("Chamfer orientation: into=", (into.x, into.y, into.z), "boss=", boss)

    # Build chamfer cutter by boolean (avoid OCC chamfer() refusal)
    c = CHAMFER
    if boss:
        # convex rim: remove wedge between outer cylinder and narrowing cone
        cutter = cq.Solid.makeCylinder(r0 + 0.2, c, C0, into).cut(
            cq.Solid.makeCone(r0 - c, r0, c, pnt=C0, dir=into)
        )
    else:
        # hole entrance: conical ring (opening becomes larger at the surface)
        cutter = cq.Solid.makeCone(r0 + c, r0, c, pnt=C0, dir=into).cut(
            cq.Solid.makeCylinder(r0, c + 0.1, C0 - into * 0.05, into)
        )

    before = healed
    after = before.cut(cutter)

    # Locality / removal checks
    removed = before.cut(after)
    try:
        rv = removed.Volume()
        rc = removed.Center()
        print("Removed vol:", rv, "removed center:", (rc.x, rc.y, rc.z))
        # rough expected volume for hole chamfer: V ~= (c^2/2) * 2*pi*(r + c/3)
        exp = (c * c / 2.0) * (2.0 * pi * (r0 + c / 3.0))
        print("Expected wedge vol (rough):", exp, "delta:", rv - exp)
        print("Removed center dy to Y_FRONT:", rc.y - Y_FRONT)
    except Exception as ex:
        print("Removal measurement failed (non-fatal):", ex)

    # Rebuild compound with untouched other solids
    out_solids = []
    for i, s in enumerate(solids):
        out_solids.append(after if i == front_solid_idx else s)

    if len(out_solids) == 1:
        out = out_solids[0]
    else:
        out = cq.Compound.makeCompound(out_solids)

    return out