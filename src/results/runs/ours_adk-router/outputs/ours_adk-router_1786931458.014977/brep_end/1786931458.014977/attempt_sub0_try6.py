def my_cad_function(args):
    import cadquery as cq
    from math import cos, sin, pi, radians

    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input
    base = shape.val() if hasattr(shape, "val") else shape

    def bb_tuple(shp):
        bb = shp.BoundingBox()
        return ([bb.xmin, bb.ymin, bb.zmin], [bb.xmax, bb.ymax, bb.zmax])

    # -------------------- separate solids: s0 (small) to edit, s1 preserved --------------------
    sols = base.Solids()
    print(f"INPUT: solids={len(sols)}")
    for i, s in enumerate(sols):
        print(f"  solid[{i}]: vol={s.Volume():.3f} bbox={bb_tuple(s)}")

    # Identify s0 by unique measured volume/bbox from prompt
    target_s0_vol = 2162.777
    target_s0_bb_min = (-15.75, -4.625, -15.75)
    target_s0_bb_max = (15.75, 3.175, 15.75)

    def score_s0(s):
        bb = s.BoundingBox()
        dv = abs(s.Volume() - target_s0_vol)
        db = (
            abs(bb.xmin - target_s0_bb_min[0]) + abs(bb.ymin - target_s0_bb_min[1]) + abs(bb.zmin - target_s0_bb_min[2]) +
            abs(bb.xmax - target_s0_bb_max[0]) + abs(bb.ymax - target_s0_bb_max[1]) + abs(bb.zmax - target_s0_bb_max[2])
        )
        return dv * 10.0 + db  # bias to match bbox too

    s0_idx = min(range(len(sols)), key=lambda i: score_s0(sols[i]))
    s0 = sols[s0_idx]
    s1 = sols[1 - s0_idx] if len(sols) == 2 else None

    s0_vol_pre = s0.Volume()
    s0_bb_pre = bb_tuple(s0)
    print(f"SELECTED: 1 solid for edit as s0 idx={s0_idx} vol={s0_vol_pre:.3f} bbox={s0_bb_pre}")

    if s1 is None:
        print("ERROR: expected exactly 2 solids; aborting")
        return shape

    s1_vol_pre = s1.Volume()
    s1_bb_pre = bb_tuple(s1)
    print(f"SELECTED: 1 solid preserved as s1 idx={1 - s0_idx} vol={s1_vol_pre:.3f} bbox={s1_bb_pre}")

    # -------------------- parameters from sub-goal --------------------
    top_y = 3.175
    bot_y = -4.625
    y_mid = 0.5 * (top_y + bot_y)  # -0.725
    print("ANCHORS:")
    print(f"  top_y={top_y}  bot_y={bot_y}  y_mid={y_mid}")

    # -------------------- find the two 192-edge mouth loops on s0 --------------------
    def find_mouth_face(solid, y_target, nsign, expected_inner_edges=192):
        out = []
        for f in solid.Faces():
            if f.geomType() != "PLANE":
                continue
            c = f.Center()
            if abs(c.y - y_target) > 0.05:
                continue
            n = f.normalAt()
            if nsign > 0 and n.y < 0.8:
                continue
            if nsign < 0 and n.y > -0.8:
                continue
            iws = f.innerWires()
            if len(iws) != 1:
                continue
            w = iws[0]
            ne = len(w.Edges())
            if expected_inner_edges is not None and ne != expected_inner_edges:
                continue
            out.append((f, w))
        return out

    top_mouth = find_mouth_face(s0, top_y, +1, 192)
    bot_mouth = find_mouth_face(s0, bot_y, -1, 192)
    print(f"SELECTED: {len(top_mouth)} faces for top flower mouth @Y={top_y} (expect 1)")
    if top_mouth:
        f, w = top_mouth[0]
        print(f"  TOP MOUTH face: center={f.Center().toTuple()} normal={f.normalAt().toTuple()} inner_edges={len(w.Edges())}")
    print(f"SELECTED: {len(bot_mouth)} faces for bottom flower mouth @Y={bot_y} (expect 1)")
    if bot_mouth:
        f, w = bot_mouth[0]
        print(f"  BOT MOUTH face: center={f.Center().toTuple()} normal={f.normalAt().toTuple()} inner_edges={len(w.Edges())}")

    if len(top_mouth) != 1 or len(bot_mouth) != 1:
        print("ERROR: could not uniquely identify both 192-edge mouth faces; aborting unchanged")
        return shape

    top_face, top_wire = top_mouth[0]
    bot_face, bot_wire = bot_mouth[0]

    # -------------------- build exact cavity plug by void-extraction with temporary sealing caps --------------------
    # Create planar faces exactly matching the mouth loops
    cap_top_face = cq.Face.makeFromWires(top_wire)
    cap_bot_face = cq.Face.makeFromWires(bot_wire)

    # Extrude caps OUTWARD to temporarily seal the through opening (do NOT use these in final)
    cap_t = 1.0
    cap_top_solid = cq.Solid.extrudeLinear(cap_top_face, cq.Vector(0, cap_t, 0))
    cap_bot_solid = cq.Solid.extrudeLinear(cap_bot_face, cq.Vector(0, -cap_t, 0))

    print(f"CAP TOP SOLID: vol={cap_top_solid.Volume():.3f} bbox={bb_tuple(cap_top_solid)}")
    print(f"CAP BOT SOLID: vol={cap_bot_solid.Volume():.3f} bbox={bb_tuple(cap_bot_solid)}")

    capped_s0 = s0.fuse(cap_top_solid).fuse(cap_bot_solid)
    print(f"TEMP capped_s0: vol={capped_s0.Volume():.3f} bbox={bb_tuple(capped_s0)}")

    # Bounding box solid around capped_s0
    bb = capped_s0.BoundingBox()
    margin = 5.0
    box_min = cq.Vector(bb.xmin - margin, bb.ymin - margin, bb.zmin - margin)
    box_xlen = bb.xlen + 2 * margin
    box_ylen = bb.ylen + 2 * margin
    box_zlen = bb.zlen + 2 * margin
    box = cq.Solid.makeBox(box_xlen, box_ylen, box_zlen, box_min)
    box_bb = box.BoundingBox()
    print(f"VOID-EXTRACT BOX: min={(box_bb.xmin, box_bb.ymin, box_bb.zmin)} max={(box_bb.xmax, box_bb.ymax, box_bb.zmax)}")

    outside = box.cut(capped_s0)
    void_sols = outside.Solids()
    print(f"SELECTED: {len(void_sols)} solids in (box - capped_s0) for void extraction")

    # Pick internal void solid(s) not touching the box boundary; then choose the one spanning the hole Y range
    tol_touch = 1e-3
    internal = []
    for i, vs in enumerate(void_sols):
        vbb = vs.BoundingBox()
        touch = (
            abs(vbb.xmin - box_bb.xmin) < tol_touch or abs(vbb.xmax - box_bb.xmax) < tol_touch or
            abs(vbb.ymin - box_bb.ymin) < tol_touch or abs(vbb.ymax - box_bb.ymax) < tol_touch or
            abs(vbb.zmin - box_bb.zmin) < tol_touch or abs(vbb.zmax - box_bb.zmax) < tol_touch
        )
        print(f"  VOID_SOLID[{i}]: vol={vs.Volume():.3f} center={vs.Center().toTuple()} bbox={bb_tuple(vs)} touch_box={touch}")
        if not touch:
            internal.append(vs)

    print(f"SELECTED: {len(internal)} internal void solids (not touching box boundary)")
    if len(internal) == 0:
        print("ERROR: no internal void solid found; sealing likely failed -> aborting unchanged")
        return shape

    # Choose best candidate by Y-span and proximity to origin in XZ
    def candidate_score(vs):
        vbb = vs.BoundingBox()
        yspan = vbb.ymax - vbb.ymin
        c = vs.Center()
        # prefer the one matching the expected through-span and centered at X=Z=0
        return abs(yspan - (top_y - bot_y)) * 10.0 + (c.x * c.x + c.z * c.z)

    plug = min(internal, key=candidate_score)
    plug_bb = plug.BoundingBox()
    print(f"SELECTED: 1 plug solid from internal voids: vol={plug.Volume():.3f} center={plug.Center().toTuple()} bbox={bb_tuple(plug)}")
    print(f"PLUG Y span: {plug_bb.ymin:.3f} .. {plug_bb.ymax:.3f} (target {bot_y}..{top_y})")

    # Fuse plug only to original s0 (NOT to s1)
    s0_plugged = s0.fuse(plug)
    print(f"s0 after plug fuse: vol={s0_plugged.Volume():.3f} bbox={bb_tuple(s0_plugged)}")

    # -------------------- subtract the specified hexagonal prism through full Y span --------------------
    R = 10.5
    across_corners = 2 * R
    across_flats = 2 * R * cos(pi / 6.0)
    vertex_angles_deg = [30, 90, 150, 210, 270, 330]

    print("HEX OPENING SPECS:")
    print("  opening center: (X,Z)=(0,0), axis=[0,1,0]")
    print(f"  required Y span: {bot_y} .. {top_y} (len {top_y - bot_y})")
    print(f"  vertex angles (deg from +X): {vertex_angles_deg} (90-deg vertex -> +Z)")
    print(f"  circumradius R={R} mm => across_corners={across_corners:.3f} mm, across_flats={across_flats:.3f} mm")

    # Build explicit 2D hex in the XZ plane at y=y_mid.
    # Plane: normal +Y, xDir +X -> local y axis corresponds to world -Z.
    pts2d = []
    for a in vertex_angles_deg:
        ang = radians(a)
        xw = R * cos(ang)
        zw = R * sin(ang)
        pts2d.append((xw, -zw))
    pts2d.append(pts2d[0])

    prism_h = 40.0  # extend well beyond both end planes
    hex_plane = cq.Plane(origin=(0, y_mid, 0), normal=(0, 1, 0), xDir=(1, 0, 0))
    print(f"HEX SKETCH PLANE: origin={(0, y_mid, 0)} normal={(0, 1, 0)} xDir={(1, 0, 0)}")

    hex_tool = (
        cq.Workplane(hex_plane)
        .polyline(pts2d)
        .close()
        .extrude(prism_h, both=True)
        .val()
    )

    htbb = hex_tool.BoundingBox()
    print(
        "HEX TOOL BBOX: "
        f"xmin={htbb.xmin:.3f} xmax={htbb.xmax:.3f} "
        f"ymin={htbb.ymin:.3f} ymax={htbb.ymax:.3f} "
        f"zmin={htbb.zmin:.3f} zmax={htbb.zmax:.3f}"
    )
    print(
        f"HEX TOOL Y-OVERHANG vs required planes: top_over={htbb.ymax - top_y:.3f} "
        f"bottom_over={bot_y - htbb.ymin:.3f} (both should be >0)"
    )

    s0_hex = s0_plugged.cut(hex_tool)
    print(f"s0 after hex cut: vol={s0_hex.Volume():.3f} bbox={bb_tuple(s0_hex)}")

    # -------------------- verify both mouths show one identical open regular hex (6 edges) --------------------
    def find_hex_mouth_face(solid, y_target, nsign):
        for f in solid.Faces():
            if f.geomType() != "PLANE":
                continue
            c = f.Center()
            if abs(c.y - y_target) > 0.05:
                continue
            n = f.normalAt()
            if nsign > 0 and n.y < 0.8:
                continue
            if nsign < 0 and n.y > -0.8:
                continue
            iws = f.innerWires()
            if len(iws) != 1:
                continue
            w = iws[0]
            if len(w.Edges()) == 6:
                return f, w
        return None, None

    top_after, top_w_after = find_hex_mouth_face(s0_hex, top_y, +1)
    bot_after, bot_w_after = find_hex_mouth_face(s0_hex, bot_y, -1)

    if top_w_after:
        print(
            f"VERIFY TOP MOUTH: inner_edges={len(top_w_after.Edges())} (expect 6) "
            f"face_center={top_after.Center().toTuple()} normal={top_after.normalAt().toTuple()}"
        )
    else:
        print("VERIFY TOP MOUTH: FAILED to find top mouth face with 6-edge inner wire")

    if bot_w_after:
        print(
            f"VERIFY BOTTOM MOUTH: inner_edges={len(bot_w_after.Edges())} (expect 6) "
            f"face_center={bot_after.Center().toTuple()} normal={bot_after.normalAt().toTuple()}"
        )
    else:
        print("VERIFY BOTTOM MOUTH: FAILED to find bottom mouth face with 6-edge inner wire")

    # Additional diagnostic: look for any remaining planar 'floor' faces inside the Y span near the axis
    suspicious = []
    for f in s0_hex.Faces():
        if f.geomType() != "PLANE":
            continue
        n = f.normalAt()
        if abs(abs(n.y) - 1.0) > 1e-3:
            continue
        c = f.Center()
        if c.y <= bot_y + 0.05 or c.y >= top_y - 0.05:
            continue
        if (c.x * c.x + c.z * c.z) > (12.0 * 12.0):
            continue
        iw = f.innerWires()
        suspicious.append((f, c, len(iw), [len(w.Edges()) for w in iw]))
    print(f"DIAG: found {len(suspicious)} internal planar +/-Y faces near axis within hole Y-span (should be 0 for clean through-hex)")
    for i, (f, c, nloops, loopsizes) in enumerate(suspicious[:8]):
        print(f"  INTERNAL_FACE[{i}]: y={c.y:.3f} center={c.toTuple()} loops={nloops} inner_edge_counts={loopsizes}")

    # -------------------- final report: which solid changed + s1 invariance --------------------
    s0_vol_post = s0_hex.Volume()
    s0_bb_post = bb_tuple(s0_hex)
    s1_vol_post = s1.Volume()
    s1_bb_post = bb_tuple(s1)

    print("SOLID CHANGE REPORT:")
    print(f"  s0 (changed) pre:  vol={s0_vol_pre:.3f} bbox={s0_bb_pre}")
    print(f"  s0 (changed) post: vol={s0_vol_post:.3f} bbox={s0_bb_post}")
    print(f"  s1 (unchanged) pre:  vol={s1_vol_pre:.3f} bbox={s1_bb_pre}")
    print(f"  s1 (unchanged) post: vol={s1_vol_post:.3f} bbox={s1_bb_post}")
    print(f"  s1 delta vol={s1_vol_post - s1_vol_pre:.9f} (should be 0)")

    # Reassemble WITHOUT boolean between bodies
    out = cq.Compound.makeCompound([s0_hex, s1])
    print(f"OUTPUT: solids in compound={len(out.Solids())}")
    return out