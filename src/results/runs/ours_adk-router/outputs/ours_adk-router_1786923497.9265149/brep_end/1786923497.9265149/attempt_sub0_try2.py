def my_cad_function(args):
    import cadquery as cq

    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input
    base = shape.val() if hasattr(shape, "val") else shape

    def v3(v):
        return (float(v.x), float(v.y), float(v.z))

    # Resolve and print the referenced faces to confirm indexing matches the provided geometry index
    faces = base.Faces()
    f53 = faces[53]
    f72 = faces[72]
    n53 = f53.normalAt()
    n72 = f72.normalAt()
    print(
        f"RESOLVED: face#53 center={tuple(round(c, 3) for c in v3(f53.Center()))} "
        f"area={round(f53.Area(), 3)} normal={tuple(round(c, 3) for c in v3(n53))}"
    )
    print(
        f"RESOLVED: face#72 center={tuple(round(c, 3) for c in v3(f72.Center()))} "
        f"area={round(f72.Area(), 3)} normal={tuple(round(c, 3) for c in v3(n72))}"
    )

    # Parameters from sub-goal
    w = 50.8
    h = 50.8
    r = 7.62
    t = 2.54
    y_top0 = 27.94
    y_top1 = 30.48
    y_bot0 = -15.24
    y_bot1 = -17.78

    print("TARGETS:")
    print(f"  outer contour X/Z bounds = ±25.4 (width/height {w}x{h}), corner radius r={r}")
    print(f"  top cover Y range = {y_top0}..{y_top1} (thickness {t})")
    print(f"  bot cover Y range = {y_bot1}..{y_bot0} (thickness {t})")

    def make_cover(y_seat, normal_y, thickness):
        # Build a rounded-rectangle PROFILE (2D) and then extrude; avoid 3D fillet (needs solid on stack)
        pl = cq.Plane(origin=(0.0, float(y_seat), 0.0), xDir=(1.0, 0.0, 0.0), normal=(0.0, float(normal_y), 0.0))
        print(f"PLANE: origin={(0.0, float(y_seat), 0.0)} normal={(0.0, float(normal_y), 0.0)}")

        wp = (
            cq.Workplane(pl)
            .sketch()
            .rect(w, h)
            .vertices()
            .fillet(r)
            .finalize()
            .extrude(thickness)
        )
        cover_solid = wp.val()
        if cover_solid.ShapeType() != "Solid":
            print(f"WARNING: cover built is not a Solid (type={cover_solid.ShapeType()})")
        return cover_solid

    # Build the two covers as NEW separate solids (do not boolean with existing solids)
    cover_top = make_cover(y_top0, +1.0, t)
    cover_bot = make_cover(y_bot0, -1.0, t)

    def bbox_report(tag, solid):
        bb = solid.BoundingBox()
        print(
            f"BBOX {tag}: x[{bb.xmin:.3f},{bb.xmax:.3f}] y[{bb.ymin:.3f},{bb.ymax:.3f}] z[{bb.zmin:.3f},{bb.zmax:.3f}] "
            f"(xlen={bb.xlen:.3f} ylen={bb.ylen:.3f} zlen={bb.zlen:.3f})"
        )
        return bb

    bb_top = bbox_report("top(pre)", cover_top)
    bb_bot = bbox_report("bot(pre)", cover_bot)

    tol = 1e-3

    # Correct placement in the same attempt if achieved Y ranges differ
    # Top: expect ymin=27.94, ymax=30.48
    if abs(bb_top.ymin - y_top0) > tol or abs(bb_top.ymax - y_top1) > tol:
        shift = y_top0 - bb_top.ymin
        print(f"ADJUST: top cover Y shift {shift:.6f} to match y[{y_top0},{y_top1}]")
        cover_top = cover_top.translate((0.0, float(shift), 0.0))
        bb_top = bbox_report("top(post)", cover_top)

    # Bottom: expect ymin=-17.78, ymax=-15.24
    if abs(bb_bot.ymin - y_bot1) > tol or abs(bb_bot.ymax - y_bot0) > tol:
        shift = y_bot0 - bb_bot.ymax
        print(f"ADJUST: bot cover Y shift {shift:.6f} to match y[{y_bot1},{y_bot0}]")
        cover_bot = cover_bot.translate((0.0, float(shift), 0.0))
        bb_bot = bbox_report("bot(post)", cover_bot)

    # Verification prints per sub-goal
    print("VERIFY:")
    print(f"  top X range = {bb_top.xmin:.3f}..{bb_top.xmax:.3f} (target -25.4..25.4)")
    print(f"  top Z range = {bb_top.zmin:.3f}..{bb_top.zmax:.3f} (target -25.4..25.4)")
    print(f"  top Y range = {bb_top.ymin:.3f}..{bb_top.ymax:.3f} (target {y_top0}..{y_top1})")
    print(f"  bot X range = {bb_bot.xmin:.3f}..{bb_bot.xmax:.3f} (target -25.4..25.4)")
    print(f"  bot Z range = {bb_bot.zmin:.3f}..{bb_bot.zmax:.3f} (target -25.4..25.4)")
    print(f"  bot Y range = {bb_bot.ymin:.3f}..{bb_bot.ymax:.3f} (target {y_bot1}..{y_bot0})")

    # Keep original solids untouched; return a compound with the two added solids as separate bodies
    sols = base.Solids()
    print(f"SELECTED: {len(sols)} existing solids from input")
    out = cq.Compound.makeCompound(list(sols) + [cover_top, cover_bot])
    print("SELECTED: 2 new cover solids added as separate bodies")

    return out