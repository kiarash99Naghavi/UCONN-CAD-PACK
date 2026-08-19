def my_cad_function(args):
    import cadquery as cq
    import math
    from OCP.BRepAdaptor import BRepAdaptor_Curve, BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_Circle, GeomAbs_Cylinder

    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input
    base = shape.val() if hasattr(shape, "val") else shape

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

    # --- Absolute numbers from the sub-goal (s9) ---
    z0 = -23.996
    z1 = -21.996
    thick = z1 - z0

    pA = (-37.304, 44.606)    # endpoint hole axis (left/top)
    pM = (2.720, 23.179)      # middle hole axis
    pB = (42.743, 1.753)      # endpoint hole axis (right/bottom)

    r_caps = 4.9
    width = 2.0 * r_caps      # 9.8

    d_bore = 6.0
    r_bore = d_bore / 2.0
    bore_axis = (0.0, 0.0, 1.0)

    dx = pB[0] - pA[0]
    dy = pB[1] - pA[1]
    dist = math.hypot(dx, dy)
    ang_deg = math.degrees(math.atan2(dy, dx))

    # colinearity check of pM on AB
    # distance from pM to line through pA->pB
    if dist > 1e-9:
        t = ((pM[0] - pA[0]) * dx + (pM[1] - pA[1]) * dy) / (dist * dist)
        proj = (pA[0] + t * dx, pA[1] + t * dy)
        off = math.hypot(pM[0] - proj[0], pM[1] - proj[1])
    else:
        off = float("nan")

    print("CHECK: target endpoint centers pA=", pA, " pB=", pB)
    print("CHECK: target middle hole center pM=", pM, " (off-line distance=", round(off, 6), "mm)")
    print("CHECK: target width=", width, " (r_caps=", r_caps, ")")
    print("CHECK: target Z span=", (z0, z1), " thickness=", thick)
    print("CHECK: target bore d=", d_bore, " axis=", bore_axis)
    print("CHECK: computed major-axis angle=", round(ang_deg, 6), "deg (expected ~ -28.16)")

    exp_ang = -28.16
    # Correct in same attempt if angle is flipped (or otherwise off)
    if abs(ang_deg - exp_ang) > 0.5:
        print("WARN: major-axis angle deviates >0.5deg; flipping endpoints to correct")
        pA, pB = pB, pA
        dx = pB[0] - pA[0]
        dy = pB[1] - pA[1]
        dist = math.hypot(dx, dy)
        ang_deg = math.degrees(math.atan2(dy, dx))
        print("CHECK: corrected endpoint centers pA=", pA, " pB=", pB)
        print("CHECK: corrected major-axis angle=", round(ang_deg, 6), "deg")

    # --- Resolve and print the referenced face/edge tags (diagnostic) ---
    faces = base.Faces()
    edges = base.Edges()
    print("INFO: total faces=", len(faces), " total edges=", len(edges))

    # Faces named in the sub-goal (s9): planar faces #167,#169,#180,#182; torus faces #168,#170,#181,#187
    face_tags = [167, 169, 180, 182, 168, 170, 181, 187]
    ok_face_tags = [i for i in face_tags if i < len(faces)]
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

    # Hole rim edges (three inner loops referenced): edge_idx [356,357,358,382,383,384]
    edge_tags = [356, 357, 358, 382, 383, 384]
    ok_edge_tags = [i for i in edge_tags if i < len(edges)]
    print("SELECTED:", len(ok_edge_tags), "edges by provided edge_idx tags for diagnostic idx=", ok_edge_tags)
    for i in ok_edge_tags:
        e = edges[i]
        cen, rad = circle_center_radius(e)
        if cen is None:
            print("  edge#", i, "type=", e.geomType(), "(not circle)")
        else:
            print(
                "  edge#", i,
                "circle center=", tuple(round(v, 3) for v in cen),
                " r=", round(rad, 4),
                " d=", round(2 * rad, 4),
            )

    # --- Isolate solid s9 by bbox z=-23.996..-21.996 ---
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

    tol = 1e-3
    cand = []
    for si, s in enumerate(sols):
        bb = s.BoundingBox()
        if abs(bb.zmin - z0) < tol and abs(bb.zmax - z1) < tol:
            cand.append(si)
    print("SELECTED:", len(cand), "solids matching bbox Z=-23.996..-21.996 candidates=", cand)
    if len(cand) != 1:
        print("ERROR: expected exactly one solid for s9 isolation; returning unmodified shape")
        return shape

    s9_idx = cand[0]
    s9 = sols[s9_idx]

    # Confirm existing s9 has three r=2.5 (d=5.0) bores with axis ~Z
    s9_cyl = []
    for fi, f in enumerate(s9.Faces()):
        info = cyl_info(f)
        if not info:
            continue
        ax = info["axis"]
        if abs(info["r"] - 2.5) < 1e-3 and abs(ax[0]) < 1e-6 and abs(ax[1]) < 1e-6 and abs(abs(ax[2]) - 1.0) < 1e-6:
            s9_cyl.append((fi, info))
    print("SELECTED:", len(s9_cyl), "cyl faces on isolated s9 with r=2.5 axis=Z (existing d=5.0 bores)")
    for fi, info in s9_cyl:
        print(
            "  s9 face", fi,
            "bore axis XY=", (round(info["xy"][0], 3), round(info["xy"][1], 3)),
            " r=", round(info["r"], 3),
            " axis=", tuple(round(v, 6) for v in info["axis"]),
        )

    # --- Build NEW constant-width obround (capsule) prism at world Z=-23.996..-21.996 ---
    mx = 0.5 * (pA[0] + pB[0])
    my = 0.5 * (pA[1] + pB[1])

    plane_z0 = cq.Plane(origin=(0, 0, z0), normal=(0, 0, 1))
    plane_caps = cq.Plane(origin=(mx, my, z0), normal=(0, 0, 1), xDir=(dx, dy, 0))
    print("PLANE: capsule base plane origin=", (round(mx, 3), round(my, 3), z0), " normal=(0,0,1) xDir=", (round(dx, 3), round(dy, 3), 0))

    rect = cq.Workplane(plane_caps).rect(dist, width).extrude(thick).val()
    capA = cq.Workplane(plane_z0).center(pA[0], pA[1]).circle(r_caps).extrude(thick).val()
    capB = cq.Workplane(plane_z0).center(pB[0], pB[1]).circle(r_caps).extrude(thick).val()
    capsule = rect.fuse(capA).fuse(capB)

    bb_caps = capsule.BoundingBox()
    print(
        "TOOL: capsule bbox=",
        (round(bb_caps.xmin, 3), round(bb_caps.ymin, 3), round(bb_caps.zmin, 3), round(bb_caps.xmax, 3), round(bb_caps.ymax, 3), round(bb_caps.zmax, 3)),
        " (expect zmin=-23.996 zmax=-21.996)",
    )

    # --- Cut three d=6.0 bores through the capsule ---
    bore_centers = [pA, pM, pB]
    bore_solids = []
    for (x, y) in bore_centers:
        bore_solids.append(cq.Workplane(plane_z0).center(x, y).circle(r_bore).extrude(thick).val())
    bore_tool = cq.Compound.makeCompound(bore_solids)
    print("SELECTED:", len(bore_solids), "cut cylinders for d=6.0 bores at centers=", bore_centers)

    s9_new = capsule.cut(bore_tool)

    # --- Placement/achievement prints (as requested) ---
    print("ACHIEVED: endpoint centers=", pA, pB)
    print("ACHIEVED: hole centers=", bore_centers)
    print("ACHIEVED: width=", width)
    print("ACHIEVED: major-axis angle=", round(ang_deg, 6), "deg")

    # --- Self-check delta volumes vs old s9 ---
    added = s9_new.cut(s9)
    removed = s9.cut(s9_new)
    print(
        "CHECK: s9 old vol=", round(s9.Volume(), 3),
        " s9 new vol=", round(s9_new.Volume(), 3),
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

    # --- Reassemble all solids unchanged except replace only original s9 ---
    out_sols = []
    for i, s in enumerate(sols):
        out_sols.append(s9_new if i == s9_idx else s)
    out = cq.Compound.makeCompound(out_sols)

    print("DONE: replaced only solid index", s9_idx, "(s9) and left other", len(sols) - 1, "solids untouched")
    return out