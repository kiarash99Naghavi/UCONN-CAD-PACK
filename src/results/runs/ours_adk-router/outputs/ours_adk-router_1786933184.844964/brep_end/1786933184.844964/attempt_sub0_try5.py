def my_cad_function(args):
    import cadquery as cq
    from math import sqrt

    shape = cq.importers.importStep(args["input_file"])
    base = shape.val() if hasattr(shape, "val") else shape
    solids = base.Solids()
    print(f"INPUT: solids={len(solids)} faces={len(base.Faces())} edges={len(base.Edges())}")

    if len(solids) != 1:
        print("ERROR: expected exactly 1 solid; returning input")
        return shape

    solid0 = solids[0]
    bbox_min = (-949.62, -506.698, 26.8)
    bbox_max = (-163.62, -338.409, 595.312)
    print("NAMED bbox min", list(bbox_min))
    print("NAMED bbox max", list(bbox_max))
    print("NAMED r=10 rim arc centers loop:")
    rim_centers = [
        (-226.62, -348.409, 583.515),
        (-173.62, -362.126, 532.32),
        (-173.62, -481.183, 87.995),
        (-226.62, -494.9, 36.8),
        (-886.62, -494.9, 36.8),
        (-939.62, -481.183, 87.995),
        (-939.62, -362.126, 532.32),
        (-886.62, -348.409, 583.515),
    ]
    for p in rim_centers:
        print("  ", list(p))

    # ---- helpers ----
    def vtuple(v):
        return (float(v.x), float(v.y), float(v.z))

    def dist(a, b):
        return sqrt((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2)

    def shared_edges(fa, fb):
        ea = fa.Edges()
        eb = fb.Edges()
        out = []
        for e in ea:
            for ee in eb:
                if e.isSame(ee):
                    out.append(e)
                    break
        # de-dup
        uniq = []
        for e in out:
            if not any(e.isSame(u) for u in uniq):
                uniq.append(e)
        return uniq

    def face_surface_info(face):
        """Return (stype, params) where stype in {PLANE,CYLINDER,TORUS,OTHER}.
        CYLINDER params: (radius, dir_tuple, loc_tuple)
        TORUS params: (majorR, minorR)
        PLANE params: (normal_tuple, point_tuple)
        """
        try:
            from OCP.BRepAdaptor import BRepAdaptor_Surface
            from OCP.GeomAbs import GeomAbs_Plane, GeomAbs_Cylinder, GeomAbs_Torus

            ad = BRepAdaptor_Surface(face.wrapped)
            st = ad.GetType()
            if st == GeomAbs_Plane:
                pl = ad.Plane()
                n = pl.Axis().Direction()
                p = pl.Location()
                return "PLANE", (vtuple(n), vtuple(p))
            if st == GeomAbs_Cylinder:
                cy = ad.Cylinder()
                r = float(cy.Radius())
                d = cy.Axis().Direction()
                p = cy.Axis().Location()
                return "CYLINDER", (r, vtuple(d), vtuple(p))
            if st == GeomAbs_Torus:
                to = ad.Torus()
                return "TORUS", (float(to.MajorRadius()), float(to.MinorRadius()))
            return "OTHER", None
        except Exception as ex:
            print("WARN: face_surface_info failed:", ex)
            return "OTHER", None

    def find_plane_face(solid, normal_target, center_target=None, center_axis=None, tol_n=0.02):
        """Pick the planar face whose normal matches (up to sign?) and optionally closest center."""
        best = None
        best_score = 1e99
        faces = solid.Faces()
        for i, f in enumerate(faces):
            st, prm = face_surface_info(f)
            if st != "PLANE":
                continue
            n = prm[0]
            # compare with target direction (same direction, not opposite)
            dn = abs(n[0] - normal_target[0]) + abs(n[1] - normal_target[1]) + abs(n[2] - normal_target[2])
            if dn > tol_n:
                continue
            c = f.Center()
            ct = (float(c.x), float(c.y), float(c.z))
            score = dn * 1000.0
            if center_target is not None:
                score += dist(ct, center_target)
            if center_axis is not None:
                # weight one axis heavily (e.g. x plane selection)
                axis, val = center_axis
                if axis == "x":
                    score += 10.0 * abs(ct[0] - val)
                elif axis == "y":
                    score += 10.0 * abs(ct[1] - val)
                elif axis == "z":
                    score += 10.0 * abs(ct[2] - val)
            if score < best_score:
                best_score = score
                best = (i, f)
        return best

    # ---- identify the four r=10 cylindrical faces by index (as given) ----
    face_indices_r10 = [36, 38, 40, 42]
    all_faces0 = solid0.Faces()
    r10_faces = []
    for idx in face_indices_r10:
        if idx < 0 or idx >= len(all_faces0):
            print(f"ERROR: face_idx {idx} out of range (0..{len(all_faces0)-1})")
            continue
        f = all_faces0[idx]
        st, prm = face_surface_info(f)
        print(f"RESOLVE face #{idx}: stype={st} center={[round(f.Center().x,3),round(f.Center().y,3),round(f.Center().z,3)]} area={round(f.Area(),3)}")
        if st != "CYLINDER" or prm is None:
            print(f"WARN: face #{idx} is not CYLINDER per adaptor; still using it as fillet-local box anchor")
        r10_faces.append((idx, f, st, prm))

    print(f"SELECTED: {len(r10_faces)} faces as r=10 cylindrical blend candidates idx={face_indices_r10}")

    # ---- select the named planar support faces from ORIGINAL by their indices for halfspaces ----
    # Use direct indices to avoid any selector fragility on original solid.
    f12 = all_faces0[12]
    f9 = all_faces0[9]
    f45 = all_faces0[45]
    f24 = all_faces0[24]
    f43 = all_faces0[43]
    print("RESOLVE plane #12 center", [round(f12.Center().x,3), round(f12.Center().y,3), round(f12.Center().z,3)], "area", round(f12.Area(), 3))
    print("RESOLVE plane #9  center", [round(f9.Center().x,3), round(f9.Center().y,3), round(f9.Center().z,3)], "area", round(f9.Area(), 3))
    print("RESOLVE plane #45 center", [round(f45.Center().x,3), round(f45.Center().y,3), round(f45.Center().z,3)], "area", round(f45.Area(), 3))
    print("RESOLVE plane #24 center", [round(f24.Center().x,3), round(f24.Center().y,3), round(f24.Center().z,3)], "area", round(f24.Area(), 3))
    print("RESOLVE plane #43 center", [round(f43.Center().x,3), round(f43.Center().y,3), round(f43.Center().z,3)], "area", round(f43.Area(), 3))

    solid_center = solid0.Center()
    sc = cq.Vector(solid_center.x, solid_center.y, solid_center.z)
    print("Solid center (used to pick inside halfspaces)", [round(sc.x, 3), round(sc.y, 3), round(sc.z, 3)])

    def make_inside_halfspace(plane_face):
        # pick the side of the plane that contains the solid center
        try:
            hs = cq.Solid.makeHalfSpace(plane_face, sc)
            return hs
        except Exception as ex:
            print("ERROR: makeHalfSpace failed:", ex)
            return None

    hs12 = make_inside_halfspace(f12)
    hs9 = make_inside_halfspace(f9)
    hs45 = make_inside_halfspace(f45)
    hs24 = make_inside_halfspace(f24)
    hs43 = make_inside_halfspace(f43)

    # ---- create local filler volumes near each r=10 cylinder face (simple, local, no global reshape) ----
    filled = solid0
    total_added = None

    def local_box_around_face(face, pad=25.0):
        bb = face.BoundingBox()
        cx = (bb.xmin + bb.xmax) * 0.5
        cy = (bb.ymin + bb.ymax) * 0.5
        cz = (bb.zmin + bb.zmax) * 0.5
        dx = (bb.xmax - bb.xmin) + 2 * pad
        dy = (bb.ymax - bb.ymin) + 2 * pad
        dz = (bb.zmax - bb.zmin) + 2 * pad
        pnt = cq.Vector(cx - dx / 2, cy - dy / 2, cz - dz / 2)
        box = cq.Solid.makeBox(dx, dy, dz, pnt=pnt)
        return box

    def add_filler_for_pair(local_face, hsA, hsB, name):
        nonlocal filled, total_added
        if hsA is None or hsB is None:
            print(f"SKIP filler {name}: missing halfspace")
            return
        try:
            box = local_box_around_face(local_face, pad=30.0)
            wedge = hsA.intersect(hsB)
            candidate = wedge.intersect(box)
            filler = candidate.cut(filled)
            v = filler.Volume() if hasattr(filler, "Volume") else 0.0
            if v <= 1e-6:
                print(f"FILLER {name}: vol ~0 (no material to add)")
                return
            filled2 = filled.fuse(filler)
            print(f"FILLER {name}: added vol={round(v,3)} center={[round(filler.Center().x,3),round(filler.Center().y,3),round(filler.Center().z,3)]}")
            # accumulate added for placement self-check
            total_added = filler if total_added is None else total_added.fuse(filler)
            filled = filled2
        except Exception as ex:
            print(f"FILLER {name}: FAILED:", ex)

    # decide plane pairs based on cylinder axis and position (simple rule)
    for (idx, f, st, prm) in r10_faces:
        c = f.Center()
        axis_dir = None
        if st == "CYLINDER" and prm is not None:
            axis_dir = prm[1]
        # heuristics:
        # - axis nearly X => between plane#12 and either plane#9 (low z) or plane#45 (high z)
        # - otherwise => between plane#12 and either x-max plane#43 (right side) or x-min plane#24 (left side)
        name = f"face#{idx}"
        if axis_dir is not None and abs(axis_dir[0]) > 0.9:
            # X axis
            if c.z < 312.0:
                add_filler_for_pair(f, hs12, hs9, name + ":(12&9)")
            else:
                add_filler_for_pair(f, hs12, hs45, name + ":(12&45)")
        else:
            # tilted axis -> vertical plane pairing
            if c.x > -556.62:
                add_filler_for_pair(f, hs12, hs43, name + ":(12&xmax)")
            else:
                add_filler_for_pair(f, hs12, hs24, name + ":(12&xmin)")

    if total_added is not None:
        bb = total_added.BoundingBox()
        print(
            "PLACEMENT added-total:",
            "vol=",
            round(total_added.Volume(), 3),
            "center=",
            [round(total_added.Center().x, 3), round(total_added.Center().y, 3), round(total_added.Center().z, 3)],
            "bb=",
            [round(bb.xmin, 3), round(bb.ymin, 3), round(bb.zmin, 3), round(bb.xmax, 3), round(bb.ymax, 3), round(bb.zmax, 3)],
        )
    else:
        print("PLACEMENT added-total: ~0 (no filler succeeded)")

    # ---- re-find key planes on the filled solid (face order may change) ----
    n12 = (0.0, -0.966, 0.259)
    n9 = (0.0, 0.259, 0.966)
    n45 = (0.0, -0.259, -0.966)
    nxp = (1.0, 0.0, 0.0)
    nxn = (-1.0, 0.0, 0.0)

    f12_new = find_plane_face(filled, n12, center_target=(-556.62, -431.314, 312.746))
    f9_new = find_plane_face(filled, n9, center_target=(-556.62, -494.124, 39.698))
    f45_new = find_plane_face(filled, n45, center_target=(-556.62, -349.185, 580.617))
    fxmax_new = find_plane_face(filled, nxp, center_axis=("x", -163.62))
    fxmin_new = find_plane_face(filled, nxn, center_axis=("x", -949.62))

    def dbg_face_pick(tag, picked):
        if picked is None:
            print(f"PICK {tag}: NONE")
            return
        i, f = picked
        st, prm = face_surface_info(f)
        print(f"PICK {tag}: face_idx_now={i} stype={st} center={[round(f.Center().x,3),round(f.Center().y,3),round(f.Center().z,3)]} area={round(f.Area(),3)}")

    dbg_face_pick("plane12", f12_new)
    dbg_face_pick("plane9", f9_new)
    dbg_face_pick("plane45", f45_new)
    dbg_face_pick("xmax", fxmax_new)
    dbg_face_pick("xmin", fxmin_new)

    # ---- find newly-created sharp edges between these planes and fillet them to r=2 ----
    edges_to_fillet = []

    def add_shared_edges(pA, pB, purpose):
        nonlocal edges_to_fillet
        if pA is None or pB is None:
            print(f"SELECTED: 0 edges for {purpose} (missing face)")
            return
        fa = pA[1]
        fb = pB[1]
        es = shared_edges(fa, fb)
        # keep straight long edges
        long_lines = []
        for e in es:
            try:
                if e.geomType() != "LINE":
                    continue
                if e.Length() < 50.0:
                    continue
                long_lines.append(e)
            except Exception:
                pass
        print(f"SELECTED: {len(long_lines)} edges for {purpose} (shared plane intersection)")
        edges_to_fillet.extend(long_lines)

    add_shared_edges(f12_new, f9_new, "r=2 rim fillet bottom (plane12-plane9)")
    add_shared_edges(f12_new, f45_new, "r=2 rim fillet top (plane12-plane45)")
    add_shared_edges(f12_new, fxmax_new, "r=2 rim fillet right (plane12-xmax)")
    add_shared_edges(f12_new, fxmin_new, "r=2 rim fillet left (plane12-xmin)")

    # de-duplicate edges
    uniq_edges = []
    for e in edges_to_fillet:
        if not any(e.isSame(u) for u in uniq_edges):
            uniq_edges.append(e)
    edges_to_fillet = uniq_edges
    print(f"SELECTED: {len(edges_to_fillet)} unique edges total for r=2 rim fillet")

    out = filled
    if len(edges_to_fillet) == 0:
        print("ERROR: selected 0 edges for r=2 rim fillet; returning filled solid (non-no-op)")
    else:
        ok = 0
        for k, e in enumerate(edges_to_fillet):
            try:
                out = out.fillet(2.0, edgeList=[e])
                ok += 1
                print(f"Fillet r=2: per-edge OK ({ok}/{len(edges_to_fillet)})")
            except Exception as ex:
                print(f"Fillet r=2: per-edge FAILED ({k+1}/{len(edges_to_fillet)}):", ex)
        print(f"Fillet r=2: successes={ok}/{len(edges_to_fillet)}")

    # ---- delta / bbox self-check ----
    try:
        added = out.cut(solid0)
        removed = solid0.cut(out)
        if added.Volume() > 1e-6:
            bb = added.BoundingBox()
            print(
                "DELTA added:",
                "vol=",
                round(added.Volume(), 3),
                "center=",
                [round(added.Center().x, 3), round(added.Center().y, 3), round(added.Center().z, 3)],
                "bb=",
                [round(bb.xmin, 3), round(bb.ymin, 3), round(bb.zmin, 3), round(bb.xmax, 3), round(bb.ymax, 3), round(bb.zmax, 3)],
            )
        else:
            print("DELTA added: ~0")
        if removed.Volume() > 1e-6:
            bb = removed.BoundingBox()
            print(
                "DELTA removed:",
                "vol=",
                round(removed.Volume(), 3),
                "center=",
                [round(removed.Center().x, 3), round(removed.Center().y, 3), round(removed.Center().z, 3)],
                "bb=",
                [round(bb.xmin, 3), round(bb.ymin, 3), round(bb.zmin, 3), round(bb.xmax, 3), round(bb.ymax, 3), round(bb.zmax, 3)],
            )
        else:
            print("DELTA removed: ~0")
    except Exception as ex:
        print("DELTA isolation: FAILED:", ex)

    bb = out.BoundingBox()
    got_min = (bb.xmin, bb.ymin, bb.zmin)
    got_max = (bb.xmax, bb.ymax, bb.zmax)
    print("RESULT bbox min", [round(x, 6) for x in got_min], "delta", [round(got_min[i] - bbox_min[i], 6) for i in range(3)])
    print("RESULT bbox max", [round(x, 6) for x in got_max], "delta", [round(got_max[i] - bbox_max[i], 6) for i in range(3)])

    # ---- QA: count remaining r=10 cylinders and tori with minor r=10 ----
    cyl10 = []
    tor_minor10 = []
    for i, f in enumerate(out.Faces()):
        st, prm = face_surface_info(f)
        if st == "CYLINDER" and prm is not None:
            r = prm[0]
            if abs(r - 10.0) < 1e-2:
                cyl10.append(i)
        if st == "TORUS" and prm is not None:
            R, r = prm
            if abs(r - 10.0) < 1e-2:
                tor_minor10.append(i)

    print(f"CHECK: cylindrical faces r≈10.0 count={len(cyl10)} idx={cyl10}")
    print(f"CHECK: torus faces minor r≈10.0 count={len(tor_minor10)} idx={tor_minor10}")

    return out