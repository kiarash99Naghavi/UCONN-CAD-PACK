def my_cad_function(args):
    import cadquery as cq
    import math
    from OCP.BRepAdaptor import BRepAdaptor_Curve, BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_Circle, GeomAbs_Cylinder

    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input
    base = shape.val() if hasattr(shape, "val") else shape

    # ---------------- helpers ----------------
    def circle_center_radius(edge):
        ad = BRepAdaptor_Curve(edge.wrapped)
        if ad.GetType() != GeomAbs_Circle:
            return None, None
        circ = ad.Circle()
        loc = circ.Location()
        return (loc.X(), loc.Y(), loc.Z()), circ.Radius()

    def cyl_info(face):
        ad = BRepAdaptor_Surface(face.wrapped)
        if ad.GetType() != GeomAbs_Cylinder:
            return None
        cyl = ad.Cylinder()
        loc = cyl.Location()
        ax = cyl.Axis().Direction()
        return {
            "loc": (loc.X(), loc.Y(), loc.Z()),
            "xy": (loc.X(), loc.Y()),
            "r": cyl.Radius(),
            "axis": (ax.X(), ax.Y(), ax.Z()),
        }

    def uniq_pts_xy(pts, tol=1e-4):
        out = []
        for p in pts:
            found = False
            for q in out:
                if math.hypot(p[0] - q[0], p[1] - q[1]) <= tol:
                    found = True
                    break
            if not found:
                out.append(p)
        return out

    def nearest(pt, targets):
        best = None
        bestd = 1e99
        for t in targets:
            d = math.hypot(pt[0] - t[0], pt[1] - t[1])
            if d < bestd:
                bestd = d
                best = t
        return best, bestd

    # ---------------- absolute targets from sub-goal ----------------
    z0 = -25.996
    z1 = -23.996
    thick = z1 - z0

    exp_A = (-37.304, 1.753)
    exp_M = (2.720, 23.179)
    exp_B = (42.743, 44.606)

    width = 9.8
    r_caps = 4.9

    d_bore = 6.0
    r_bore = d_bore / 2.0
    bore_axis = (0.0, 0.0, 1.0)

    # expected major-axis angle from +X, using exp_A->exp_B
    exp_ang = math.degrees(math.atan2(exp_B[1] - exp_A[1], exp_B[0] - exp_A[0]))

    print("CHECK: target endpoints A=", exp_A, " B=", exp_B)
    print("CHECK: target mid M=", exp_M)
    print("CHECK: target Z span=", (z0, z1), " thickness=", thick)
    print("CHECK: target width=", width, " r_caps=", r_caps)
    print("CHECK: target hole d=", d_bore, " axis=", bore_axis)
    print("CHECK: target major-axis angle=", round(exp_ang, 6), "deg (expected ~+28.16deg)")

    # ---------------- diagnostics: resolve referenced face tags if present ----------------
    faces = base.Faces()
    edges = base.Edges()
    print("INFO: total faces=", len(faces), " total edges=", len(edges))

    diag_face_tags = [196, 198, 209, 211, 197, 199, 210, 216, 200, 212]
    ok_face_tags = [i for i in diag_face_tags if i < len(faces)]
    print("SELECTED:", len(ok_face_tags), "faces by provided face_idx tags for diagnostic idx=", ok_face_tags)
    for i in ok_face_tags:
        f = faces[i]
        c = f.Center()
        info = cyl_info(f)
        if info:
            print(
                "  face#", i,
                "type=CYL r=", round(info["r"], 4),
                " axis=", tuple(round(v, 6) for v in info["axis"]),
                " center=", (round(c.x, 3), round(c.y, 3), round(c.z, 3)),
                " area=", round(f.Area(), 3),
            )
        else:
            print(
                "  face#", i,
                "type=", f.geomType(),
                " center=", (round(c.x, 3), round(c.y, 3), round(c.z, 3)),
                " area=", round(f.Area(), 3),
            )

    # ---------------- isolate solid s10 by Z thickness ----------------
    sols = base.Solids()
    print("INFO: imported solids count=", len(sols))
    for si, s in enumerate(sols):
        bb = s.BoundingBox()
        print(
            "  solid", si,
            "vol=", round(s.Volume(), 3),
            "bbox x=", (round(bb.xmin, 3), round(bb.xmax, 3)),
            "bbox y=", (round(bb.ymin, 3), round(bb.ymax, 3)),
            "bbox z=", (round(bb.zmin, 3), round(bb.zmax, 3)),
        )

    tolz = 1e-3
    cand = []
    for si, s in enumerate(sols):
        bb = s.BoundingBox()
        if abs(bb.zmin - z0) < tolz and abs(bb.zmax - z1) < tolz:
            cand.append(si)
    print("SELECTED:", len(cand), "solids matching bbox Z=-25.996..-23.996 candidates=", cand)
    if len(cand) != 1:
        print("ERROR: expected exactly one solid for s10 isolation; returning unmodified shape")
        return shape

    s10_idx = cand[0]
    s10 = sols[s10_idx]

    # ---------------- extract existing d=5 hole centers from s10 ----------------
    hole_xy = []
    for f in s10.Faces():
        info = cyl_info(f)
        if not info:
            continue
        ax = info["axis"]
        if abs(info["r"] - 2.5) < 2e-3 and abs(ax[0]) < 1e-6 and abs(ax[1]) < 1e-6 and abs(abs(ax[2]) - 1.0) < 1e-6:
            hole_xy.append(info["xy"])
    hole_xy = uniq_pts_xy(hole_xy, tol=1e-4)
    print("SELECTED:", len(hole_xy), "unique existing hole axes on isolated s10 (expect 3) xy=", [(round(x,3), round(y,3)) for x,y in hole_xy])
    if len(hole_xy) != 3:
        print("ERROR: did not find 3 existing d=5 holes on s10; returning unmodified shape")
        return shape

    # map extracted holes to expected A/M/B by nearest
    exp_pts = [exp_A, exp_M, exp_B]
    mapped = {}
    used = set()
    for label, exp in [("A", exp_A), ("M", exp_M), ("B", exp_B)]:
        best = None
        bestd = 1e99
        besti = None
        for i, p in enumerate(hole_xy):
            if i in used:
                continue
            d = math.hypot(p[0] - exp[0], p[1] - exp[1])
            if d < bestd:
                bestd = d
                best = p
                besti = i
        mapped[label] = (best, bestd)
        if besti is not None:
            used.add(besti)

    print("CHECK: extracted->expected deltas:")
    for label, exp in [("A", exp_A), ("M", exp_M), ("B", exp_B)]:
        p, d = mapped[label]
        print("  ", label, " extracted=", (round(p[0], 6), round(p[1], 6)), " expected=", exp, " delta=", round(d, 6), "mm")

    if max(mapped["A"][1], mapped["M"][1], mapped["B"][1]) > 0.5:
        print("ERROR: hole centers on s10 do not match expected A/M/B within 0.5mm; returning unmodified shape")
        return shape

    pA = (float(mapped["A"][0][0]), float(mapped["A"][0][1]))
    pM = (float(mapped["M"][0][0]), float(mapped["M"][0][1]))
    pB = (float(mapped["B"][0][0]), float(mapped["B"][0][1]))

    # major axis and angle
    dx = pB[0] - pA[0]
    dy = pB[1] - pA[1]
    dist = math.hypot(dx, dy)
    ang_deg = math.degrees(math.atan2(dy, dx))

    # colinearity check: distance from pM to AB
    if dist > 1e-9:
        t = ((pM[0] - pA[0]) * dx + (pM[1] - pA[1]) * dy) / (dist * dist)
        proj = (pA[0] + t * dx, pA[1] + t * dy)
        off = math.hypot(pM[0] - proj[0], pM[1] - proj[1])
    else:
        off = float("nan")

    print("CHECK: achieved (from extracted holes) endpoints A=", (round(pA[0],6), round(pA[1],6)), " B=", (round(pB[0],6), round(pB[1],6)))
    print("CHECK: achieved middle M=", (round(pM[0],6), round(pM[1],6)), " off-line=", round(off, 6), "mm")
    print("CHECK: achieved major-axis angle=", round(ang_deg, 6), "deg; target=", round(exp_ang, 6), "deg")

    # Correct in same attempt if something about endpoint ordering produced the wrong signed angle
    # (Keep endpoint centers fixed to the named A/B by mapping above, but if angle is off, bail)
    if abs(ang_deg - exp_ang) > 0.5:
        print("ERROR: major-axis angle deviates >0.5deg from target; returning unmodified shape")
        return shape

    # ---------------- build NEW constant-width capsule prism ----------------
    mx = 0.5 * (pA[0] + pB[0])
    my = 0.5 * (pA[1] + pB[1])

    plane_z0 = cq.Plane(origin=(0, 0, z0), normal=(0, 0, 1))
    plane_caps = cq.Plane(origin=(mx, my, z0), normal=(0, 0, 1), xDir=(dx, dy, 0))
    print("PLANE: capsule base plane origin=", (round(mx, 6), round(my, 6), z0), " normal=(0,0,1)")
    print("PLANE: capsule xDir=", (round(dx, 6), round(dy, 6), 0), " dist=", round(dist, 6))

    # rectangle (length = center-to-center distance) + full circles at the endpoints
    rect = cq.Workplane(plane_caps).rect(dist, width).extrude(thick).val()
    capA = cq.Workplane(plane_z0).center(pA[0], pA[1]).circle(r_caps).extrude(thick).val()
    capB = cq.Workplane(plane_z0).center(pB[0], pB[1]).circle(r_caps).extrude(thick).val()
    capsule = rect.fuse(capA).fuse(capB)

    bb_caps = capsule.BoundingBox()
    print(
        "TOOL: capsule bbox=",
        (round(bb_caps.xmin, 3), round(bb_caps.ymin, 3), round(bb_caps.zmin, 3), round(bb_caps.xmax, 3), round(bb_caps.ymax, 3), round(bb_caps.zmax, 3)),
        " (expect zmin=-25.996 zmax=-23.996)",
    )

    # ---------------- enlarge holes to d=6 through thickness ----------------
    # Use an over-thick cut tool so it always cuts cleanly through the 2mm solid.
    cut_z0 = z0 - 1.0
    cut_th = thick + 2.0
    plane_cut = cq.Plane(origin=(0, 0, cut_z0), normal=(0, 0, 1))

    bore_centers = [pA, pM, pB]
    bore_solids = []
    for (x, y) in bore_centers:
        bore_solids.append(cq.Workplane(plane_cut).center(x, y).circle(r_bore).extrude(cut_th).val())
    bore_tool = cq.Compound.makeCompound(bore_solids)
    print("SELECTED:", len(bore_solids), "cut cylinders for d=6.0 bores at centers=", [(round(x,3), round(y,3)) for x,y in bore_centers])

    s10_new = capsule.cut(bore_tool)

    # ---------------- optional: add small perimeter fillets similar to original r=0.1 blends ----------------
    fillet_r = 0.1
    edge_tol = 5e-4
    outer_edges = []
    for e in s10_new.Edges():
        bb = e.BoundingBox()
        on_z0 = abs(bb.zmin - z0) < edge_tol and abs(bb.zmax - z0) < edge_tol
        on_z1 = abs(bb.zmin - z1) < edge_tol and abs(bb.zmax - z1) < edge_tol
        if not (on_z0 or on_z1):
            continue
        cen, rad = circle_center_radius(e)
        # Exclude the hole rim circles (r=3.0)
        if rad is not None and abs(rad - r_bore) < 2e-3:
            continue
        outer_edges.append(e)

    print("SELECTED:", len(outer_edges), "edges for outer perimeter fillet r=0.1 (excluding d=6 hole rims)")
    try:
        s10_new = s10_new.fillet(fillet_r, outer_edges)
        print("DONE: applied perimeter fillet r=", fillet_r)
    except Exception as ex:
        print("WARN: perimeter fillet failed; continuing without fillet. reason=", repr(ex))

    # ---------------- achieved prints (requested) ----------------
    print("ACHIEVED: endpoint centers=", (round(pA[0],6), round(pA[1],6)), (round(pB[0],6), round(pB[1],6)))
    print("ACHIEVED: hole centers=", [(round(x,6), round(y,6)) for x,y in bore_centers])
    print("ACHIEVED: width=", width)
    print("ACHIEVED: major-axis angle=", round(ang_deg, 6), "deg")

    # ---------------- verify resulting d=6 holes on s10_new ----------------
    new_hole_xy = []
    for f in s10_new.Faces():
        info = cyl_info(f)
        if not info:
            continue
        ax = info["axis"]
        if abs(info["r"] - r_bore) < 2e-3 and abs(ax[0]) < 1e-6 and abs(ax[1]) < 1e-6 and abs(abs(ax[2]) - 1.0) < 1e-6:
            new_hole_xy.append(info["xy"])
    new_hole_xy = uniq_pts_xy(new_hole_xy, tol=1e-4)
    print("SELECTED:", len(new_hole_xy), "unique resulting d=6 hole axes on s10_new xy=", [(round(x,3), round(y,3)) for x,y in new_hole_xy])
    for target in [exp_A, exp_M, exp_B]:
        _, d = nearest(target, new_hole_xy)
        print("CHECK: nearest new hole to expected", target, " delta=", round(d, 6), "mm")

    # ---------------- self-check delta vs old s10 ----------------
    added = s10_new.cut(s10)
    removed = s10.cut(s10_new)
    print(
        "CHECK: s10 old vol=", round(s10.Volume(), 3),
        " s10 new vol=", round(s10_new.Volume(), 3),
        " added vol=", round(added.Volume(), 3),
        " removed vol=", round(removed.Volume(), 3),
    )
    if added.Volume() > 1e-9:
        bb = added.BoundingBox()
        c = added.Center()
        print(
            "CHECK: added center=", (round(c.x, 3), round(c.y, 3), round(c.z, 3)),
            " added bbox=", (round(bb.xmin, 3), round(bb.ymin, 3), round(bb.zmin, 3), round(bb.xmax, 3), round(bb.ymax, 3), round(bb.zmax, 3)),
        )
    if removed.Volume() > 1e-9:
        bb = removed.BoundingBox()
        c = removed.Center()
        print(
            "CHECK: removed center=", (round(c.x, 3), round(c.y, 3), round(c.z, 3)),
            " removed bbox=", (round(bb.xmin, 3), round(bb.ymin, 3), round(bb.zmin, 3), round(bb.xmax, 3), round(bb.ymax, 3), round(bb.zmax, 3)),
        )

    # ---------------- reassemble all solids unchanged except replace only s10 ----------------
    out_sols = []
    for i, s in enumerate(sols):
        out_sols.append(s10_new if i == s10_idx else s)
    out = cq.Compound.makeCompound(out_sols)

    print("DONE: replaced only solid index", s10_idx, "(s10) and left other", len(sols) - 1, "solids untouched")
    return out