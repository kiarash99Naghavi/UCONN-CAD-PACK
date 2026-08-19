def my_cad_function(args):
    import cadquery as cq
    import math

    shape = cq.importers.importStep(args["input_file"])
    base = shape.val() if hasattr(shape, "val") else shape

    # -------------------- Locate solids (s0 hub to edit; s1 gear must remain unchanged) --------------------
    solids = base.Solids()
    print(f"INFO: base has {len(solids)} solids, {len(base.Faces())} faces, {len(base.Edges())} edges")

    # Named numbers from the sub-goal
    y_top = 3.175
    y_bot = -4.625
    thickness = y_top - y_bot  # 7.8
    axis = (0.0, 1.0, 0.0)
    cx, cz = 0.0, 0.0
    R = 10.5
    across_corners = 21.0
    across_flats = across_corners * math.cos(math.radians(30.0))

    print(f"TARGET: center X={cx:.3f}, Z={cz:.3f}; Y span [{y_bot:.3f}..{y_top:.3f}] (thickness={thickness:.3f}) axis={axis}")
    print(f"TARGET: hex across_corners={across_corners:.3f} (R={R:.3f}); across_flats={across_flats:.3f} (expected ~18.187)")

    # Select s0 by bbox match from the geometry index
    s0_idx = None
    for i, s in enumerate(solids):
        bb = s.BoundingBox()
        print(
            f"INFO: solid[{i}] bbox min=({bb.xmin:.3f},{bb.ymin:.3f},{bb.zmin:.3f}) "
            f"max=({bb.xmax:.3f},{bb.ymax:.3f},{bb.zmax:.3f}) lens=({bb.xlen:.3f},{bb.ylen:.3f},{bb.zlen:.3f}) vol={s.Volume():.3f}"
        )
        if (
            abs(bb.xmin - (-15.75)) < 0.5
            and abs(bb.xmax - (15.75)) < 0.5
            and abs(bb.zmin - (-15.75)) < 0.5
            and abs(bb.zmax - (15.75)) < 0.5
            and abs(bb.ymin - (y_bot)) < 0.5
            and abs(bb.ymax - (y_top)) < 0.5
        ):
            s0_idx = i

    if s0_idx is None:
        vols = [(i, s.Volume()) for i, s in enumerate(solids)]
        vols.sort(key=lambda t: t[1])
        s0_idx = vols[0][0]
        print(f"WARN: exact bbox match for s0 not found; falling back to smallest-volume solid idx={s0_idx}")

    print(f"SELECTED: 1 solid for edit (s0 hub) idx=[{s0_idx}]")
    untouched_idx = [i for i in range(len(solids)) if i != s0_idx]
    print(f"SELECTED: {len(untouched_idx)} solids left untouched (including s1 gear) idx={untouched_idx}")

    s0 = solids[s0_idx]

    # -------------------- Resolve the referenced faces (global indices) and verify --------------------
    faces_all = base.Faces()
    # Top face #1 (s0), bottom face #233 (s0) in the provided geometry index
    f_top = faces_all[1]
    f_bot = faces_all[233]
    ct = f_top.Center(); cb = f_bot.Center()
    print(
        f"CHECK: face#1 center=({ct.x:.3f},{ct.y:.3f},{ct.z:.3f}) area={f_top.Area():.3f} normal={tuple(round(v,3) for v in f_top.normalAt().toTuple())}"
    )
    print(
        f"CHECK: face#233 center=({cb.x:.3f},{cb.y:.3f},{cb.z:.3f}) area={f_bot.Area():.3f} normal={tuple(round(v,3) for v in f_bot.normalAt().toTuple())}"
    )

    # -------------------- Build an exact fill solid from the flower opening wire on top face #1 --------------------
    wires = f_top.Wires()
    print(f"SELECTED: {len(wires)} wires on face#1 (expect 2: outer + 1 inner loop)")

    # Identify inner wire as the one with the smaller bbox footprint
    wire_infos = []
    for wi, w in enumerate(wires):
        bb = w.BoundingBox()
        wire_infos.append((wi, w, bb.xlen * bb.zlen, bb))
        c = w.Center()
        print(
            f"  wire[{wi}] center=({c.x:.3f},{c.y:.3f},{c.z:.3f}) bbox xlen={bb.xlen:.3f} zlen={bb.zlen:.3f} (proxy_area={bb.xlen*bb.zlen:.3f}) edges={len(w.Edges())}"
        )

    wire_infos.sort(key=lambda t: t[2])
    inner_wire = wire_infos[0][1]
    inner_bb = wire_infos[0][3]
    print(
        f"SELECTED: 1 inner wire for flower opening fill  idx=[{wire_infos[0][0]}] "
        f"bbox xlen={inner_bb.xlen:.3f} zlen={inner_bb.zlen:.3f} edges={len(inner_wire.Edges())}"
    )

    # Make a planar face from that inner wire and extrude it through the hub thickness to 'plug' the flower opening
    vec_fill = cq.Vector(0, y_bot - y_top, 0)  # downward from top to bottom
    print(f"INFO: fill extrusion vector={tuple(round(v,3) for v in vec_fill.toTuple())} (should be (0,-7.8,0))")

    try:
        flower_face = cq.Face.makeFromWires(inner_wire)
        fill_tool = cq.Solid.extrudeLinear(flower_face, vec_fill)
        print("INFO: fill_tool built via Face.makeFromWires + Solid.extrudeLinear")
    except Exception as e:
        print(f"WARN: fill_tool primary build failed: {e}")
        # Fallback: use a workplane on the top face plane and extrude the wire
        wp_top = cq.Workplane(cq.Plane(origin=(0, y_top, 0), normal=axis, xDir=(1, 0, 0)))
        fill_tool = wp_top.add(inner_wire).toPending().extrude(y_bot - y_top).val()
        print("INFO: fill_tool built via Workplane.add(inner_wire).toPending().extrude")

    bb_fill = fill_tool.BoundingBox()
    c_fill = fill_tool.Center()
    print(
        f"ACHIEVED(fill_tool): center=({c_fill.x:.3f},{c_fill.y:.3f},{c_fill.z:.3f}) "
        f"axial_span_y=[{bb_fill.ymin:.3f}..{bb_fill.ymax:.3f}] (target [{y_bot:.3f}..{y_top:.3f}])"
    )

    # Fuse the fill tool into s0 to remove the existing through-void fully
    s0_filled = s0.fuse(fill_tool)

    # -------------------- Build the hex cut tool (regular hex through) --------------------
    # Vertex angles requested (in X-Z plane, measured from +X): 30, 90, 150, 210, 270, 330
    vertex_angles = [30.0, 90.0, 150.0, 210.0, 270.0, 330.0]

    # Plane: normal +Y; note plane local Y axis maps to global -Z when xDir=(+X)
    # So we pass points as (x_world, y_local) where y_local = -z_world
    def build_hex_pts(angles_deg):
        pts_local = []
        z_worlds = []
        for ang in angles_deg:
            t = math.radians(ang)
            xw = R * math.cos(t)
            zw = R * math.sin(t)
            pts_local.append((xw, -zw))
            z_worlds.append(zw)
        return pts_local, z_worlds

    pts_local, z_worlds = build_hex_pts(vertex_angles)
    max_z = max(z_worlds)

    # If a flat points toward +Z, then max_z will be < R. Rotate by +30 deg in the same attempt.
    if abs(max_z - R) > 1e-6:
        print(
            f"WARN: orientation check suggests not vertex-to-+Z (max_z={max_z:.6f} vs R={R:.6f}). "
            "Rotating profile by +30 deg and rebuilding."
        )
        vertex_angles = [a + 30.0 for a in vertex_angles]
        pts_local, z_worlds = build_hex_pts(vertex_angles)
        max_z = max(z_worlds)

    wp_bot = cq.Workplane(cq.Plane(origin=(0, y_bot, 0), normal=axis, xDir=(1, 0, 0)))
    print(f"INFO: hex sketch plane origin=(0,{y_bot:.3f},0) normal={axis} xDir=(1,0,0)")

    hex_tool = wp_bot.polyline(pts_local).close().extrude(thickness).val()

    # Self-check: enforce center X=0, Z=0 for the tool
    c_hex = hex_tool.Center()
    bb_hex = hex_tool.BoundingBox()
    print(
        f"ACHIEVED(hex tool pre-correct): center=({c_hex.x:.6f},{c_hex.y:.6f},{c_hex.z:.6f}) "
        f"axial_span_y=[{bb_hex.ymin:.3f}..{bb_hex.ymax:.3f}]"
    )
    if abs(c_hex.x - cx) > 1e-7 or abs(c_hex.z - cz) > 1e-7:
        dx, dz = (cx - c_hex.x), (cz - c_hex.z)
        print(f"CORRECTING: translating hex tool by dX={dx:.6f}, dZ={dz:.6f} to enforce center at X=0,Z=0")
        hex_tool = hex_tool.translate((dx, 0, dz))
        c_hex2 = hex_tool.Center()
        bb_hex2 = hex_tool.BoundingBox()
        print(
            f"ACHIEVED(hex tool corrected): center=({c_hex2.x:.6f},{c_hex2.y:.6f},{c_hex2.z:.6f}) "
            f"axial_span_y=[{bb_hex2.ymin:.3f}..{bb_hex2.ymax:.3f}]"
        )

    print(
        "ACHIEVED(hex vertices angles in X-Z plane from +X): "
        + ", ".join([f"{a:.1f}deg" for a in vertex_angles])
        + f" ; max_vertex_z={max_z:.3f} (should be ~{R:.3f} for vertex toward +Z)"
    )

    # -------------------- Cut the hex through-opening on s0 (after filling flower void) --------------------
    s0_hex = s0_filled.cut(hex_tool)

    # -------------------- Verification: sections near top & bottom should show a hex hole, not flower scallops --------------------
    def extract_wires_from_section(sec_shape):
        # sec_shape may be Wire/Compound/EdgeCompound depending on CQ internals
        try:
            if isinstance(sec_shape, cq.Wire):
                return [sec_shape]
        except Exception:
            pass
        try:
            ws = sec_shape.Wires()
            if ws and len(ws) > 0:
                return ws
        except Exception:
            pass
        # Try converting to Compound and extracting
        try:
            comp = cq.Compound.makeCompound([sec_shape])
            return comp.Wires()
        except Exception:
            return []

    def section_report(solid, y, tag):
        plane = cq.Plane(origin=(0, y, 0), normal=axis, xDir=(1, 0, 0))
        sec = cq.Workplane(plane).add(solid).section()
        sec_val = sec.val()
        wires = extract_wires_from_section(sec_val)
        print(f"VERIFY({tag}): section at Y={y:.3f} produced {len(wires)} wires")

        # Print up to 12 wires (outer boundary + any inner boundaries)
        for i, w in enumerate(wires[:12]):
            bb = w.BoundingBox()
            c = w.Center()
            en = len(w.Edges())
            print(
                f"  wire[{i}] edges={en} center=({c.x:.3f},{c.y:.3f},{c.z:.3f}) "
                f"bboxX=[{bb.xmin:.3f},{bb.xmax:.3f}] bboxZ=[{bb.zmin:.3f},{bb.zmax:.3f}]"
            )

        # Specifically look for the inner hex wire: 6 edges and centered near origin
        hex_like = []
        for w in wires:
            c = w.Center()
            en = len(w.Edges())
            if en == 6 and abs(c.x) < 0.5 and abs(c.z) < 0.5:
                hex_like.append(w)
        print(f"VERIFY({tag}): found {len(hex_like)} candidate inner hex wires (edges==6, near center)")
        if hex_like:
            w = hex_like[0]
            bb = w.BoundingBox()
            print(
                f"VERIFY({tag}): hex candidate bboxX=[{bb.xmin:.3f},{bb.xmax:.3f}] bboxZ=[{bb.zmin:.3f},{bb.zmax:.3f}] "
                f"(expect max|Z|~{R:.3f}, max|X|~{R*math.cos(math.radians(30.0)):.3f})"
            )

    section_report(s0_hex, y_top - 0.02, "+Y view / near top")
    section_report(s0_hex, y_bot + 0.02, "-Y view / near bottom")

    # -------------------- Ensure s1 remains unchanged: reuse original solids list order, replacing only s0 --------------------
    out_solids = list(solids)
    out_solids[s0_idx] = s0_hex

    # Print untouched solids volumes/bboxes for diagnostics
    for i in untouched_idx:
        s = out_solids[i]
        bb = s.BoundingBox()
        print(
            f"INFO: untouched solid[{i}] vol={s.Volume():.3f} bbox lens=({bb.xlen:.3f},{bb.ylen:.3f},{bb.zlen:.3f})"
        )

    out = cq.Compound.makeCompound(out_solids)
    return out