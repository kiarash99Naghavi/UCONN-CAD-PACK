def my_cad_function(args):
    import cadquery as cq
    import math

    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input
    base = shape.val() if hasattr(shape, "val") else shape

    # --- Sub-goal absolute targets (s10) ---
    z_top = -23.996  # +Z face of s10
    z_bot = -25.996
    thick = z_top - z_bot  # 2.0

    pA = (-37.304, 1.753)   # endpoint 1
    pM = (2.720, 23.179)    # middle
    pB = (42.743, 44.606)   # endpoint 2

    r_caps = 4.9
    width = 2.0 * r_caps  # 9.8

    d_hole = 6.0
    r_hole = d_hole / 2.0

    dx = pB[0] - pA[0]
    dy = pB[1] - pA[1]
    dist = math.hypot(dx, dy)
    ang_deg = math.degrees(math.atan2(dy, dx))

    # Colinearity check of pM on AB
    if dist > 1e-12:
        t = ((pM[0] - pA[0]) * dx + (pM[1] - pA[1]) * dy) / (dist * dist)
        proj = (pA[0] + t * dx, pA[1] + t * dy)
        off = math.hypot(pM[0] - proj[0], pM[1] - proj[1])
    else:
        off = float("nan")

    print("CHECK(s10): endpoint centers pA=", pA, " pB=", pB)
    print("CHECK(s10): middle hole center pM=", pM, " off-line distance=", round(off, 6), "mm")
    print("CHECK(s10): Z limits z_bot=", z_bot, " z_top=", z_top, " thickness=", thick)
    print("CHECK(s10): capsule r=", r_caps, " width=", width)
    print("CHECK(s10): hole diameter=", d_hole, " axis=[0,0,1]")
    print("CHECK(s10): major-axis angle atan2(dy,dx)=", round(ang_deg, 6), "deg (expected ~ +28.16)")

    # --- Isolate solid s10 by bbox Z=-25.996..-23.996 ---
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

    tol = 1e-3
    cand = []
    for si, s in enumerate(sols):
        bb = s.BoundingBox()
        if abs(bb.zmin - z_bot) < tol and abs(bb.zmax - z_top) < tol:
            cand.append(si)
    print("SELECTED:", len(cand), "solids matching bbox Z=-25.996..-23.996 candidates=", cand)
    if len(cand) != 1:
        print("ERROR: expected exactly one solid for s10 isolation; returning unmodified shape")
        return shape

    s10_idx = cand[0]
    s10 = sols[s10_idx]

    # --- Build capsule on the +Z face plane at z_top, extruding DOWN to z_bot ---
    mx = 0.5 * (pA[0] + pB[0])
    my = 0.5 * (pA[1] + pB[1])

    plane_top = cq.Plane(origin=(0, 0, z_top), normal=(0, 0, 1))
    plane_caps = cq.Plane(origin=(mx, my, z_top), normal=(0, 0, 1), xDir=(dx, dy, 0))
    print("PLANE(s10): anchor +Z face plane origin=", (0, 0, z_top), " normal=(0,0,1)")
    print(
        "PLANE(s10): capsule plane origin=", (round(mx, 3), round(my, 3), z_top),
        " xDir=", (round(dx, 3), round(dy, 3), 0),
    )

    # Rectangle (tangent side lines) + end circles; then extrude -thick
    rect = cq.Workplane(plane_caps).rect(dist, width).extrude(-thick).val()
    capA = cq.Workplane(plane_top).center(pA[0], pA[1]).circle(r_caps).extrude(-thick).val()
    capB = cq.Workplane(plane_top).center(pB[0], pB[1]).circle(r_caps).extrude(-thick).val()
    capsule = rect.fuse(capA).fuse(capB)

    bb_caps = capsule.BoundingBox()
    print(
        "TOOL(s10): capsule bbox=",
        (round(bb_caps.xmin, 3), round(bb_caps.ymin, 3), round(bb_caps.zmin, 3),
         round(bb_caps.xmax, 3), round(bb_caps.ymax, 3), round(bb_caps.zmax, 3)),
        " expect zmin=", z_bot, " zmax=", z_top,
    )

    # --- Union capsule with imported s10 (adds stiffness material) ---
    s10_fused = s10.fuse(capsule)

    # Self-check: material added by capsule union (before hole recut)
    added_union = s10_fused.cut(s10)
    print(
        "CHECK(s10): pre-cut union volumes old=", round(s10.Volume(), 3),
        " fused=", round(s10_fused.Volume(), 3),
        " added_by_union=", round(added_union.Volume(), 3),
    )
    if added_union.Volume() > 1e-9:
        bb = added_union.BoundingBox()
        c = added_union.Center()
        print(
            "CHECK(s10): added_by_union center=", (round(c.x, 3), round(c.y, 3), round(c.z, 3)),
            " bbox=", (round(bb.xmin, 3), round(bb.ymin, 3), round(bb.zmin, 3),
                      round(bb.xmax, 3), round(bb.ymax, 3), round(bb.zmax, 3)),
        )

    # --- Cut three concentric through-cylinders d=6.0 along axis [0,0,1] ---
    bore_centers = [pA, pM, pB]
    # Make tool slightly longer than thickness to guarantee through-cut
    cut_len = thick + 1.0
    bore_solids = []
    for (x, y) in bore_centers:
        bore_solids.append(cq.Workplane(plane_top).center(x, y).circle(r_hole).extrude(-cut_len).val())
    bore_tool = cq.Compound.makeCompound(bore_solids)
    print("SELECTED:", len(bore_solids), "cut cylinders for d=6.0 bores at centers=", bore_centers, " len=", cut_len)

    s10_new = s10_fused.cut(bore_tool)

    # --- Achievement report (as requested) ---
    print("ACHIEVED(s10): endpoint centers=", pA, pB)
    print("ACHIEVED(s10): all three hole centers=", bore_centers)
    print("ACHIEVED(s10): Z limits=", (z_bot, z_top), " thickness=", thick)
    print("ACHIEVED(s10): width=", width, " (should be 9.8)")
    print("ACHIEVED(s10): hole diameter=", d_hole)
    print("ACHIEVED(s10): major-axis angle=", round(ang_deg, 6), "deg (expected ~ +28.16)")

    # Delta volumes vs original s10 (after union+cut)
    added = s10_new.cut(s10)
    removed = s10.cut(s10_new)
    print(
        "CHECK(s10): final volumes old=", round(s10.Volume(), 3),
        " new=", round(s10_new.Volume(), 3),
        " added=", round(added.Volume(), 3),
        " removed=", round(removed.Volume(), 3),
    )
    if added.Volume() > 1e-9:
        bb = added.BoundingBox()
        c = added.Center()
        print(
            "CHECK(s10): added center=", (round(c.x, 3), round(c.y, 3), round(c.z, 3)),
            " added bbox=", (round(bb.xmin, 3), round(bb.ymin, 3), round(bb.zmin, 3),
                             round(bb.xmax, 3), round(bb.ymax, 3), round(bb.zmax, 3)),
        )
    if removed.Volume() > 1e-9:
        bb = removed.BoundingBox()
        c = removed.Center()
        print(
            "CHECK(s10): removed center=", (round(c.x, 3), round(c.y, 3), round(c.z, 3)),
            " removed bbox=", (round(bb.xmin, 3), round(bb.ymin, 3), round(bb.zmin, 3),
                               round(bb.xmax, 3), round(bb.ymax, 3), round(bb.zmax, 3)),
        )

    # --- Reassemble: replace only s10; keep all other solids unchanged (including s9 as-is) ---
    out_sols = []
    for i, s in enumerate(sols):
        out_sols.append(s10_new if i == s10_idx else s)
    out = cq.Compound.makeCompound(out_sols)

    print("DONE: replaced only solid index", s10_idx, "(s10) and left other", len(sols) - 1, "solids untouched")
    return out