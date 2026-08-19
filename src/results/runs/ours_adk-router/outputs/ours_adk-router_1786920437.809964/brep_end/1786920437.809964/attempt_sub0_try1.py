def my_cad_function(args):
    import cadquery as cq
    from OCP.BRepAdaptor import BRepAdaptor_Surface, BRepAdaptor_Curve
    from OCP.GeomAbs import GeomAbs_Cylinder, GeomAbs_Circle

    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input
    base = shape.val() if hasattr(shape, "val") else shape

    # --- helpers ---
    def vlen(v):
        return (v.x * v.x + v.y * v.y + v.z * v.z) ** 0.5

    def circle_params(edge):
        ad = BRepAdaptor_Curve(edge.wrapped)
        if ad.GetType() != GeomAbs_Circle:
            return None
        c = ad.Circle()
        p = c.Location()
        return {
            "center": (p.X(), p.Y(), p.Z()),
            "r": float(c.Radius()),
        }

    def cyl_params(face):
        ad = BRepAdaptor_Surface(face.wrapped, True)
        if ad.GetType() != GeomAbs_Cylinder:
            return None
        cyl = ad.Cylinder()
        ax = cyl.Axis()
        loc = ax.Location()
        d = ax.Direction()
        dv = cq.Vector(float(d.X()), float(d.Y()), float(d.Z()))
        return {
            "axis_loc": (float(loc.X()), float(loc.Y()), float(loc.Z())),
            "axis_dir": (dv.x, dv.y, dv.z),
            "r": float(cyl.Radius()),
        }

    def find_circle_edge_near(shp, target_center, tol=0.08):
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

    def find_bore_cyl_faces(shp, y0, z0, r_target=0.85):
        hits = []
        for fi, f in enumerate(shp.Faces()):
            cp = cyl_params(f)
            if not cp:
                continue
            axx, axy, axz = cp["axis_loc"]
            dx, dy, dz = cp["axis_dir"]
            # axis ~ X (either direction) and close to the named centerline in Y/Z
            if abs(abs(dx) - 1.0) < 0.03 and abs(dy) < 0.03 and abs(dz) < 0.03:
                if abs(axy - y0) < 0.25 and abs(axz - z0) < 0.25:
                    # prefer the bore radius
                    if abs(cp["r"] - r_target) < 0.4:
                        hits.append((fi, cp))
        return hits

    # --- named targets (self-check list) ---
    target_axis = (1.0, 0.0, 0.0)
    target_y = 3.51
    target_z = 2.943
    target_d = 1.7
    target_r = target_d / 2.0
    lug_spans = [(4.698, 5.298), (6.948, 7.548)]

    print("TARGETS: axis=%s  centerline=(Y=%.3f, Z=%.3f)  diameter=%.3f (r=%.3f)" % (str(list(target_axis)), target_y, target_z, target_d, target_r))
    print("TARGETS: lug spans X=%.3f..%.3f and X=%.3f..%.3f; preserve open gap" % (lug_spans[0][0], lug_spans[0][1], lug_spans[1][0], lug_spans[1][1]))

    # --- resolve and print the referenced faces/edges on the INPUT ---
    faces0 = base.Faces()
    edges0 = base.Edges()

    bore_face_ids = [44, 46]
    mouth_plane_ids = [78, 90, 89, 79]
    mouth_edge_ids = [162, 163, 173, 174]

    sel_bore_faces = []
    for i in bore_face_ids:
        if i < len(faces0):
            sel_bore_faces.append(faces0[i])
    print(f"SELECTED: {len(sel_bore_faces)} faces for existing bore walls idx={bore_face_ids}")
    for i in bore_face_ids:
        if i >= len(faces0):
            print(f"  INPUT face #{i} missing (faces={len(faces0)})")
            continue
        cp = cyl_params(faces0[i])
        c = faces0[i].Center()
        if cp:
            print("  INPUT face #%d center=%s cyl_r=%.4f axis_loc=%s axis_dir=%s" % (i, tuple(round(x, 6) for x in (c.x, c.y, c.z)), cp["r"], tuple(round(x, 6) for x in cp["axis_loc"]), tuple(round(x, 6) for x in cp["axis_dir"])))
        else:
            print("  INPUT face #%d center=%s (NOT a cylinder by adaptor)" % (i, tuple(round(x, 6) for x in (c.x, c.y, c.z))))

    sel_mouth_planes = []
    for i in mouth_plane_ids:
        if i < len(faces0):
            sel_mouth_planes.append(faces0[i])
    print(f"SELECTED: {len(sel_mouth_planes)} planar faces for bore mouths idx={mouth_plane_ids}")
    for i in mouth_plane_ids:
        if i >= len(faces0):
            print(f"  INPUT plane face #{i} missing")
            continue
        f = faces0[i]
        c = f.Center()
        n = f.normalAt()
        print("  INPUT plane face #%d center=%s normal=%s" % (i, tuple(round(x, 6) for x in (c.x, c.y, c.z)), tuple(round(x, 6) for x in (n.x, n.y, n.z))))

    sel_mouth_edges = []
    for i in mouth_edge_ids:
        if i < len(edges0):
            sel_mouth_edges.append(edges0[i])
    print(f"SELECTED: {len(sel_mouth_edges)} edges for existing mouth loops idx={mouth_edge_ids}")
    for i in mouth_edge_ids:
        if i >= len(edges0):
            print(f"  INPUT edge #{i} missing (edges={len(edges0)})")
            continue
        cp = circle_params(edges0[i])
        if cp:
            print("  INPUT edge #%d circle_center=%s r=%.4f" % (i, tuple(round(x, 6) for x in cp["center"]), cp["r"]))
        else:
            ec = edges0[i].Center()
            print("  INPUT edge #%d (NOT a circle) edge.Center()=%s" % (i, tuple(round(x, 6) for x in (ec.x, ec.y, ec.z))))

    # --- build cut tool: two separate cylinders, one per lug span (do NOT bridge the gap) ---
    overlap = 0.2

    def build_tool(y0, z0, r):
        cyls = []
        for (xmin, xmax) in lug_spans:
            start = xmin - overlap
            height = (xmax - xmin) + 2 * overlap
            cyl = cq.Solid.makeCylinder(r, height, cq.Vector(start, y0, z0), cq.Vector(1, 0, 0))
            cyls.append(cyl)
        tool = cyls[0].fuse(cyls[1])
        return tool

    # --- attempt + self-correct in the same run if measurement differs ---
    y_work = float(target_y)
    z_work = float(target_z)
    r_work = float(target_r)

    out = None
    for attempt in range(2):
        tool = build_tool(y_work, z_work, r_work)
        print("TOOL: attempt=%d y=%.6f z=%.6f r=%.6f" % (attempt + 1, y_work, z_work, r_work))
        bb = tool.BoundingBox()
        print("TOOL: bbox x[%.6f, %.6f] y[%.6f, %.6f] z[%.6f, %.6f]" % (bb.xmin, bb.xmax, bb.ymin, bb.ymax, bb.zmin, bb.zmax))
        # placement self-check vs lug spans and centerline
        print("CHECK: tool spans (should cover only lug X-spans, not the gap). spansX=%.6f..%.6f" % (bb.xmin, bb.xmax))
        print("CHECK: named centerline Y=%.3f (delta %.6f) Z=%.3f (delta %.6f)" % (target_y, y_work - target_y, target_z, z_work - target_z))

        out = base.cut(tool)

        # Verify achieved bore cylinder faces
        cyl_hits = find_bore_cyl_faces(out, target_y, target_z, r_target=target_r)
        print("SELECTED: %d cylindrical faces near target centerline for achieved bore verification" % (len(cyl_hits),))
        for fi, cp in cyl_hits[:10]:
            print("  OUT cyl face #%d r=%.6f axis_loc=%s axis_dir=%s" % (fi, cp["r"], tuple(round(x, 6) for x in cp["axis_loc"]), tuple(round(x, 6) for x in cp["axis_dir"])))

        if cyl_hits:
            ys = [cp["axis_loc"][1] for _, cp in cyl_hits]
            zs = [cp["axis_loc"][2] for _, cp in cyl_hits]
            rs = [cp["r"] for _, cp in cyl_hits]
            y_meas = sum(ys) / len(ys)
            z_meas = sum(zs) / len(zs)
            r_meas = sum(rs) / len(rs)
            d_meas = 2.0 * r_meas
        else:
            y_meas, z_meas, d_meas = None, None, None

        # Verify all four mouths radii (circle edges) at the named centers
        mouth_centers = [
            (4.698, target_y, target_z),
            (5.298, target_y, target_z),
            (6.948, target_y, target_z),
            (7.548, target_y, target_z),
        ]
        mouth_radii = []
        for mc in mouth_centers:
            (hit, dist) = find_circle_edge_near(out, mc, tol=0.10)
            if hit:
                ei, cp = hit
                mouth_radii.append(cp["r"])
                print("MOUTH: target_center=%s matched OUT edge #%d center=%s r=%.6f (center_dist=%.6f)" % (
                    tuple(round(x, 6) for x in mc), ei, tuple(round(x, 6) for x in cp["center"]), cp["r"], dist
                ))
            else:
                mouth_radii.append(None)
                print("MOUTH: target_center=%s matched OUT edge NONE within tol" % (tuple(round(x, 6) for x in mc),))

        # Print achieved bore centerline + diameter
        if d_meas is not None:
            print("ACHIEVED: bore centerline Y=%.6f (delta %.6f), Z=%.6f (delta %.6f), diameter=%.6f (delta %.6f)" % (
                y_meas, y_meas - target_y, z_meas, z_meas - target_z, d_meas, d_meas - target_d
            ))
        else:
            print("ACHIEVED: could not measure bore cylinder faces; will rely on mouth circles")

        # decide whether to correct
        ok = True
        if d_meas is not None:
            if abs(y_meas - target_y) > 0.01 or abs(z_meas - target_z) > 0.01 or abs(d_meas - target_d) > 0.01:
                ok = False
        # also require all 4 mouth radii ~0.85
        for mr in mouth_radii:
            if mr is None or abs(mr - target_r) > 0.01:
                ok = False

        if ok:
            print("VERIFY: PASS (centerline and all four mouths at r=0.85 within tolerance)")
            break

        print("VERIFY: FAIL -> correcting and retrying")
        # corrections (best-effort)
        if d_meas is not None:
            y_work += (target_y - y_meas)
            z_work += (target_z - z_meas)
            r_work += (target_r - (d_meas / 2.0))
        else:
            # if cylinder measure failed but mouths exist, correct by average mouth radius
            valid_mr = [mr for mr in mouth_radii if mr is not None]
            if valid_mr:
                r_avg = sum(valid_mr) / len(valid_mr)
                r_work += (target_r - r_avg)

    return out