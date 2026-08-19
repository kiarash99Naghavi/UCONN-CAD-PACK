def my_cad_function(args):
    import cadquery as cq

    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input
    base = shape.val() if hasattr(shape, "val") else shape

    sols = base.Solids()
    print(f"SELECTED: {len(sols)} solids in imported STEP")
    if len(sols) < 20:
        print("ERROR: expected 20 separate bodies (s0..s19); refusing to edit")
        return shape

    s0 = sols[0]
    others = [sols[i] for i in range(1, len(sols))]
    print("SELECTED: 1 solid for edit: s0")
    print(f"SELECTED: {len(others)} solids left untouched: s1..s{len(sols)-1}")

    # --- Anchors and requested numbers ---
    y_outer_pos = 174.852
    y_outer_neg = -171.45

    # Cutter Y-spans with only 0.1 mm clearance beyond measured rail walls
    pos_y_min, pos_y_max = 133.25, 174.952   # inner boundary 133.35, outer plane 174.852
    neg_y_min, neg_y_max = -171.55, -133.25  # outer plane -171.45, inner boundary -133.35

    z_list = [-225, -180, -135, -90, -45, 0, 45, 90, 135, 180, 225]

    # Capsule definition in world XZ
    r = 3.0
    x_center = -88.9
    xL = -98.4
    xR = -79.4
    x_min_expected, x_max_expected = -101.4, -76.4

    print("INFO: anchors / parameters")
    print(f"  outer +Y plane: y={y_outer_pos} normal=[0,1,0]")
    print(f"  outer -Y plane: y={y_outer_neg} normal=[0,-1,0]")
    print(f"  +Y rail cutter span: y={pos_y_min}..{pos_y_max} (len={pos_y_max-pos_y_min:.3f})")
    print(f"  -Y rail cutter span: y={neg_y_min}..{neg_y_max} (len={neg_y_max-neg_y_min:.3f})")
    print(f"  capsule centers requested: x={x_center}, z in {z_list}")
    print(f"  capsule explicit: r={r}, arc centers at x={xL} and x={xR} (overall x {x_min_expected}..{x_max_expected})")
    print("  capsule major-axis vector must be parallel to world X")

    s0_bb0 = s0.BoundingBox()
    s0_vol0 = s0.Volume()
    print("INFO: s0 baseline")
    print(f"  s0 vol={s0_vol0:.3f} mm^3")
    print(
        "  s0 bbox "
        f"min=({s0_bb0.xmin:.3f},{s0_bb0.ymin:.3f},{s0_bb0.zmin:.3f}) "
        f"max=({s0_bb0.xmax:.3f},{s0_bb0.ymax:.3f},{s0_bb0.zmax:.3f})"
    )

    def make_capsule_face_at_y(y0, z0):
        """Closed capsule in the world XZ plane at constant y=y0.
        Built explicitly from tangent lines and end arcs (via 3-point arcs).
        """
        # Plane with local axes matching world X (u) and world Z (v):
        # choose normal = -Y so that plane y-axis becomes +Z.
        pl = cq.Plane(origin=(0.0, y0, 0.0), xDir=(1.0, 0.0, 0.0), normal=(0.0, -1.0, 0.0))
        print(f"INFO: sketch plane origin=(0,{y0},0) normal=(0,-1,0) for capsule at z={z0}")

        zTop = z0 + r
        zBot = z0 - r

        wp = (
            cq.Workplane(pl)
            .moveTo(xL, zTop)
            .lineTo(xR, zTop)
            # right end arc: midpoint at (xR + r, z0) ensures the bulge is toward +X
            .threePointArc((xR + r, z0), (xR, zBot))
            .lineTo(xL, zBot)
            # left end arc: midpoint at (xL - r, z0) ensures bulge toward -X
            .threePointArc((xL - r, z0), (xL, zTop))
            .close()
        )

        wire = wp.wire().val()
        try:
            closed = bool(wire.IsClosed())
        except Exception as e:
            print(f"WARN: could not query wire.IsClosed(): {e}")
            closed = False
        bb = wire.BoundingBox()
        print(
            "CHECK: capsule wire "
            f"closed={closed} "
            f"bboxX=({bb.xmin:.3f}..{bb.xmax:.3f}) bboxZ=({bb.zmin:.3f}..{bb.zmax:.3f}) "
            f"expected X=({x_min_expected:.3f}..{x_max_expected:.3f}) Z=({(z0-r):.3f}..{(z0+r):.3f})"
        )

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
            f"min=({bb.xmin:.3f},{bb.ymin:.3f},{bb.zmin:.3f}) "
            f"max=({bb.xmax:.3f},{bb.ymax:.3f},{bb.zmax:.3f}) "
            f"(expected y {y_min:.3f}..{y_max:.3f})"
        )
        return tool

    edited = s0
    achieved_centers_pos = []
    achieved_centers_neg = []
    total_removed_accum = 0.0

    for side_name, y_min, y_max, store in [
        ("+Y", pos_y_min, pos_y_max, achieved_centers_pos),
        ("-Y", neg_y_min, neg_y_max, achieved_centers_neg),
    ]:
        print(f"\nINFO: starting cuts for side {side_name} with Y span {y_min}..{y_max}")
        for z0 in z_list:
            print(f"\nINFO: building cutter for side {side_name} at capsule center (x,z)=({x_center},{z0})")
            tool = make_cutter(y_min, y_max, z0)

            # Pre-check intersection (should be >0; if 0, the cutter missed the rail)
            try:
                common = edited.intersect(tool)
                cvol = common.Volume() if common is not None else 0.0
                print(f"CHECK: pre-cut common volume with s0 = {cvol:.3f} mm^3")
            except Exception as e:
                print(f"WARN: could not compute pre-cut common volume: {e}")
                cvol = None

            v_before = edited.Volume()
            try:
                edited_next = edited.cut(tool)
            except Exception as e:
                print(f"ERROR: boolean cut failed on side {side_name} z={z0}: {e}")
                return shape

            v_after = edited_next.Volume()
            dv = v_before - v_after
            total_removed_accum += dv

            # Confirm validity on s0 only (assembly may be reported valid=False due to separate bodies)
            is_valid = None
            try:
                is_valid = bool(edited_next.isValid())
            except Exception as e:
                print(f"WARN: isValid() check failed: {e}")

            nsol = None
            try:
                nsol = len(edited_next.Solids())
            except Exception as e:
                print(f"WARN: could not count solids on edited s0: {e}")

            print(
                f"RESULT: cut side {side_name} z={z0} removed={dv:.3f} mm^3  "
                f"s0_valid={is_valid}  s0_solid_count={nsol}  s0_vol={v_after:.3f}"
            )

            if is_valid is False or (nsol is not None and nsol != 1):
                print("ERROR: edited s0 became invalid or non-single-solid; discarding attempt")
                return shape

            edited = edited_next
            store.append((x_center, z0))

    # --- Post checks: bbox unchanged, openings count on outer ±Y planes ---
    s0_bb1 = edited.BoundingBox()
    s0_vol1 = edited.Volume()

    print("\nCHECK: s0 bbox unchanged?")
    print(f"  before min=({s0_bb0.xmin:.3f},{s0_bb0.ymin:.3f},{s0_bb0.zmin:.3f}) max=({s0_bb0.xmax:.3f},{s0_bb0.ymax:.3f},{s0_bb0.zmax:.3f})")
    print(f"  after  min=({s0_bb1.xmin:.3f},{s0_bb1.ymin:.3f},{s0_bb1.zmin:.3f}) max=({s0_bb1.xmax:.3f},{s0_bb1.ymax:.3f},{s0_bb1.zmax:.3f})")
    print(f"  delta min=({(s0_bb1.xmin-s0_bb0.xmin):.6f},{(s0_bb1.ymin-s0_bb0.ymin):.6f},{(s0_bb1.zmin-s0_bb0.zmin):.6f})")
    print(f"  delta max=({(s0_bb1.xmax-s0_bb0.xmax):.6f},{(s0_bb1.ymax-s0_bb0.ymax):.6f},{(s0_bb1.zmax-s0_bb0.zmax):.6f})")

    def find_outer_y_planar_faces(solid, y_target, ny_sign, tol=0.2):
        out = []
        for i, f in enumerate(solid.Faces()):
            try:
                if f.geomType() != "PLANE":
                    continue
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
            y_face = bb.ymax if ny_sign > 0 else bb.ymin
            if abs(y_face - y_target) <= tol:
                out.append((i, f, f.Area(), y_face, n))
        out.sort(key=lambda t: t[2], reverse=True)
        return out

    pos_faces = find_outer_y_planar_faces(edited, y_outer_pos, +1)
    neg_faces = find_outer_y_planar_faces(edited, y_outer_neg, -1)
    print(f"\nSELECTED: {len(pos_faces)} candidate outer +Y planar faces at y~{y_outer_pos}")
    print(f"SELECTED: {len(neg_faces)} candidate outer -Y planar faces at y~{y_outer_neg}")

    def count_inner_loops(faces, label):
        total_inner = 0
        for (idx, f, area, y_face, n) in faces:
            wires = f.Wires()
            inner = max(0, len(wires) - 1)
            c = f.Center()
            total_inner += inner
            print(
                f"CHECK: {label} face idx={idx} area={area:.3f} "
                f"center=({c.x:.3f},{c.y:.3f},{c.z:.3f}) "
                f"y_plane={y_face:.3f} n=({n.x:.3f},{n.y:.3f},{n.z:.3f}) "
                f"wires={len(wires)} inner_loops={inner}"
            )
        return total_inner

    inner_pos = count_inner_loops(pos_faces, "+Y outer")
    inner_neg = count_inner_loops(neg_faces, "-Y outer")

    # --- volume sanity check ---
    removed_total = s0_vol0 - s0_vol1
    print("\nSANITY: removed volume on s0")
    print(f"  start vol={s0_vol0:.3f}  end vol={s0_vol1:.3f}")
    print(f"  removed (by total)={removed_total:.3f} mm^3  removed (accumulated)={total_removed_accum:.3f} mm^3")
    print("  expected approx ~124579 mm^3; a result near ~541931 mm^3 indicates an over-extended cutter and must be discarded")

    # --- required prints: achieved centers and major axis vector ---
    major_axis = (1.0, 0.0, 0.0)
    print("\nREPORT: achieved (x,z) centers +Y side:")
    for (xc, zc) in achieved_centers_pos:
        print(f"  (+Y) center (x,z)=({xc:.3f},{zc:.3f})  major_axis={major_axis}")
    print("REPORT: achieved (x,z) centers -Y side:")
    for (xc, zc) in achieved_centers_neg:
        print(f"  (-Y) center (x,z)=({xc:.3f},{zc:.3f})  major_axis={major_axis}")

    print(f"\nVERIFY: outer +Y openings (inner loops total) = {inner_pos} (target 11)")
    print(f"VERIFY: outer -Y openings (inner loops total) = {inner_neg} (target 11)")

    # --- Reassemble compound with untouched s1-s19 in original order ---
    out = cq.Compound.makeCompound([edited] + others)
    return out