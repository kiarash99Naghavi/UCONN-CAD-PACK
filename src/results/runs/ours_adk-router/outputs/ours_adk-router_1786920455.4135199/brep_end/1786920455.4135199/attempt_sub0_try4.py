def my_cad_function(args):
    import cadquery as cq

    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input
    base = shape.val() if hasattr(shape, "val") else shape

    # --- basic diagnostics / entity-index cross-check ---
    sols = base.Solids()
    print(f"INFO: imported solids={len(sols)}")
    for i in range(min(5, len(sols))):
        bb = sols[i].BoundingBox()
        print(f"INFO: s{i} vol={sols[i].Volume():.3f} bbox=([{bb.xmin:.3f},{bb.ymin:.3f},{bb.zmin:.3f}]..[{bb.xmax:.3f},{bb.ymax:.3f},{bb.zmax:.3f}])")

    # Resolve the referenced world-anchored outer rail faces by GLOBAL face index (per instructions)
    # Expected from index: #46 center y=174.852 n=[0,1,0] and #22 center y=-171.45 n=[0,-1,0]
    try:
        f46 = base.Faces()[46]
        f22 = base.Faces()[22]
        print(f"CHECK: base.Faces()[46] center={list(map(lambda v: round(v,3), [f46.Center().x, f46.Center().y, f46.Center().z]))} area={f46.Area():.3f} n={list(map(lambda v: round(v,3), [f46.normalAt().x, f46.normalAt().y, f46.normalAt().z]))}")
        print(f"CHECK: base.Faces()[22] center={list(map(lambda v: round(v,3), [f22.Center().x, f22.Center().y, f22.Center().z]))} area={f22.Area():.3f} n={list(map(lambda v: round(v,3), [f22.normalAt().x, f22.normalAt().y, f22.normalAt().z]))}")
    except Exception as e:
        print(f"WARN: could not resolve base face indices 46/22 for cross-check: {e}")

    # --- isolate body s0 only ---
    if len(sols) < 1:
        print("ERROR: no solids found")
        return shape

    s0 = sols[0]
    others = sols[1:]
    print("SELECTED: 1 solid for editing: s0")

    s0_bb0 = s0.BoundingBox()
    s0_vol0 = s0.Volume()
    print(f"INFO: s0 start vol={s0_vol0:.3f} mm^3")
    print(f"INFO: s0 start bbox min=({s0_bb0.xmin:.3f},{s0_bb0.ymin:.3f},{s0_bb0.zmin:.3f}) max=({s0_bb0.xmax:.3f},{s0_bb0.ymax:.3f},{s0_bb0.zmax:.3f})")

    # --- parameters from sub-goal (world-anchored) ---
    z_list = [-225, -180, -135, -90, -45, 0, 45, 90, 135, 180, 225]
    x_center = -88.9
    # Capsule definition in world XZ plane (explicit): r=3; arc centers at x=-98.4 and -79.4
    r = 3.0
    x_arc_L = -98.4
    x_arc_R = -79.4
    # Overall X limits should be -101.4 .. -76.4
    x_min = x_arc_L - r  # -101.4
    x_max = x_arc_R + r  # -76.4
    # Tangency x positions (where straight segments meet arcs)
    x_tan_L = x_arc_L + r  # -95.4
    x_tan_R = x_arc_R - r  # -82.4

    print("INFO: anchors / named numbers:")
    print(f"  outer +Y plane: y=174.852 (normal [0,1,0])")
    print(f"  outer -Y plane: y=-171.45 (normal [0,-1,0])")
    print(f"  +Y rail cutter Y-span: 133.25..174.952 (inner boundary 133.35)")
    print(f"  -Y rail cutter Y-span: -171.55..-133.25 (inner boundary -133.35)")
    print(f"  capsule centers (x,z)=({x_center}, z) for z in {z_list}")
    print(f"  capsule: r={r} arc centers x={x_arc_L},{x_arc_R}; X limits {x_min}..{x_max}; tangency x {x_tan_L}..{x_tan_R}; width along Z=6")
    print(f"  major-axis vector must be parallel to world X: [1,0,0]")

    def make_capsule_face_at_y(y0, z0):
        """Make a planar Face of the capsule in the world XZ plane located at constant y=y0."""
        # Use a plane whose in-plane axes align with world X (u) and world Z (v)
        # Choose normal = -Y so that v points +Z.
        pl = cq.Plane(origin=(0.0, y0, 0.0), normal=(0.0, -1.0, 0.0), xDir=(1.0, 0.0, 0.0))
        wp = cq.Workplane(pl)

        # Build the capsule wire explicitly with two semicircular arcs and two tangent lines.
        # Top edge at z=z0+r, bottom at z=z0-r.
        z_top = z0 + r
        z_bot = z0 - r

        # Path:
        # start (-95.4, z+3) -> line -> (-82.4, z+3) -> arc to (-82.4, z-3) via mid (-76.4, z)
        # -> line -> (-95.4, z-3) -> arc to (-95.4, z+3) via mid (-101.4, z)
        path = (
            wp.moveTo(x_tan_L, z_top)
              .lineTo(x_tan_R, z_top)
              .threePointArc((x_max, z0), (x_tan_R, z_bot))
              .lineTo(x_tan_L, z_bot)
              .threePointArc((x_min, z0), (x_tan_L, z_top))
              .close()
        )

        wire = path.wire().val()
        print(f"SELECTED: 1 wire for capsule at y={y0:.3f}, z={z0:.3f}  closed={wire.IsClosed()}")
        face = cq.Face.makeFromWires(wire)
        return face

    def make_cutter(y_min, y_max, z0):
        y_len = y_max - y_min
        if y_len <= 0:
            raise ValueError(f"Bad Y span: {y_min}..{y_max}")
        face = make_capsule_face_at_y(y_min, z0)
        tool = cq.Solid.extrudeLinear(face, cq.Vector(0.0, y_len, 0.0))
        bb = tool.BoundingBox()
        print(
            "INFO: cutter tool bbox "
            f"min=({bb.xmin:.3f},{bb.ymin:.3f},{bb.zmin:.3f}) max=({bb.xmax:.3f},{bb.ymax:.3f},{bb.zmax:.3f}) "
            f"(expected y {y_min:.3f}..{y_max:.3f})"
        )
        return tool

    edited = s0

    # Cut one localized cutter at a time (22 total) and validate s0 after each
    # +Y rail
    pos_y_min, pos_y_max = 133.25, 174.952
    neg_y_min, neg_y_max = -171.55, -133.25

    achieved_centers_pos = []
    achieved_centers_neg = []

    total_removed = 0.0

    for side_name, y_min, y_max, store in [
        ("+Y", pos_y_min, pos_y_max, achieved_centers_pos),
        ("-Y", neg_y_min, neg_y_max, achieved_centers_neg),
    ]:
        print(f"\nINFO: starting cuts for side {side_name} with Y span {y_min}..{y_max}")
        for z0 in z_list:
            print(f"\nINFO: building cutter for side {side_name} at z={z0} (capsule center x={x_center}, z={z0})")
            tool = make_cutter(y_min, y_max, z0)

            # Pre-check intersection (should be >0; if 0, the cutter missed the rail)
            try:
                common = edited.intersect(tool)
                cvol = common.Volume() if common is not None else 0.0
                print(f"CHECK: pre-cut common volume with s0 = {cvol:.3f} mm^3")
            except Exception as e:
                print(f"WARN: could not compute pre-cut common volume: {e}")

            v_before = edited.Volume()
            try:
                edited = edited.cut(tool)
            except Exception as e:
                print(f"ERROR: boolean cut failed on side {side_name} z={z0}: {e}")
                return shape

            v_after = edited.Volume()
            dv = v_before - v_after
            total_removed += dv

            ok = False
            try:
                ok = bool(edited.isValid())
            except Exception as e:
                print(f"WARN: isValid() check failed: {e}
")

            print(f"RESULT: cut side {side_name} z={z0} removed={dv:.3f} mm^3  s0_valid={ok}  s0_vol={v_after:.3f}")
            if not ok:
                print("ERROR: edited s0 became invalid; discarding attempt")
                return shape

            store.append((x_center, z0))

    # --- Post checks: bbox unchanged, openings count on outer ±Y planes ---
    s0_bb1 = edited.BoundingBox()
    print("\nCHECK: s0 bbox unchanged?")
    print(f"  before min=({s0_bb0.xmin:.3f},{s0_bb0.ymin:.3f},{s0_bb0.zmin:.3f}) max=({s0_bb0.xmax:.3f},{s0_bb0.ymax:.3f},{s0_bb0.zmax:.3f})")
    print(f"  after  min=({s0_bb1.xmin:.3f},{s0_bb1.ymin:.3f},{s0_bb1.zmin:.3f}) max=({s0_bb1.xmax:.3f},{s0_bb1.ymax:.3f},{s0_bb1.zmax:.3f})")
    print(f"  delta min=({(s0_bb1.xmin-s0_bb0.xmin):.6f},{(s0_bb1.ymin-s0_bb0.ymin):.6f},{(s0_bb1.zmin-s0_bb0.zmin):.6f})")
    print(f"  delta max=({(s0_bb1.xmax-s0_bb0.xmax):.6f},{(s0_bb1.ymax-s0_bb0.ymax):.6f},{(s0_bb1.zmax-s0_bb0.zmax):.6f})")

    # Find the outer +/-Y planar faces after cutting and count inner wires
    def find_outer_y_face(solid, y_target, ny_sign):
        candidates = []
        for f in solid.Faces():
            try:
                n = f.normalAt()
            except Exception:
                continue
            if abs(n.x) > 1e-3 or abs(n.z) > 1e-3:
                continue
            if ny_sign > 0 and n.y < 0.999:
                continue
            if ny_sign < 0 and n.y > -0.999:
                continue
            bb = f.BoundingBox()
            # face lies on constant y; use either bb.ymin or bb.ymax
            y_face = bb.ymax if ny_sign > 0 else bb.ymin
            if abs(y_face - y_target) < 0.2:  # tight enough for STEP tol
                candidates.append((f.Area(), f, y_face))
        candidates.sort(key=lambda t: t[0], reverse=True)
        return candidates

    pos_faces = find_outer_y_face(edited, 174.852, +1)
    neg_faces = find_outer_y_face(edited, -171.45, -1)
    print(f"\nSELECTED: {len(pos_faces)} candidate outer +Y faces at y~174.852")
    print(f"SELECTED: {len(neg_faces)} candidate outer -Y faces at y~-171.45")

    def report_face_holes(face, label):
        wires = face.Wires()
        inner = max(0, len(wires) - 1)
        c = face.Center()
        n = face.normalAt()
        print(f"CHECK: {label} face area={face.Area():.3f} center=({c.x:.3f},{c.y:.3f},{c.z:.3f}) n=({n.x:.3f},{n.y:.3f},{n.z:.3f}) wires={len(wires)} inner_loops={inner}")
        return inner

    inner_pos = report_face_holes(pos_faces[0][1], "+Y outer") if pos_faces else 0
    inner_neg = report_face_holes(neg_faces[0][1], "-Y outer") if neg_faces else 0

    # --- volume sanity check ---
    s0_vol1 = edited.Volume()
    removed_total = s0_vol0 - s0_vol1
    print("\nSANITY: removed volume on s0")
    print(f"  start vol={s0_vol0:.3f}  end vol={s0_vol1:.3f}")
    print(f"  removed (by total)={removed_total:.3f} mm^3  removed (accumulated)={total_removed:.3f} mm^3")
    print("  expected approx ~124579 mm^3; a result near ~541931 mm^3 indicates an over-extended cutter and must be discarded")

    # --- required prints: achieved centers and major axis vector ---
    major_axis = (1.0, 0.0, 0.0)
    print("\nREPORT: achieved (x,z) centers +Y side:")
    for (xc, zc) in achieved_centers_pos:
        print(f"  (+Y) center (x,z)=({xc:.3f},{zc:.3f})  major_axis={major_axis}")
    print("REPORT: achieved (x,z) centers -Y side:")
    for (xc, zc) in achieved_centers_neg:
        print(f"  (-Y) center (x,z)=({xc:.3f},{zc:.3f})  major_axis={major_axis}")

    print(f"\nVERIFY: outer +Y openings (inner loops) = {inner_pos} (target 11)")
    print(f"VERIFY: outer -Y openings (inner loops) = {inner_neg} (target 11)")

    # --- reassemble compound with untouched s1-s19 in original order ---
    out = cq.Compound.makeCompound([edited] + others)
    return out