def my_cad_function(args):
    import cadquery as cq
    import math

    shape = cq.importers.importStep(args["input_file"])
    base = shape.val() if hasattr(shape, "val") else shape
    solid0 = base.Solids()[0]

    print("INPUT: solids=", len(base.Solids()), "faces=", len(base.Faces()), "edges=", len(base.Edges()))

    # ---- Named numbers (from prompt) ----
    bbox_min = (-949.62, -506.698, 26.8)
    bbox_max = (-163.62, -338.409, 595.312)
    rim_arc_centers = [
        (-226.62, -348.409, 583.515),
        (-173.62, -362.126, 532.32),
        (-173.62, -481.183, 87.995),
        (-226.62, -494.9, 36.8),
        (-886.62, -494.9, 36.8),
        (-939.62, -481.183, 87.995),
        (-939.62, -362.126, 532.32),
        (-886.62, -348.409, 583.515),
    ]
    print("NAMED bbox min", list(bbox_min))
    print("NAMED bbox max", list(bbox_max))
    print("NAMED r=10 rim arc centers loop:")
    for p in rim_arc_centers:
        print("  ", list(p))

    # target r=10 cylindrical blend faces (from geometry index)
    r10_cyl_face_idx = [36, 38, 40, 42]
    # target r=10 minor torus corner faces (from geometry index other-faces list and prior check)
    r10_tor_face_idx = [35, 37, 39, 41]

    def v3(t):
        return cq.Vector(float(t[0]), float(t[1]), float(t[2]))

    def is_inside(shp, pt, tol=1e-6):
        # pt: cq.Vector
        try:
            return bool(shp.isInside(pt, tol))
        except Exception:
            try:
                return bool(cq.Shape.cast(shp.wrapped).isInside(pt, tol))
            except Exception:
                # last resort: assume point is not inside
                return False

    def orth_xdir(n: cq.Vector) -> cq.Vector:
        n = n.normalized()
        ref = cq.Vector(0, 0, 1) if abs(n.z) < 0.9 else cq.Vector(1, 0, 0)
        xdir = ref.cross(n)
        if xdir.Length < 1e-9:
            ref = cq.Vector(0, 1, 0)
            xdir = ref.cross(n)
        return xdir.normalized()

    def halfspace_slab_from_plane_face(plane_face, inside_dir: cq.Vector, big=12000.0, depth=12000.0):
        # Build a big finite slab approximating the halfspace on the inside side of the plane.
        origin = plane_face.Center()
        n = inside_dir.normalized()
        xdir = orth_xdir(n)
        pln = cq.Plane(origin=(origin.x, origin.y, origin.z), xDir=(xdir.x, xdir.y, xdir.z), normal=(n.x, n.y, n.z))
        slab = cq.Workplane(pln).rect(big, big).extrude(depth).val()
        return slab

    def expanded_box_from_bb(bb, margin=5.0):
        xmin, ymin, zmin = bb.xmin - margin, bb.ymin - margin, bb.zmin - margin
        xmax, ymax, zmax = bb.xmax + margin, bb.ymax + margin, bb.zmax + margin
        dx, dy, dz = xmax - xmin, ymax - ymin, zmax - zmin
        return cq.Solid.makeBox(dx, dy, dz, pnt=cq.Vector(xmin, ymin, zmin))

    def adjacent_faces(face, all_faces):
        # Find faces that share at least one edge with 'face'
        adj = []
        fedges = face.Edges()
        for i, f in enumerate(all_faces):
            if f.isSame(face):
                continue
            hit = False
            for e in fedges:
                for e2 in f.Edges():
                    if e2.isSame(e):
                        hit = True
                        break
                if hit:
                    break
            if hit:
                adj.append((i, f))
        return adj

    # Resolve and print the target r=10 faces
    faces0 = base.Faces()

    r10_cyl_faces = []
    for idx in r10_cyl_face_idx:
        if idx >= len(faces0):
            print(f"RESOLVE r10 cyl face #{idx}: OUT OF RANGE")
            continue
        f = faces0[idx]
        print(
            f"RESOLVE r10 cyl face #{idx}: geomType={f.geomType()} center={[round(f.Center().x,3),round(f.Center().y,3),round(f.Center().z,3)]} area={round(f.Area(),3)}"
        )
        r10_cyl_faces.append((idx, f))
    print(f"SELECTED: {len(r10_cyl_faces)} faces as r=10 cylindrical blend candidates idx={r10_cyl_face_idx}")

    r10_tor_faces = []
    for idx in r10_tor_face_idx:
        if idx >= len(faces0):
            print(f"RESOLVE r10 torus face #{idx}: OUT OF RANGE")
            continue
        f = faces0[idx]
        print(
            f"RESOLVE r10 torus face #{idx}: geomType={f.geomType()} center={[round(f.Center().x,3),round(f.Center().y,3),round(f.Center().z,3)]} area={round(f.Area(),3)}"
        )
        r10_tor_faces.append((idx, f))
    print(f"SELECTED: {len(r10_tor_faces)} faces as r=10 torus corner candidates idx={r10_tor_face_idx}")

    # ---- Build filler solids to bury the r=10 blend faces (add back material) ----
    fillers = []
    all_faces0 = faces0

    def plane_inside_dir(plane_face):
        # Determine direction from plane into material by probing
        c = plane_face.Center()
        n = plane_face.normalAt().normalized()
        eps = 0.25
        # Prefer into = -outward if that is inside
        if is_inside(solid0, c - n * eps):
            return (-n).normalized(), True
        if is_inside(solid0, c + n * eps):
            return (n).normalized(), False
        # fallback: assume -n
        return (-n).normalized(), None

    def make_filler_for_target_face(target_face, need_planes=2):
        # Use adjacent planar faces as bounding planes for wedge
        adj = adjacent_faces(target_face, all_faces0)
        planar_adj = [(i, f) for (i, f) in adj if f.geomType() == "PLANE"]
        print(f"ADJ: target geomType={target_face.geomType()} -> adjacent planar faces count={len(planar_adj)} idx={[i for i,_ in planar_adj]}")
        if len(planar_adj) < need_planes:
            return None
        # Take the biggest-area planes (more likely the primary support faces)
        planar_adj.sort(key=lambda it: it[1].Area(), reverse=True)
        use = planar_adj[:need_planes]
        slabs = []
        for (i, pf) in use:
            into, ok = plane_inside_dir(pf)
            print(
                f"  PLANE use face#{i}: area={round(pf.Area(),3)} center={[round(pf.Center().x,3),round(pf.Center().y,3),round(pf.Center().z,3)]}"
                f" normal={[round(pf.normalAt().x,3),round(pf.normalAt().y,3),round(pf.normalAt().z,3)]} into={[round(into.x,3),round(into.y,3),round(into.z,3)]} probe_ok={ok}"
            )
            slabs.append(halfspace_slab_from_plane_face(pf, into, big=12000.0, depth=12000.0))
        # Intersect slabs to make wedge
        wedge = slabs[0]
        for s in slabs[1:]:
            wedge = wedge.intersect(s)
        # Clip to a region around the target face
        bb = target_face.BoundingBox()
        clip = expanded_box_from_bb(bb, margin=6.0)
        filler = wedge.intersect(clip)
        return filler

    # Cylinder fillers: 2 planes
    for idx, f in r10_cyl_faces:
        try:
            filler = make_filler_for_target_face(f, need_planes=2)
            if filler is None:
                print(f"FILLER: SKIP r10 cyl face#{idx}: insufficient planes")
                continue
            v = filler.Volume()
            bb = filler.BoundingBox()
            print(
                f"FILLER: r10 cyl face#{idx} filler vol={round(v,3)} bb=[{round(bb.xmin,3)},{round(bb.ymin,3)},{round(bb.zmin,3)},{round(bb.xmax,3)},{round(bb.ymax,3)},{round(bb.zmax,3)}]"
            )
            if v > 1e-6:
                fillers.append(filler)
        except Exception as ex:
            print(f"FILLER: FAILED r10 cyl face#{idx}:", ex)

    # Torus fillers: 3 planes (trihedral corner)
    for idx, f in r10_tor_faces:
        try:
            filler = make_filler_for_target_face(f, need_planes=3)
            if filler is None:
                print(f"FILLER: SKIP r10 tor face#{idx}: insufficient planes")
                continue
            v = filler.Volume()
            bb = filler.BoundingBox()
            print(
                f"FILLER: r10 tor face#{idx} filler vol={round(v,3)} bb=[{round(bb.xmin,3)},{round(bb.ymin,3)},{round(bb.zmin,3)},{round(bb.xmax,3)},{round(bb.ymax,3)},{round(bb.zmax,3)}]"
            )
            if v > 1e-6:
                fillers.append(filler)
        except Exception as ex:
            print(f"FILLER: FAILED r10 tor face#{idx}:", ex)

    print(f"SELECTED: {len(fillers)} filler solids to add back material")

    filled = solid0
    total_added = None
    for k, fi in enumerate(fillers):
        try:
            before = filled
            filled = filled.fuse(fi)
            added_k = filled.cut(before)
            if total_added is None:
                total_added = added_k
            else:
                try:
                    total_added = total_added.fuse(added_k)
                except Exception:
                    # fallback accumulate by fuse with fi (overcounts overlap but ok for diagnostics)
                    total_added = total_added.fuse(fi)
            print(f"FUSE: filler {k+1}/{len(fillers)} done; incremental added vol≈{round(added_k.Volume(),3)}")
        except Exception as ex:
            print(f"FUSE: filler {k+1}/{len(fillers)} FAILED:", ex)

    if total_added is not None and total_added.Volume() > 1e-6:
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
        print("PLACEMENT added-total: ~0 (filler union likely failed or was empty)")

    # ---- Find key plane faces on the filled solid (by normal & center) ----
    def find_plane_face_by_normal_center(sld, n_target, c_target=None, c_tol=15.0, ndot_tol=0.999):
        nT = cq.Vector(*n_target).normalized()
        best = None
        bestScore = -1e9
        for i, f in enumerate(sld.Faces()):
            if f.geomType() != "PLANE":
                continue
            n = f.normalAt().normalized()
            # accept either orientation
            ndot = abs(n.dot(nT))
            if ndot < ndot_tol:
                continue
            score = ndot
            if c_target is not None:
                ct = cq.Vector(*c_target)
                d = (f.Center() - ct).Length
                if d > c_tol:
                    continue
                score += (1.0 - d / c_tol)
            if score > bestScore:
                bestScore = score
                best = (i, f)
        return best

    def find_plane_face_by_x(sld, xval, tol=1e-3):
        best = None
        bestErr = 1e99
        for i, f in enumerate(sld.Faces()):
            if f.geomType() != "PLANE":
                continue
            bb = f.BoundingBox()
            # plane at constant x: bb.xlen ~0
            if bb.xlen > 1e-3:
                continue
            x = 0.5 * (bb.xmin + bb.xmax)
            err = abs(x - xval)
            if err < bestErr and err < 0.5:
                bestErr = err
                best = (i, f)
        return best

    n12 = (0.0, -0.966, 0.259)
    n9 = (0.0, 0.259, 0.966)
    n45 = (0.0, -0.259, -0.966)

    f12_new = find_plane_face_by_normal_center(filled, n12, c_target=(-556.62, -431.314, 312.746))
    f9_new = find_plane_face_by_normal_center(filled, n9, c_target=(-556.62, -494.124, 39.698))
    f45_new = find_plane_face_by_normal_center(filled, n45, c_target=(-556.62, -349.185, 580.617))
    fxmin_new = find_plane_face_by_x(filled, -949.62)
    fxmax_new = find_plane_face_by_x(filled, -163.62)

    def dbg_face_pick(tag, picked):
        if picked is None:
            print(f"PICK {tag}: NONE")
            return
        i, f = picked
        print(
            f"PICK {tag}: face_idx_now={i} geomType={f.geomType()} center={[round(f.Center().x,3),round(f.Center().y,3),round(f.Center().z,3)]} area={round(f.Area(),3)}"
        )

    dbg_face_pick("plane12", f12_new)
    dbg_face_pick("plane9", f9_new)
    dbg_face_pick("plane45", f45_new)
    dbg_face_pick("xmin", fxmin_new)
    dbg_face_pick("xmax", fxmax_new)

    # ---- Select shared sharp edges between these planes and fillet them to r=2.0 ----
    def shared_edges(fa, fb):
        out = []
        for ea in fa.Edges():
            for eb in fb.Edges():
                if ea.isSame(eb):
                    out.append(ea)
                    break
        return out

    edges_to_fillet = []

    def add_shared_line_edges(pA, pB, purpose, min_len=50.0):
        nonlocal edges_to_fillet
        if pA is None or pB is None:
            print(f"SELECTED: 0 edges for {purpose} (missing face)")
            return
        fa = pA[1]
        fb = pB[1]
        es = shared_edges(fa, fb)
        sel = []
        for e in es:
            try:
                if e.geomType() != "LINE":
                    continue
                if e.Length() < min_len:
                    continue
                sel.append(e)
            except Exception:
                pass
        print(f"SELECTED: {len(sel)} edges for {purpose}")
        edges_to_fillet.extend(sel)

    # likely rim edges around the outside
    add_shared_line_edges(f12_new, f9_new, "outer rim sharp edge (plane12-plane9)")
    add_shared_line_edges(f12_new, f45_new, "outer rim sharp edge (plane12-plane45)")
    add_shared_line_edges(f12_new, fxmin_new, "outer rim sharp edge (plane12-xmin)")
    add_shared_line_edges(f12_new, fxmax_new, "outer rim sharp edge (plane12-xmax)")

    # also include the other two plane intersections that may exist after refill
    add_shared_line_edges(f9_new, fxmin_new, "corner edge (plane9-xmin)", min_len=5.0)
    add_shared_line_edges(f9_new, fxmax_new, "corner edge (plane9-xmax)", min_len=5.0)
    add_shared_line_edges(f45_new, fxmin_new, "corner edge (plane45-xmin)", min_len=5.0)
    add_shared_line_edges(f45_new, fxmax_new, "corner edge (plane45-xmax)", min_len=5.0)

    # de-duplicate edges
    uniq_edges = []
    for e in edges_to_fillet:
        if not any(e.isSame(u) for u in uniq_edges):
            uniq_edges.append(e)
    edges_to_fillet = uniq_edges
    print(f"SELECTED: {len(edges_to_fillet)} unique edges total for r=2 rim fillet")

    out = filled
    if len(edges_to_fillet) == 0:
        print("ERROR: selected 0 edges for r=2 rim fillet; returning filled solid anyway (must not no-op)")
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

    # ---- Delta / bbox self-check ----
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

    # ---- QA: count remaining r=10 cylinders and tori minor r=10 ----
    try:
        from OCP.BRepAdaptor import BRepAdaptor_Surface
        from OCP.GeomAbs import GeomAbs_Cylinder, GeomAbs_Torus

        cyl10 = []
        tor_minor10 = []
        for i, f in enumerate(out.Faces()):
            try:
                ad = BRepAdaptor_Surface(f.wrapped, True)
                typ = ad.GetType()
                if typ == GeomAbs_Cylinder:
                    r = float(ad.Cylinder().Radius())
                    if abs(r - 10.0) < 1e-2:
                        cyl10.append(i)
                elif typ == GeomAbs_Torus:
                    tor = ad.Torus()
                    rmin = float(tor.MinorRadius())
                    if abs(rmin - 10.0) < 1e-2:
                        tor_minor10.append(i)
            except Exception:
                pass

        print(f"CHECK: cylindrical faces r≈10.0 count={len(cyl10)} idx={cyl10}")
        print(f"CHECK: torus faces minor r≈10.0 count={len(tor_minor10)} idx={tor_minor10}")
    except Exception as ex:
        print("CHECK: surface QA scan FAILED:", ex)

    return out