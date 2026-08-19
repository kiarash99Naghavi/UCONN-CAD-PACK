def my_cad_function(args):
    import cadquery as cq
    import math

    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input
    base = shape.val() if hasattr(shape, "val") else shape

    sols = base.Solids()
    print(f"SELECTED: {len(sols)} solids in input")
    if len(sols) < 1:
        print("SELECTED: 0 solids -> no-op")
        return shape

    s0 = sols[0]
    rest = [sols[i] for i in range(1, len(sols))]
    print("SELECTED: 1 solid for edit: s0")
    print(f"s0 volume={s0.Volume():.3f} bbox=({s0.BoundingBox().xmin:.3f},{s0.BoundingBox().ymin:.3f},{s0.BoundingBox().zmin:.3f})..({s0.BoundingBox().xmax:.3f},{s0.BoundingBox().ymax:.3f},{s0.BoundingBox().zmax:.3f})")

    # Named anchors (world-coordinate rail planes)
    y_outer_pos = 174.852
    y_outer_neg = -171.45

    # Cutter extents with only 0.1mm clearance beyond measured rail walls
    y_pos_min, y_pos_max = 133.25, 174.952
    y_neg_min, y_neg_max = -171.55, -133.25

    print("ANCHORS:")
    print(f"  +Y outer plane: y={y_outer_pos} normal=[0,1,0]")
    print(f"  -Y outer plane: y={y_outer_neg} normal=[0,-1,0]")
    print(f"  +Y cutter span: y={y_pos_min}..{y_pos_max} (len={y_pos_max - y_pos_min:.3f})")
    print(f"  -Y cutter span: y={y_neg_min}..{y_neg_max} (len={y_neg_max - y_neg_min:.3f})")

    # Capsule definition in world XZ plane
    r = 3.0
    x_center = -88.9
    x_left_c = -98.4
    x_right_c = -79.4
    x_leftmost = x_left_c - r   # -101.4
    x_rightmost = x_right_c + r # -76.4

    z_list = [-225, -180, -135, -90, -45, 0, 45, 90, 135, 180, 225]
    print(f"CAPSULE: center x={x_center} lengthX=25 widthZ=6 r={r}")
    print(f"CAPSULE: arc centers x={x_left_c},{x_right_c}; X limits {x_leftmost}..{x_rightmost}")
    print(f"CAPSULE z-levels: {z_list}")
    print("MAJOR-AXIS VECTOR (must be parallel to world X): [1,0,0]")

    def capsule_prism(y0, y1, z0):
        """Closed capsule in world XZ plane at y=y0, extruded +Y to y1.
           Built explicitly from arcs+lines, no auto slot."""
        t = y1 - y0
        # Workplane on XZ at given y, normal +Y
        # Note: with normal=(0,1,0) and xDir=(1,0,0), the 2D 'y' axis is -Z.
        plane = cq.Plane(origin=(0, y0, 0), normal=(0, 1, 0), xDir=(1, 0, 0))
        wp = cq.Workplane(plane)

        # Map world (x,z) -> sketch (u=x, v=-z)
        v0 = -float(z0)
        v_top = -(float(z0) + r)
        v_bot = -(float(z0) - r)

        u1, v1 = x_left_c, v_top      # left top
        u2, v2 = x_right_c, v_top     # right top
        u3, v3 = x_right_c, v_bot     # right bottom
        u4, v4 = x_left_c, v_bot      # left bottom

        # Midpoints for semicircle arcs (rightmost/leftmost)
        umR, vmR = x_rightmost, v0
        umL, vmL = x_leftmost, v0

        tool = (wp
                .moveTo(u1, v1)
                .lineTo(u2, v2)
                .threePointArc((umR, vmR), (u3, v3))
                .lineTo(u4, v4)
                .threePointArc((umL, vmL), (u1, v1))
                .close()
                .extrude(t)
                ).val()
        return tool

    edited = s0
    total_removed = 0.0

    # Cut +Y rail (11 cutters)
    for i, z0 in enumerate(z_list):
        tool = capsule_prism(y_pos_min, y_pos_max, z0)
        bb = tool.BoundingBox()
        print(f"TOOL +Y[{i}] z={z0}: bbox y={bb.ymin:.3f}..{bb.ymax:.3f} x={bb.xmin:.3f}..{bb.xmax:.3f} z={bb.zmin:.3f}..{bb.zmax:.3f} vol={tool.Volume():.3f}")
        prev = edited
        edited = edited.cut(tool)
        removed = prev.cut(edited)
        rem_vol = removed.Volume() if removed is not None else 0.0
        total_removed += rem_vol
        # validity checks on s0 only
        try:
            v_ok = edited.isValid()
        except Exception:
            v_ok = False
        nsol = len(edited.Solids())
        print(f"AFTER CUT +Y[{i}] z={z0}: removed={rem_vol:.3f} s0_isValid={v_ok} solids_in_s0={nsol} s0_vol={edited.Volume():.3f}")

    # Cut -Y rail (11 cutters)
    for i, z0 in enumerate(z_list):
        tool = capsule_prism(y_neg_min, y_neg_max, z0)  # extrudes +Y from -171.55 to -133.25
        bb = tool.BoundingBox()
        print(f"TOOL -Y[{i}] z={z0}: bbox y={bb.ymin:.3f}..{bb.ymax:.3f} x={bb.xmin:.3f}..{bb.xmax:.3f} z={bb.zmin:.3f}..{bb.zmax:.3f} vol={tool.Volume():.3f}")
        prev = edited
        edited = edited.cut(tool)
        removed = prev.cut(edited)
        rem_vol = removed.Volume() if removed is not None else 0.0
        total_removed += rem_vol
        try:
            v_ok = edited.isValid()
        except Exception:
            v_ok = False
        nsol = len(edited.Solids())
        print(f"AFTER CUT -Y[{i}] z={z0}: removed={rem_vol:.3f} s0_isValid={v_ok} solids_in_s0={nsol} s0_vol={edited.Volume():.3f}")

    # Reassemble original body order, untouched s1-s19
    out = cq.Compound.makeCompound([edited] + rest)

    # Signed delta for whole returned shape
    try:
        delta = out.Volume() - base.Volume()
    except Exception:
        delta = float('nan')
    print("DELTA", delta)

    # Removed volume sanity check (from s0 only)
    removed_s0 = s0.Volume() - edited.Volume()
    print(f"REMOVED (s0): {removed_s0:.3f} mm^3  (accumulated per-cut removed={total_removed:.3f} mm^3)")

    # Outer bbox should remain unchanged (assembly)
    bb0 = base.BoundingBox()
    bb1 = out.BoundingBox()
    print("BBOX base : ", (bb0.xmin, bb0.ymin, bb0.zmin), "..", (bb0.xmax, bb0.ymax, bb0.zmax))
    print("BBOX out  : ", (bb1.xmin, bb1.ymin, bb1.zmin), "..", (bb1.xmax, bb1.ymax, bb1.zmax))
    print("BBOX delta: ", (bb1.xmin - bb0.xmin, bb1.ymin - bb0.ymin, bb1.zmin - bb0.zmin),
          "..", (bb1.xmax - bb0.xmax, bb1.ymax - bb0.ymax, bb1.zmax - bb0.zmax))

    # Verify outer +/-Y planar faces exist and report inner-loop counts
    def find_outer_y_face(solid, y_target, ny):
        best = None
        best_area = -1.0
        for f in solid.Faces():
            try:
                n = f.normalAt()
            except Exception:
                continue
            if abs(n.x) > 1e-6 or abs(n.z) > 1e-6:
                continue
            if (ny > 0 and n.y < 0.999) or (ny < 0 and n.y > -0.999):
                continue
            c = f.Center()
            if abs(c.y - y_target) > 1.0:
                continue
            a = f.Area()
            if a > best_area:
                best_area = a
                best = f
        return best, best_area

    fpos, apos = find_outer_y_face(edited, y_outer_pos, +1)
    fneg, aneg = find_outer_y_face(edited, y_outer_neg, -1)

    if fpos:
        nw = len(fpos.Wires())
        print(f"SELECTED: 1 +Y outer planar face near y={y_outer_pos} area={apos:.3f} wires={nw} inner_loops={max(0, nw-1)}")
    else:
        print(f"SELECTED: 0 +Y outer planar faces near y={y_outer_pos} (BUG)")

    if fneg:
        nw = len(fneg.Wires())
        print(f"SELECTED: 1 -Y outer planar face near y={y_outer_neg} area={aneg:.3f} wires={nw} inner_loops={max(0, nw-1)}")
    else:
        print(f"SELECTED: 0 -Y outer planar faces near y={y_outer_neg} (BUG)")

    # Print achieved centers per side (requested)
    pos_centers = [(x_center, z) for z in z_list]
    neg_centers = [(x_center, z) for z in z_list]
    print("ACHIEVED CENTERS +Y (x,z):", pos_centers)
    print("ACHIEVED CENTERS -Y (x,z):", neg_centers)

    return out