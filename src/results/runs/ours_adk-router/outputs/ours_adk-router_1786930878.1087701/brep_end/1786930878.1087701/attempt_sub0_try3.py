def my_cad_function(args):
    import cadquery as cq
    import math
    from OCP.BRepAdaptor import BRepAdaptor_Curve, BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_Circle, GeomAbs_Cylinder

    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input

    base = shape.val() if hasattr(shape, "val") else shape

    def bb_info(s):
        bb = s.BoundingBox()
        return (bb.xmin, bb.ymin, bb.zmin, bb.xmax, bb.ymax, bb.zmax)

    def circle_center_radius(edge):
        ad = BRepAdaptor_Curve(edge.wrapped)
        if ad.GetType() != GeomAbs_Circle:
            return None, None
        circ = ad.Circle()
        loc = circ.Location()
        return (loc.X(), loc.Y(), loc.Z()), circ.Radius()

    def cyl_axis_xy_radius(face):
        ad = BRepAdaptor_Surface(face.wrapped)
        if ad.GetType() != GeomAbs_Cylinder:
            return None
        cyl = ad.Cylinder()
        loc = cyl.Location()
        ax = cyl.Axis().Direction()
        return {
            "xy": (loc.X(), loc.Y()),
            "z": loc.Z(),
            "r": cyl.Radius(),
            "axis": (ax.X(), ax.Y(), ax.Z()),
        }

    # --- Absolute numbers from the sub-goal ---
    z0 = 23.004
    z1 = 25.004
    thick = z1 - z0
    p1 = (-37.304, 44.606)
    pm = (2.720, 23.179)
    p2 = (42.743, 1.753)
    r_caps = 4.9
    w_caps = 2.0 * r_caps
    d_bore = 6.0
    r_bore = d_bore / 2.0

    dx = p2[0] - p1[0]
    dy = p2[1] - p1[1]
    dist = math.hypot(dx, dy)
    ang_deg = math.degrees(math.atan2(dy, dx))

    print("CHECK: endpoint centers p1=", p1, " p2=", p2)
    print("CHECK: midpoint hole center pm=", pm)
    print("CHECK: capsule radius=", r_caps, " width=", w_caps)
    print("CHECK: Z thickness=", thick, " Z span=", (z0, z1))
    print("CHECK: bore diameter=", d_bore, " axis=[0,0,1]")
    print("CHECK: major-axis angle atan2(dy,dx)=", ang_deg, "deg (expected ~ -28.16 deg)")

    # --- Optional view anchor: confirm global planar face #12 matches s0 lower face at Z=23.004 ---
    try:
        f12 = base.Faces()[12]
        c12 = f12.Center()
        n12 = f12.normalAt()
        print(
            "ANCHOR: resolved global face #12 center=",
            (c12.x, c12.y, c12.z),
            " normal=",
            (n12.x, n12.y, n12.z),
            " area=",
            f12.Area(),
        )
    except Exception as e:
        print("ANCHOR: could not resolve/print face #12:", e)

    # --- Confirm the three d=5.0 bore circle edges on s0 using the provided edge indices ---
    edge_idx = [33, 34, 35, 59, 60, 61]
    edges = base.Edges()
    good_edges = []
    for i in edge_idx:
        if i < len(edges):
            good_edges.append(i)
    print("SELECTED:", len(good_edges), "edges for d=5.0 bore rim confirmation idx=", good_edges)
    for i in good_edges:
        e = edges[i]
        cen, rad = circle_center_radius(e)
        if cen is None:
            print("  edge", i, "geomType=", e.geomType(), "(not a circle?)")
        else:
            print("  edge", i, "circle center=", tuple(round(v, 3) for v in cen), " r=", round(rad, 3))

    # --- Isolate s0 uniquely by bbox Z=23.004..25.004 BEFORE any booleans ---
    sols = base.Solids()
    print("INFO: imported solids count=", len(sols))
    for i, s in enumerate(sols):
        bb = s.BoundingBox()
        print(
            "  solid",
            i,
            "vol=",
            round(s.Volume(), 3),
            "bbox z=",
            (round(bb.zmin, 3), round(bb.zmax, 3)),
            "bbox x=",
            (round(bb.xmin, 3), round(bb.xmax, 3)),
            "bbox y=",
            (round(bb.ymin, 3), round(bb.ymax, 3)),
        )

    tol = 1e-3
    cand = []
    for i, s in enumerate(sols):
        bb = s.BoundingBox()
        if abs(bb.zmin - z0) < tol and abs(bb.zmax - z1) < tol:
            cand.append(i)
    print("SELECTED:", len(cand), "solids matching bbox Z=23.004..25.004 candidates=", cand)
    if len(cand) != 1:
        print("ERROR: expected exactly one solid for s0 isolation; returning unmodified shape")
        return shape

    s0_idx = cand[0]
    s0 = sols[s0_idx]

    # --- Confirm three Z-axis d=5.0 cylindrical faces in the isolated s0 ---
    cyl_faces = []
    for fi, f in enumerate(s0.Faces()):
        info = cyl_axis_xy_radius(f)
        if not info:
            continue
        if abs(info["r"] - 2.5) < 1e-3 and abs(info["axis"][0]) < 1e-6 and abs(info["axis"][1]) < 1e-6 and abs(abs(info["axis"][2]) - 1.0) < 1e-6:
            cyl_faces.append((fi, info))

    print("SELECTED:", len(cyl_faces), "cylindrical faces (r=2.5, axis=Z) for bore confirmation on isolated s0")
    bore_xys = []
    for fi, info in cyl_faces:
        xy = (round(info["xy"][0], 3), round(info["xy"][1], 3))
        bore_xys.append(xy)
        print("  s0 face", fi, "cyl axis XY=", xy, " r=", round(info["r"], 3), " axis=", tuple(round(v, 6) for v in info["axis"]))

    # --- Build additive capsule/obround prism exactly spanning world Z=23.004..25.004 ---
    mx = 0.5 * (p1[0] + p2[0])
    my = 0.5 * (p1[1] + p2[1])

    plane_z0 = cq.Plane(origin=(0, 0, z0), normal=(0, 0, 1))
    plane_caps = cq.Plane(origin=(mx, my, z0), normal=(0, 0, 1), xDir=(dx, dy, 0))
    print("PLANE: capsule base plane origin=", (mx, my, z0), " normal=(0,0,1) xDir=", (dx, dy, 0))

    rect = cq.Workplane(plane_caps).rect(dist, w_caps).extrude(thick).val()
    c1 = cq.Workplane(plane_z0).center(p1[0], p1[1]).circle(r_caps).extrude(thick).val()
    c2 = cq.Workplane(plane_z0).center(p2[0], p2[1]).circle(r_caps).extrude(thick).val()
    capsule = rect.fuse(c1).fuse(c2)

    bb_caps = capsule.BoundingBox()
    print(
        "TOOL: capsule bbox=",
        (
            round(bb_caps.xmin, 3),
            round(bb_caps.ymin, 3),
            round(bb_caps.zmin, 3),
            round(bb_caps.xmax, 3),
            round(bb_caps.ymax, 3),
            round(bb_caps.zmax, 3),
        ),
        " (expect zmin=23.004 zmax=25.004)",
    )

    # Fuse capsule ONLY into isolated s0
    s0_union = s0.fuse(capsule)
    added = s0_union.cut(s0)
    bb_added = added.BoundingBox() if added.Volume() > 0 else None
    print("CHECK: s0 volume=", round(s0.Volume(), 3), " s0+capsule volume=", round(s0_union.Volume(), 3), " added volume=", round(added.Volume(), 3))
    if bb_added:
        print(
            "CHECK: added(bbox)=",
            (
                round(bb_added.xmin, 3),
                round(bb_added.ymin, 3),
                round(bb_added.zmin, 3),
                round(bb_added.xmax, 3),
                round(bb_added.ymax, 3),
                round(bb_added.zmax, 3),
            ),
            " z-extents delta from (23.004..25.004)=",
            (round(bb_added.zmin - z0, 6), round(bb_added.zmax - z1, 6)),
        )

    # --- Subtract three concentric d=6.0 cylinders through Z=23.004..25.004 ---
    cyl_cut_solids = []
    for pt in (p1, pm, p2):
        cyl = cq.Workplane(plane_z0).center(pt[0], pt[1]).circle(r_bore).extrude(thick).val()
        cyl_cut_solids.append(cyl)
    cut_tool = cq.Compound.makeCompound(cyl_cut_solids)

    print("SELECTED:", len(cyl_cut_solids), "cut cylinders for d=6.0 bores at centers=", [p1, pm, p2])

    s0_final = s0_union.cut(cut_tool)
    removed = s0_union.cut(s0_final)
    bb_removed = removed.BoundingBox() if removed.Volume() > 0 else None
    print("CHECK: after bore cuts volume=", round(s0_final.Volume(), 3), " removed volume=", round(removed.Volume(), 3))
    if bb_removed:
        print(
            "CHECK: removed(bbox)=",
            (
                round(bb_removed.xmin, 3),
                round(bb_removed.ymin, 3),
                round(bb_removed.zmin, 3),
                round(bb_removed.xmax, 3),
                round(bb_removed.ymax, 3),
                round(bb_removed.zmax, 3),
            ),
            " z-extents delta from (23.004..25.004)=",
            (round(bb_removed.zmin - z0, 6), round(bb_removed.zmax - z1, 6)),
        )

    # --- Reassemble all 12 solids unchanged except replace only original s0 ---
    out_sols = []
    for i, s in enumerate(sols):
        out_sols.append(s0_final if i == s0_idx else s)
    out = cq.Compound.makeCompound(out_sols)

    print("DONE: replaced only solid index", s0_idx, "(s0) and left other", len(sols) - 1, "solids untouched")
    return out