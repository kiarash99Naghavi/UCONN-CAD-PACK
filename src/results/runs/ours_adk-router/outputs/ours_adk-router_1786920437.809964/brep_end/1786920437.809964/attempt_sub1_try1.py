def my_cad_function(args):
    import cadquery as cq
    from math import cos, sin, pi
    from OCP.BRepAdaptor import BRepAdaptor_Surface, BRepAdaptor_Curve
    from OCP.GeomAbs import GeomAbs_Cylinder, GeomAbs_Circle

    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input
    base = shape.val() if hasattr(shape, "val") else shape

    # --- helpers ---
    def circle_params(edge):
        ad = BRepAdaptor_Curve(edge.wrapped)
        if ad.GetType() != GeomAbs_Circle:
            return None
        c = ad.Circle()
        p = c.Location()
        return {"center": (float(p.X()), float(p.Y()), float(p.Z())), "r": float(c.Radius())}

    def cyl_params(face):
        ad = BRepAdaptor_Surface(face.wrapped, True)
        if ad.GetType() != GeomAbs_Cylinder:
            return None
        cyl = ad.Cylinder()
        ax = cyl.Axis()
        loc = ax.Location()
        d = ax.Direction()
        return {
            "axis_loc": (float(loc.X()), float(loc.Y()), float(loc.Z())),
            "axis_dir": (float(d.X()), float(d.Y()), float(d.Z())),
            "r": float(cyl.Radius()),
        }

    def find_circle_edge_near(shp, target_center, tol=0.10):
        tx, ty, tz = target_center
        best = None
        best_dist = 1e9
        for ei, e in enumerate(shp.Edges()):
            cp = circle_params(e)
            if not cp:
                continue
            cx, cy, cz = cp["center"]
            dist = ((cx - tx) ** 2 + (cy - ty) ** 2 + (cz - tz) ** 2) ** 0.5
            if dist < best_dist and dist <= tol:
                best_dist = dist
                best = (ei, cp)
        return best, best_dist

    def find_cyl_faces(shp, y0, z0, r_target, r_tol=0.02, yz_tol=0.25, axis_tol=0.03):
        hits = []
        for fi, f in enumerate(shp.Faces()):
            cp = cyl_params(f)
            if not cp:
                continue
            axx, axy, axz = cp["axis_loc"]
            dx, dy, dz = cp["axis_dir"]
            if abs(abs(dx) - 1.0) < axis_tol and abs(dy) < axis_tol and abs(dz) < axis_tol:
                if abs(axy - y0) < yz_tol and abs(axz - z0) < yz_tol:
                    if abs(cp["r"] - r_target) <= r_tol:
                        hits.append((fi, cp))
        return hits

    # --- targets (explicit, per instruction) ---
    axis_dir = cq.Vector(1, 0, 0)
    y0 = 3.51
    z0 = 2.943
    r_bore = 0.85
    groove_depth = 0.10
    r_groove = r_bore + groove_depth
    lug_spans = [(4.698, 5.298), (6.948, 7.548)]

    # groove pattern params
    n_grooves = 16
    ang_spacing = 2 * pi / n_grooves
    groove_ang_width = ang_spacing * 0.40  # narrow wedge within each pitch
    half_w = groove_ang_width / 2.0
    wedge_R = 2.2  # must exceed r_groove and clear into material

    # mouths (must remain unchanged) - use circular rims at these centers
    mouth_centers = [
        (4.698, y0, z0),
        (5.298, y0, z0),
        (6.948, y0, z0),
        (7.548, y0, z0),
    ]

    print("TARGETS: bore axis=[1,0,0]; centerline=(Y=%.3f, Z=%.3f)" % (y0, z0))
    print("TARGETS: bore r=%.3f (d=%.3f); groove depth=%.3f => groove-bottom r=%.3f" % (r_bore, 2 * r_bore, groove_depth, r_groove))
    print("TARGETS: lug spans X=[%.3f..%.3f] and [%.3f..%.3f]" % (lug_spans[0][0], lug_spans[0][1], lug_spans[1][0], lug_spans[1][1]))
    print("TARGETS: grooves n=%d spacing=%.3fdeg width=%.3fdeg" % (n_grooves, ang_spacing * 180 / pi, groove_ang_width * 180 / pi))

    # --- resolve and print the referenced face indices mentioned in the prompt (diagnostic only) ---
    faces0 = base.Faces()
    for idx in [44, 46, 103, 176, 78, 90, 89, 79]:
        if idx >= len(faces0):
            print("DIAG: face #%d missing (faces=%d)" % (idx, len(faces0)))
            continue
        f = faces0[idx]
        c = f.Center()
        cp = cyl_params(f)
        if cp:
            print("DIAG: face #%d center=%s CYL r=%.4f axis_loc=%s axis_dir=%s" % (
                idx,
                tuple(round(v, 6) for v in (c.x, c.y, c.z)),
                cp["r"],
                tuple(round(v, 6) for v in cp["axis_loc"]),
                tuple(round(v, 6) for v in cp["axis_dir"]),
            ))
        else:
            n = f.normalAt()
            print("DIAG: face #%d center=%s non-cyl; normal=%s" % (
                idx,
                tuple(round(v, 6) for v in (c.x, c.y, c.z)),
                tuple(round(v, 6) for v in (n.x, n.y, n.z)),
            ))

    # --- attempt loop: adjust end_margin if mouths get altered ---
    out = None
    for attempt, end_margin in enumerate([0.02, 0.08], start=1):
        print("\nGROOVE-ATTEMPT %d: end_margin=%.3f (leave rim rings at both ends of each lug)" % (attempt, end_margin))

        # build all groove-cut sectors as a Compound (no need to fuse)
        sectors = []
        for (xmin, xmax) in lug_spans:
            x_start = xmin + end_margin
            x_end = xmax - end_margin
            L = x_end - x_start
            if L <= 0:
                print("WARNING: span X=[%.3f..%.3f] invalid after margin %.3f" % (xmin, xmax, end_margin))
                continue

            # tubular shell (thickness = groove_depth)
            cyl_big = cq.Solid.makeCylinder(r_groove, L, cq.Vector(x_start, y0, z0), axis_dir)
            cyl_small = cq.Solid.makeCylinder(r_bore, L, cq.Vector(x_start, y0, z0), axis_dir)
            shell = cyl_big.cut(cyl_small)

            # wedge plane for sketches: normal +X, origin at x_start, oriented so sketch X->+Y, Y->+Z
            pln = cq.Plane(origin=(x_start, y0, z0), normal=(1, 0, 0), xDir=(0, 1, 0))

            for k in range(n_grooves):
                ang = k * ang_spacing
                a1 = ang - half_w
                a2 = ang + half_w

                # triangle in YZ (sketch coords are (Y,Z) offsets)
                p0 = (0.0, 0.0)
                p1 = (wedge_R * cos(a1), wedge_R * sin(a1))
                p2 = (wedge_R * cos(a2), wedge_R * sin(a2))
                wedge = cq.Workplane(pln).polyline([p0, p1, p2]).close().extrude(L)

                sector = shell.intersect(wedge.val() if hasattr(wedge, "val") else wedge)
                sectors.append(sector)

            print("SPAN: X=%.3f..%.3f (L=%.3f) grooves=%d" % (x_start, x_end, L, n_grooves))

        print("SELECTED: %d groove sectors for cutting" % (len(sectors),))
        if len(sectors) == 0:
            print("ERROR: no sectors built; returning base unchanged (would be a no-op)")
            return base

        tool = cq.Compound.makeCompound(sectors)
        bb = tool.BoundingBox()
        print("TOOL: bbox x[%.6f, %.6f] y[%.6f, %.6f] z[%.6f, %.6f]" % (bb.xmin, bb.xmax, bb.ymin, bb.ymax, bb.zmin, bb.zmax))
        print("CHECK: tool centerline nominal Y=%.3f Z=%.3f (tool bb center Y=%.6f Z=%.6f)" % (y0, z0, bb.center.y, bb.center.z))

        # apply cut from the *original* base each attempt
        out_try = base.cut(tool)

        # --- verify mouths unchanged (r=0.85 circle edges at the four centers) ---
        mouth_ok = True
        for mc in mouth_centers:
            (hit, dist) = find_circle_edge_near(out_try, mc, tol=0.12)
            if not hit:
                mouth_ok = False
                print("MOUTH-CHECK: target_center=%s -> NO circle edge found within tol" % (tuple(round(x, 6) for x in mc),))
                continue
            ei, cp = hit
            dr = cp["r"] - r_bore
            print("MOUTH-CHECK: center=%s matched edge #%d circle_center=%s r=%.6f (dr=%.6f) dist=%.6f" % (
                tuple(round(x, 6) for x in mc),
                ei,
                tuple(round(x, 6) for x in cp["center"]),
                cp["r"],
                dr,
                dist,
            ))
            if abs(dr) > 0.005:
                mouth_ok = False

        # --- verify achieved bore centerline from existing r=0.85 cylinder faces ---
        bore_faces = find_cyl_faces(out_try, y0, z0, r_bore, r_tol=0.02)
        print("SELECTED: %d cylindrical faces at r~%.3f for bore verification" % (len(bore_faces), r_bore))
        if bore_faces:
            y_meas = sum(cp["axis_loc"][1] for _, cp in bore_faces) / len(bore_faces)
            z_meas = sum(cp["axis_loc"][2] for _, cp in bore_faces) / len(bore_faces)
            r_meas = sum(cp["r"] for _, cp in bore_faces) / len(bore_faces)
            print("ACHIEVED: bore axis_loc avg Y=%.6f (dY=%.6f) Z=%.6f (dZ=%.6f) r=%.6f" % (y_meas, y_meas - y0, z_meas, z_meas - z0, r_meas))
        else:
            print("ACHIEVED: could not find r=0.85 bore cylinder faces after grooving (unexpected)")

        # --- verify groove depth by finding new cylindrical faces at r~0.95 ---
        groove_faces = find_cyl_faces(out_try, y0, z0, r_groove, r_tol=0.03)
        print("SELECTED: %d cylindrical faces at r~%.3f for groove-bottom verification" % (len(groove_faces), r_groove))
        groove_depth_meas = None
        if groove_faces and bore_faces:
            r_g_meas = sum(cp["r"] for _, cp in groove_faces) / len(groove_faces)
            r_b_meas = sum(cp["r"] for _, cp in bore_faces) / len(bore_faces)
            groove_depth_meas = r_g_meas - r_b_meas
            print("ACHIEVED: groove-bottom r_avg=%.6f; bore r_avg=%.6f => groove_depth=%.6f (target=%.6f, d=%.6f)" % (
                r_g_meas, r_b_meas, groove_depth_meas, groove_depth, groove_depth_meas - groove_depth
            ))
        else:
            print("ACHIEVED: could not robustly measure groove depth from cylindrical faces (need both r=0.85 and r=0.95 faces)")

        # always print design intent (requested)
        print("REPORT: groove angular spacing=%.6fdeg; groove angular width=%.6fdeg" % (ang_spacing * 180 / pi, groove_ang_width * 180 / pi))
        for (xmin, xmax) in lug_spans:
            print("REPORT: axial groove span used (with margin)=X %.6f..%.6f" % (xmin + end_margin, xmax - end_margin))
        print("REPORT: bore centerline nominal (Y=%.3f, Z=%.3f)" % (y0, z0))

        if mouth_ok:
            print("VERIFY: PASS (mouth circle rims preserved within tolerance)")
            out = out_try
            break
        else:
            print("VERIFY: FAIL (mouth rims altered or not found) -> will retry with larger end_margin")

    return out if out is not None else base