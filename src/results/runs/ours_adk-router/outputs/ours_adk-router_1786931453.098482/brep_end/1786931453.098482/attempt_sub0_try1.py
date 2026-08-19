def my_cad_function(args):
    import cadquery as cq
    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input

    base = shape.val() if hasattr(shape, "val") else shape
    solids = base.Solids()
    print(f"SELECTED: {len(solids)} solids in imported shape")
    if len(solids) == 0:
        print("ERROR: No solids found; returning input")
        return shape

    # Target body s0 is expected to be solids[0] per provided index
    s0 = solids[0]
    bb0 = s0.BoundingBox()
    print(f"s0 bbox: xmin={bb0.xmin:.3f} xmax={bb0.xmax:.3f} xlen={bb0.xlen:.3f} | ymin={bb0.ymin:.3f} ymax={bb0.ymax:.3f} | zmin={bb0.zmin:.3f} zmax={bb0.zmax:.3f}")

    # Resolve and verify reference faces #5 and #12 (on s0)
    faces0 = s0.Faces()
    f5 = faces0[5]
    f12 = faces0[12]
    c5 = f5.Center(); n5 = f5.normalAt(); a5 = f5.Area()
    c12 = f12.Center(); n12 = f12.normalAt(); a12 = f12.Area()
    print(f"SELECTED: 1 face for ref skin #5   center={[round(c5.x,3), round(c5.y,3), round(c5.z,3)]} area={a5:.3f} normal={[round(n5.x,3), round(n5.y,3), round(n5.z,3)]}")
    print(f"SELECTED: 1 face for ref skin #12  center={[round(c12.x,3), round(c12.y,3), round(c12.z,3)]} area={a12:.3f} normal={[round(n12.x,3), round(n12.y,3), round(n12.z,3)]}")

    # Resolve the existing matching port mouth edges edge_idx [73, 91] and print their measured info
    edges0 = s0.Edges()
    e73 = edges0[73]
    e91 = edges0[91]
    def edge_info(e, idx):
        c = e.Center()
        gt = None
        r = None
        try:
            gt = e.geomType()
        except Exception:
            gt = "<geomType unavailable>"
        try:
            r = e.radius()
        except Exception:
            r = None
        msg = f"edge#{idx}: geomType={gt} center={[round(c.x,3), round(c.y,3), round(c.z,3)]}"
        if r is not None:
            msg += f" radius={r:.4f} dia={2*r:.4f}"
        return msg
    print("SELECTED: 2 edges for existing port mouth reference idx=[73, 91]")
    print("  " + edge_info(e73, 73))
    print("  " + edge_info(e91, 91))

    # Sub-goal numbers (explicit print)
    target_y = 146.05
    target_z = -241.30
    target_d = 44.45
    target_r = target_d / 2.0
    print(f"TARGET: new outlet port center YZ=({target_y:.2f}, {target_z:.2f}) diameter={target_d:.2f} radius={target_r:.4f}")

    # Build a cylindrical through-cut along measured X direction (use face #5 normal as X direction)
    # Use a tool that spans beyond s0 bbox in X to guarantee through.
    axis_dir = cq.Vector(n5.x, n5.y, n5.z)
    # Ensure axis_dir points along +X (pure direction; if face #5 normal isn't +X for some reason, still ok for cylinder)
    # Start point well before bbox on the -X side relative to global; use global +X direction for robustness.
    axis_dir = cq.Vector(1, 0, 0)
    tool_len = bb0.xlen + 200.0
    tool_base = cq.Vector(bb0.xmin - 100.0, target_y, target_z)

    tool = cq.Solid.makeCylinder(target_r, tool_len, pnt=tool_base, dir=axis_dir)
    print(f"TOOL: cylinder r={target_r:.4f} len={tool_len:.3f} base={[round(tool_base.x,3), round(tool_base.y,3), round(tool_base.z,3)]} dir={[axis_dir.x, axis_dir.y, axis_dir.z]}")

    # First cut attempt
    edited_s0 = s0.cut(tool)

    # Self-check: removed material
    removed = s0.cut(edited_s0)
    try:
        rem_vol = removed.Volume()
    except Exception:
        rem_vol = None
    if rem_vol is None or rem_vol < 1e-6:
        print("WARNING: Removed volume is ~0; tool may not have intersected. Returning original shape.")
        return shape

    rem_bb = removed.BoundingBox()
    rem_c = rem_bb.center
    achieved_y = rem_c.y
    achieved_z = rem_c.z
    achieved_d = max(rem_bb.ylen, rem_bb.zlen)
    print(f"ACHIEVED (attempt1): removed_bbox_center YZ=({achieved_y:.3f}, {achieved_z:.3f}) approx_d={achieved_d:.3f}")
    print(f"DELTA (attempt1): dY={achieved_y-target_y:+.3f} dZ={achieved_z-target_z:+.3f} dD={achieved_d-target_d:+.3f}")

    # Correct within same attempt if needed
    if abs(achieved_y - target_y) > 0.01 or abs(achieved_z - target_z) > 0.01 or abs(achieved_d - target_d) > 0.02:
        dy = target_y - achieved_y
        dz = target_z - achieved_z
        # Diameter mismatch would indicate wrong radius; adjust if needed.
        dr = 0.0
        if abs(achieved_d - target_d) > 0.02:
            dr = (target_d - achieved_d) / 2.0
        print(f"CORRECTION: translating tool by (0,{dy:+.3f},{dz:+.3f}) and adjusting radius by {dr:+.4f}")
        corr_r = target_r + dr
        tool2_base = cq.Vector(bb0.xmin - 100.0, target_y, target_z)
        tool2 = cq.Solid.makeCylinder(corr_r, tool_len, pnt=tool2_base, dir=axis_dir)
        edited_s0 = s0.cut(tool2)
        removed2 = s0.cut(edited_s0)
        rem2_bb = removed2.BoundingBox()
        rem2_c = rem2_bb.center
        achieved2_y = rem2_c.y
        achieved2_z = rem2_c.z
        achieved2_d = max(rem2_bb.ylen, rem2_bb.zlen)
        print(f"ACHIEVED (attempt2): removed_bbox_center YZ=({achieved2_y:.3f}, {achieved2_z:.3f}) approx_d={achieved2_d:.3f}")
        print(f"DELTA (attempt2): dY={achieved2_y-target_y:+.3f} dZ={achieved2_z-target_z:+.3f} dD={achieved2_d-target_d:+.3f}")

    # Recompound: keep every other body unchanged
    out = cq.Compound.makeCompound([s for i, s in enumerate(solids) if i != 0] + [edited_s0])
    return out