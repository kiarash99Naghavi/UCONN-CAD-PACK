def my_cad_function(args):
    import cadquery as cq
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_Cylinder

    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input

    base = shape.val() if hasattr(shape, "val") else shape
    sols = base.Solids()
    print(f"INFO: imported solids={len(sols)}")
    if len(sols) != 1:
        print("ERROR: expected exactly 1 solid; returning original")
        return shape
    solid = sols[0]

    # --- Resolve target entities by geometry index tags ---
    faces = base.Faces()
    edges = base.Edges()
    print(f"INFO: base faces={len(faces)} edges={len(edges)}")

    green_face_idx = [21, 23, 44, 48]
    planar_candidate_idx = [22, 24, 43, 49]

    green_faces = []
    for i in green_face_idx:
        try:
            f = faces[i]
        except Exception as e:
            print(f"ERROR: cannot resolve face idx {i}: {e}")
            continue
        c = f.Center()
        a = f.Area()
        ad = BRepAdaptor_Surface(f.wrapped)
        st = ad.GetType()
        if st == GeomAbs_Cylinder:
            cyl = ad.Cylinder()
            rr = cyl.Radius()
            d = cyl.Axis().Direction()
            loc = cyl.Axis().Location()
            print(
                f"RESOLVED: face#{i} center={[round(c.x,3),round(c.y,3),round(c.z,3)]} area={round(a,3)} "
                f"CYL r={round(rr,3)} axis_dir={[round(d.X(),6),round(d.Y(),6),round(d.Z(),6)]} "
                f"axis_loc={[round(loc.X(),3),round(loc.Y(),3),round(loc.Z(),3)]}"
            )
        else:
            print(
                f"RESOLVED: face#{i} center={[round(c.x,3),round(c.y,3),round(c.z,3)]} area={round(a,3)} type={st}"
            )
        green_faces.append((i, f))

    print(f"SELECTED: {len(green_faces)} faces for GREEN R63 corner family  idx={green_face_idx}")
    if len(green_faces) != 4:
        print("ERROR: did not resolve 4 green faces; returning original")
        return shape

    planar_faces = {}
    for i in planar_candidate_idx:
        try:
            pf = faces[i]
            c = pf.Center()
            n = pf.normalAt()
            print(
                f"RESOLVED: planar face#{i} center={[round(c.x,3),round(c.y,3),round(c.z,3)]} "
                f"area={round(pf.Area(),3)} normal={[round(n.x,6),round(n.y,6),round(n.z,6)]}"
            )
            planar_faces[i] = pf
        except Exception as e:
            print(f"ERROR: cannot resolve planar face idx {i}: {e}")

    print(f"SELECTED: {len(planar_faces)} faces for planar-candidates  idx={planar_candidate_idx}")

    def edge_key(e):
        # stable-ish key to detect shared TopoDS_Edge
        try:
            return int(e.wrapped.HashCode(1000003))
        except Exception:
            # fallback: geometric hash
            c = e.Center()
            return (round(c.x, 6), round(c.y, 6), round(c.z, 6), round(e.Length(), 6))

    def make_aabb_from_bb(bb, pad=5.0):
        dx = bb.xlen + 2 * pad
        dy = bb.ylen + 2 * pad
        dz = bb.zlen + 2 * pad
        p0 = cq.Vector(bb.xmin - pad, bb.ymin - pad, bb.zmin - pad)
        return cq.Solid.makeBox(dx, dy, dz, pnt=p0)

    def make_slab_on_plane(p_on_plane, outward_n, thickness=200.0, size=600.0):
        # slab inside the solid = extrude along inward normal (-outward_n)
        n_in = cq.Vector(-outward_n.x, -outward_n.y, -outward_n.z)
        # choose xDir not parallel to normal
        cand = cq.Vector(1, 0, 0)
        if abs(n_in.normalized().dot(cand)) > 0.9:
            cand = cq.Vector(0, 1, 0)
        pl = cq.Plane(origin=(p_on_plane.x, p_on_plane.y, p_on_plane.z), normal=(n_in.x, n_in.y, n_in.z), xDir=(cand.x, cand.y, cand.z))
        wp = cq.Workplane(pl)
        return wp.rect(size, size).extrude(thickness).val()

    def solve_point_on_line_of_two_planes_closest_to_ref(p1, n1, p2, n2, pref):
        # Minimize ||p - pref|| subject to n1·(p-p1)=0 and n2·(p-p2)=0
        # p = pref - l1*n1 - l2*n2
        n1v = cq.Vector(n1.x, n1.y, n1.z)
        n2v = cq.Vector(n2.x, n2.y, n2.z)
        # ensure unit normals
        if n1v.Length > 0:
            n1v = n1v.normalized()
        if n2v.Length > 0:
            n2v = n2v.normalized()

        a11 = n1v.dot(n1v)
        a12 = n1v.dot(n2v)
        a21 = a12
        a22 = n2v.dot(n2v)
        b1 = n1v.dot(pref - p1)
        b2 = n2v.dot(pref - p2)
        det = a11 * a22 - a12 * a21
        if abs(det) < 1e-12:
            # nearly parallel; fallback: just project to plane1 then to plane2
            p = pref - n1v * b1
            b2b = n2v.dot(p - p2)
            return p - n2v * b2b
        l1 = (b1 * a22 - b2 * a12) / det
        l2 = (a11 * b2 - a21 * b1) / det
        return pref - n1v * l1 - n2v * l2

    # --- Apply corner conversion: R63 -> R50 on 4 green faces ---
    target_r = 50.0

    for fi, gf in green_faces:
        gcen = gf.Center()
        gbb = gf.BoundingBox()
        local = make_aabb_from_bb(gbb, pad=8.0)
        print(
            f"INFO: face#{fi} local-box bb: xmin={round(local.BoundingBox().xmin,3)} xmax={round(local.BoundingBox().xmax,3)} "
            f"ymin={round(local.BoundingBox().ymin,3)} ymax={round(local.BoundingBox().ymax,3)} "
            f"zmin={round(local.BoundingBox().zmin,3)} zmax={round(local.BoundingBox().zmax,3)}"
        )

        # Find which two of the candidate planar faces actually adjoin this green face
        g_edges = {edge_key(e) for e in gf.Edges()}
        adj_planars = []
        for pi, pf in planar_faces.items():
            p_edges = {edge_key(e) for e in pf.Edges()}
            if len(g_edges.intersection(p_edges)) > 0:
                adj_planars.append((pi, pf))

        print(f"SELECTED: {len(adj_planars)} planar faces adjoining green face#{fi}  idx={[p[0] for p in adj_planars]}")
        if len(adj_planars) != 2:
            print(f"WARNING: expected 2 adjoining planar faces for green face#{fi}; skipping this corner")
            continue

        (p1i, p1f), (p2i, p2f) = adj_planars
        n1 = p1f.normalAt()
        n2 = p2f.normalAt()
        p1 = p1f.Center()
        p2 = p2f.Center()
        print(
            f"INFO: green face#{fi} adjoins planar#{p1i} n1={[round(n1.x,6),round(n1.y,6),round(n1.z,6)]} "
            f"and planar#{p2i} n2={[round(n2.x,6),round(n2.y,6),round(n2.z,6)]}"
        )

        # 1) FILL: add back material locally to remove the existing (larger) fillet
        try:
            slab1 = make_slab_on_plane(p1, n1, thickness=220.0, size=900.0)
            slab2 = make_slab_on_plane(p2, n2, thickness=220.0, size=900.0)
            wedge = slab1.intersect(slab2).intersect(local)
            wbb = wedge.BoundingBox()
            print(
                f"INFO: filler-wedge for face#{fi} bb=[{round(wbb.xmin,3)},{round(wbb.ymin,3)},{round(wbb.zmin,3)}] to "
                f"[{round(wbb.xmax,3)},{round(wbb.ymax,3)},{round(wbb.zmax,3)}]"
            )
            solid = solid.fuse(wedge)
            print(f"APPLIED: fused filler wedge for green face#{fi}")
        except Exception as e:
            print(f"ERROR: filler wedge fuse failed for green face#{fi}: {e}")
            continue

        # 2) CUT: carve the new R50 fillet using a cylinder whose axis is the intersection of the two planes offset inward by R
        try:
            n1u = n1.normalized()
            n2u = n2.normalized()
            p1off = p1 - n1u * target_r
            p2off = p2 - n2u * target_r
            d = n1u.cross(n2u)
            if d.Length < 1e-9:
                print(f"ERROR: computed near-zero axis direction for green face#{fi}; skipping cut")
                continue
            d = d.normalized()

            # choose axis point as closest on the intersection line to the old face center
            axis_pt = solve_point_on_line_of_two_planes_closest_to_ref(p1off, n1u, p2off, n2u, gcen)

            # long cutter cylinder, clipped to local box
            height = max(400.0, gbb.xlen + gbb.ylen + gbb.zlen + 300.0)
            base_pt = axis_pt - d * (height / 2.0)
            cyl = cq.Solid.makeCylinder(target_r, height, pnt=base_pt, dir=d)
            cutter = cyl.intersect(local)

            # placement self-check
            cbb = cutter.BoundingBox()
            print(
                f"CHECK: cutter for face#{fi}: R={target_r} axis_dir={[round(d.x,6),round(d.y,6),round(d.z,6)]} "
                f"axis_pt={[round(axis_pt.x,3),round(axis_pt.y,3),round(axis_pt.z,3)]} "
                f"cutter_bb=[{round(cbb.xmin,3)},{round(cbb.ymin,3)},{round(cbb.zmin,3)}] to [{round(cbb.xmax,3)},{round(cbb.ymax,3)},{round(cbb.zmax,3)}]"
            )

            solid = solid.cut(cutter)
            print(f"APPLIED: cut R50 cylinder tool for green face#{fi}")
        except Exception as e:
            print(f"ERROR: cylinder cut failed for green face#{fi}: {e}")
            continue

    # --- Verification: bbox and presence of 4 cylinder faces near the old green centers with ~R50 ---
    out_bb = solid.BoundingBox()
    print(
        "VERIFY: output bbox min="
        f"[{round(out_bb.xmin,3)},{round(out_bb.ymin,3)},{round(out_bb.zmin,3)}] "
        "max="
        f"[{round(out_bb.xmax,3)},{round(out_bb.ymax,3)},{round(out_bb.zmax,3)}]"
    )
    exp_min = (-949.62, -506.698, 26.8)
    exp_max = (-163.62, -338.409, 595.312)
    print(
        "VERIFY: expected bbox min="
        f"{list(exp_min)} max={list(exp_max)}  "
        "dmin="
        f"[{round(out_bb.xmin-exp_min[0],3)},{round(out_bb.ymin-exp_min[1],3)},{round(out_bb.zmin-exp_min[2],3)}] "
        "dmax="
        f"[{round(out_bb.xmax-exp_max[0],3)},{round(out_bb.ymax-exp_max[1],3)},{round(out_bb.zmax-exp_max[2],3)}]"
    )

    # find cylindrical faces near each original green face center with radius ~50
    out_faces = solid.Faces()
    hits = []
    for (fi, gf) in green_faces:
        ref = gf.Center()
        best = None
        best_dist = 1e99
        for f in out_faces:
            ad = BRepAdaptor_Surface(f.wrapped)
            if ad.GetType() != GeomAbs_Cylinder:
                continue
            r = ad.Cylinder().Radius()
            if abs(r - target_r) > 1.0:
                continue
            c = f.Center()
            d2 = (c.x - ref.x) ** 2 + (c.y - ref.y) ** 2 + (c.z - ref.z) ** 2
            if d2 < best_dist:
                best_dist = d2
                best = (r, c)
        if best is None:
            print(f"VERIFY: green face#{fi} -> found 0 nearby CYL faces with R~{target_r}")
        else:
            r, c = best
            dist = best_dist ** 0.5
            print(
                f"VERIFY: green face#{fi} -> nearest CYL face R={round(r,3)} center={[round(c.x,3),round(c.y,3),round(c.z,3)]} "
                f"dist_to_old_center={round(dist,3)}"
            )
            hits.append(fi)

    print(f"VERIFY: matched {len(hits)}/4 green corners to CYL R~{target_r}")

    return solid