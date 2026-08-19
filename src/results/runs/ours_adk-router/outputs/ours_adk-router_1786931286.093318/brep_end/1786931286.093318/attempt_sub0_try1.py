def my_cad_function(args):
    import cadquery as cq
    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input

    base = shape.val() if hasattr(shape, "val") else shape

    # --- Resolve and report target face #7 (per provided geometry index) ---
    faces = base.Faces()
    f7 = faces[7]
    try:
        f7_c = f7.Center()
    except Exception:
        f7_c = cq.Vector(0, 0, 0)
    try:
        f7_a = f7.Area()
    except Exception:
        f7_a = None
    try:
        f7_n = f7.normalAt()
    except Exception:
        f7_n = None
    print(f"SELECTED: 1 face for +X end face #7  idx=[7] center={list(map(float, (f7_c.x, f7_c.y, f7_c.z)))} area={float(f7_a) if f7_a is not None else None} normal={list(map(float, (f7_n.x, f7_n.y, f7_n.z))) if f7_n is not None else None}")

    # --- Sub-goal named numbers (explicit) ---
    Xc = 80.0
    Zc = 4.0
    axis_dir = cq.Vector(0.0, 1.0, 0.0)

    outer_r = 7.0
    outer_y0, outer_y1 = 24.0, 34.0
    outer_h = outer_y1 - outer_y0

    bore_r = 5.0
    bore_y0, bore_y1 = 25.0, 34.0
    bore_h = bore_y1 - bore_y0

    print("TARGET placement numbers:")
    print(f"  axis_dir={[0.0, 1.0, 0.0]}")
    print(f"  axis line passes through (X,Z)=({Xc},{Zc})")
    print(f"  OUTER: r={outer_r} Y={outer_y0}..{outer_y1} (h={outer_h})")
    print(f"  BORE : r={bore_r}  Y={bore_y0}..{bore_y1} (h={bore_h})")

    def build_bearing(xc, zc):
        boss = cq.Solid.makeCylinder(
            outer_r,
            outer_h,
            cq.Vector(xc, outer_y0, zc),
            axis_dir,
        )
        bore = cq.Solid.makeCylinder(
            bore_r,
            bore_h,
            cq.Vector(xc, bore_y0, zc),
            axis_dir,
        )
        return boss, bore

    boss, bore = build_bearing(Xc, Zc)

    # Placement self-check against named axis center X=80, Z=4; correct in same attempt if needed.
    bb_boss = boss.BoundingBox()
    boss_cx = 0.5 * (bb_boss.xmin + bb_boss.xmax)
    boss_cz = 0.5 * (bb_boss.zmin + bb_boss.zmax)
    dx, dz = (Xc - boss_cx), (Zc - boss_cz)
    print(f"CHECK boss bbox-center (X,Z)=({boss_cx:.6f},{boss_cz:.6f}) vs target ({Xc:.6f},{Zc:.6f})  delta=({dx:.6f},{dz:.6f})")

    if abs(dx) > 1e-6 or abs(dz) > 1e-6:
        # Rebuild with corrected X/Z (keeps absolute intent)
        Xc2, Zc2 = Xc + dx, Zc + dz
        print(f"CORRECTING bearing axis center by rebuild: new (X,Z)=({Xc2:.6f},{Zc2:.6f})")
        boss, bore = build_bearing(Xc2, Zc2)
        bb_boss2 = boss.BoundingBox()
        boss_cx2 = 0.5 * (bb_boss2.xmin + bb_boss2.xmax)
        boss_cz2 = 0.5 * (bb_boss2.zmin + bb_boss2.zmax)
        print(f"RECHECK boss bbox-center (X,Z)=({boss_cx2:.6f},{boss_cz2:.6f})")

    # --- Apply booleans (join boss to arm, then cut bore) ---
    with_boss = base.fuse(boss)
    out = with_boss.cut(bore)

    # --- REPORT: isolate change (added / removed) ---
    added = out.cut(base)
    removed = base.cut(out)

    try:
        bb_added = added.BoundingBox()
        c_added = added.Center()
        print(f"ADDED material: center={list(map(float, (c_added.x, c_added.y, c_added.z)))} bbox=(xmin={bb_added.xmin:.3f}, xmax={bb_added.xmax:.3f}, ymin={bb_added.ymin:.3f}, ymax={bb_added.ymax:.3f}, zmin={bb_added.zmin:.3f}, zmax={bb_added.zmax:.3f})")
    except Exception as e:
        print(f"ADDED material: failed to measure ({e})")

    try:
        bb_removed = removed.BoundingBox()
        c_removed = removed.Center()
        print(f"REMOVED material: center={list(map(float, (c_removed.x, c_removed.y, c_removed.z)))} bbox=(xmin={bb_removed.xmin:.3f}, xmax={bb_removed.xmax:.3f}, ymin={bb_removed.ymin:.3f}, ymax={bb_removed.ymax:.3f}, zmin={bb_removed.zmin:.3f}, zmax={bb_removed.zmax:.3f})")
    except Exception as e:
        print(f"REMOVED material: failed to measure ({e})")

    # --- Print achieved (intended) bearing definition ---
    # Axis/center: explicitly defined by construction.
    print("ACHIEVED bearing (by construction):")
    print(f"  axis_dir={[float(axis_dir.x), float(axis_dir.y), float(axis_dir.z)]}")
    print(f"  axis passes through X={Xc:.6f}, Z={Zc:.6f} (center at [80.0, *, 4.0])")
    print(f"  outer radius={outer_r:.6f}, Y extents={outer_y0:.6f}..{outer_y1:.6f}")
    print(f"  bore  radius={bore_r:.6f}, Y extents={bore_y0:.6f}..{bore_y1:.6f}")

    return out