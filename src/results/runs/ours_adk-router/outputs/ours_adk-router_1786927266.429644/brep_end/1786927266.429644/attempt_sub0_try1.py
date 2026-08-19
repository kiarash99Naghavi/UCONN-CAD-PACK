def my_cad_function(args):
    import cadquery as cq

    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input
    base = shape.val() if hasattr(shape, "val") else shape

    # --- Resolve and verify planar face #32 (per provided geometry index) ---
    faces = base.Faces()
    print(f"INFO: base faces={len(faces)} edges={len(base.Edges())} verts={len(base.Vertices())} solids={len(base.Solids())}")
    f32 = faces[32]
    f32_c = f32.Center()
    f32_n = f32.normalAt()
    try:
        f32_area = f32.Area()
    except Exception:
        f32_area = None
    print(
        "RESOLVED: face #32 center=[{:.3f},{:.3f},{:.3f}] normal=[{:.6f},{:.6f},{:.6f}] area={}".format(
            f32_c.x, f32_c.y, f32_c.z, f32_n.x, f32_n.y, f32_n.z, ("{:.3f}".format(f32_area) if f32_area is not None else "NA")
        )
    )

    # --- Target lug parameters (absolute, per sub-goal) ---
    axis_dir = cq.Vector(0, 0, 1)  # along world Z
    outer_d = 40.0
    inner_d = 20.0
    r_outer = outer_d / 2.0
    r_inner = inner_d / 2.0

    z0 = -395.0
    z1 = -365.0
    thickness = z1 - z0

    # Axis center: x=-20 gives tangency to plane x=0 with r=20; y=260 centered on face #32
    axis_center = cq.Vector(-20.0, 260.0, (z0 + z1) / 2.0)

    # --- Build annular lug: axis along Z, spanning z0..z1 ---
    lug_plane = cq.Plane(origin=(axis_center.x, axis_center.y, z0), normal=(0, 0, 1))
    print(f"INFO: lug sketch plane origin={lug_plane.origin.toTuple()} normal={(lug_plane.zDir.x, lug_plane.zDir.y, lug_plane.zDir.z)}")

    lug_wp = (
        cq.Workplane(lug_plane)
        .circle(r_outer)
        .circle(r_inner)
        .extrude(thickness)
    )
    lug = lug_wp.val()

    lug_bb = lug.BoundingBox()
    lug_center_bb = cq.Vector((lug_bb.xmin + lug_bb.xmax) / 2.0, (lug_bb.ymin + lug_bb.ymax) / 2.0, (lug_bb.zmin + lug_bb.zmax) / 2.0)
    print(
        "BUILT: lug (pre-fuse) bbox xmin/xmax=({:.3f},{:.3f}) ymin/ymax=({:.3f},{:.3f}) zmin/zmax=({:.3f},{:.3f}) center~=[{:.3f},{:.3f},{:.3f}]".format(
            lug_bb.xmin, lug_bb.xmax, lug_bb.ymin, lug_bb.ymax, lug_bb.zmin, lug_bb.zmax,
            lug_center_bb.x, lug_center_bb.y, lug_center_bb.z
        )
    )

    # Expected tangency point (at mid-Z): x=0, y=260
    tangency = cq.Vector(0.0, axis_center.y, axis_center.z)

    # --- Fuse with base ---
    out = base.fuse(lug)
    solids_after = out.Solids()
    print(f"RESULT: solids after tangent fuse = {len(solids_after)}")

    # If tangential-only contact failed to merge into one solid, add a tiny bridge that minimally intersects both.
    # (Bridge is only used as a robustness fallback.)
    used_bridge = False
    if len(solids_after) != 1:
        used_bridge = True
        bridge_r = 1.0  # small
        # Center slightly negative in X so it intersects lug; radius extends into x>0 to intersect body.
        bridge_cx = -0.5
        bridge_plane = cq.Plane(origin=(bridge_cx, axis_center.y, z0), normal=(0, 0, 1))
        bridge = cq.Workplane(bridge_plane).circle(bridge_r).extrude(thickness).val()
        tmp = lug.fuse(bridge)
        out = base.fuse(tmp)
        solids_after = out.Solids()
        print(f"FALLBACK: added bridge r={bridge_r} at x={bridge_cx}; solids after fuse={len(solids_after)}")

    # --- Isolate added material for self-check ---
    try:
        added = out.cut(base)
        added_bb = added.BoundingBox()
        added_center = added.Center()
        print(
            "ADDED: center=[{:.3f},{:.3f},{:.3f}] bbox xmin/xmax=({:.3f},{:.3f}) ymin/ymax=({:.3f},{:.3f}) zmin/zmax=({:.3f},{:.3f})".format(
                added_center.x, added_center.y, added_center.z,
                added_bb.xmin, added_bb.xmax, added_bb.ymin, added_bb.ymax, added_bb.zmin, added_bb.zmax
            )
        )
        # Derived achieved values from added bbox
        ach_center = cq.Vector((added_bb.xmin + added_bb.xmax) / 2.0, (added_bb.ymin + added_bb.ymax) / 2.0, (added_bb.zmin + added_bb.zmax) / 2.0)
        ach_outer_dx = added_bb.xlen
        ach_outer_dy = added_bb.ylen
        print(
            "CHECK: achieved axis_dir={} center~=[{:.3f},{:.3f},{:.3f}] outer_d~(x,y)=({:.3f},{:.3f}) z_endpoints=({:.3f},{:.3f}) x_tangent_target=0.000 achieved_xmax={:.3f}".format(
                (axis_dir.x, axis_dir.y, axis_dir.z),
                ach_center.x, ach_center.y, ach_center.z,
                ach_outer_dx, ach_outer_dy,
                added_bb.zmin, added_bb.zmax,
                added_bb.xmax
            )
        )
    except Exception as e:
        print(f"WARN: could not compute added material via out.cut(base): {e}")

    # --- Required prints (targets vs achieved design intent) ---
    print(
        "TARGETS: axis_dir=(0,0,1) axis_center=[-20,260,-380] outer_d=40 inner_d=20 z_span=[-395,-365] tangency~=[0,260,-380] reach_xmin=-40 no Y/Z envelope change"
    )
    print(
        "ACHIEVED_INTENT: axis_dir=(0,0,1) axis_center=[{:.3f},{:.3f},{:.3f}] outer_d={:.3f} inner_d={:.3f} z_span=[{:.3f},{:.3f}] tangency~=[{:.3f},{:.3f},{:.3f}] used_bridge={} body_count={}".format(
            axis_center.x, axis_center.y, axis_center.z,
            outer_d, inner_d,
            z0, z1,
            tangency.x, tangency.y, tangency.z,
            used_bridge,
            len(out.Solids())
        )
    )

    return out