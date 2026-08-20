def my_cad_function(args):
    import cadquery as cq
    import math

    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input

    base = shape.val() if hasattr(shape, "val") else shape
    solids = base.Solids()
    print(f"INPUT: solids={len(solids)} faces={len(base.Faces())} edges={len(base.Edges())}")
    if len(solids) != 1:
        print("FATAL: expected exactly 1 solid in the STEP; returning input unchanged")
        return shape
    s = solids[0]

    a = cq.Vector(0.0, 0.966, -0.259).normalized()
    print(f"ANCHOR axis a=({a.x:.6f},{a.y:.6f},{a.z:.6f})")

    faces = s.Faces()

    def vdot(u, v):
        return float(u.x * v.x + u.y * v.y + u.z * v.z)

    def det3(m):
        return (
            m[0][0] * (m[1][1] * m[2][2] - m[1][2] * m[2][1])
            - m[0][1] * (m[1][0] * m[2][2] - m[1][2] * m[2][0])
            + m[0][2] * (m[1][0] * m[2][1] - m[1][1] * m[2][0])
        )

    def solve_planes(n1, d1, n2, d2, n3, d3):
        # Solve:
        #   n1·p = d1
        #   n2·p = d2
        #   n3·p = d3
        A = [
            [float(n1.x), float(n1.y), float(n1.z)],
            [float(n2.x), float(n2.y), float(n2.z)],
            [float(n3.x), float(n3.y), float(n3.z)],
        ]
        D = det3(A)
        if abs(D) < 1e-12:
            raise ValueError(f"degenerate 3-plane intersection (det={D})")
        Ax = [[d1, A[0][1], A[0][2]], [d2, A[1][1], A[1][2]], [d3, A[2][1], A[2][2]]]
        Ay = [[A[0][0], d1, A[0][2]], [A[1][0], d2, A[1][2]], [A[2][0], d3, A[2][2]]]
        Az = [[A[0][0], A[0][1], d1], [A[1][0], A[1][1], d2], [A[2][0], A[2][1], d3]]
        x = det3(Ax) / D
        y = det3(Ay) / D
        z = det3(Az) / D
        return cq.Vector(x, y, z)

    def circle_center_of_edge(e):
        # TRUE circle center from OCC adaptor
        from OCP.BRepAdaptor import BRepAdaptor_Curve
        from OCP.GeomAbs import GeomAbs_Circle

        ad = BRepAdaptor_Curve(e.wrapped)
        if ad.GetType() != GeomAbs_Circle:
            return None
        circ = ad.Circle()
        p = circ.Location()
        return cq.Vector(float(p.X()), float(p.Y()), float(p.Z()))

    def removed_profile_solid(corner, v1, v2, axis_dir, R, L):
        # 2D removed region in plane normal to axis_dir:
        # curved triangle (0,0)->(R,0)->arc->(0,R)->(0,0), with arc centered at (R,R)
        p0 = corner
        p1 = corner + v1.multiply(R)
        p2 = corner + v2.multiply(R)
        k = (R - R / math.sqrt(2.0))
        pm = corner + v1.multiply(k) + v2.multiply(k)

        e1 = cq.Edge.makeLine(p0, p1)
        e2 = cq.Edge.makeThreePointArc(p1, pm, p2)
        e3 = cq.Edge.makeLine(p2, p0)
        w = cq.Wire.assembleEdges([e1, e2, e3])
        f = cq.Face.makeFromWires(w)
        vec = axis_dir.multiply(L)
        sol = cq.Solid.extrudeLinear(f, vec)
        return sol

    # Resolve and print the support planes mentioned in the sub-goal
    support_face_idxs = [22, 24, 43, 49]
    support = {}
    for fi in support_face_idxs:
        f = faces[fi]
        c = f.Center()
        n = f.normalAt()
        print(
            f"RESOLVED support face #{fi}: area={f.Area():.3f} "
            f"c=({c.x:.3f},{c.y:.3f},{c.z:.3f}) n=({n.x:.6f},{n.y:.6f},{n.z:.6f})"
        )
        support[fi] = (f, cq.Vector(n.x, n.y, n.z).normalized(), cq.Vector(c.x, c.y, c.z))

    # Outward normals from the index (used only for direction; the actual plane point comes from the resolved face)
    # X planes:
    x_left = -949.62
    x_right = -163.62
    nX_left_out = cq.Vector(-1, 0, 0)   # face #24
    nX_right_out = cq.Vector(1, 0, 0)   # face #43

    # Slanted planes: use resolved face normals/centers so we don't guess offsets
    nS_bottom_out = support[22][1]
    pS_bottom = support[22][2]
    nS_top_out = support[49][1]
    pS_top = support[49][2]

    # Target corner cylinders (R63) by face index
    target_face_idxs = [21, 23, 44, 48]
    selected_targets = []
    for fi in target_face_idxs:
        if fi < 0 or fi >= len(faces):
            continue
        selected_targets.append(fi)
    print(f"SELECTED: {len(selected_targets)} faces for R63 corner patches idx={selected_targets} (expected 4)")
    if len(selected_targets) == 0:
        print("FATAL: no target faces resolved; returning input unchanged")
        return shape

    # Build crescent tools (additive) for each target face
    crescents = []
    overlap = 0.5  # mm along axis to ensure full coverage at patch ends
    R_outer = 63.0
    R_outer_overlap = 63.05  # tiny overlap avoids coincident faces in booleans
    R_new = 50.0

    for fi in selected_targets:
        f = faces[fi]
        cf = f.Center()
        nf = f.normalAt()
        print(
            f"TARGET face #{fi}: area={f.Area():.3f} "
            f"c=({cf.x:.3f},{cf.y:.3f},{cf.z:.3f}) n=({nf.x:.6f},{nf.y:.6f},{nf.z:.6f})"
        )

        # Extract the two circular boundary edges of this 90deg cylinder patch
        circ_edges = []
        circ_centers = []
        for e in f.Edges():
            if e.geomType() == "CIRCLE":
                ccen = circle_center_of_edge(e)
                if ccen is not None:
                    circ_edges.append(e)
                    circ_centers.append(ccen)

        # If more than 2 circles were found, keep the two farthest apart along the axis direction
        print(f"SELECTED: {len(circ_edges)} circular edges on target face #{fi} for axial-span measurement")
        if len(circ_centers) < 2:
            print(f"ERROR: face #{fi}: expected >=2 circular edges; skipping this patch")
            continue

        # Compute params along axis for each center
        params = [vdot(c, a) for c in circ_centers]
        i0 = int(min(range(len(params)), key=lambda i: params[i]))
        i1 = int(max(range(len(params)), key=lambda i: params[i]))
        c0 = circ_centers[i0]
        c1 = circ_centers[i1]
        t0 = params[i0]
        t1 = params[i1]
        L = float(t1 - t0)
        if L < 1.0:
            print(f"ERROR: face #{fi}: suspicious span length L={L:.6f}; skipping")
            continue

        # Build start plane origin slightly before first end to overlap
        start_plane_origin = c0 - a.multiply(overlap)
        L_ext = L + 2.0 * overlap

        # Determine which side (left/right) and which slanted plane (top/bottom)
        side = "left" if cf.x < -556.62 else "right"
        is_top = True if cf.z > 300.0 else False

        if side == "left":
            xPlane = x_left
            nX_out = nX_left_out
        else:
            xPlane = x_right
            nX_out = nX_right_out

        if is_top:
            nS_out = nS_top_out
            pS = pS_top
            sl_name = "top"
        else:
            nS_out = nS_bottom_out
            pS = pS_bottom
            sl_name = "bottom"

        # Interior directions in the section plane (perpendicular to each support plane)
        v1 = (-nX_out).normalized()     # into solid from X-plane
        v2 = (-nS_out).normalized()     # into solid from slanted plane

        # Corner point = intersection of:
        #   plane x = xPlane
        #   slanted plane (nS_out·p = nS_out·pS)
        #   section plane normal to axis a through start_plane_origin (a·p = a·start_plane_origin)
        try:
            corner = solve_planes(
                cq.Vector(1, 0, 0), float(xPlane),
                nS_out, vdot(nS_out, pS),
                a, vdot(a, start_plane_origin),
            )
        except Exception as e:
            print(f"ERROR: face #{fi}: failed to compute corner intersection point: {e}")
            continue

        print(
            f"PATCH face #{fi}: side={side} slanted={sl_name} xPlane={xPlane} "
            f"corner=({corner.x:.3f},{corner.y:.3f},{corner.z:.3f}) "
            f"span_L={L:.3f} L_ext={L_ext:.3f}"
        )

        # Build additive crescent = removed(R63) - removed(R50)
        try:
            sol_outer = removed_profile_solid(corner, v1, v2, a, R_outer_overlap, L_ext)
            sol_newcut = removed_profile_solid(corner, v1, v2, a, R_new, L_ext)
            cres = sol_outer.cut(sol_newcut)

            bb = cres.BoundingBox()
            cc = cres.Center()
            print(
                f"PATCH face #{fi}: crescent tool built. "
                f"center=({cc.x:.3f},{cc.y:.3f},{cc.z:.3f}) "
                f"bbox=([{bb.xmin:.3f},{bb.ymin:.3f},{bb.zmin:.3f}].."
                f"[{bb.xmax:.3f},{bb.ymax:.3f},{bb.zmax:.3f}])"
            )
            crescents.append(cres)
        except Exception as e:
            print(f"ERROR: face #{fi}: failed to build crescent tool: {e}")

    print(f"SELECTED: {len(crescents)} solids for additive crescent tools (expected 4)")
    if len(crescents) == 0:
        print("FATAL: no crescent tools were created; returning input solid unchanged")
        return shape

    # Fuse all four tools into the base in ONE boolean operation
    tool = cq.Compound.makeCompound(crescents)
    print("BOOL: fusing compound(tool) into base solid in one fuse")
    out = s.fuse(tool)

    # Refine: remove internal splitter faces if any (removeSplitter is not available on Workplane here)
    try:
        out = out.clean()
        print("REFINE: out.clean() succeeded")
    except Exception as e:
        print(f"REFINE: out.clean() failed: {e}")
        try:
            out = cq.Workplane(obj=out).clean().val()
            print("REFINE: Workplane(obj=out).clean().val() succeeded")
        except Exception as e2:
            print(f"REFINE: Workplane clean also failed: {e2}")

    # --- Self-checks ---
    print(f"OUTPUT: solids={len(out.Solids())} faces={len(out.Faces())} edges={len(out.Edges())}")

    # BBox check against required unchanged bbox
    exp_min = (-949.62, -506.698, 26.8)
    exp_max = (-163.62, -338.409, 595.312)
    bb_out = out.BoundingBox()
    out_min = (bb_out.xmin, bb_out.ymin, bb_out.zmin)
    out_max = (bb_out.xmax, bb_out.ymax, bb_out.zmax)
    dmin = (out_min[0] - exp_min[0], out_min[1] - exp_min[1], out_min[2] - exp_min[2])
    dmax = (out_max[0] - exp_max[0], out_max[1] - exp_max[1], out_max[2] - exp_max[2])
    print(
        "BBOX OUT: "
        f"min=[{out_min[0]:.3f},{out_min[1]:.3f},{out_min[2]:.3f}] "
        f"max=[{out_max[0]:.3f},{out_max[1]:.3f},{out_max[2]:.3f}]"
    )
    print(
        "BBOX DELTA vs expected: "
        f"dmin=[{dmin[0]:.3f},{dmin[1]:.3f},{dmin[2]:.3f}] "
        f"dmax=[{dmax[0]:.3f},{dmax[1]:.3f},{dmax[2]:.3f}]"
    )

    # Isolate and report added material
    try:
        added = out.cut(s)
        bb_add = added.BoundingBox()
        c_add = added.Center()
        print(
            f"ADDED: volume={added.Volume():.3f} center=({c_add.x:.3f},{c_add.y:.3f},{c_add.z:.3f}) "
            f"bbox=([{bb_add.xmin:.3f},{bb_add.ymin:.3f},{bb_add.zmin:.3f}].."
            f"[{bb_add.xmax:.3f},{bb_add.ymax:.3f},{bb_add.zmax:.3f}])"
        )
    except Exception as e:
        print(f"ADDED: could not compute (out.cut(base)) due to: {e}")

    # Diagnostic: count remaining cylindrical faces near r=63 and r=50 on the target axis direction
    try:
        from OCP.BRepAdaptor import BRepAdaptor_Surface
        from OCP.GeomAbs import GeomAbs_Cylinder

        n63 = 0
        n50 = 0
        for ff in out.Faces():
            ad = BRepAdaptor_Surface(ff.wrapped, True)
            if ad.GetType() == GeomAbs_Cylinder:
                cyl = ad.Cylinder()
                r = float(cyl.Radius())
                ddir = cyl.Axis().Direction()
                dv = cq.Vector(float(ddir.X()), float(ddir.Y()), float(ddir.Z())).normalized()
                ax_close = abs(abs(vdot(dv, a)) - 1.0) < 1e-3
                if ax_close and abs(r - 63.0) < 0.25:
                    n63 += 1
                if ax_close and abs(r - 50.0) < 0.25:
                    n50 += 1
        print(f"CHECK: cylindrical faces on target axis ~a: count(r~63)={n63}, count(r~50)={n50}")
    except Exception as e:
        print(f"CHECK: could not scan cylinder radii (OCP adaptor) due to: {e}")

    return out