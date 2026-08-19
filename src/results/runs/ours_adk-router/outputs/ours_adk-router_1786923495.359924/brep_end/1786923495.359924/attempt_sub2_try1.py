def my_cad_function(args):
    import cadquery as cq

    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input
    base = shape.val() if hasattr(shape, "val") else shape

    solids = base.Solids()
    print(f"SELECTED: {len(solids)} solids from imported STEP")
    for i, s in enumerate(solids):
        bb = s.BoundingBox()
        try:
            v = s.Volume()
        except Exception:
            v = float('nan')
        print(
            f"  solid s{i}: vol={v:.3f}  bbox=({bb.xmin:.3f},{bb.ymin:.3f},{bb.zmin:.3f})..({bb.xmax:.3f},{bb.ymax:.3f},{bb.zmax:.3f})  "
            f"lens=({bb.xlen:.3f},{bb.ylen:.3f},{bb.zlen:.3f})"
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
        gt = f.geomType()
        try:
            n = f.normalAt() if gt == "PLANE" else cq.Vector(0, 0, 0)
            n_txt = f"[{n.x:.3f},{n.y:.3f},{n.z:.3f}]" if gt == "PLANE" else "(n/a)"
        except Exception:
            n_txt = "(failed)"
        bb = f.BoundingBox()
        print(
            f"SELECTED: 1 face for face_idx #{idx}{(' ' + label) if label else ''}  "
            f"type={gt}  center=[{c.x:.3f},{c.y:.3f},{c.z:.3f}]  area={a:.3f}  normal={n_txt}  "
            f"bbox=({bb.xmin:.3f},{bb.ymin:.3f},{bb.zmin:.3f})..({bb.xmax:.3f},{bb.ymax:.3f},{bb.zmax:.3f})"
        )

    f38 = None
    f45 = None
    for idx, lab in [(38, "(target planar face for central crossing region)"), (45, "(target full cylindrical face r~6.477 @ y~9.525)")]:
        if idx >= len(faces):
            print(f"SELECTED: 0 faces for face_idx #{idx} (out of range)")
            continue
        f = faces[idx]
        print_face(idx, f, lab)
        if idx == 38:
            f38 = f
        elif idx == 45:
            f45 = f

    # --- Identify solid s1 by bbox (thin in X, long in Z, y 0..12.7) ---
    s1_idx = None
    for i, s in enumerate(solids):
        bb = s.BoundingBox()
        if (abs(bb.xlen - 25.4) < 1.0) and (abs(bb.ylen - 12.7) < 1.0) and (bb.zlen > 300):
            s1_idx = i
            break

    if s1_idx is None:
        # fallback: pick the longest in Z among those with xlen ~25.4 and ylen ~12.7
        cands = []
        for i, s in enumerate(solids):
            bb = s.BoundingBox()
            if (abs(bb.xlen - 25.4) < 2.0) and (abs(bb.ylen - 12.7) < 2.0):
                cands.append((bb.zlen, i))
        print(f"SELECTED: {len(cands)} candidate solids for s1 by (xlen~25.4,ylen~12.7)")
        if not cands:
            print("ERROR: Could not identify s1; returning input")
            return shape
        cands.sort(reverse=True)
        s1_idx = cands[0][1]

    s1 = solids[s1_idx]
    bb1_before = s1.BoundingBox()
    print(
        f"USING: solid s{s1_idx} as s1 for edit  bbox_before=({bb1_before.xmin:.3f},{bb1_before.ymin:.3f},{bb1_before.zmin:.3f})..({bb1_before.xmax:.3f},{bb1_before.ymax:.3f},{bb1_before.zmax:.3f})"
    )

    # --- Find the central full cylinder on s1 (fallback if face#45 isn't it) ---
    target_r = 6.477
    tol_r = 0.12
    tol_xyz = 0.75
    target_c = cq.Vector(0.0, 9.525, 0.0)

    cyl_face = None
    if f45 is not None and f45.geomType() == "CYLINDER":
        bb = f45.BoundingBox()
        r_est = 0.5 * max(bb.xlen, bb.zlen)
        c = f45.Center()
        if abs(r_est - target_r) <= tol_r and abs(c.x - target_c.x) <= tol_xyz and abs(c.y - target_c.y) <= tol_xyz and abs(c.z - target_c.z) <= tol_xyz:
            cyl_face = f45
            print(f"SELECTED: 1 face for central cylinder from global face#45  r_est={r_est:.3f} center=[{c.x:.3f},{c.y:.3f},{c.z:.3f}]")
        else:
            print(f"SELECTED: 0 faces for central cylinder from global face#45 (mismatch)  r_est={r_est:.3f} center=[{c.x:.3f},{c.y:.3f},{c.z:.3f}]")

    if cyl_face is None:
        cands = []
        for fi, f in enumerate(s1.Faces()):
            if f.geomType() != "CYLINDER":
                continue
            bb = f.BoundingBox()
            r_est = 0.5 * max(bb.xlen, bb.zlen)
            c = f.Center()
            if abs(r_est - target_r) <= tol_r and abs(c.x - target_c.x) <= tol_xyz and abs(c.y - target_c.y) <= tol_xyz and abs(c.z - target_c.z) <= tol_xyz:
                cands.append((fi, f, r_est, c))
        print(f"SELECTED: {len(cands)} candidate r~6.477 full cylindrical faces on s{s1_idx} near center [0,9.525,0]")
        if not cands:
            print("ERROR: Could not find the central full cylindrical face on s1; returning input")
            return shape
        cands.sort(key=lambda t: abs(t[3].y - target_c.y))
        fi, cyl_face, r_est, c = cands[0]
        print(f"  -> using s{s1_idx} local_face_idx={fi} as central cylinder  r_est={r_est:.3f} center=[{c.x:.3f},{c.y:.3f},{c.z:.3f}]")

    # --- Determine XZ footprint for central crossing region ---
    r_pad = target_r + 1.0
    x_half = max(abs(bb1_before.xmin), abs(bb1_before.xmax)) + 0.25  # default from overall blade width
    z_half = r_pad + 0.75  # safe default near-center

    used_face38 = False
    if f38 is not None and f38.geomType() == "PLANE":
        bb38 = f38.BoundingBox()
        xh = max(abs(bb38.xmin), abs(bb38.xmax)) + 0.75
        zh = max(abs(bb38.zmin), abs(bb38.zmax)) + 0.75
        # sanity: if zh looks like it includes the arms, clamp and warn
        if zh > 60:
            print(f"WARNING: face#38-derived z_half={zh:.3f} looks too large; clamping to 25.0 to avoid thinning arms")
            zh = 25.0
        if xh > 60:
            print(f"WARNING: face#38-derived x_half={xh:.3f} looks too large; clamping to blade half-width")
            xh = x_half
        x_half = max(x_half, xh, r_pad + 0.5)
        z_half = max(z_half, zh, r_pad + 0.5)
        used_face38 = True

    # final clamp to ensure we don't reach far into arms
    if z_half > 30.0:
        print(f"WARNING: computed z_half={z_half:.3f} still large; clamping to 30.0")
        z_half = 30.0

    print(
        "FOOTPRINT (s1 central crossing) halfspans: "
        f"x_half={x_half:.3f}  z_half={z_half:.3f}  (used_face#38={used_face38})  min_required_from_cyl_r={r_pad:.3f}"
    )

    # --- Central thinning target for s1: keep only upper layer Y=6.56..6.98 (0.42mm) in this central region ---
    # Numbers explicitly named by the sub-goal:
    y_keep_min = 6.56
    y_keep_max = 6.98
    y_keep_thk = y_keep_max - y_keep_min

    # Operate over the upper-half stack region (was 6.35 thick: 6.35..12.7)
    y_reg_min = 6.35 - 0.25
    y_reg_max = 12.70 + 0.25
    y_reg_len = y_reg_max - y_reg_min

    print(f"CENTRAL TARGET (s1) KEEP Y LIMITS: {y_keep_min:.2f}..{y_keep_max:.2f} mm  (thk {y_keep_thk:.2f} mm)")
    print(f"CENTRAL REGION BOX (s1) Y LIMITS: {y_reg_min:.2f}..{y_reg_max:.2f} mm")
    print(f"REFERENCE CYLINDER: r={target_r:.3f} mm  axis~[0,1,0]  center~[0,9.525,0]")

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

    central_before = s1.intersect(region_box)
    try:
        v_cb = central_before.Volume()
    except Exception:
        v_cb = float('nan')
    print(f"PROBE: central_before(s1∩region_box) volume={v_cb:.3f} mm^3")

    tool_remove = central_before.cut(slab_box)
    try:
        v_tr = tool_remove.Volume()
    except Exception:
        v_tr = float('nan')
    print(f"TOOL: tool_remove volume={v_tr:.3f} mm^3")

    if (v_tr == 0) or (abs(v_tr) < 1e-6):
        print("ERROR: tool_remove has ~0 volume (no-op risk). Returning input unchanged.")
        return shape

    try:
        s1_edited = s1.cut(tool_remove)
    except Exception as e:
        print(f"ERROR: Failed to cut s1 with tool_remove: {e}")
        return shape

    # --- Verification: resulting central Y limits and 0.42mm thickness ---
    central_after = s1_edited.intersect(region_box)
    bb_ca = central_after.BoundingBox()
    thk_meas = bb_ca.ymax - bb_ca.ymin
    print(
        "CENTRAL AFTER (s1 within region_box): "
        f"y[{bb_ca.ymin:.3f},{bb_ca.ymax:.3f}]  thk={thk_meas:.3f}  "
        f"(delta_ymin={bb_ca.ymin - y_keep_min:+.3f}, delta_ymax={bb_ca.ymax - y_keep_max:+.3f}, delta_thk={thk_meas - y_keep_thk:+.3f})"
    )

    bb1_after = s1_edited.BoundingBox()
    print(
        f"s1 bbox AFTER: ({bb1_after.xmin:.3f},{bb1_after.ymin:.3f},{bb1_after.zmin:.3f})..({bb1_after.xmax:.3f},{bb1_after.ymax:.3f},{bb1_after.zmax:.3f})"
    )
    print(
        "s1 bbox delta: "
        f"dxmin={bb1_after.xmin - bb1_before.xmin:+.3f}, dxmax={bb1_after.xmax - bb1_before.xmax:+.3f}, "
        f"dymin={bb1_after.ymin - bb1_before.ymin:+.3f}, dymax={bb1_after.ymax - bb1_before.ymax:+.3f}, "
        f"dzmin={bb1_after.zmin - bb1_before.zmin:+.3f}, dzmax={bb1_after.zmax - bb1_before.zmax:+.3f}"
    )

    # --- Recompound with only s1 replaced; all other bodies untouched ---
    out_solids = []
    for i, s in enumerate(solids):
        out_solids.append(s1_edited if i == s1_idx else s)
    out = cq.Compound.makeCompound(out_solids)
    return out