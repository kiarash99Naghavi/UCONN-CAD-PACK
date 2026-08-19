def my_cad_function(args):
    import cadquery as cq

    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input
    base = shape.val() if hasattr(shape, "val") else shape

    # --- Resolve solids ---
    solids = base.Solids()
    print(f"SELECTED: {len(solids)} solids from imported STEP")
    for i, s in enumerate(solids):
        bb = s.BoundingBox()
        try:
            v = s.Volume()
        except Exception:
            v = float('nan')
        print(
            f"  solid s{i}: vol={v:.3f}  bbox=({bb.xmin:.3f},{bb.ymin:.3f},{bb.zmin:.3f})..({bb.xmax:.3f},{bb.ymax:.3f},{bb.zmax:.3f})"
        )

    if len(solids) < 1:
        print("ERROR: No solids found; returning input")
        return shape

    # --- Resolve referenced faces by GLOBAL face indices (as instructed) ---
    faces = base.Faces()
    print(f"SELECTED: {len(faces)} faces on base shape (global face indexing)")

    def print_face(idx, f, label=""):
        c = f.Center()
        a = f.Area()
        n = f.normalAt()
        bb = f.BoundingBox()
        print(
            f"SELECTED: 1 face for face_idx #{idx}{(' ' + label) if label else ''}  "
            f"center=[{c.x:.3f},{c.y:.3f},{c.z:.3f}]  area={a:.3f}  "
            f"normal=[{n.x:.3f},{n.y:.3f},{n.z:.3f}]  "
            f"bbox=({bb.xmin:.3f},{bb.ymin:.3f},{bb.zmin:.3f})..({bb.xmax:.3f},{bb.ymax:.3f},{bb.zmax:.3f})"
        )

    f2 = f14 = f15 = None
    for idx, lab in [(2, "(target planar +Y @~6.35)"), (14, "(target planar -Y @~0)"), (15, "(target cylindrical r~6.477)" )]:
        if idx >= len(faces):
            print(f"SELECTED: 0 faces for face_idx #{idx} (out of range)")
            continue
        f = faces[idx]
        print_face(idx, f, lab)
        if idx == 2:
            f2 = f
        elif idx == 14:
            f14 = f
        elif idx == 15:
            f15 = f

    # --- Find which solid is s0: contains the full cylindrical face r=6.477 centered near [0,3.175,0] ---
    target_r = 6.477
    target_cy = 3.175
    tol_r = 0.10
    tol_y = 0.50

    s0_idx = None
    s0_cyl_face = None

    for si, s in enumerate(solids):
        cyl_cands = []
        for fi, f in enumerate(s.Faces()):
            if f.geomType() != "CYLINDER":
                continue
            bb = f.BoundingBox()
            # For a cylinder with axis ~Y, xlen and zlen ~ 2r
            r_est = 0.5 * max(bb.xlen, bb.zlen)
            c = f.Center()
            if abs(r_est - target_r) <= tol_r and abs(c.x) <= 0.5 and abs(c.z) <= 0.5 and abs(c.y - target_cy) <= tol_y:
                cyl_cands.append((fi, f, r_est, c))
        print(f"SELECTED: {len(cyl_cands)} candidate r~6.477 cylindrical faces on solid s{si} for identifying s0")
        if cyl_cands:
            # Prefer the closest in y to 3.175
            cyl_cands.sort(key=lambda t: abs(t[3].y - target_cy))
            fi, f, r_est, c = cyl_cands[0]
            print(
                f"  -> best candidate on s{si}: local_face_idx={fi} r_est={r_est:.3f} center=[{c.x:.3f},{c.y:.3f},{c.z:.3f}]"
            )
            s0_idx = si
            s0_cyl_face = f
            break

    if s0_idx is None:
        print("ERROR: Could not identify s0 by its r=6.477 full cylinder near y=3.175; returning input")
        return shape

    s0 = solids[s0_idx]
    bb0_before = s0.BoundingBox()
    print(
        f"USING: solid s{s0_idx} as s0 for edit  bbox_before=({bb0_before.xmin:.3f},{bb0_before.ymin:.3f},{bb0_before.zmin:.3f})..({bb0_before.xmax:.3f},{bb0_before.ymax:.3f},{bb0_before.zmax:.3f})"
    )

    # --- Build an XZ footprint for the central crossing portion from planar face #2 (preferred) ---
    # Fallback: find planar face on s0 at y~6.35 with +Y normal.
    if f2 is not None:
        bb2 = f2.BoundingBox()
        n2 = f2.normalAt()
        if abs(bb2.ymin - 6.35) > 0.25 and abs(bb2.ymax - 6.35) > 0.25:
            print("WARNING: global face #2 does not appear to lie at y~6.35; attempting fallback planar-face search on s0")
            f2 = None
        elif abs(n2.y - 1.0) < 0.5:
            # OK
            pass
        else:
            print("WARNING: global face #2 normal is not strongly +Y; attempting fallback planar-face search on s0")
            f2 = None

    if f2 is None:
        # Search within s0
        cands = []
        for fi, f in enumerate(s0.Faces()):
            if f.geomType() != "PLANE":
                continue
            c = f.Center()
            n = f.normalAt()
            if abs(c.y - 6.35) <= 0.25 and n.y > 0.8:
                cands.append((fi, f, f.Area(), c, n))
        print(f"SELECTED: {len(cands)} planar +Y faces near y=6.35 on s0 for footprint")
        if not cands:
            print("ERROR: Could not find planar +Y face near y=6.35 on s0; returning input")
            return shape
        cands.sort(key=lambda t: -t[2])
        fi, f2, area, c, n = cands[0]
        print(
            f"  -> using s0 local_face_idx={fi} as footprint face: area={area:.3f} center=[{c.x:.3f},{c.y:.3f},{c.z:.3f}] normal=[{n.x:.3f},{n.y:.3f},{n.z:.3f}]"
        )

    bb2 = f2.BoundingBox()
    x_half = max(abs(bb2.xmin), abs(bb2.xmax)) + 0.75
    z_half = max(abs(bb2.zmin), abs(bb2.zmax)) + 0.75
    print(
        f"FOOTPRINT from face#2 bbox: x[{bb2.xmin:.3f},{bb2.xmax:.3f}] z[{bb2.zmin:.3f},{bb2.zmax:.3f}] -> halfspans x_half={x_half:.3f} z_half={z_half:.3f}"
    )

    # --- Central thinning target: keep only Y=5.72..6.14 (0.42mm) in this central region; leave outer arms untouched ---
    y_keep_min = 5.72
    y_keep_max = 6.14
    y_keep_thk = y_keep_max - y_keep_min

    # region box spans the *lower half* that currently is 6.35 thick (0..6.35), with margin
    y_reg_min = -0.25
    y_reg_max = 6.60
    y_reg_len = y_reg_max - y_reg_min

    region_box = cq.Solid.makeBox(
        2 * x_half,
        y_reg_len,
        2 * z_half,
        cq.Vector(-x_half, y_reg_min, -z_half),
    )
    slab_box = cq.Solid.makeBox(
        2 * x_half,
        y_keep_thk,
        2 * z_half,
        cq.Vector(-x_half, y_keep_min, -z_half),
    )

    print(f"CENTRAL TARGET (s0) KEEP Y LIMITS: {y_keep_min:.2f}..{y_keep_max:.2f} mm  (thk {y_keep_thk:.2f} mm)")
    print(f"CENTRAL REGION BOX Y LIMITS: {y_reg_min:.2f}..{y_reg_max:.2f} mm")

    # Isolate central region volume and cut away everything except slab
    central_before = s0.intersect(region_box)
    try:
        v_cb = central_before.Volume()
    except Exception:
        v_cb = float('nan')
    print(f"PROBE: central_before volume={v_cb:.3f} mm^3")

    tool_remove = central_before.cut(slab_box)
    try:
        v_tr = tool_remove.Volume()
    except Exception:
        v_tr = float('nan')
    print(f"TOOL: tool_remove volume={v_tr:.3f} mm^3")

    if (v_tr == 0) or (abs(v_tr) < 1e-6):
        print("ERROR: tool_remove has ~0 volume (no-op risk). Returning input unchanged.")
        return shape

    # Apply cut only to s0
    try:
        s0_edited = s0.cut(tool_remove)
    except Exception as e:
        print(f"ERROR: Failed to cut s0 with tool_remove: {e}")
        return shape

    # --- Verification: resulting central Y limits and 0.42mm thickness ---
    central_after = s0_edited.intersect(region_box)
    bb_ca = central_after.BoundingBox()
    thk_meas = bb_ca.ymax - bb_ca.ymin
    print(
        "CENTRAL AFTER (s0 within region_box): "
        f"y[{bb_ca.ymin:.3f},{bb_ca.ymax:.3f}]  thk={thk_meas:.3f}  "
        f"(delta_ymin={bb_ca.ymin - y_keep_min:+.3f}, delta_ymax={bb_ca.ymax - y_keep_max:+.3f}, delta_thk={thk_meas - y_keep_thk:+.3f})"
    )

    bb0_after = s0_edited.BoundingBox()
    print(
        f"s0 bbox AFTER: ({bb0_after.xmin:.3f},{bb0_after.ymin:.3f},{bb0_after.zmin:.3f})..({bb0_after.xmax:.3f},{bb0_after.ymax:.3f},{bb0_after.zmax:.3f})"
    )
    print(
        "s0 bbox delta: "
        f"dxmin={bb0_after.xmin - bb0_before.xmin:+.3f}, dxmax={bb0_after.xmax - bb0_before.xmax:+.3f}, "
        f"dymin={bb0_after.ymin - bb0_before.ymin:+.3f}, dymax={bb0_after.ymax - bb0_before.ymax:+.3f}, "
        f"dzmin={bb0_after.zmin - bb0_before.zmin:+.3f}, dzmax={bb0_after.zmax - bb0_before.zmax:+.3f}"
    )

    # --- Recompound with only s0 replaced; all other bodies untouched ---
    out_solids = []
    for i, s in enumerate(solids):
        out_solids.append(s0_edited if i == s0_idx else s)
    out = cq.Compound.makeCompound(out_solids)
    return out