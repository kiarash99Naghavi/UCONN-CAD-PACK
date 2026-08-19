def my_cad_function(args):
    import cadquery as cq

    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input

    base = shape.val() if hasattr(shape, "val") else shape
    solids = base.Solids() if hasattr(base, "Solids") else []
    print(f"SELECTED: {len(solids)} solids in imported shape")
    solid = solids[0] if len(solids) else base

    faces = solid.Faces()
    print(f"SELECTED: {len(faces)} faces on base solid")

    # Targets from sub-goal
    plateau_y = 14.0
    top_y = 15.0
    emboss_h = 1.0
    target_center_x = -0.273
    target_center_z = -51.776
    x_min_allowed, x_max_allowed = -7.491, 6.945
    z_min_allowed, z_max_allowed = -97.4, -6.152

    print(
        "TARGETS: center(X,Z)=({:.3f},{:.3f}) plateau_y={:.3f} top_y={:.3f} emboss_h={:.3f} Xlim[{:.3f},{:.3f}] Zlim[{:.3f},{:.3f}]".format(
            target_center_x,
            target_center_z,
            plateau_y,
            top_y,
            emboss_h,
            x_min_allowed,
            x_max_allowed,
            z_min_allowed,
            z_max_allowed,
        )
    )

    # Find plateau face near Y=14 with +Y normal (for sanity only)
    plateau_cands = []
    top_cands = []
    for i, f in enumerate(faces):
        try:
            if f.geomType() != "PLANE":
                continue
            c = f.Center()
            n = f.normalAt()
            if n.y > 0.9 and abs(c.y - plateau_y) < 0.25:
                plateau_cands.append((i, f, f.Area(), c, n))
            if n.y > 0.9 and abs(c.y - top_y) < 0.25:
                top_cands.append((i, f, f.Area(), c, n))
        except Exception:
            pass

    print(f"SELECTED: {len(plateau_cands)} planar faces near Y=14 (+Y normal) for plateau reference")
    if plateau_cands:
        plateau_cands.sort(key=lambda t: t[2], reverse=True)
        i, f, a, c, n = plateau_cands[0]
        print(
            "REF PLATEAU(best): face_idx={} area={:.3f} center=[{:.3f},{:.3f},{:.3f}] normal=[{:.3f},{:.3f},{:.3f}]".format(
                i, a, c.x, c.y, c.z, n.x, n.y, n.z
            )
        )
    else:
        print("WARNING: could not find plateau face near Y=14 with +Y normal; proceeding with absolute plane")

    print(f"SELECTED: {len(top_cands)} planar faces near Y=15 (+Y normal) for top reference")
    if top_cands:
        top_cands.sort(key=lambda t: t[2], reverse=True)
        i, f, a, c, n = top_cands[0]
        print(
            "REF TOP(best): face_idx={} area={:.3f} center=[{:.3f},{:.3f},{:.3f}] normal=[{:.3f},{:.3f},{:.3f}]".format(
                i, a, c.x, c.y, c.z, n.x, n.y, n.z
            )
        )

    def bb_info(shp):
        bb = shp.BoundingBox()
        cx = 0.5 * (bb.xmin + bb.xmax)
        cy = 0.5 * (bb.ymin + bb.ymax)
        cz = 0.5 * (bb.zmin + bb.zmax)
        return bb, (cx, cy, cz)

    # Build a plane on the plateau: normal +Y, orient text along world-Z; height spans world-X
    plane = cq.Plane(origin=(target_center_x, plateau_y, target_center_z), normal=(0, 1, 0), xDir=(0, 0, 1))
    print(
        "TEXT PLANE: origin=[{:.3f},{:.3f},{:.3f}] normal=[{:.1f},{:.1f},{:.1f}] xDir=[{:.1f},{:.1f},{:.1f}]".format(
            plane.origin.x,
            plane.origin.y,
            plane.origin.z,
            plane.zDir.x,
            plane.zDir.y,
            plane.zDir.z,
            plane.xDir.x,
            plane.xDir.y,
            plane.xDir.z,
        )
    )

    # Create text solid (no combine) and then fuse it ourselves
    txt_wp = cq.Workplane(plane)
    font_used = "Arial"
    try:
        txt_wp = txt_wp.text(
            "TOP",
            10.0,
            emboss_h,
            combine=False,
            clean=True,
            font=font_used,
            kind="regular",
            halign="center",
            valign="center",
        )
    except Exception as e:
        # Arial may not exist; fall back to common substitute while keeping instruction intent.
        print(f"WARNING: failed to build text with font '{font_used}': {e}")
        font_used = "Liberation Sans"
        txt_wp = cq.Workplane(plane).text(
            "TOP",
            10.0,
            emboss_h,
            combine=False,
            clean=True,
            font=font_used,
            kind="regular",
            halign="center",
            valign="center",
        )

    text_solid = txt_wp.val()

    bb0, c0 = bb_info(text_solid)
    print(
        "TEXT(raw,font={}): bb_center=[{:.3f},{:.3f},{:.3f}] bbox X[{:.3f},{:.3f}] Y[{:.3f},{:.3f}] Z[{:.3f},{:.3f}]".format(
            font_used,
            c0[0],
            c0[1],
            c0[2],
            bb0.xmin,
            bb0.xmax,
            bb0.ymin,
            bb0.ymax,
            bb0.zmin,
            bb0.zmax,
        )
    )

    # First correction: center to target (X,Z), and make top flush at Y=15
    dx = target_center_x - c0[0]
    dz = target_center_z - c0[2]
    dy = top_y - bb0.ymax
    if abs(dx) > 1e-4 or abs(dy) > 1e-4 or abs(dz) > 1e-4:
        print(
            "CORRECT(1): translating text by d=[{:.6f},{:.6f},{:.6f}] to hit center(XZ) and Ymax=15".format(
                dx, dy, dz
            )
        )
        text_solid = text_solid.translate((dx, dy, dz))

    bb1, c1 = bb_info(text_solid)
    print(
        "TEXT(after1): bb_center=[{:.3f},{:.3f},{:.3f}] bbox X[{:.3f},{:.3f}] Y[{:.3f},{:.3f}] Z[{:.3f},{:.3f}]".format(
            c1[0],
            c1[1],
            c1[2],
            bb1.xmin,
            bb1.xmax,
            bb1.ymin,
            bb1.ymax,
            bb1.zmin,
            bb1.zmax,
        )
    )

    # Second correction: force Ymin to exactly 14 if drift occurred (should keep Ymax at 15 if thickness is 1)
    dy2 = plateau_y - bb1.ymin
    if abs(dy2) > 1e-4:
        print(f"CORRECT(2): translating text by dy={dy2:.6f} to hit Ymin=14")
        text_solid = text_solid.translate((0, dy2, 0))

    bb2, c2 = bb_info(text_solid)
    print(
        "TEXT(final): bb_center=[{:.3f},{:.3f},{:.3f}] footprint X[{:.3f},{:.3f}] Z[{:.3f},{:.3f}] Yrange[{:.3f},{:.3f}]".format(
            c2[0],
            c2[1],
            c2[2],
            bb2.xmin,
            bb2.xmax,
            bb2.zmin,
            bb2.zmax,
            bb2.ymin,
            bb2.ymax,
        )
    )

    within_x = (bb2.xmin >= x_min_allowed - 1e-6) and (bb2.xmax <= x_max_allowed + 1e-6)
    within_z = (bb2.zmin >= z_min_allowed - 1e-6) and (bb2.zmax <= z_max_allowed + 1e-6)
    within_y = (abs(bb2.ymin - plateau_y) < 1e-3) and (abs(bb2.ymax - top_y) < 1e-3)
    center_ok = (abs(c2[0] - target_center_x) < 0.5) and (abs(c2[2] - target_center_z) < 0.5)
    print(
        "CHECK(text): center_dX={:.3f} center_dZ={:.3f} | within X={} Z={} | Yspan_ok={}".format(
            c2[0] - target_center_x,
            c2[2] - target_center_z,
            within_x,
            within_z,
            within_y,
        )
    )

    if not (within_x and within_z and within_y and center_ok):
        print("WARNING: constraints not fully met; proceeding but diagnostics above show the mismatch")

    # Fuse text onto the part
    out = solid.fuse(text_solid)

    # Self-check: isolate added material and print achieved bbox center/footprint/Y range
    try:
        added = out.cut(solid)
        added_sols = added.Solids() if hasattr(added, "Solids") else []
        print(f"SELECTED: {len(added_sols)} solids in ADDED(TEXT) (expect 1 if fuse merged)")
        bbA, cA = bb_info(added)
        print(
            "ADDED(TEXT): bb_center=[{:.3f},{:.3f},{:.3f}] footprint X[{:.3f},{:.3f}] Z[{:.3f},{:.3f}] Yrange[{:.3f},{:.3f}]".format(
                cA[0],
                cA[1],
                cA[2],
                bbA.xmin,
                bbA.xmax,
                bbA.zmin,
                bbA.zmax,
                bbA.ymin,
                bbA.ymax,
            )
        )
        print(
            "ADDED vs TARGET: dCenterX={:.3f} dCenterZ={:.3f} dYmin={:.3f} dYmax={:.3f}".format(
                cA[0] - target_center_x,
                cA[2] - target_center_z,
                bbA.ymin - plateau_y,
                bbA.ymax - top_y,
            )
        )
        print(
            "ADDED CONSTRAINTS: within X={} within Z={} Yspan_exact={}".format(
                (bbA.xmin >= x_min_allowed - 1e-6) and (bbA.xmax <= x_max_allowed + 1e-6),
                (bbA.zmin >= z_min_allowed - 1e-6) and (bbA.zmax <= z_max_allowed + 1e-6),
                (abs(bbA.ymin - plateau_y) < 1e-3) and (abs(bbA.ymax - top_y) < 1e-3),
            )
        )
    except Exception as e:
        print(f"ERROR: could not compute ADDED(TEXT) as out.cut(solid): {e}")

    return out