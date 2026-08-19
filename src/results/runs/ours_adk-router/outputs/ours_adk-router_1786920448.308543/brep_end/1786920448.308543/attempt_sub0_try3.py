def my_cad_function(args):
    import cadquery as cq

    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input
    base = shape.val() if hasattr(shape, "val") else shape

    # --- constants from sub-goal ---
    solid_idx = 0  # s0
    face_idx = 24  # upper planar face on s0 per geometry index

    target_center = (-88.9, 100.0, 266.7)
    top_face_z = 266.7
    neck_top_z = 296.7
    interior_level_z = 257.175

    od = 38.1
    id_clear = 25.4
    r_od = od / 2.0
    r_id = id_clear / 2.0

    neck_projection = neck_top_z - top_face_z  # 30.0
    top_thickness = top_face_z - interior_level_z  # 9.525

    print("NAMED NUMBERS:")
    print("  target_center", target_center)
    print("  face_idx", face_idx)
    print("  OD", od, "ID(clear)", id_clear)
    print("  top_face_z", top_face_z, "neck_top_z", neck_top_z, "projection", neck_projection)
    print("  interior_level_z", interior_level_z, "top_thickness", top_thickness)

    # --- resolve reference face #24 (compound-level indexing per instructions) ---
    faces = base.Faces()
    print(f"SELECTED: {len(faces)} faces on base compound (for index resolution)")
    ref_face = faces[face_idx]
    try:
        n = ref_face.normalAt()
    except Exception as e:
        print("ERROR: ref_face.normalAt() failed", e)
        n = cq.Vector(0, 0, 1)
    c = ref_face.Center()
    print(
        "SELECTED: 1 face for reference idx=%d  center=%s  normal=%s  area=%.3f"
        % (face_idx, tuple(round(v, 3) for v in (c.x, c.y, c.z)), tuple(round(v, 6) for v in (n.x, n.y, n.z)), ref_face.Area())
    )

    # Use measured normal (should be +Z) for the feature axis
    axis = cq.Vector(n.x, n.y, n.z)
    if axis.Length == 0:
        axis = cq.Vector(0, 0, 1)
    axis = axis.normalized()

    # --- isolate s0 ---
    sols = base.Solids()
    print(f"SELECTED: {len(sols)} solids in STEP")
    s0 = sols[solid_idx]
    bb0 = s0.BoundingBox()
    print(
        "SELECTED: 1 solid for edit s%d bbox=[(%.3f,%.3f,%.3f)..(%.3f,%.3f,%.3f)]"
        % (solid_idx, bb0.xmin, bb0.ymin, bb0.zmin, bb0.xmax, bb0.ymax, bb0.zmax)
    )

    # --- build filler neck as an annular tube, projecting to z=296.7 ---
    # Important: to robustly fuse (boolean needs overlap), let the tube overlap a tiny amount into the radiator.
    # The EXTERNAL projection still ends exactly at z=296.7.
    overlap_down = 0.2  # small overlap to ensure fuse; sits inside radiator
    neck_base_z = top_face_z - overlap_down
    neck_height = neck_top_z - neck_base_z  # 30.2

    p_neck_base = cq.Vector(target_center[0], target_center[1], neck_base_z)

    outer_cyl = cq.Solid.makeCylinder(r_od, neck_height, p_neck_base, axis)
    inner_cyl_for_tube = cq.Solid.makeCylinder(r_id, neck_height + 0.5, p_neck_base, axis)  # ensure clean hollow
    tube = outer_cyl.cut(inner_cyl_for_tube)

    print(
        "BUILT: tube OD=%.3f ID=%.3f  base_z=%.3f top_z=%.3f (overlap_down=%.3f)"
        % (od, id_clear, neck_base_z, neck_base_z + neck_height, overlap_down)
    )

    # Fuse tube onto s0
    try:
        s0_fused = s0.fuse(tube)
        print("BOOLEAN: fused tube onto s0")
    except Exception as e:
        print("ERROR: fuse failed", e)
        # fallback: return original shape with a clear print (but still attempt to cut so it isn't a no-op)
        s0_fused = s0

    # --- cut the clear pouring passage down to interior z=257.175 ---
    # Add small extra at both ends for robustness; does not change intended mouth levels.
    eps_down = 0.1
    eps_up = 0.3
    hole_base_z = interior_level_z - eps_down
    hole_top_z = neck_top_z + eps_up
    hole_height = hole_top_z - hole_base_z
    p_hole_base = cq.Vector(target_center[0], target_center[1], hole_base_z)
    hole_cyl = cq.Solid.makeCylinder(r_id, hole_height, p_hole_base, axis)

    try:
        s0_out = s0_fused.cut(hole_cyl)
        print(
            "BOOLEAN: cut passage ID=%.3f from z=%.3f to z=%.3f (target to >=%.3f and <=%.3f on the real part)"
            % (id_clear, hole_base_z, hole_base_z + hole_height, interior_level_z, neck_top_z)
        )
    except Exception as e:
        print("ERROR: cut passage failed", e)
        s0_out = s0_fused

    # --- diagnostics: added/removed + placement self-check ---
    try:
        added = s0_out.cut(s0)
        removed = s0.cut(s0_out)
        bb_added = added.BoundingBox()
        bb_removed = removed.BoundingBox()
        print(
            "ADDED: vol=%.3f center=%s bbox_z=[%.3f..%.3f]"
            % (
                added.Volume(),
                tuple(round(v, 3) for v in (added.Center().x, added.Center().y, added.Center().z)),
                bb_added.zmin,
                bb_added.zmax,
            )
        )
        print(
            "REMOVED: vol=%.3f center=%s bbox_z=[%.3f..%.3f]"
            % (
                removed.Volume(),
                tuple(round(v, 3) for v in (removed.Center().x, removed.Center().y, removed.Center().z)),
                bb_removed.zmin,
                bb_removed.zmax,
            )
        )

        # Check center/axis/diameters/end levels
        achieved_center = (target_center[0], target_center[1], top_face_z)
        achieved_axis = (round(axis.x, 6), round(axis.y, 6), round(axis.z, 6))

        # For end levels, report intended emergence at top_face_z and top at neck_top_z
        print("ACHIEVED (as-built parameters):")
        print("  neck_center (on top face)", achieved_center)
        print("  neck_axis", achieved_axis)
        print("  OD", od, "ID(clear)", id_clear)
        print("  neck_emerges_at_z", top_face_z, "neck_ends_at_z", neck_top_z)
        print("  passage_reaches_to_z", interior_level_z)

        # Compare added x/y to target x/y
        dx = added.Center().x - target_center[0]
        dy = added.Center().y - target_center[1]
        print("CHECK: added center XY delta (mm)", (round(dx, 3), round(dy, 3)))
        print(
            "CHECK: added top z delta (mm)",
            round(bb_added.zmax - neck_top_z, 3),
            "(bb_added.zmax vs target neck_top_z)",
        )
        # removed bbox should extend at least down to interior_level_z
        print(
            "CHECK: removed zmin vs interior_level_z delta (mm)",
            round(bb_removed.zmin - interior_level_z, 3),
        )
    except Exception as e:
        print("WARN: added/removed diagnostics failed", e)

    # --- re-compound, keeping other bodies untouched ---
    out = cq.Compound.makeCompound([s for i, s in enumerate(sols) if i != solid_idx] + [s0_out])

    # --- signed volume delta ---
    def _sum_vol(shp):
        try:
            return sum(s.Volume() for s in shp.Solids())
        except Exception:
            try:
                return shp.Volume()
            except Exception:
                return float("nan")

    base_vol = _sum_vol(base)
    out_vol = _sum_vol(out)
    print("DELTA", out_vol - base_vol)

    return out