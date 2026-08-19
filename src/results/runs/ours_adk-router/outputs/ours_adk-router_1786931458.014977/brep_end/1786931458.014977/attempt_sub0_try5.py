def my_cad_function(args):
    import cadquery as cq
    from math import cos, sin, radians, pi, sqrt

    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input
    base = shape.val() if hasattr(shape, "val") else shape

    def bb_tuple(s):
        bb = s.BoundingBox()
        return ([bb.xmin, bb.ymin, bb.zmin], [bb.xmax, bb.ymax, bb.zmax])

    def close(a, b, tol=1e-3):
        return abs(a - b) <= tol

    def face_same(a, b):
        try:
            return a.isSame(b)
        except Exception:
            return False

    def face_in_list(f, flist):
        for ff in flist:
            if face_same(f, ff):
                return True
        return False

    def edge_same(a, b):
        try:
            return a.isSame(b)
        except Exception:
            return False

    def edge_in_list(e, elist):
        for ee in elist:
            if edge_same(e, ee):
                return True
        return False

    def face_touches_any_edge(face, edges):
        fes = face.Edges()
        for fe in fes:
            # quick reject: center distance in XYZ (cheap) then exact isSame
            fc = fe.Center()
            for me in edges:
                mc = me.Center()
                if abs(fc.x - mc.x) > 1e-2 or abs(fc.y - mc.y) > 1e-2 or abs(fc.z - mc.z) > 1e-2:
                    continue
                if edge_same(fe, me):
                    return True
        # If centers didn't match (tolerance), fall back to exact isSame scan (still manageable)
        for fe in fes:
            for me in edges:
                if edge_same(fe, me):
                    return True
        return False

    def faces_share_any_edge(fa, fb):
        ea = fa.Edges()
        eb = fb.Edges()
        for e1 in ea:
            for e2 in eb:
                if edge_same(e1, e2):
                    return True
        return False

    solids = base.Solids()
    print(f"INPUT: solids={len(solids)}")
    for i, s in enumerate(solids):
        print(f"  solid[{i}]: vol={s.Volume():.3f} bbox={bb_tuple(s)}")

    if len(solids) != 2:
        print("ABORT: expected exactly 2 solids")
        return shape

    # Identify s0 by the unique measured volume/bbox (use volume match primarily)
    target_v0 = 2162.777
    target_v1 = 27615.571

    idx_s0 = None
    idx_s1 = None
    for i, s in enumerate(solids):
        if abs(s.Volume() - target_v0) < 0.5:
            idx_s0 = i
        if abs(s.Volume() - target_v1) < 0.5:
            idx_s1 = i

    if idx_s0 is None or idx_s1 is None or idx_s0 == idx_s1:
        # fallback: pick smallest as s0
        vols = [(i, solids[i].Volume()) for i in range(len(solids))]
        vols.sort(key=lambda t: t[1])
        idx_s0 = vols[0][0]
        idx_s1 = vols[1][0]
        print("WARN: volume matching failed, falling back to smallest-as-s0")

    s0 = solids[idx_s0]
    s1 = solids[idx_s1]

    s0_vol_pre = s0.Volume()
    s1_vol_pre = s1.Volume()
    s0_bb_pre = bb_tuple(s0)
    s1_bb_pre = bb_tuple(s1)

    print(f"SELECTED: 1 solid for edit as s0 idx={idx_s0} vol={s0_vol_pre:.3f} bbox={s0_bb_pre}")
    print(f"SELECTED: 1 solid preserved as s1 idx={idx_s1} vol={s1_vol_pre:.3f} bbox={s1_bb_pre}")

    # --- locate top & bottom mouth faces on s0 (planar at specified Y levels, inner wire edges=192) ---
    top_y = 3.175
    bot_y = -4.625
    mouth_edge_count = 192

    top_face = None
    bot_face = None

    for f in s0.Faces():
        if f.geomType() != "PLANE":
            continue
        c = f.Center()
        n = f.normalAt()
        iws = f.innerWires()
        if len(iws) != 1:
            continue
        ne = len(iws[0].Edges())
        if ne != mouth_edge_count:
            continue
        if close(c.y, top_y, tol=5e-2) and n.y > 0.9:
            top_face = f
        if close(c.y, bot_y, tol=5e-2) and n.y < -0.9:
            bot_face = f

    if top_face is None or bot_face is None:
        # looser match on normals (some imports flip) and y
        for f in s0.Faces():
            if f.geomType() != "PLANE":
                continue
            c = f.Center()
            iws = f.innerWires()
            if len(iws) != 1:
                continue
            ne = len(iws[0].Edges())
            if ne != mouth_edge_count:
                continue
            if top_face is None and close(c.y, top_y, tol=1e-1):
                top_face = f
            if bot_face is None and close(c.y, bot_y, tol=1e-1):
                bot_face = f

    if top_face is None or bot_face is None:
        print("ABORT: failed to find both mouth faces on s0")
        print(f"  found top_face={top_face is not None} bot_face={bot_face is not None}")
        return shape

    top_iw = top_face.innerWires()[0]
    bot_iw = bot_face.innerWires()[0]
    top_edges = top_iw.Edges()
    bot_edges = bot_iw.Edges()

    print(f"SELECTED: 1 faces for top mouth @Y={top_y}")
    print(f"  TOP MOUTH face: center={top_face.Center().toTuple()} normal={top_face.normalAt().toTuple()} inner_edges={len(top_edges)}")
    print(f"SELECTED: 1 faces for bottom mouth @Y={bot_y}")
    print(f"  BOT MOUTH face: center={bot_face.Center().toTuple()} normal={bot_face.normalAt().toTuple()} inner_edges={len(bot_edges)}")

    # --- build planar caps for the mouth loops ---
    cap_top = cq.Face.makeFromWires(top_iw)
    cap_bot = cq.Face.makeFromWires(bot_iw)

    nt = cap_top.normalAt()
    nb = cap_bot.normalAt()
    if nt.y < 0:
        cap_top = cap_top.reverse()
    if nb.y > 0:
        cap_bot = cap_bot.reverse()

    print(f"CAP TOP: center={cap_top.Center().toTuple()} normal={cap_top.normalAt().toTuple()} (expect +Y)")
    print(f"CAP BOT: center={cap_bot.Center().toTuple()} normal={cap_bot.normalAt().toTuple()} (expect -Y)")

    # --- collect the connected cavity-wall faces between these two mouth loops ---
    # Start: faces touching either mouth inner wire edges, restricted to central region
    central_lim = 12.5  # safely beyond flower extents (~10.5R) but below outer radius (15.75)

    mouth_edges_all = list(top_edges) + list(bot_edges)

    initial = []
    for f in s0.Faces():
        if face_same(f, top_face) or face_same(f, bot_face):
            continue
        bb = f.BoundingBox()
        if bb.xmin < -central_lim or bb.xmax > central_lim or bb.zmin < -central_lim or bb.zmax > central_lim:
            continue
        # within Y span neighborhood
        if bb.ymax < bot_y - 0.2 or bb.ymin > top_y + 0.2:
            continue
        if face_touches_any_edge(f, mouth_edges_all):
            initial.append(f)

    print(f"SELECTED: {len(initial)} faces touching mouth loops as initial cavity-wall candidates")
    if len(initial) == 0:
        print("ABORT: no cavity wall candidates found; cannot build plug")
        return shape

    # Expand by connectivity in the central region (BFS without hashing/sets)
    selected = list(initial)
    changed = True
    it = 0
    while changed and it < 10:
        changed = False
        it += 1
        for f in s0.Faces():
            if face_same(f, top_face) or face_same(f, bot_face):
                continue
            if face_in_list(f, selected):
                continue
            bb = f.BoundingBox()
            if bb.xmin < -central_lim or bb.xmax > central_lim or bb.zmin < -central_lim or bb.zmax > central_lim:
                continue
            if bb.ymax < bot_y - 0.2 or bb.ymin > top_y + 0.2:
                continue
            # connect to any already-selected face
            for sf in selected:
                if faces_share_any_edge(f, sf):
                    selected.append(f)
                    changed = True
                    break

    cavity_faces = selected
    print(f"SELECTED: {len(cavity_faces)} faces for cavity-wall set after connectivity expansion (iters={it})")
    # Print a few diagnostics
    for k in range(min(6, len(cavity_faces))):
        fc = cavity_faces[k].Center()
        print(f"  CAVITY_FACE[{k}] type={cavity_faces[k].geomType()} center={fc.toTuple()}")

    # Reverse cavity faces for plug orientation (avoid using set/dict of faces: HashCode issues)
    cavity_faces_rev = [f.reverse() for f in cavity_faces]

    plug = None
    try:
        shell0 = cq.Shell.makeShell(cavity_faces_rev + [cap_top, cap_bot])
        plug = cq.Solid.makeSolid(shell0)
        print("INFO: plug makeSolid succeeded with reversed cavity faces")
    except Exception as ex:
        print(f"ERROR: plug makeSolid failed (reversed cavity faces): {ex}")
        try:
            shell1 = cq.Shell.makeShell(cavity_faces + [cap_top, cap_bot])
            plug = cq.Solid.makeSolid(shell1)
            print("INFO: plug makeSolid succeeded WITHOUT reversing cavity faces")
        except Exception as ex2:
            print(f"ERROR: plug makeSolid failed again: {ex2}")
            print("ABORT: returning original shape unchanged")
            return shape

    plug_bb = plug.BoundingBox()
    print(f"PLUG: vol={plug.Volume():.3f} bbox={bb_tuple(plug)} center={plug.Center().toTuple()}")
    print(f"PLUG Y span: {plug_bb.ymin:.3f} .. {plug_bb.ymax:.3f} (target {bot_y}..{top_y})")

    # Fuse plug into s0 (fills old flower cavity)
    s0_plugged = s0.fuse(plug)
    print(f"s0 after plug fuse: vol={s0_plugged.Volume():.3f} bbox={bb_tuple(s0_plugged)}")

    # --- subtract the specified hexagonal prism through the full Y span ---
    R = 10.5
    across_corners = 2 * R
    across_flats = 2 * R * cos(pi / 6.0)

    y_mid = 0.5 * (top_y + bot_y)  # -0.725
    prism_h = 30.0  # extend beyond both end planes

    vertex_angles_deg = [30, 90, 150, 210, 270, 330]

    print("HEX OPENING SPECS:")
    print("  opening center: (X,Z)=(0,0), axis=[0,1,0]")
    print(f"  required Y span: {bot_y} .. {top_y} (len {top_y - bot_y})")
    print(f"  vertex angles (deg from +X): {vertex_angles_deg} (90-deg vertex -> +Z)")
    print(f"  circumradius R={R} mm => across_corners={across_corners:.3f} mm, across_flats={across_flats:.3f} mm")

    # Build explicit 2D hex in the XZ plane at y=y_mid.
    # Plane: normal +Y, xDir +X -> 2D y axis corresponds to world -Z.
    pts2d = []
    for a in vertex_angles_deg:
        ang = radians(a)
        xw = R * cos(ang)
        zw = R * sin(ang)
        pts2d.append((xw, -zw))
    pts2d.append(pts2d[0])

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
    print(f"HEX TOOL BBOX: xmin={htbb.xmin:.3f} xmax={htbb.xmax:.3f} ymin={htbb.ymin:.3f} ymax={htbb.ymax:.3f} zmin={htbb.zmin:.3f} zmax={htbb.zmax:.3f}")
    print(f"HEX TOOL Y-OVERHANG vs required planes: top_over={htbb.ymax - top_y:.3f} bottom_over={bot_y - htbb.ymin:.3f} (both should be >0)")

    s0_hex = s0_plugged.cut(hex_tool)
    print(f"s0 after hex cut: vol={s0_hex.Volume():.3f} bbox={bb_tuple(s0_hex)}")

    # --- verify the two mouths now show a single open regular hex (6 edges) ---
    def find_hex_mouth_face(solid, y_target, nsign):
        best = None
        for f in solid.Faces():
            if f.geomType() != "PLANE":
                continue
            c = f.Center()
            n = f.normalAt()
            if abs(c.y - y_target) > 5e-2:
                continue
            if nsign > 0 and n.y < 0.8:
                continue
            if nsign < 0 and n.y > -0.8:
                continue
            iws = f.innerWires()
            if len(iws) != 1:
                continue
            w = iws[0]
            ne = len(w.Edges())
            if ne == 6:
                best = f
                break
        if best is None:
            return None, None
        return best, best.innerWires()[0]

    top_after, top_w_after = find_hex_mouth_face(s0_hex, top_y, +1)
    bot_after, bot_w_after = find_hex_mouth_face(s0_hex, bot_y, -1)

    if top_w_after:
        print(f"VERIFY TOP MOUTH: inner_edges={len(top_w_after.Edges())} (expect 6) face_center={top_after.Center().toTuple()} normal={top_after.normalAt().toTuple()}")
    else:
        print("VERIFY TOP MOUTH: FAILED to find top mouth face with 6-edge inner wire")

    if bot_w_after:
        print(f"VERIFY BOTTOM MOUTH: inner_edges={len(bot_w_after.Edges())} (expect 6) face_center={bot_after.Center().toTuple()} normal={bot_after.normalAt().toTuple()}")
    else:
        print("VERIFY BOTTOM MOUTH: FAILED to find bottom mouth face with 6-edge inner wire")

    # --- final report: which solid changed + s1 invariance ---
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
    return out