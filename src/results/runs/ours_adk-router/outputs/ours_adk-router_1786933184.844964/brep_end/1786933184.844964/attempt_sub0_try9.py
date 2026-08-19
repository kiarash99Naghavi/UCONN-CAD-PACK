def my_cad_function(args):
    import cadquery as cq
    import math

    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input
    base = shape.val() if hasattr(shape, "val") else shape

    # --- constants from prompt ---
    target_bbox_min = (-949.62, -506.698, 26.8)
    target_bbox_max = (-163.62, -338.409, 595.312)
    r_big = 10.0
    r_small = 2.0
    eps_ax = 0.1  # small axial overlap to ensure boolean fuses
    eps_ang = 1e-6

    print("NAMED: bbox min", target_bbox_min, "max", target_bbox_max)
    print("NAMED: r_big=", r_big, "r_small=", r_small)

    # --- OCC helpers ---
    from OCP.BRepAdaptor import BRepAdaptor_Surface, BRepAdaptor_Curve
    from OCP.GeomAbs import GeomAbs_Plane, GeomAbs_Cylinder, GeomAbs_Torus, GeomAbs_Circle, GeomAbs_Line
    from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE
    from OCP.TopExp import TopExp
    from OCP.TopTools import TopTools_IndexedDataMapOfShapeListOfShape

    def v_add(a, b):
        return cq.Vector(a.x + b.x, a.y + b.y, a.z + b.z)

    def v_sub(a, b):
        return cq.Vector(a.x - b.x, a.y - b.y, a.z - b.z)

    def v_mul(a, s):
        return cq.Vector(a.x * s, a.y * s, a.z * s)

    def v_dot(a, b):
        return a.x * b.x + a.y * b.y + a.z * b.z

    def v_cross(a, b):
        return cq.Vector(a.y * b.z - a.z * b.y, a.z * b.x - a.x * b.z, a.x * b.y - a.y * b.x)

    def v_len(a):
        return math.sqrt(v_dot(a, a))

    def v_norm(a):
        L = v_len(a)
        if L < 1e-12:
            return cq.Vector(0, 0, 0)
        return v_mul(a, 1.0 / L)

    def plane_intersection_line(p1, n1, p2, n2):
        """Return (point_on_line, direction) for intersection of two non-parallel planes."""
        n1 = v_norm(n1)
        n2 = v_norm(n2)
        d = v_cross(n1, n2)
        dd = v_dot(d, d)
        if dd < 1e-12:
            raise ValueError("Planes nearly parallel; cannot intersect reliably")
        c1 = v_dot(n1, p1)
        c2 = v_dot(n2, p2)
        # x0 = (c1*(n2 x d) + c2*(d x n1)) / |d|^2
        term1 = v_mul(v_cross(n2, d), c1)
        term2 = v_mul(v_cross(d, n1), c2)
        x0 = v_mul(v_add(term1, term2), 1.0 / dd)
        return x0, v_norm(d)

    def uv_bounds(face):
        ad = BRepAdaptor_Surface(face.wrapped, True)
        u1 = float(ad.FirstUParameter())
        u2 = float(ad.LastUParameter())
        v1 = float(ad.FirstVParameter())
        v2 = float(ad.LastVParameter())
        return u1, u2, v1, v2, ad

    # Build edge->faces adjacency map once
    edge_face_map = TopTools_IndexedDataMapOfShapeListOfShape()
    TopExp.MapShapesAndAncestors_s(base.wrapped, TopAbs_EDGE, TopAbs_FACE, edge_face_map)

    def faces_sharing_edge(edge):
        lst = edge_face_map.FindFromKey(edge.wrapped)
        out = []
        it = lst.begin()
        while it != lst.end():
            out.append(cq.Shape.cast(it.Value()))
            it.Next()
        return out

    def other_face_across_edge(this_face, edge):
        fs = faces_sharing_edge(edge)
        others = []
        for f in fs:
            # compare wrapped pointers by hashCode
            if f.wrapped.HashCode(1000000) != this_face.wrapped.HashCode(1000000):
                others.append(f)
        return others

    # --- resolve target faces by given indices ---
    faces = base.Faces()
    cyl10_face_idx = [36, 38, 40, 42]
    tor10_face_idx = [35, 37, 39, 41]

    cyl_faces = [faces[i] for i in cyl10_face_idx]
    tor_faces = [faces[i] for i in tor10_face_idx]
    print(f"SELECTED: {len(cyl_faces)} faces for r=10 cylindrical blend members idx={cyl10_face_idx}")
    print(f"SELECTED: {len(tor_faces)} faces for r=10 toroidal corner blend members idx={tor10_face_idx}")

    # --- build 4 cylinder fillers using actual adjacent planar supports (offset-by-2 logic) ---
    def cylinder_filler_from_support_planes(cyl_face):
        # identify the two support faces via the two non-circular edges
        side_supports = []
        side_edges = []
        circ_edges = []

        for e in cyl_face.Edges():
            cad = BRepAdaptor_Curve(e.wrapped)
            ctyp = cad.GetType()
            if ctyp == GeomAbs_Circle:
                circ_edges.append(e)
                continue
            # treat as side edge
            side_edges.append(e)
            others = other_face_across_edge(cyl_face, e)
            if len(others) == 0:
                continue
            # choose planar face among others if possible
            picked = None
            for of in others:
                sad = BRepAdaptor_Surface(of.wrapped, True)
                if sad.GetType() == GeomAbs_Plane:
                    picked = of
                    break
            if picked is None:
                picked = others[0]
            side_supports.append(picked)

        # unique by hash
        uniq = {}
        for sf in side_supports:
            uniq[sf.wrapped.HashCode(1000000)] = sf
        side_supports = list(uniq.values())

        print(f"SELECTED: {len(side_edges)} side edges on cyl face (non-circle) for support detection")
        print(f"SELECTED: {len(circ_edges)} circular end edges on cyl face")
        print(f"SELECTED: {len(side_supports)} adjacent support faces for cyl face")

        if len(side_supports) < 2:
            raise ValueError("Could not resolve 2 support faces for cylinder blend")

        # choose two planar supports if available
        planes = []
        for sf in side_supports:
            sad = BRepAdaptor_Surface(sf.wrapped, True)
            if sad.GetType() == GeomAbs_Plane:
                planes.append(sf)
        if len(planes) >= 2:
            s1, s2 = planes[0], planes[1]
        else:
            s1, s2 = side_supports[0], side_supports[1]

        n1_out = v_norm(s1.normalAt())
        n2_out = v_norm(s2.normalAt())
        p1 = s1.Center()
        p2 = s2.Center()

        # sharp edge intersection line (original supports)
        o0, d = plane_intersection_line(p1, n1_out, p2, n2_out)

        # move o0 along d to be close to cyl face center
        cc = cyl_face.Center()
        t_closest = v_dot(v_sub(cc, o0), d)
        o0 = v_add(o0, v_mul(d, t_closest))

        # build orthonormal cross-section frame using inward normals
        i1 = v_norm(v_mul(n1_out, -1.0))
        i2_nom = v_norm(v_mul(n2_out, -1.0))

        # make i2 perpendicular to i1 and d
        i2 = v_norm(v_cross(d, i1))
        if v_dot(i2, i2_nom) < 0:
            i2 = v_mul(i2, -1.0)

        # ensure i1 points so that cyl face center is in +x
        if v_dot(v_sub(cc, o0), i1) < 0:
            i1 = v_mul(i1, -1.0)
            i2 = v_norm(v_cross(d, i1))
            if v_dot(i2, i2_nom) < 0:
                i2 = v_mul(i2, -1.0)

        # ensure i2 points so that cyl face center is in +y
        if v_dot(v_sub(cc, o0), i2) < 0:
            i2 = v_mul(i2, -1.0)

        # right-handed check
        if v_dot(v_cross(i1, i2), d) < 0:
            i2 = v_mul(i2, -1.0)

        # diagnose orthogonality
        print("CHECK: support plane normals dot=", float(v_dot(n1_out, n2_out)))
        print("CHECK: frame dot(i1,i2)=", float(v_dot(i1, i2)), "dot(i1,d)=", float(v_dot(i1, d)), "dot(i2,d)=", float(v_dot(i2, d)))

        # axial extent from cyl face vertices projected onto d
        ts = []
        for vtx in cyl_face.Vertices():
            pv = vtx.Center()
            ts.append(v_dot(v_sub(pv, o0), d))
        tmin, tmax = min(ts), max(ts)
        L = (tmax - tmin) + 2 * eps_ax
        o_start = v_add(o0, v_mul(d, tmin - eps_ax))

        # 2D profile between r=10 and r=2 in i1/i2 plane, extruded along d
        plane = cq.Plane(origin=o_start.toTuple(), normal=d.toTuple(), xDir=i1.toTuple())
        wp = cq.Workplane(plane)

        rb = r_big
        rs = r_small
        rt2 = math.sqrt(2.0)
        mid_big = (rb - rb / rt2, rb - rb / rt2)
        mid_small = (rs - rs / rt2, rs - rs / rt2)

        pA = (0.0, rb)
        pB = (rb, 0.0)
        pC = (rs, 0.0)
        pD = (0.0, rs)

        prof = (
            wp.moveTo(*pA)
              .threePointArc(mid_big, pB)
              .lineTo(*pC)
              .threePointArc(mid_small, pD)
              .lineTo(*pA)
              .close()
        )

        solid_wp = prof.extrude(L, combine=False)
        solid = solid_wp.val()

        bb = solid.BoundingBox()
        print(
            "BUILT: cyl filler using support planes",
            "L=", float(L),
            "bbox=", ((bb.xmin, bb.ymin, bb.zmin), (bb.xmax, bb.ymax, bb.zmax)),
        )
        return solid

    cyl_fillers = []
    for k, f in enumerate(cyl_faces):
        try:
            sad = BRepAdaptor_Surface(f.wrapped, True)
            print(f"CHECK: cyl face {k} type=", sad.GetType(), "center=", f.Center().toTuple(), "area=", float(f.Area()))
            cyl_fillers.append(cylinder_filler_from_support_planes(f))
        except Exception as e:
            print(f"FAILED: cyl filler {k} due to {e}")

    print(f"SELECTED: {len(cyl_fillers)} cylinder additive fillers (expected 4)")

    # --- build 4 torus fillers between minor radii 10->2 over the same UV bounds ---
    def torus_minor_filler(tor_face):
        u1, u2, v1, v2, ad = uv_bounds(tor_face)
        if ad.GetType() != GeomAbs_Torus:
            raise ValueError("Not a torus face")
        tor = ad.Torus()
        R = float(tor.MajorRadius())
        rmin = float(tor.MinorRadius())
        pos = tor.Position()  # gp_Ax3
        origin = cq.Vector(pos.Location().X(), pos.Location().Y(), pos.Location().Z())
        zdir = cq.Vector(pos.Direction().X(), pos.Direction().Y(), pos.Direction().Z())
        xdir = cq.Vector(pos.XDirection().X(), pos.XDirection().Y(), pos.XDirection().Z())
        ydir = cq.Vector(pos.YDirection().X(), pos.YDirection().Y(), pos.YDirection().Z())

        du = (u2 - u1)
        dv = (v2 - v1)

        print(
            "CHECK: torus face",
            "center=", tor_face.Center().toTuple(),
            "R=", R,
            "rmin=", rmin,
            "u(deg)=", (math.degrees(u1), math.degrees(u2)),
            "v(deg)=", (math.degrees(v1), math.degrees(v2)),
        )

        # build profile in the torus XZ plane: plane normal is Y
        # plane axes: x along xdir, y along (normal x xdir) = ydir x xdir = -zdir
        plane = cq.Plane(origin=origin.toTuple(), normal=ydir.toTuple(), xDir=xdir.toTuple())
        wp = cq.Workplane(plane)

        # helper: in this plane, coordinates correspond to:
        #   X axis = +xdir
        #   Y axis = (ydir x xdir) = -zdir
        # Torus param at u=0 lies in this plane: x = R + r*cos(v), z = r*sin(v)
        # Our plane Y is -z => y = -z = -r*sin(v)
        def p_on_minor(rr, vv):
            return (R + rr * math.cos(vv), -rr * math.sin(vv))

        vo1, vo2 = v1, v2
        vm = 0.5 * (vo1 + vo2)

        p1o = p_on_minor(r_big, vo1)
        p2o = p_on_minor(r_big, vo2)
        pmo = p_on_minor(r_big, vm)

        p2i = p_on_minor(r_small, vo2)
        p1i = p_on_minor(r_small, vo1)
        pmi = p_on_minor(r_small, vm)

        prof = (
            wp.moveTo(*p1o)
              .threePointArc(pmo, p2o)
              .lineTo(*p2i)
              .threePointArc(pmi, p1i)
              .close()
        )

        axis_start = origin.toTuple()
        axis_end = (origin + v_mul(v_norm(zdir), 1.0)).toTuple()

        solid_wp = prof.revolve(math.degrees(du), axis_start, axis_end)
        solid = solid_wp.val()

        # rotate wedge to start at u1
        solid = solid.rotate(axis_start, axis_end, math.degrees(u1))

        bb = solid.BoundingBox()
        print(
            "BUILT: tor filler",
            "du(deg)=", float(math.degrees(du)),
            "dv(deg)=", float(math.degrees(dv)),
            "bbox=", ((bb.xmin, bb.ymin, bb.zmin), (bb.xmax, bb.ymax, bb.zmax)),
        )
        return solid

    tor_fillers = []
    for k, f in enumerate(tor_faces):
        try:
            tor_fillers.append(torus_minor_filler(f))
        except Exception as e:
            print(f"FAILED: tor filler {k} due to {e}")

    print(f"SELECTED: {len(tor_fillers)} torus additive fillers (expected 4)")

    fillers = cyl_fillers + tor_fillers
    print(f"SELECTED: {len(fillers)} additive filler solids for union (expected 8)")

    # If we built nothing, we MUST still do something (avoid no-op):
    if len(fillers) == 0:
        # Force a tiny non-functional change (very small cube) would violate bbox and intent,
        # so instead raise to surface failure loudly.
        raise RuntimeError("No fillers built; cannot proceed without a no-op")

    # --- fuse fillers with base ---
    out = base
    for i, tool in enumerate(fillers):
        try:
            out = out.fuse(tool)
            sols = out.Solids()
            print(f"FUSE: succeeded for filler {i}; out solids now=", len(sols))
        except Exception as e:
            print(f"FUSE: FAILED for filler {i} due to {e}")

    # --- self-check: isolate added material ---
    try:
        added = out.cut(base)
        bbA = added.BoundingBox()
        cA = added.Center()
        print("SELF-CHECK: added material center=", cA.toTuple())
        print(
            "SELF-CHECK: added bbox min=",
            (bbA.xmin, bbA.ymin, bbA.zmin),
            "max=",
            (bbA.xmax, bbA.ymax, bbA.zmax),
            "size=",
            (bbA.xlen, bbA.ylen, bbA.zlen),
        )
    except Exception as e:
        print("SELF-CHECK: could not compute added=out.cut(base):", e)

    # --- Validate bbox preservation ---
    bb1 = out.BoundingBox()
    out_min = (bb1.xmin, bb1.ymin, bb1.zmin)
    out_max = (bb1.xmax, bb1.ymax, bb1.zmax)
    print("CHECK: out bbox min=", out_min, "max=", out_max)
    print(
        "CHECK: bbox delta min=",
        (out_min[0] - target_bbox_min[0], out_min[1] - target_bbox_min[1], out_min[2] - target_bbox_min[2]),
        "delta max=",
        (out_max[0] - target_bbox_max[0], out_max[1] - target_bbox_max[1], out_max[2] - target_bbox_max[2]),
    )

    # --- Confirm one valid solid ---
    sols = out.Solids()
    print("CHECK: out solids=", len(sols))

    # --- Confirm no remaining r=10 cylindrical or toroidal faces on boundary ---
    rem_cyl10 = []
    rem_tor10 = []
    for fi, f in enumerate(out.Faces()):
        ad = BRepAdaptor_Surface(f.wrapped, True)
        t = ad.GetType()
        if t == GeomAbs_Cylinder:
            cyl = ad.Cylinder()
            r = float(cyl.Radius())
            if abs(r - 10.0) < 0.05:
                # check if it's a 90-degree patch (blend-like)
                u1 = float(ad.FirstUParameter())
                u2 = float(ad.LastUParameter())
                if abs((u2 - u1) - (math.pi / 2.0)) < 0.05:
                    rem_cyl10.append(fi)
        elif t == GeomAbs_Torus:
            tor = ad.Torus()
            r = float(tor.MinorRadius())
            if abs(r - 10.0) < 0.05:
                rem_tor10.append(fi)

    print(f"CHECK: remaining boundary r=10 cylindrical blend faces: {len(rem_cyl10)} idx={rem_cyl10}")
    print(f"CHECK: remaining boundary r=10 toroidal faces: {len(rem_tor10)} idx={rem_tor10}")

    return out