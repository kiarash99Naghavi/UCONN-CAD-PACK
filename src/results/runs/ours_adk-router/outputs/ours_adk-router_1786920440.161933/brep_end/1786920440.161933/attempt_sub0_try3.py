def my_cad_function(args):
    import cadquery as cq

    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input

    base = shape.val() if hasattr(shape, "val") else shape
    sols = base.Solids()
    print(f"INFO: imported solids={len(sols)}")
    solid = sols[0] if len(sols) else base
    print(f"INFO: solid faces={len(solid.Faces())} edges={len(solid.Edges())} verts={len(solid.Vertices())}")

    # --- Targets ---
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

    # --- Find floor planar face at y=-0.01, normal approx -Y ---
    floor_cands = []
    for i, f in enumerate(faces):
        if f.geomType() != "PLANE":
            continue
        n = f.normalAt()
        c = f.Center()
        if abs(n.x) < 0.05 and n.y < -0.95 and abs(n.z) < 0.05 and abs(c.y - floor_y) < 0.05:
            floor_cands.append((f.Area(), i, f, c, n))
    floor_cands.sort(reverse=True, key=lambda t: t[0])
    print(f"SELECTED: {len(floor_cands)} faces for floor candidates (plane, n~-Y, center.y~{floor_y})")
    if floor_cands:
        _, floor_i, floor_face, floor_c, floor_n = floor_cands[0]
        fbb = floor_face.BoundingBox()
        print(
            f"MATCH floor_face: idx={floor_i} area={floor_face.Area():.3f} "
            f"center=({floor_c.x:.3f},{floor_c.y:.3f},{floor_c.z:.3f}) "
            f"normal=({floor_n.x:.3f},{floor_n.y:.3f},{floor_n.z:.3f}) "
            f"bb.y=({fbb.ymin:.3f}..{fbb.ymax:.3f})"
        )
    else:
        print("WARNING: floor face not found by filter; proceeding without it (tools are still absolute)")

    # --- Find underside bridge plane face by measured normal/area/location (current index suggests ~area 3.5, n~(-0.17,0.481,-0.86)) ---
    target_bridge_c = cq.Vector(6.098, 3.897, -1.278)
    target_bridge_n = cq.Vector(-0.17, 0.481, -0.86)
    target_bridge_n = target_bridge_n.multiply(1.0 / target_bridge_n.Length)

    bridge_cands = []
    for i, f in enumerate(faces):
        if f.geomType() != "PLANE":
            continue
        a = f.Area()
        if a < 0.5 or a > 8.0:
            continue
        c = f.Center()
        n = f.normalAt()
        nn = cq.Vector(n.x, n.y, n.z)
        if nn.Length < 1e-9:
            continue
        nn = nn.multiply(1.0 / nn.Length)
        dot = abs(nn.dot(target_bridge_n))
        dc = (c - target_bridge_c).Length
        # generous: location within 3 mm, dot strong
        if dot > 0.97 and dc < 3.0:
            bridge_cands.append((dot, -abs(a - 3.495), -dc, i, f, c, n, a))
    bridge_cands.sort(reverse=True)
    print("SELECTED: {} faces for underside-bridge candidates (plane, dot(n,target) high, near expected center)".format(len(bridge_cands)))
    if not bridge_cands:
        # fallback: any plane with strong -Z and +Y component, moderate area
        for i, f in enumerate(faces):
            if f.geomType() != "PLANE":
                continue
            a = f.Area()
            if a < 0.5 or a > 15.0:
                continue
            c = f.Center()
            n = f.normalAt()
            if n.y > 0.15 and n.z < -0.6:
                bridge_cands.append((0.0, -abs(a - 3.5), -((c - target_bridge_c).Length), i, f, c, n, a))
        bridge_cands.sort(reverse=True)
        print("FALLBACK SELECTED: {} faces for underside-bridge candidates (n.y>0.15, n.z<-0.6)".format(len(bridge_cands)))

    if bridge_cands:
        _, _, _, bridge_i, bridge_face, bridge_c, bridge_n, bridge_a = bridge_cands[0]
        print(
            f"MATCH underside_bridge_face: idx={bridge_i} area={bridge_a:.3f} "
            f"center=({bridge_c.x:.3f},{bridge_c.y:.3f},{bridge_c.z:.3f}) "
            f"normal=({bridge_n.x:.3f},{bridge_n.y:.3f},{bridge_n.z:.3f})"
        )
    else:
        # As absolute fallback, define a plane similar to target (better than no-op)
        bridge_i, bridge_face, bridge_c, bridge_n, bridge_a = -1, None, target_bridge_c, target_bridge_n, 3.5
        print("WARNING: could not match underside bridge face; using approximate plane from target numbers")

    # --- Construct cavity halfspace below/inside bridge plane (choose side by probe point) ---
    npl = cq.Vector(bridge_n.x, bridge_n.y, bridge_n.z)
    if npl.Length < 1e-9:
        npl = target_bridge_n
    npl = npl.multiply(1.0 / npl.Length)
    cpl = cq.Vector(bridge_c.x, bridge_c.y, bridge_c.z)

    probe_pt = cq.Vector(target_xc, floor_y, -0.6)
    d_probe = npl.dot(probe_pt - cpl)
    print(
        "PLANE(bridge) preflip: "
        f"origin=({cpl.x:.3f},{cpl.y:.3f},{cpl.z:.3f}) normal=({npl.x:.3f},{npl.y:.3f},{npl.z:.3f}) "
        f"signed_dist_at_probe={d_probe:.6f} probe=({probe_pt.x:.3f},{probe_pt.y:.3f},{probe_pt.z:.3f})"
    )
    # We want halfspace that CONTAINS the probe point, i.e. probe has positive distance into the extrude direction
    if d_probe < 0:
        npl = npl.multiply(-1)
        print(f"INFO: flipped bridge plane normal to include probe point; new normal=({npl.x:.3f},{npl.y:.3f},{npl.z:.3f})")

    plane_bridge = cq.Plane(origin=(cpl.x, cpl.y, cpl.z), normal=(npl.x, npl.y, npl.z), xDir=(1, 0, 0))
    # finite halfspace block in +normal direction
    halfspace_keep = cq.Workplane(plane_bridge).rect(250, 250).extrude(250).val()

    # --- Define a local cavity envelope to limit cutting to the rib region (avoid touching outer envelope/other solids) ---
    # Envelope chosen around prior QA rib bbox and central cavity only
    env_xmin = 4.20
    env_xmax = 8.10
    env_ymin = floor_y - 0.002
    env_ymax = 3.45
    env_zmin = -1.95
    env_zmax = 0.95
    print(
        "CAVITY_ENVELOPE: "
        f"x=({env_xmin:.3f}..{env_xmax:.3f}) y=({env_ymin:.3f}..{env_ymax:.3f}) z=({env_zmin:.3f}..{env_zmax:.3f})"
    )

    env_box = cq.Solid.makeBox(
        env_xmax - env_xmin,
        env_ymax - env_ymin,
        env_zmax - env_zmin,
        cq.Vector(env_xmin, env_ymin, env_zmin),
    )
    cavity_region = env_box.intersect(halfspace_keep)

    # --- Build removal tools: everything in cavity_region with x < target_xmin OR x > target_xmax ---
    left_raw = cq.Solid.makeBox(
        200.0,
        env_ymax - env_ymin,
        env_zmax - env_zmin,
        cq.Vector(target_xmin - 200.0, env_ymin, env_zmin),
    )
    right_raw = cq.Solid.makeBox(
        200.0,
        env_ymax - env_ymin,
        env_zmax - env_zmin,
        cq.Vector(target_xmax, env_ymin, env_zmin),
    )

    left_tool = left_raw.intersect(cavity_region)
    right_tool = right_raw.intersect(cavity_region)

    lbb = left_tool.BoundingBox()
    rbb = right_tool.BoundingBox()
    print(
        f"TOOLS_BBOX: left(x<{target_xmin:.3f}) xmin={lbb.xmin:.3f} xmax={lbb.xmax:.3f} "
        f"y=({lbb.ymin:.3f}..{lbb.ymax:.3f}) z=({lbb.zmin:.3f}..{lbb.zmax:.3f})"
    )
    print(
        f"TOOLS_BBOX: right(x>{target_xmax:.3f}) xmin={rbb.xmin:.3f} xmax={rbb.xmax:.3f} "
        f"y=({rbb.ymin:.3f}..{rbb.ymax:.3f}) z=({rbb.zmin:.3f}..{rbb.zmax:.3f})"
    )

    # --- Apply cuts (trim existing rib; do NOT add another) ---
    trimmed = solid
    try:
        trimmed = trimmed.cut(left_tool)
    except Exception as e:
        print(f"WARNING: left cut failed: {e}")
    try:
        trimmed = trimmed.cut(right_tool)
    except Exception as e:
        print(f"WARNING: right cut failed: {e}")

    removed = solid.cut(trimmed)
    try:
        rv = removed.Volume()
    except Exception:
        rv = 0.0
    print(f"INFO: removed_volume={rv:.6f} mm^3")
    if rv > 1e-9:
        rbb2 = removed.BoundingBox()
        print(
            f"REMOVED_BBOX: xmin={rbb2.xmin:.3f} xmax={rbb2.xmax:.3f} (dx={rbb2.xlen:.3f}) "
            f"y=({rbb2.ymin:.3f}..{rbb2.ymax:.3f}) z=({rbb2.zmin:.3f}..{rbb2.zmax:.3f})"
        )
    else:
        print("WARNING: removed ~0 volume; either rib already trimmed here or tools missed it")

    out_bb = trimmed.BoundingBox()
    print(
        f"PART_BBOX(output): xmin={out_bb.xmin:.3f} xmax={out_bb.xmax:.3f} "
        f"ymin={out_bb.ymin:.3f} ymax={out_bb.ymax:.3f} "
        f"zmin={out_bb.zmin:.3f} zmax={out_bb.zmax:.3f}"
    )
    print(
        "BBOX_DELTA(vs input): "
        f"dxmin={out_bb.xmin - part_bb.xmin:.6f} dxmax={out_bb.xmax - part_bb.xmax:.6f} "
        f"dymin={out_bb.ymin - part_bb.ymin:.6f} dymax={out_bb.ymax - part_bb.ymax:.6f} "
        f"dzmin={out_bb.zmin - part_bb.zmin:.6f} dzmax={out_bb.zmax - part_bb.zmax:.6f}"
    )

    # --- Self-check: measure remaining material in the cavity region near the rib ---
    meas = trimmed.intersect(cavity_region)
    meas_sols = meas.Solids()
    print(f"INFO: meas intersect (trimmed ∩ cavity_region) solids={len(meas_sols)}")
    if len(meas_sols) > 0:
        mbb = meas.BoundingBox()
        achieved_xmin = mbb.xmin
        achieved_xmax = mbb.xmax
        achieved_xc = 0.5 * (achieved_xmin + achieved_xmax)
        print(
            f"MEAS_CAVITY_MAT_BBOX: xmin={achieved_xmin:.3f} xmax={achieved_xmax:.3f} (dx={mbb.xlen:.3f}) center_x={achieved_xc:.3f} "
            f"y=({mbb.ymin:.3f}..{mbb.ymax:.3f}) z=({mbb.zmin:.3f}..{mbb.zmax:.3f})"
        )
        print(
            "ACHIEVED_DELTAS_X(vs targets): "
            f"dxmin={achieved_xmin - target_xmin:.6f} dxmax={achieved_xmax - target_xmax:.6f} dxc={achieved_xc - target_xc:.6f}"
        )
    else:
        print("WARNING: measurement produced zero solids; cavity_region may be too strict")

    # --- Extra check: look for planar rib side faces near x=target_xmin/target_xmax inside envelope ---
    out_faces = trimmed.Faces()
    xmin_faces = []
    xmax_faces = []
    for i, f in enumerate(out_faces):
        if f.geomType() != "PLANE":
            continue
        c = f.Center()
        if c.x < env_xmin - 0.1 or c.x > env_xmax + 0.1:
            continue
        if c.y < env_ymin - 0.1 or c.y > env_ymax + 0.1:
            continue
        if c.z < env_zmin - 0.2 or c.z > env_zmax + 0.2:
            continue
        n = f.normalAt()
        if n.x < -0.95 and abs(c.x - target_xmin) < 0.25:
            xmin_faces.append((f.Area(), i, c, n))
        if n.x > 0.95 and abs(c.x - target_xmax) < 0.25:
            xmax_faces.append((f.Area(), i, c, n))

    xmin_faces.sort(reverse=True)
    xmax_faces.sort(reverse=True)
    print(f"SELECTED: {len(xmin_faces)} planar faces near x_min wall (n~-X, |x-{target_xmin:.3f}|<0.25) in cavity envelope")
    if xmin_faces:
        a, i, c, n = xmin_faces[0]
        print(f"MATCH x_min_wall_face: idx={i} area={a:.3f} center=({c.x:.3f},{c.y:.3f},{c.z:.3f}) normal=({n.x:.3f},{n.y:.3f},{n.z:.3f})")
    print(f"SELECTED: {len(xmax_faces)} planar faces near x_max wall (n~+X, |x-{target_xmax:.3f}|<0.25) in cavity envelope")
    if xmax_faces:
        a, i, c, n = xmax_faces[0]
        print(f"MATCH x_max_wall_face: idx={i} area={a:.3f} center=({c.x:.3f},{c.y:.3f},{c.z:.3f}) normal=({n.x:.3f},{n.y:.3f},{n.z:.3f})")

    final_sols = trimmed.Solids()
    print(f"INFO: final solids={len(final_sols)}")

    return trimmed