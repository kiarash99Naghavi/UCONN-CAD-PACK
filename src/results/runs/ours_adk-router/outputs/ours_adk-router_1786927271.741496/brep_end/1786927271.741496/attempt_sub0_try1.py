def my_cad_function(args):
    import cadquery as cq

    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input

    base = shape.val() if hasattr(shape, "val") else shape
    solids = base.Solids()
    print(f"SELECTED: {len(solids)} solids in imported shape")
    if len(solids) != 1:
        print("WARNING: expected exactly 1 solid; will edit the first one")
    solid = solids[0]

    # Resolve indexed reference face (#25) to confirm mirror plane is world ZX at Y=0
    faces = solid.Faces()
    print(f"SELECTED: {len(faces)} faces on base solid")
    if len(faces) > 25:
        f25 = faces[25]
        c25 = f25.Center()
        n25 = f25.normalAt()
        a25 = f25.Area()
        print(
            "REF: face #25 resolved  area={:.3f}  center=[{:.3f},{:.3f},{:.3f}]  normal=[{:.3f},{:.3f},{:.3f}]".format(
                a25, c25.x, c25.y, c25.z, n25.x, n25.y, n25.z
            )
        )
    else:
        print("ERROR: could not resolve face #25 (faces < 26)")

    # Mirror across world ZX plane (XZ) at Y=0
    orig_bb = solid.BoundingBox()
    print(
        "BASE BBOX: ymin={:.6f} ymax={:.6f} (expected 0..15)".format(
            orig_bb.ymin, orig_bb.ymax
        )
    )

    wp = cq.Workplane(cq.Plane.XY()).newObject([solid])
    mirrored_wp = wp.mirror(mirrorPlane="XZ")  # mirror across XZ/ZX plane => Y->-Y
    mirrored = mirrored_wp.val()

    mir_bb = mirrored.BoundingBox()
    print(
        "MIRROR (raw) BBOX: ymin={:.6f} ymax={:.6f} (expected -15..0)".format(
            mir_bb.ymin, mir_bb.ymax
        )
    )

    # Self-correct mirror in the same attempt if numerical drift exists
    desired_mir_ymin = -orig_bb.ymax
    dy = desired_mir_ymin - mir_bb.ymin
    if abs(dy) > 1e-4:
        print(f"CORRECT: translating mirrored copy by dy={dy:.6f} to hit ymin={desired_mir_ymin:.6f}")
        mirrored = mirrored.translate((0, dy, 0))
        mir_bb = mirrored.BoundingBox()
        print(
            "MIRROR (corrected) BBOX: ymin={:.6f} ymax={:.6f}".format(
                mir_bb.ymin, mir_bb.ymax
            )
        )
    else:
        print("CORRECT: no mirrored translation needed (dy within tolerance)")

    # Unite
    out = solid.fuse(mirrored)

    out_bb = out.BoundingBox()
    print(
        "OUT BBOX: ymin={:.6f} ymax={:.6f}  (TARGET -15..15)  dymin={:.6f} dymax={:.6f}".format(
            out_bb.ymin,
            out_bb.ymax,
            out_bb.ymin - (-15.0),
            out_bb.ymax - (15.0),
        )
    )

    # Find mirrored plateau counterpart (original plateau face #1 is at Y=14, so mirrored should be at Y=-14)
    plateau_candidates = []
    out_faces = out.Faces()
    for i, f in enumerate(out_faces):
        try:
            if f.geomType() != "PLANE":
                continue
            c = f.Center()
            if abs(c.y - (-14.0)) < 0.25:
                plateau_candidates.append((i, f, f.Area(), c, f.normalAt()))
        except Exception:
            pass

    print(f"SELECTED: {len(plateau_candidates)} planar faces near Y=-14 for mirrored plateau check")
    if plateau_candidates:
        plateau_candidates.sort(key=lambda t: t[2], reverse=True)
        i, f, a, c, n = plateau_candidates[0]
        print(
            "MIRRORED PLATEAU (best): face_idx={} area={:.3f} center.y={:.6f} (TARGET -14) delta={:.6f} normal=[{:.3f},{:.3f},{:.3f}]".format(
                i, a, c.y, c.y - (-14.0), n.x, n.y, n.z
            )
        )
    else:
        print("MIRRORED PLATEAU: not found near Y=-14 (will rely on bbox correction above)")

    # Final check: if extents are still off materially, print a loud warning (do not move the original)
    if (abs(out_bb.ymin - (-15.0)) > 1e-3) or (abs(out_bb.ymax - 15.0) > 1e-3):
        print("WARNING: Y extents not at target -15..15 after mirror+fuse; transform may be wrong")

    return out