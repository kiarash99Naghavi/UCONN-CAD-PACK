def my_cad_function(args):
    import cadquery as cq

    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input
    base = shape.val() if hasattr(shape, "val") else shape

    # --- Resolve and report the referenced anchor face (#11) ---
    faces = base.Faces()
    print(f"INFO: base has {len(faces)} faces")
    f11 = faces[11]
    c11 = f11.Center()
    n11 = f11.normalAt()
    print(
        "SELECTED: 1 face for anchor bottom face #11 "
        f"center=({c11.x:.3f},{c11.y:.3f},{c11.z:.3f}) normal=({n11.x:.3f},{n11.y:.3f},{n11.z:.3f}) area={f11.Area():.3f}"
    )

    # --- Parameters from sub-goal (explicit absolute anchors) ---
    z0 = -0.75
    flange_thk = 0.5
    z1 = z0 + flange_thk  # -0.25

    inner_xmin, inner_xmax = -1.0, 1.0
    inner_ymin, inner_ymax = -3.0, 3.0
    outer_xmin, outer_xmax = -3.0, 3.0
    outer_ymin, outer_ymax = -5.0, 5.0

    inner_w = inner_xmax - inner_xmin  # 2
    inner_h = inner_ymax - inner_ymin  # 6
    outer_w = outer_xmax - outer_xmin  # 6
    outer_h = outer_ymax - outer_ymin  # 10

    print(
        "INFO: target flange numbers: "
        f"z0={z0}, z1={z1}, thk={flange_thk}; "
        f"inner x=[{inner_xmin},{inner_xmax}] y=[{inner_ymin},{inner_ymax}]; "
        f"outer x=[{outer_xmin},{outer_xmax}] y=[{outer_ymin},{outer_ymax}]"
    )

    # Build as a full outer plate and fuse to base.
    # The central region is already occupied by the base solid, so the union adds only the perimeter flange.
    plane = cq.Plane(origin=(0, 0, z0), normal=(0, 0, 1))
    print(f"INFO: sketch plane origin={(0, 0, z0)} normal={(0, 0, 1)}")

    flange_wp = cq.Workplane(plane).rect(outer_w, outer_h).extrude(flange_thk)
    flange_solid = flange_wp.val()
    bb_tool = flange_solid.BoundingBox()
    print(
        "SELECTED: 1 solid for flange tool (outer plate) "
        f"tool_bbox x=[{bb_tool.xmin:.3f},{bb_tool.xmax:.3f}] y=[{bb_tool.ymin:.3f},{bb_tool.ymax:.3f}] z=[{bb_tool.zmin:.3f},{bb_tool.zmax:.3f}]"
    )

    # --- Placement self-check: isolate actually-added material (should be a rectangular ring) ---
    try:
        added_pre = flange_solid.cut(base)
        bb_add_pre = added_pre.BoundingBox()
        print(
            "CHECK: added (tool minus base) bbox "
            f"x=[{bb_add_pre.xmin:.3f},{bb_add_pre.xmax:.3f}] y=[{bb_add_pre.ymin:.3f},{bb_add_pre.ymax:.3f}] z=[{bb_add_pre.zmin:.3f},{bb_add_pre.zmax:.3f}]"
        )
        print(
            "CHECK: target added flange bounds "
            f"x=[{outer_xmin:.3f},{outer_xmax:.3f}] y=[{outer_ymin:.3f},{outer_ymax:.3f}] z=[{z0:.3f},{z1:.3f}]"
        )
        dx0 = bb_add_pre.xmin - outer_xmin
        dx1 = bb_add_pre.xmax - outer_xmax
        dy0 = bb_add_pre.ymin - outer_ymin
        dy1 = bb_add_pre.ymax - outer_ymax
        dz0 = bb_add_pre.zmin - z0
        dz1t = bb_add_pre.zmax - z1
        print(
            "CHECK: deltas (added - target) "
            f"dxmin={dx0:.3f} dxmax={dx1:.3f} dymin={dy0:.3f} dymax={dy1:.3f} dzmin={dz0:.3f} dzmax={dz1t:.3f}"
        )
    except Exception as e:
        print(f"CHECK: could not compute added_pre bbox (tool cut base) due to: {e}")
        added_pre = None

    # --- Fuse to base ---
    out = base.fuse(flange_solid)

    # --- Added material check AFTER fuse (more reliable) ---
    try:
        added_post = out.cut(base)
        bb_add_post = added_post.BoundingBox()
        vol_base = base.Volume()
        vol_out = out.Volume()
        vol_added = added_post.Volume()
        print(
            "CHECK: added (out minus base) bbox "
            f"x=[{bb_add_post.xmin:.3f},{bb_add_post.xmax:.3f}] y=[{bb_add_post.ymin:.3f},{bb_add_post.ymax:.3f}] z=[{bb_add_post.zmin:.3f},{bb_add_post.zmax:.3f}]"
        )
        print(
            "CHECK: volumes base/out/added(out-base) "
            f"Vbase={vol_base:.4f} Vout={vol_out:.4f} Vadded={vol_added:.4f} (Vout-Vbase)={(vol_out - vol_base):.4f}"
        )

        # If bounds are significantly off, correct by rebuilding tool with exact named numbers (single attempt correction).
        tol = 0.05
        need_fix = (
            abs(bb_add_post.xmin - outer_xmin) > tol
            or abs(bb_add_post.xmax - outer_xmax) > tol
            or abs(bb_add_post.ymin - outer_ymin) > tol
            or abs(bb_add_post.ymax - outer_ymax) > tol
            or abs(bb_add_post.zmin - z0) > tol
            or abs(bb_add_post.zmax - z1) > tol
        )
        print(f"CHECK: flange bounds within tol={tol}mm? {'NO' if need_fix else 'YES'}")

        if need_fix:
            print("INFO: correcting flange by rebuilding with explicit absolute extents")
            # Rebuild tool by constructing a box from absolute mins/maxs (still anchored at z0)
            # Note: Workplane.box is centered; compute center+sizes explicitly.
            cx = 0.5 * (outer_xmin + outer_xmax)
            cy = 0.5 * (outer_ymin + outer_ymax)
            cz = 0.5 * (z0 + z1)
            sx = outer_xmax - outer_xmin
            sy = outer_ymax - outer_ymin
            sz = z1 - z0
            flange_solid2 = cq.Workplane(cq.Plane(origin=(cx, cy, cz), normal=(0, 0, 1))).box(sx, sy, sz, centered=(True, True, True)).val()
            out2 = base.fuse(flange_solid2)
            added_post2 = out2.cut(base)
            bb_add_post2 = added_post2.BoundingBox()
            print(
                "CHECK: corrected added bbox "
                f"x=[{bb_add_post2.xmin:.3f},{bb_add_post2.xmax:.3f}] y=[{bb_add_post2.ymin:.3f},{bb_add_post2.ymax:.3f}] z=[{bb_add_post2.zmin:.3f},{bb_add_post2.zmax:.3f}]"
            )
            out = out2
    except Exception as e:
        print(f"CHECK: could not compute added_post (out cut base) due to: {e}")

    # --- Verify final bbox unchanged in Z overall (must remain -0.75..0.75) ---
    bb_out = out.BoundingBox()
    print(
        "CHECK: final body bbox "
        f"x=[{bb_out.xmin:.3f},{bb_out.xmax:.3f}] y=[{bb_out.ymin:.3f},{bb_out.ymax:.3f}] z=[{bb_out.zmin:.3f},{bb_out.zmax:.3f}]"
    )
    print(
        "CHECK: expected final z-range [-0.750, 0.750] "
        f"dzmin={bb_out.zmin - (-0.75):.3f} dzmax={bb_out.zmax - (0.75):.3f}"
    )

    return out