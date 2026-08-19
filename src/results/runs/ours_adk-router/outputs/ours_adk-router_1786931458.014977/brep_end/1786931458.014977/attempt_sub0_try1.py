def my_cad_function(args):
    import cadquery as cq
    import math

    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input
    base = shape.val() if hasattr(shape, "val") else shape

    # --- Resolve and print the referenced faces to confirm index alignment ---
    faces = base.Faces()
    print(f"INFO: base has {len(base.Solids())} solids, {len(faces)} faces")
    for fi in [1, 233]:
        try:
            f = faces[fi]
            c = f.Center()
            n = f.normalAt()
            a = f.Area()
            print(
                f"CHECK: face#{fi} center=({c.x:.3f},{c.y:.3f},{c.z:.3f}) area={a:.3f} normal=({n.x:.3f},{n.y:.3f},{n.z:.3f})"
            )
        except Exception as e:
            print(f"WARN: could not resolve face#{fi}: {e}")

    # --- Identify s0 (hub) by its bounding box (expected ~31.5 x 7.8 x 31.5) ---
    solids = base.Solids()
    s0_idx = None
    for i, s in enumerate(solids):
        bb = s.BoundingBox()
        print(
            f"INFO: solid[{i}] bbox min=({bb.xmin:.3f},{bb.ymin:.3f},{bb.zmin:.3f}) max=({bb.xmax:.3f},{bb.ymax:.3f},{bb.zmax:.3f}) lens=({bb.xlen:.3f},{bb.ylen:.3f},{bb.zlen:.3f}) vol={s.Volume():.3f}"
        )
        if (
            abs(bb.xmin - (-15.75)) < 0.5
            and abs(bb.xmax - (15.75)) < 0.5
            and abs(bb.zmin - (-15.75)) < 0.5
            and abs(bb.zmax - (15.75)) < 0.5
            and abs(bb.ymin - (-4.625)) < 0.5
            and abs(bb.ymax - (3.175)) < 0.5
        ):
            s0_idx = i

    if s0_idx is None:
        # fallback: choose the solid with smallest volume as hub-like
        vols = [(i, s.Volume()) for i, s in enumerate(solids)]
        vols.sort(key=lambda t: t[1])
        s0_idx = vols[0][0]
        print(f"WARN: exact bbox match for s0 not found; falling back to smallest-volume solid idx={s0_idx}")

    print(f"SELECTED: 1 solid for edit (s0) idx=[{s0_idx}]")

    s0 = solids[s0_idx]
    s1_others = [s for i, s in enumerate(solids) if i != s0_idx]
    if len(s1_others) == 1:
        print(f"SELECTED: 1 solid left untouched (s1) idx={[i for i in range(len(solids)) if i != s0_idx]}")
    else:
        print(f"SELECTED: {len(s1_others)} solids left untouched idx={[i for i in range(len(solids)) if i != s0_idx]}")

    # --- Named numbers from the sub-goal ---
    y_top = 3.175
    y_bot = -4.625
    thickness = y_top - y_bot  # 7.8
    axis = (0, 1, 0)
    cx, cz = 0.0, 0.0
    R = 10.5  # circumscribed radius (across corners 21.0)
    across_corners = 21.0
    across_flats = across_corners * math.cos(math.radians(30.0))  # = 18.1865...
    print(f"TARGET: center X={cx:.3f}, Z={cz:.3f}; Y span [{y_bot:.3f}..{y_top:.3f}] (thickness={thickness:.3f}) axis={axis}")
    print(f"TARGET: hex across_corners={across_corners:.3f} (R={R:.3f}); across_flats={across_flats:.3f} (expected ~18.187)")

    # --- Build a 'plug' to fill the existing flower-shaped through opening ---
    # Slightly oversize radius to ensure full fill; still well within hub OD (15.75)
    plug_r = R + 0.05
    wp_bot = cq.Workplane(cq.Plane(origin=(0, y_bot, 0), normal=axis, xDir=(1, 0, 0)))
    print(f"INFO: sketch plane origin(bottom)=(0,{y_bot:.3f},0) normal={axis} (for plug & hex)")
    plug = wp_bot.circle(plug_r).extrude(thickness).val()
    bbp = plug.BoundingBox()
    print(
        f"INFO: plug bbox ymin/ymax=({bbp.ymin:.3f},{bbp.ymax:.3f}) r~{plug_r:.3f} center=({plug.Center().x:.3f},{plug.Center().y:.3f},{plug.Center().z:.3f})"
    )

    # Fill (union) then re-cut
    s0_filled = s0.fuse(plug)

    # --- Create the regular hex cut tool, clocked with a vertex toward +Z ---
    vertex_angles = [30, 90, 150, 210, 270, 330]
    pts = []
    for ang in vertex_angles:
        t = math.radians(ang)
        x = R * math.cos(t)
        z = R * math.sin(t)
        pts.append((x, z))  # on XZ plane (workplane coords x,z)

    # Orientation check: +Z should hit a vertex => max z should be ~R
    max_z = max(p[1] for p in pts)
    if abs(max_z - R) > 1e-3:
        print(
            f"WARN: hex orientation appears to have a flat toward +Z (max_z={max_z:.6f} != R={R:.6f}); rotating by +30 deg and rebuilding"
        )
        vertex_angles = [a + 30 for a in vertex_angles]
        pts = []
        for ang in vertex_angles:
            t = math.radians(ang)
            pts.append((R * math.cos(t), R * math.sin(t)))
        max_z = max(p[1] for p in pts)

    hex_tool = wp_bot.polyline(pts).close().extrude(thickness).val()

    bbh = hex_tool.BoundingBox()
    ct = hex_tool.Center()
    print(
        f"ACHIEVED(hex tool): center=({ct.x:.3f},{ct.y:.3f},{ct.z:.3f}) axial_span_y=[{bbh.ymin:.3f}..{bbh.ymax:.3f}]"
    )
    print(
        "ACHIEVED(hex vertices): "
        + ", ".join([f"{a:.1f}deg" for a in vertex_angles])
        + f" ; max_vertex_z={max_z:.3f} (should be ~{R:.3f} to point at +Z)"
    )

    # Self-check: enforce center X=0, Z=0 for tool (if STEP imported slightly off, correct tool only)
    if abs(ct.x - cx) > 1e-6 or abs(ct.z - cz) > 1e-6:
        dx, dz = (cx - ct.x), (cz - ct.z)
        print(f"CORRECTING: translating hex tool by dX={dx:.6f}, dZ={dz:.6f} to enforce center at X=0,Z=0")
        hex_tool = hex_tool.translate((dx, 0, dz))
        ct2 = hex_tool.Center()
        bbh2 = hex_tool.BoundingBox()
        print(
            f"ACHIEVED(hex tool corrected): center=({ct2.x:.3f},{ct2.y:.3f},{ct2.z:.3f}) axial_span_y=[{bbh2.ymin:.3f}..{bbh2.ymax:.3f}]"
        )

    # --- Cut the hex through-profile (after filling the old flower hole) ---
    s0_hex = s0_filled.cut(hex_tool)

    # --- Verification via sections near top and bottom (from +Y and -Y views) ---
    def section_report(solid, y, tag):
        sec = cq.Workplane(cq.Plane(origin=(0, y, 0), normal=axis, xDir=(1, 0, 0))).add(solid).section()
        ws = sec.vals()
        print(f"VERIFY({tag}): section at Y={y:.3f} produced {len(ws)} wires")
        # Print edge-counts and approximate areas to spot hex inner wire (6 edges)
        for wi, w in enumerate(ws[:10]):
            try:
                edges_n = len(w.Edges())
            except Exception:
                edges_n = -1
            try:
                # area of a wire isn't directly available; approximate via face from wire
                f = cq.Face.makeFromWires(w)
                area = f.Area()
            except Exception:
                area = float('nan')
            c = w.Center()
            print(
                f"  wire[{wi}] edges={edges_n} approx_area={area:.3f} center=({c.x:.3f},{c.y:.3f},{c.z:.3f})"
            )

    # Use a small offset inside the solid to avoid coinciding exactly with boundary
    section_report(s0_hex, y_top - 0.01, "+Y view / near top")
    section_report(s0_hex, y_bot + 0.01, "-Y view / near bottom")

    # --- Ensure s1 is unchanged (we keep original reference and report its volume/bbox) ---
    if len(s1_others) >= 1:
        s1 = s1_others[0]
        bb1 = s1.BoundingBox()
        print(
            f"INFO: untouched solid (s1) vol={s1.Volume():.3f} bbox lens=({bb1.xlen:.3f},{bb1.ylen:.3f},{bb1.zlen:.3f})"
        )

    # --- Recompound: keep other solids byte-identical by reusing original shapes ---
    out = cq.Compound.makeCompound([s0_hex] + s1_others)
    return out