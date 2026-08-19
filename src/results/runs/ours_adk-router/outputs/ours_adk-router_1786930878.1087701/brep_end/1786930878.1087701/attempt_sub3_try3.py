def my_cad_function(args):
    import cadquery as cq
    import math
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_Cylinder

    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input
    base = shape.val() if hasattr(shape, "val") else shape

    # ---------------- numbers named by the sub-goal ----------------
    z0, z1 = -25.996, -23.996
    thick = z1 - z0

    # Measured line: [-37.304,1.753] -> [2.720,23.179] -> [42.743,44.606]
    pA = (-37.304, 1.753)
    pM = (2.720, 23.179)
    pB = (42.743, 44.606)

    r_caps = 4.9
    width = 2.0 * r_caps  # 9.8 overall

    d_hole = 6.0
    r_hole = d_hole / 2.0
    axis = (0.0, 0.0, 1.0)

    dx, dy = (pB[0] - pA[0], pB[1] - pA[1])
    dist = math.hypot(dx, dy)
    ang_deg = math.degrees(math.atan2(dy, dx))

    # Colinearity of pM on AB
    if dist > 1e-12:
        t = ((pM[0] - pA[0]) * dx + (pM[1] - pA[1]) * dy) / (dist * dist)
        proj = (pA[0] + t * dx, pA[1] + t * dy)
        off = math.hypot(pM[0] - proj[0], pM[1] - proj[1])
    else:
        off = float("nan")

    print("CHECK: target endpoint centers A=", pA, " B=", pB)
    print("CHECK: target middle hole center M=", pM, " off-line(mm)=", round(off, 6))
    print("CHECK: target width=", width, " (r_caps=", r_caps, ")")
    print("CHECK: target Z span=", (z0, z1), " thickness=", thick)
    print("CHECK: target hole d=", d_hole, " axis=", axis)
    print("CHECK: target major-axis angle=", round(ang_deg, 6), "deg (expected ~ +28.16)")

    # ---------------- helpers ----------------
    def cyl_info(face):
        ad = BRepAdaptor_Surface(face.wrapped)
        if ad.GetType() != GeomAbs_Cylinder:
            return None
        cyl = ad.Cylinder()
        loc = cyl.Location()
        ax = cyl.Axis().Direction()
        return {
            "r": cyl.Radius(),
            "axis": (ax.X(), ax.Y(), ax.Z()),
            "xy": (loc.X(), loc.Y()),
        }

    def uniq_xy(pts, tol=1e-4):
        out = []
        for x, y in pts:
            ok = True
            for xx, yy in out:
                if (x - xx) ** 2 + (y - yy) ** 2 <= tol ** 2:
                    ok = False
                    break
            if ok:
                out.append((x, y))
        return out

    def farthest_pair(pts):
        best = None
        best_d2 = -1.0
        for i in range(len(pts)):
            for j in range(i + 1, len(pts)):
                dx = pts[j][0] - pts[i][0]
                dy = pts[j][1] - pts[i][1]
                d2 = dx * dx + dy * dy
                if d2 > best_d2:
                    best_d2 = d2
                    best = (pts[i], pts[j])
        return best

    # ---------------- isolate solid s10 by bbox Z ----------------
    sols = base.Solids()
    print("INFO: imported solids count=", len(sols))
    for si, s in enumerate(sols):
        bb = s.BoundingBox()
        print(
            "  solid", si,
            "vol=", round(s.Volume(), 3),
            "bbox z=", (round(bb.zmin, 3), round(bb.zmax, 3)),
            "bbox x=", (round(bb.xmin, 3), round(bb.xmax, 3)),
            "bbox y=", (round(bb.ymin, 3), round(bb.ymax, 3)),
        )

    tolz = 1e-3
    cand = []
    for si, s in enumerate(sols):
        bb = s.BoundingBox()
        if abs(bb.zmin - z0) < tolz and abs(bb.zmax - z1) < tolz:
            cand.append(si)
    print("SELECTED:", len(cand), "solids matching bbox Z=-25.996..-23.996 (s10) candidates=", cand)
    if len(cand) < 1:
        print("ERROR: could not isolate s10; proceeding by picking closest z-range solid")
        # fallback: choose solid with minimal |zmin-z0|+|zmax-z1|
        best_i, best_sc = None, 1e99
        for si, s in enumerate(sols):
            bb = s.BoundingBox()
            sc = abs(bb.zmin - z0) + abs(bb.zmax - z1)
            if sc < best_sc:
                best_i, best_sc = si, sc
        cand = [best_i]
        print("SELECTED: fallback solid index=", best_i, " score=", best_sc)

    s10_idx = cand[0]
    s10 = sols[s10_idx]

    # ---------------- build the requested constant-width obround (capsule) ----------------
    mx, my = (0.5 * (pA[0] + pB[0]), 0.5 * (pA[1] + pB[1]))

    plane_caps = cq.Plane(origin=(mx, my, z0), normal=(0, 0, 1), xDir=(dx, dy, 0))
    plane_z0 = cq.Plane(origin=(0, 0, z0), normal=(0, 0, 1))
    print("PLANE: capsule sketch plane origin=", (round(mx, 6), round(my, 6), z0), " normal=(0,0,1) xDir=", (round(dx, 6), round(dy, 6), 0))

    # Rectangle length equals center-to-center distance; circles centered at end-hole axes.
    rect = cq.Workplane(plane_caps).rect(dist, width).extrude(thick).val()
    capA = cq.Workplane(plane_z0).center(pA[0], pA[1]).circle(r_caps).extrude(thick).val()
    capB = cq.Workplane(plane_z0).center(pB[0], pB[1]).circle(r_caps).extrude(thick).val()
    capsule = rect.fuse(capA).fuse(capB)

    bb_caps = capsule.BoundingBox()
    print(
        "TOOL: capsule bbox=",
        (round(bb_caps.xmin, 3), round(bb_caps.ymin, 3), round(bb_caps.zmin, 3), round(bb_caps.xmax, 3), round(bb_caps.ymax, 3), round(bb_caps.zmax, 3)),
        " expect z=", (z0, z1),
    )

    # ---------------- cut 3x d=6.0 through holes along measured axis [0,0,1] ----------------
    cut_z0 = z0 - 1.0
    cut_th = thick + 2.0
    plane_cut = cq.Plane(origin=(0, 0, cut_z0), normal=(0, 0, 1))

    bore_centers = [pA, pM, pB]
    bore_solids = []
    for (x, y) in bore_centers:
        bore_solids.append(cq.Workplane(plane_cut).center(x, y).circle(r_hole).extrude(cut_th).val())
    bore_tool = cq.Compound.makeCompound(bore_solids)
    print("SELECTED:", len(bore_solids), "cut cylinders for d=6.0 holes at centers=", [(round(x, 3), round(y, 3)) for x, y in bore_centers])

    s10_new = capsule.cut(bore_tool)

    # ---------------- achieved prints + self-check + correction in same attempt ----------------
    # Extract resulting hole axes (r=3.0 cylinders) to verify centers
    new_xy = []
    for f in s10_new.Faces():
        info = cyl_info(f)
        if not info:
            continue
        ax = info["axis"]
        if abs(info["r"] - r_hole) < 2e-3 and abs(ax[0]) < 1e-6 and abs(ax[1]) < 1e-6 and abs(abs(ax[2]) - 1.0) < 1e-6:
            new_xy.append(info["xy"])
    new_xy = uniq_xy(new_xy, tol=1e-4)
    print("SELECTED:", len(new_xy), "unique resulting d=6 hole axes on s10_new xy=", [(round(x, 6), round(y, 6)) for x, y in new_xy])

    # Compute achieved angle from farthest pair among the extracted hole centers if available
    achieved_ang = None
    if len(new_xy) >= 2:
        pa, pb = farthest_pair(new_xy)
        achieved_ang = math.degrees(math.atan2(pb[1] - pa[1], pb[0] - pa[0]))

    print("ACHIEVED: endpoint centers=", pA, pB)
    print("ACHIEVED: hole centers=", bore_centers)
    print("ACHIEVED: width=", width)
    print("ACHIEVED: major-axis angle (from targets)=", round(ang_deg, 6), "deg")
    if achieved_ang is not None:
        print("ACHIEVED: major-axis angle (from resulting holes)=", round(achieved_ang, 6), "deg")

    # Correct (translate) if the resulting hole centers differ materially
    exp = [pA, pM, pB]
    if len(new_xy) == 3:
        # match by nearest
        def nearest(pt, pts):
            best = None
            best_d2 = 1e99
            for q in pts:
                d2 = (pt[0] - q[0]) ** 2 + (pt[1] - q[1]) ** 2
                if d2 < best_d2:
                    best_d2 = d2
                    best = q
            return best, math.sqrt(best_d2)

        deltas = []
        for ept in exp:
            q, d = nearest(ept, new_xy)
            deltas.append(d)
            print("CHECK: nearest resulting hole to expected", ept, " is ", (round(q[0], 6), round(q[1], 6)), " delta=", round(d, 6), "mm")

        max_d = max(deltas) if deltas else 0.0
        if max_d > 0.05:
            # translate by average delta of the three (expected - matched)
            offs = []
            for ept in exp:
                q, _ = nearest(ept, new_xy)
                offs.append((ept[0] - q[0], ept[1] - q[1]))
            ox = sum(o[0] for o in offs) / len(offs)
            oy = sum(o[1] for o in offs) / len(offs)
            print("WARN: hole centers off by >0.05mm; applying XY translation correction (ox,oy)=", (round(ox, 6), round(oy, 6)))
            capsule2 = cq.Shape.cast(capsule.moved(cq.Location(cq.Vector(ox, oy, 0))))
            bore_tool2 = cq.Shape.cast(bore_tool.moved(cq.Location(cq.Vector(ox, oy, 0))))
            s10_new = capsule2.cut(bore_tool2)

            # re-extract after correction
            new_xy2 = []
            for f in s10_new.Faces():
                info = cyl_info(f)
                if not info:
                    continue
                ax = info["axis"]
                if abs(info["r"] - r_hole) < 2e-3 and abs(ax[0]) < 1e-6 and abs(ax[1]) < 1e-6 and abs(abs(ax[2]) - 1.0) < 1e-6:
                    new_xy2.append(info["xy"])
            new_xy2 = uniq_xy(new_xy2, tol=1e-4)
            print("SELECTED:", len(new_xy2), "unique resulting d=6 hole axes AFTER correction xy=", [(round(x, 6), round(y, 6)) for x, y in new_xy2])

    # Check resulting Z thickness
    bb_new = s10_new.BoundingBox()
    print("CHECK: s10_new bbox z=", (round(bb_new.zmin, 6), round(bb_new.zmax, 6)), " (target=", (z0, z1), ")")

    # delta volumes vs old s10
    added = s10_new.cut(s10)
    removed = s10.cut(s10_new)
    print(
        "CHECK: s10 old vol=", round(s10.Volume(), 3),
        " s10 new vol=", round(s10_new.Volume(), 3),
        " added vol=", round(added.Volume(), 3),
        " removed vol=", round(removed.Volume(), 3),
    )

    # ---------------- reassemble all solids unchanged except replace only s10 ----------------
    out_sols = [s10_new if i == s10_idx else s for i, s in enumerate(sols)]
    out = cq.Compound.makeCompound(out_sols)
    print("DONE: replaced only solid index", s10_idx, "(s10) and left other", len(sols) - 1, "solids untouched")
    return out