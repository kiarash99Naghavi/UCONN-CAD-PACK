def my_cad_function(args):
    import cadquery as cq
    import math

    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input
    base = shape.val() if hasattr(shape, "val") else shape

    solids = base.Solids()
    print(f"INFO: base has {len(solids)} solids, {len(base.Faces())} faces, {len(base.Edges())} edges")
    for i, s in enumerate(solids):
        bb = s.BoundingBox()
        print(
            f"INFO: solid[{i}] vol={s.Volume():.3f} "
            f"bbox min=({bb.xmin:.3f},{bb.ymin:.3f},{bb.zmin:.3f}) max=({bb.xmax:.3f},{bb.ymax:.3f},{bb.zmax:.3f}) "
            f"lens=({bb.xlen:.3f},{bb.ylen:.3f},{bb.zlen:.3f})"
        )

    # --- Target numbers from the sub-goal ---
    cx, cz = 0.0, 0.0
    y_top = 3.175
    y_bot = -4.625
    thickness = y_top - y_bot  # 7.8
    axis = cq.Vector(0, 1, 0)

    R_hex = 10.5                 # across corners 21.0
    across_corners = 2 * R_hex
    across_flats = across_corners * math.cos(math.pi / 6.0)

    print(
        f"TARGET: center X={cx:.3f}, Z={cz:.3f}; Y span [{y_bot:.3f}..{y_top:.3f}] (thickness={thickness:.3f}) axis={tuple(axis.toTuple())}"
    )
    print(
        f"TARGET: hex across_corners={across_corners:.3f} (R={R_hex:.3f}); across_flats={across_flats:.3f} (expected 18.187)"
    )

    # --- Identify s0 hub by its bbox (X/Z ~31.5, Y~7.8) ---
    s0_idx = None
    for i, s in enumerate(solids):
        bb = s.BoundingBox()
        if abs(bb.xlen - 31.5) < 0.2 and abs(bb.zlen - 31.5) < 0.2 and abs(bb.ylen - 7.8) < 0.2:
            s0_idx = i
            break
    if s0_idx is None:
        # fallback: smallest solid by volume (matches prompt: s0 is small hub)
        s0_idx = min(range(len(solids)), key=lambda k: solids[k].Volume())
        print(f"WARN: bbox-based s0 selection failed; falling back to smallest-by-volume idx={s0_idx}")

    s0 = solids[s0_idx]
    untouched_idx = [i for i in range(len(solids)) if i != s0_idx]
    print(f"SELECTED: 1 solid for edit (s0 hub) idx=[{s0_idx}]")
    print(f"SELECTED: {len(untouched_idx)} solids left untouched (including s1 gear) idx={untouched_idx}")

    # Sanity-check the referenced faces exist and match expected centers (from index)
    try:
        f_top = base.Faces()[1]
        f_bot = base.Faces()[233]
        c1 = f_top.Center(); c233 = f_bot.Center()
        print(
            f"CHECK: face#1 center=({c1.x:.3f},{c1.y:.3f},{c1.z:.3f}) area={f_top.Area():.3f} normal={tuple(f_top.normalAt().toTuple())}"
        )
        print(
            f"CHECK: face#233 center=({c233.x:.3f},{c233.y:.3f},{c233.z:.3f}) area={f_bot.Area():.3f} normal={tuple(f_bot.normalAt().toTuple())}"
        )
    except Exception as e:
        print(f"WARN: could not resolve face#1/#233 for sanity-check: {e}")

    # --- Build tools ---
    # Use a sketch plane where 2D coords are (X,Z) directly:
    # Plane normal = -Y, xDir=+X => yDir=+Z
    overshoot = 0.75
    L = thickness + 2 * overshoot
    y_start = y_top + overshoot
    tool_plane = cq.Plane(origin=(0, y_start, 0), normal=(0, -1, 0), xDir=(1, 0, 0))
    print(f"INFO: tool_plane origin=(0,{y_start:.3f},0) normal=(0,-1,0) xDir=(1,0,0) extrude_L={L:.3f}")

    # 1) Fill tool: a cylinder slightly larger than the original flower envelope,
    #    but still well within the s0 hub OD (15.75). This robustly eliminates the flower void.
    R_fill = 11.25
    fill_tool = cq.Workplane(tool_plane).circle(R_fill).extrude(L).val()
    bb_fill = fill_tool.BoundingBox(); c_fill = fill_tool.Center()
    print(
        f"ACHIEVED(fill_tool): R_fill={R_fill:.3f} center=({c_fill.x:.3f},{c_fill.y:.3f},{c_fill.z:.3f}) "
        f"axial_span_y=[{bb_fill.ymin:.3f}..{bb_fill.ymax:.3f}] (target span covers [{y_bot:.3f}..{y_top:.3f}])"
    )

    # 2) Hex cut tool: regular hex, circumradius R_hex, vertex toward +Z.
    vertex_angles = [30.0, 90.0, 150.0, 210.0, 270.0, 330.0]

    def hex_pts_from_angles(angles_deg):
        pts = []
        zvals = []
        for ang in angles_deg:
            t = math.radians(ang)
            x = R_hex * math.cos(t)
            z = R_hex * math.sin(t)
            pts.append((x, z))
            zvals.append(z)
        return pts, max(zvals)

    pts, max_z = hex_pts_from_angles(vertex_angles)
    if abs(max_z - R_hex) > 1e-6:
        # If somehow a flat is toward +Z, rotate +30 degrees in the same attempt
        print(
            f"WARN: orientation check suggests not vertex-to-+Z (max_z={max_z:.6f} vs R={R_hex:.6f}); rotating by +30 deg"
        )
        vertex_angles = [a + 30.0 for a in vertex_angles]
        pts, max_z = hex_pts_from_angles(vertex_angles)

    hex_tool = cq.Workplane(tool_plane).polyline(pts).close().extrude(L).val()
    bb_hex = hex_tool.BoundingBox(); c_hex = hex_tool.Center()
    print(
        f"ACHIEVED(hex_tool): center=({c_hex.x:.6f},{c_hex.y:.6f},{c_hex.z:.6f}) "
        f"axial_span_y=[{bb_hex.ymin:.3f}..{bb_hex.ymax:.3f}]"
    )
    if abs(c_hex.x - cx) > 1e-7 or abs(c_hex.z - cz) > 1e-7:
        dx, dz = (cx - c_hex.x), (cz - c_hex.z)
        print(f"CORRECTING: translating hex_tool by dX={dx:.6f}, dZ={dz:.6f} to enforce center at X=0,Z=0")
        hex_tool = hex_tool.translate((dx, 0, dz))
        c_hex2 = hex_tool.Center(); bb_hex2 = hex_tool.BoundingBox()
        print(
            f"ACHIEVED(hex_tool corrected): center=({c_hex2.x:.6f},{c_hex2.y:.6f},{c_hex2.z:.6f}) "
            f"axial_span_y=[{bb_hex2.ymin:.3f}..{bb_hex2.ymax:.3f}]"
        )

    print(
        "ACHIEVED(hex vertices angles in X-Z plane from +X): "
        + ", ".join([f"{a:.1f}deg" for a in vertex_angles])
        + f" ; max_vertex_z={max_z:.3f} (should be ~{R_hex:.3f} for vertex toward +Z)"
    )

    # --- Apply booleans on s0 only ---
    s1_vols_before = {i: solids[i].Volume() for i in untouched_idx}

    # Fill existing flower-shaped void, then cut hex through
    s0_filled = s0.fuse(fill_tool)
    s0_hex = s0_filled.cut(hex_tool)

    # --- Verification via sections from +Y and -Y views on the edited solid ---
    def extract_wires(sec_wp):
        try:
            v = sec_wp.val()
        except Exception:
            return []
        if v is None:
            return []
        try:
            ws = v.Wires()
            return ws if ws else []
        except Exception:
            try:
                comp = cq.Compound.makeCompound([v])
                return comp.Wires()
            except Exception:
                return []

    def section_report_on_shape(shp, y, tag):
        sec_plane = cq.Plane(origin=(0, y, 0), normal=(0, 1, 0), xDir=(1, 0, 0))
        sec_wp = cq.Workplane(sec_plane).add(shp).section()
        wires = extract_wires(sec_wp)
        print(f"VERIFY({tag}): section at Y={y:.3f} produced {len(wires)} wires")
        for wi, w in enumerate(wires[:12]):
            bb = w.BoundingBox(); c = w.Center(); en = len(w.Edges())
            print(
                f"  wire[{wi}] edges={en} center=({c.x:.3f},{c.y:.3f},{c.z:.3f}) "
                f"bboxX=[{bb.xmin:.3f},{bb.xmax:.3f}] bboxZ=[{bb.zmin:.3f},{bb.zmax:.3f}]"
            )
        hex_like = []
        for w in wires:
            c = w.Center()
            if len(w.Edges()) == 6 and abs(c.x) < 0.5 and abs(c.z) < 0.5:
                hex_like.append(w)
        print(f"VERIFY({tag}): found {len(hex_like)} candidate inner hex wires (edges==6, near center)")
        if hex_like:
            w = hex_like[0]
            bb = w.BoundingBox()
            print(
                f"VERIFY({tag}): hex candidate bboxX=[{bb.xmin:.3f},{bb.xmax:.3f}] bboxZ=[{bb.zmin:.3f},{bb.zmax:.3f}] "
                f"(expect max|Z|~{R_hex:.3f}, max|X|~{R_hex*math.cos(math.radians(30.0)):.3f})"
            )

    section_report_on_shape(s0_hex, y_top - 0.02, "+Y view / near top (s0)")
    section_report_on_shape(s0_hex, y_bot + 0.02, "-Y view / near bottom (s0)")

    # Also verify on the whole output compound (what QA views)
    out_solids = list(solids)
    out_solids[s0_idx] = s0_hex
    out = cq.Compound.makeCompound(out_solids)
    section_report_on_shape(out, y_top - 0.02, "+Y view / near top (COMPOUND)")
    section_report_on_shape(out, y_bot + 0.02, "-Y view / near bottom (COMPOUND)")

    # --- Verify untouched solids (s1) unchanged ---
    for i in untouched_idx:
        vb = s1_vols_before[i]
        va = out_solids[i].Volume()
        print(f"INFO: untouched solid[{i}] volume before={vb:.6f} after={va:.6f} delta={va-vb:.6f}")

    # Print achieved center and axial span for the resulting hex opening tool (proxy)
    print(
        f"ACHIEVED: intended hex center=({cx:.3f},*,{cz:.3f}) axial_span_y=[{y_bot:.3f}..{y_top:.3f}] "
        f"vertex_angles={vertex_angles}"
    )

    return out