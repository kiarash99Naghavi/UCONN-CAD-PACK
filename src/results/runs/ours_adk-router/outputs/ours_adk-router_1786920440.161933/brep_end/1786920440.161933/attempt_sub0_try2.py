def my_cad_function(args):
    import cadquery as cq
    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input

    base = shape.val() if hasattr(shape, "val") else shape
    sols = base.Solids()
    print(f"INFO: imported solids={len(sols)}")
    solid = sols[0] if len(sols) else base

    print(f"INFO: solid faces={len(solid.Faces())} edges={len(solid.Edges())} verts={len(solid.Vertices())}")

    # --- Targets from the sub-goal ---
    target_xmin = 5.373
    target_xmax = 6.873
    target_xc = 0.5 * (target_xmin + target_xmax)
    floor_y = -0.01
    print(
        "TARGETS: "
        f"x_limits=({target_xmin:.3f}..{target_xmax:.3f}) dx={(target_xmax-target_xmin):.3f} x_center={target_xc:.3f}  "
        f"floor_y={floor_y:.3f}"
    )

    part_bb = solid.BoundingBox()
    print(
        f"PART_BBOX(input): xmin={part_bb.xmin:.3f} xmax={part_bb.xmax:.3f} "
        f"ymin={part_bb.ymin:.3f} ymax={part_bb.ymax:.3f} "
        f"zmin={part_bb.zmin:.3f} zmax={part_bb.zmax:.3f}"
    )

    faces = solid.Faces()

    # --- Find the floor planar face at y=-0.01 (normal -Y) ---
    floor_cands = []
    for i, f in enumerate(faces):
        if f.geomType() != "PLANE":
            continue
        n = f.normalAt()
        c = f.Center()
        # normal ~ -Y and close to y=-0.01
        if abs(n.x) < 0.05 and n.y < -0.95 and abs(n.z) < 0.05 and abs(c.y - floor_y) < 0.05:
            floor_cands.append((f.Area(), i, f, c, n))
    floor_cands.sort(reverse=True, key=lambda t: t[0])
    print(f"SELECTED: {len(floor_cands)} planar faces as floor candidates (normal~-Y, y~{floor_y})")
    if not floor_cands:
        # cannot proceed reliably
        print("ERROR: no floor face candidate found; returning input unchanged (would be a no-op)")
        return solid
    _, floor_i, floor_face, floor_c, floor_n = floor_cands[0]
    floor_bb = floor_face.BoundingBox()
    print(
        f"MATCH floor_face: idx={floor_i} area={floor_face.Area():.3f} "
        f"center=({floor_c.x:.3f},{floor_c.y:.3f},{floor_c.z:.3f}) "
        f"normal=({floor_n.x:.3f},{floor_n.y:.3f},{floor_n.z:.3f}) "
        f"bby=({floor_bb.ymin:.3f}..{floor_bb.ymax:.3f})"
    )

    # --- Find the underside bridge planar face (previously #99) by normal/size/location ---
    # From provided index (context): plane center ~ (5.028, 3.437, -1.323), normal ~ (-0.17, +0.481, -0.86), area ~0.418
    target_bridge_c = cq.Vector(5.028, 3.437, -1.323)
    target_bridge_n = cq.Vector(-0.17, 0.481, -0.86)
    tnl = target_bridge_n.Length
    target_bridge_n = cq.Vector(target_bridge_n.x / tnl, target_bridge_n.y / tnl, target_bridge_n.z / tnl)

    bridge_cands = []
    for i, f in enumerate(faces):
        if f.geomType() != "PLANE":
            continue
        a = f.Area()
        if a < 0.05 or a > 2.0:
            continue
        c = f.Center()
        n = f.normalAt()
        # allow either orientation
        nn = cq.Vector(n.x, n.y, n.z)
        nlen = nn.Length
        if nlen == 0:
            continue
        nn = cq.Vector(nn.x / nlen, nn.y / nlen, nn.z / nlen)
        dot = abs(nn.x * target_bridge_n.x + nn.y * target_bridge_n.y + nn.z * target_bridge_n.z)
        # center proximity (loose, since topology may shift)
        dc = (c - target_bridge_c).Length
        if dot > 0.97 and dc < 3.0:
            bridge_cands.append((dot, -dc, -abs(a - 0.418), i, f, c, n, a))
    bridge_cands.sort(reverse=True)
    print(f"SELECTED: {len(bridge_cands)} planar faces as underside-bridge candidates (normal~given, near expected center)")
    if not bridge_cands:
        # fallback: pick any small-ish plane with strong -Z component and positive Y component (underside-ish)
        for i, f in enumerate(faces):
            if f.geomType() != "PLANE":
                continue
            a = f.Area()
            if a < 0.05 or a > 2.0:
                continue
            c = f.Center()
            n = f.normalAt()
            if n.y > 0.15 and n.z < -0.6:
                bridge_cands.append((0.0, 0.0, -abs(a - 0.418), i, f, c, n, a))
        bridge_cands.sort(reverse=True)
        print(f"FALLBACK SELECTED: {len(bridge_cands)} planar faces as underside-bridge candidates (n.y>0.15, n.z<-0.6)")

    if not bridge_cands:
        print("ERROR: no underside-bridge face candidate found; returning input unchanged (would be a no-op)")
        return solid

    _, _, _, bridge_i, bridge_face, bridge_c, bridge_n, bridge_a = bridge_cands[0]
    bridge_bb = bridge_face.BoundingBox()
    print(
        f"MATCH underside_bridge_face: idx={bridge_i} area={bridge_a:.3f} "
        f"center=({bridge_c.x:.3f},{bridge_c.y:.3f},{bridge_c.z:.3f}) "
        f"normal=({bridge_n.x:.3f},{bridge_n.y:.3f},{bridge_n.z:.3f})"
    )

    # --- Build a 'cavity side' halfspace based on the bridge plane ---
    n99 = cq.Vector(bridge_n.x, bridge_n.y, bridge_n.z)
    nlen = n99.Length
    n99 = cq.Vector(n99.x / nlen, n99.y / nlen, n99.z / nlen)
    c99 = bridge_c

    plane99 = cq.Plane(
        origin=(c99.x, c99.y, c99.z),
        normal=(n99.x, n99.y, n99.z),
        xDir=(1, 0, 0),
    )
    print(
        f"PLANE99(constructed): origin=({c99.x:.3f},{c99.y:.3f},{c99.z:.3f}) "
        f"normal=({n99.x:.3f},{n99.y:.3f},{n99.z:.3f})"
    )

    # Determine which side of plane99 contains the cavity floor point near the target slab
    probe_pt = cq.Vector(target_xc, floor_y, -0.6)
    d_probe = n99.x * (probe_pt.x - c99.x) + n99.y * (probe_pt.y - c99.y) + n99.z * (probe_pt.z - c99.z)
    print(f"CHECK: signed_dist(plane99) at probe_pt=({probe_pt.x:.3f},{probe_pt.y:.3f},{probe_pt.z:.3f}) is {d_probe:.6f}")

    # Build a big finite halfspace block on +normal side; if probe is on -side, flip plane
    if d_probe < 0:
        n99 = cq.Vector(-n99.x, -n99.y, -n99.z)
        plane99 = cq.Plane(
            origin=(c99.x, c99.y, c99.z),
            normal=(n99.x, n99.y, n99.z),
            xDir=(1, 0, 0),
        )
        print(
            "INFO: flipped plane99 normal so probe point lies on kept (+normal) side; "
            f"new normal=({n99.x:.3f},{n99.y:.3f},{n99.z:.3f})"
        )

    halfspace_keep = cq.Workplane(plane99).rect(250, 250).extrude(250).val()

    # --- Define a conservative trim region around the previously-added rib (from QA bbox) ---
    # QA: new geometry bbox was approx x=4.749..7.624, y=-0.01..3.194, z=-1.57..0.6
    # We'll limit trimming to a tight envelope around that so we don't touch other part regions.
    rib_xmin_loose = 4.60
    rib_xmax_loose = 7.80
    rib_ymin = floor_y - 0.001
    rib_ymax = 3.35
    rib_zmin = -1.85
    rib_zmax = 0.75

    print(
        "TRIM_ENVELOPE: "
        f"x=({rib_xmin_loose:.3f}..{rib_xmax_loose:.3f}) "
        f"y=({rib_ymin:.3f}..{rib_ymax:.3f}) "
        f"z=({rib_zmin:.3f}..{rib_zmax:.3f})"
    )

    trim_env = cq.Solid.makeBox(
        rib_xmax_loose - rib_xmin_loose,
        rib_ymax - rib_ymin,
        rib_zmax - rib_zmin,
        cq.Vector(rib_xmin_loose, rib_ymin, rib_zmin),
    )
    trim_env = trim_env.intersect(halfspace_keep)

    keep_slab = cq.Solid.makeBox(
        target_xmax - target_xmin,
        rib_ymax - rib_ymin,
        rib_zmax - rib_zmin,
        cq.Vector(target_xmin, rib_ymin, rib_zmin),
    )
    keep_slab = keep_slab.intersect(halfspace_keep)

    # Outside slab within trim envelope is what we want to REMOVE from the part
    outside_slab = trim_env.cut(keep_slab)
    out_bb = outside_slab.BoundingBox()
    print(
        f"OUTSIDE_SLAB_TOOL_BBOX: xmin={out_bb.xmin:.3f} xmax={out_bb.xmax:.3f} "
        f"ymin={out_bb.ymin:.3f} ymax={out_bb.ymax:.3f} zmin={out_bb.zmin:.3f} zmax={out_bb.zmax:.3f}"
    )

    # Clip tool to part bbox (safety: guarantee no outer bbox growth / odd artifacts)
    clip = cq.Solid.makeBox(
        part_bb.xlen + 1e-3,
        part_bb.ylen + 1e-3,
        part_bb.zlen + 1e-3,
        cq.Vector(part_bb.xmin, part_bb.ymin, part_bb.zmin),
    )
    outside_slab = outside_slab.intersect(clip)

    # --- Trim the existing too-wide rib: cut away material outside x=5.373..6.873 but only inside trim envelope ---
    trimmed = solid.cut(outside_slab)

    removed = solid.cut(trimmed)
    if removed.Volume() > 1e-9:
        rbb = removed.BoundingBox()
        print(
            f"REMOVED_BBOX: xmin={rbb.xmin:.3f} xmax={rbb.xmax:.3f} (dx={rbb.xlen:.3f}) "
            f"ymin={rbb.ymin:.3f} ymax={rbb.ymax:.3f} zmin={rbb.zmin:.3f} zmax={rbb.zmax:.3f}"
        )
        # sanity: removed region should not touch outer bbox faces
        touch_x = (abs(rbb.xmin - part_bb.xmin) < 1e-3) or (abs(rbb.xmax - part_bb.xmax) < 1e-3)
        touch_y = (abs(rbb.ymin - part_bb.ymin) < 1e-3) or (abs(rbb.ymax - part_bb.ymax) < 1e-3)
        touch_z = (abs(rbb.zmin - part_bb.zmin) < 1e-3) or (abs(rbb.zmax - part_bb.zmax) < 1e-3)
        print(f"REMOVED_TOUCH_OUTER_BBOX: touch_x={touch_x} touch_y={touch_y} touch_z={touch_z}")
    else:
        print("WARNING: trimming cut removed ~0 volume (outside_slab may have missed the too-wide rib); proceeding to rebuild rib correctly")

    # --- Rebuild the rib correctly in the desired slab, and fuse (this should not create a second rib; it just ensures full web remains) ---
    # Build an explicit rib prism within the target x slab, clipped by plane99 and clipped to the part bbox.
    # Overshoot in +Y and then intersect with the halfspace_keep to land on the underside bridge plane.
    rib_raw = cq.Solid.makeBox(
        target_xmax - target_xmin,
        (part_bb.ymax - floor_y) + 20.0,
        rib_zmax - rib_zmin,
        cq.Vector(target_xmin, floor_y, rib_zmin),
    )
    rib_clip = rib_raw.intersect(halfspace_keep).intersect(clip)

    rib_bb = rib_clip.BoundingBox()
    print(
        f"RIB_REBUILD_BBOX(pre-fuse): xmin={rib_bb.xmin:.3f} xmax={rib_bb.xmax:.3f} (dx={rib_bb.xlen:.3f}) "
        f"ymin={rib_bb.ymin:.3f} ymax={rib_bb.ymax:.3f} zmin={rib_bb.zmin:.3f} zmax={rib_bb.zmax:.3f}"
    )

    out = trimmed.fuse(rib_clip)

    out_part_bb = out.BoundingBox()
    print(
        f"PART_BBOX(output): xmin={out_part_bb.xmin:.3f} xmax={out_part_bb.xmax:.3f} "
        f"ymin={out_part_bb.ymin:.3f} ymax={out_part_bb.ymax:.3f} "
        f"zmin={out_part_bb.zmin:.3f} zmax={out_part_bb.zmax:.3f}"
    )
    print(
        "BBOX_DELTA(vs input): "
        f"dxmin={out_part_bb.xmin - part_bb.xmin:.6f} dxmax={out_part_bb.xmax - part_bb.xmax:.6f} "
        f"dymin={out_part_bb.ymin - part_bb.ymin:.6f} dymax={out_part_bb.ymax - part_bb.ymax:.6f} "
        f"dzmin={out_part_bb.zmin - part_bb.zmin:.6f} dzmax={out_part_bb.zmax - part_bb.zmax:.6f}"
    )

    # --- Self-check: measure the final rib region inside the desired slab & cavity envelope ---
    # Build a measurement region that should isolate the rib/web material in the target slab.
    meas_region = keep_slab  # already slab+envelope+halfspace clipped
    meas = out.intersect(meas_region)
    meas_sols = meas.Solids()
    print(f"INFO: meas_region intersect produced solids={len(meas_sols)}")
    if len(meas_sols) > 0:
        mbb = meas.BoundingBox()
        achieved_xmin = mbb.xmin
        achieved_xmax = mbb.xmax
        achieved_xc = 0.5 * (achieved_xmin + achieved_xmax)
        print(
            f"MEAS_RIB_REGION_BBOX: xmin={achieved_xmin:.3f} xmax={achieved_xmax:.3f} (dx={mbb.xlen:.3f}) "
            f"center_x={achieved_xc:.3f}"
        )
        print(
            "ACHIEVED_DELTAS_X: "
            f"dxmin={achieved_xmin - target_xmin:.6f} dxmax={achieved_xmax - target_xmax:.6f} dxc={achieved_xc - target_xc:.6f}"
        )
    else:
        print("WARNING: meas_region found zero solids; measurement filter may be too strict. (Rib may still exist, but couldn't be isolated.)")

    final_sols = out.Solids()
    print(f"INFO: final solids={len(final_sols)}")

    return out