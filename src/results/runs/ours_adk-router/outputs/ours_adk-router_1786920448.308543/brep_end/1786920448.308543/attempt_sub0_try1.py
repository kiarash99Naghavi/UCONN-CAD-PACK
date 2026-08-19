def my_cad_function(args):
    import cadquery as cq
    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input

    base = shape.val() if hasattr(shape, "val") else shape
    solids = base.Solids()
    print(f"SELECTED: {len(solids)} solids in STEP")

    # Pick s0 as the largest-volume solid (matches index: s0 is the main radiator body)
    vols = [(i, s.Volume()) for i, s in enumerate(solids)]
    vols_sorted = sorted(vols, key=lambda t: t[1], reverse=True)
    s0_i = vols_sorted[0][0]
    s0 = solids[s0_i]
    bb0 = s0.BoundingBox()
    print(f"SELECTED: solid s0 candidate = solids[{s0_i}] vol={s0.Volume():.3f} bbox=({bb0.xmin:.3f},{bb0.ymin:.3f},{bb0.zmin:.3f})..({bb0.xmax:.3f},{bb0.ymax:.3f},{bb0.zmax:.3f})")

    # Resolve face #24 on s0 and confirm it matches the geometry index (top planar face, z=266.7, +Z normal)
    faces0 = s0.Faces()
    print(f"INFO: s0 has {len(faces0)} faces")
    fidx = 24
    f24 = faces0[fidx]
    try:
        f24_area = f24.Area()
        f24_center = f24.Center()
        f24_n = f24.normalAt()  # no args
        print(
            "SELECTED: 1 face for top anchor face #24  "
            f"area={f24_area:.3f} center=[{f24_center.x:.3f},{f24_center.y:.3f},{f24_center.z:.3f}] "
            f"normal=[{f24_n.x:.6f},{f24_n.y:.6f},{f24_n.z:.6f}]"
        )
    except Exception as e:
        print(f"SELECTED: 0 faces for top anchor face #24 (FAILED to query face properties): {e}")
        f24_center = cq.Vector(-88.9, 4.327, 266.7)
        f24_n = cq.Vector(0, 0, 1)

    # --- Parameters from sub-goal ---
    neck_center = cq.Vector(-88.9, 100.0, 266.7)
    axis = cq.Vector(f24_n.x, f24_n.y, f24_n.z)
    # Normalize axis (robust)
    alen = (axis.x**2 + axis.y**2 + axis.z**2) ** 0.5
    if alen < 1e-9:
        axis = cq.Vector(0, 0, 1)
        alen = 1.0
    axis = cq.Vector(axis.x / alen, axis.y / alen, axis.z / alen)

    od = 38.1
    id_clear = 25.4
    r_od = od / 2.0
    r_id = id_clear / 2.0

    z_top = 266.7
    z_neck_end = 296.7
    proj = 30.0

    z_interior = 257.175
    top_thickness = 9.525

    print("TARGETS:")
    print(f"  neck center = [{neck_center.x:.3f}, {neck_center.y:.3f}, {neck_center.z:.3f}]")
    print(f"  axis (from face #24 normal) = [{axis.x:.6f}, {axis.y:.6f}, {axis.z:.6f}]")
    print(f"  OD={od:.3f} (r={r_od:.3f})  clear ID={id_clear:.3f} (r={r_id:.3f})")
    print(f"  outer projection: z {z_top:.3f} -> {z_neck_end:.3f} (len={proj:.3f})")
    print(f"  passage: z {z_interior:.3f} -> {z_neck_end:.3f} (len={(z_neck_end - z_interior):.3f}); top thickness={top_thickness:.3f}")

    # Build explicit planes at absolute coordinates (do NOT offset from picked face)
    pl_top = cq.Plane(origin=(neck_center.x, neck_center.y, z_top), normal=(axis.x, axis.y, axis.z))
    print(f"PLANE: top plane origin=[{pl_top.origin.x:.3f},{pl_top.origin.y:.3f},{pl_top.origin.z:.3f}] normal=[{pl_top.zDir.x:.6f},{pl_top.zDir.y:.6f},{pl_top.zDir.z:.6f}]")

    # Outer boss: OD 38.1, height 30.0, from z=266.7 to z=296.7
    boss = cq.Workplane(pl_top).circle(r_od).extrude(proj)

    # Inner passage tool: ID 25.4, from z=257.175 to z=296.7
    pl_hole = cq.Plane(origin=(neck_center.x, neck_center.y, z_interior), normal=(axis.x, axis.y, axis.z))
    print(f"PLANE: hole plane origin=[{pl_hole.origin.x:.3f},{pl_hole.origin.y:.3f},{pl_hole.origin.z:.3f}] normal=[{pl_hole.zDir.x:.6f},{pl_hole.zDir.y:.6f},{pl_hole.zDir.z:.6f}]")
    hole_h = (z_neck_end - z_interior)
    hole = cq.Workplane(pl_hole).circle(r_id).extrude(hole_h)

    # Apply booleans only to s0
    pre = None
    try:
        pre = s0.fuse(boss.val())
        print("BOOL: fuse boss into s0: OK")
    except Exception as e:
        print(f"BOOL: fuse boss into s0: FAILED: {e}")
        # Fallback: add a tiny overlap into the top to ensure fuse, while keeping zmax correct
        eps = 0.05
        boss_fb = cq.Workplane(cq.Plane(origin=(neck_center.x, neck_center.y, z_top - eps), normal=(axis.x, axis.y, axis.z))).circle(r_od).extrude(proj + eps)
        pre = s0.fuse(boss_fb.val())
        print("BOOL: fuse boss fallback with small overlap: OK")

    edited = None
    try:
        edited = pre.cut(hole.val())
        print("BOOL: cut inner passage through s0+boss: OK")
    except Exception as e:
        print(f"BOOL: cut inner passage through s0+boss: FAILED: {e}")
        raise

    # Placement self-check
    added = edited.cut(s0)
    add_bb = added.BoundingBox()
    add_ctr = added.Center()
    print(
        "ADDED (neck material) check: "
        f"center=[{add_ctr.x:.3f},{add_ctr.y:.3f},{add_ctr.z:.3f}] "
        f"bbox=({add_bb.xmin:.3f},{add_bb.ymin:.3f},{add_bb.zmin:.3f})..({add_bb.xmax:.3f},{add_bb.ymax:.3f},{add_bb.zmax:.3f})"
    )
    od_meas = max(add_bb.xlen, add_bb.ylen)
    print(f"  measured OD from added bbox ~ {od_meas:.3f} (target {od:.3f})  delta={od_meas-od:.3f}")
    print(f"  measured zmin ~ {add_bb.zmin:.3f} (target {z_top:.3f}) delta={add_bb.zmin-z_top:.3f}")
    print(f"  measured zmax ~ {add_bb.zmax:.3f} (target {z_neck_end:.3f}) delta={add_bb.zmax-z_neck_end:.3f}")
    print(f"  measured XY center ~ [{add_bb.center.x:.3f},{add_bb.center.y:.3f}] (target [{neck_center.x:.3f},{neck_center.y:.3f}]) delta=[{add_bb.center.x-neck_center.x:.3f},{add_bb.center.y-neck_center.y:.3f}]")

    removed = pre.cut(edited)
    rem_bb = removed.BoundingBox()
    rem_ctr = removed.Center()
    id_meas = max(rem_bb.xlen, rem_bb.ylen)
    print(
        "REMOVED (passage void) check: "
        f"center=[{rem_ctr.x:.3f},{rem_ctr.y:.3f},{rem_ctr.z:.3f}] "
        f"bbox=({rem_bb.xmin:.3f},{rem_bb.ymin:.3f},{rem_bb.zmin:.3f})..({rem_bb.xmax:.3f},{rem_bb.ymax:.3f},{rem_bb.zmax:.3f})"
    )
    print(f"  measured clear ID from removed bbox ~ {id_meas:.3f} (target {id_clear:.3f})  delta={id_meas-id_clear:.3f}")
    print(f"  measured hole zmin ~ {rem_bb.zmin:.3f} (target {z_interior:.3f}) delta={rem_bb.zmin-z_interior:.3f}")
    print(f"  measured hole zmax ~ {rem_bb.zmax:.3f} (target {z_neck_end:.3f}) delta={rem_bb.zmax-z_neck_end:.3f}")

    # If something is meaningfully off, rebuild in-place in the same attempt by translating the tools
    # (expect deltas ~0; keep tolerance tight but non-zero)
    tol = 0.5
    dx = neck_center.x - add_bb.center.x
    dy = neck_center.y - add_bb.center.y
    dz_top_err = z_top - add_bb.zmin
    dz_end_err = z_neck_end - add_bb.zmax
    if abs(dx) > tol or abs(dy) > tol or abs(dz_top_err) > tol or abs(dz_end_err) > tol:
        print("CORRECTION: detected placement error beyond tolerance; rebuilding boss/hole with translation correction")
        boss2 = boss.translate((dx, dy, dz_top_err))
        hole2 = hole.translate((dx, dy, dz_top_err))
        pre2 = s0.fuse(boss2.val())
        edited2 = pre2.cut(hole2.val())
        # re-check
        added2 = edited2.cut(s0)
        add2_bb = added2.BoundingBox()
        print(
            "ADDED (after correction) bbox="
            f"({add2_bb.xmin:.3f},{add2_bb.ymin:.3f},{add2_bb.zmin:.3f})..({add2_bb.xmax:.3f},{add2_bb.ymax:.3f},{add2_bb.zmax:.3f})"
        )
        edited = edited2

    print("ACHIEVED:")
    print(f"  neck center (target) = [{neck_center.x:.3f}, {neck_center.y:.3f}, {neck_center.z:.3f}]")
    print(f"  axis (used) = [{axis.x:.6f}, {axis.y:.6f}, {axis.z:.6f}]")
    print(f"  OD={od:.3f}  clear ID={id_clear:.3f}")
    print(f"  outer z-levels: {z_top:.3f} -> {z_neck_end:.3f}")
    print(f"  passage z-levels: {z_interior:.3f} -> {z_neck_end:.3f}")

    # Recompound: keep all other solids byte-identical
    out = cq.Compound.makeCompound([s for i, s in enumerate(solids) if i != s0_i] + [edited])
    return out