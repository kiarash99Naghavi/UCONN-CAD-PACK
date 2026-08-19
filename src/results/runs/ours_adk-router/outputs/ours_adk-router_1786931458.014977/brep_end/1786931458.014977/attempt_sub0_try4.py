def my_cad_function(args):
    import cadquery as cq
    from math import sin, cos, pi, radians

    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input

    base = shape.val() if hasattr(shape, "val") else shape

    def bb_tuple(sh):
        bb = sh.BoundingBox()
        return ([bb.xmin, bb.ymin, bb.zmin], [bb.xmax, bb.ymax, bb.zmax])

    def close(a, b, tol=1e-3):
        return abs(a - b) <= tol

    solids = base.Solids()
    print(f"INPUT: solids={len(solids)}")
    for i, s in enumerate(solids):
        print(f"  solid[{i}]: vol={s.Volume():.3f} bbox={bb_tuple(s)}")

    # Identify s0 and s1 by the given unique volume/bbox
    target_s0_vol = 2162.777
    target_s1_vol = 27615.571

    s0_idx = None
    s1_idx = None
    for i, s in enumerate(solids):
        v = s.Volume()
        if abs(v - target_s0_vol) < 0.5:
            s0_idx = i
        if abs(v - target_s1_vol) < 0.5:
            s1_idx = i

    if s0_idx is None or s1_idx is None:
        # fallback: smallest is s0, largest is s1
        vs = [(i, s.Volume()) for i, s in enumerate(solids)]
        vs_sorted = sorted(vs, key=lambda t: t[1])
        s0_idx = vs_sorted[0][0]
        s1_idx = vs_sorted[-1][0]
        print("WARN: volume matching failed; using min/max volume heuristic")

    s0 = solids[s0_idx]
    s1 = solids[s1_idx]
    s0_vol_pre = s0.Volume()
    s1_vol_pre = s1.Volume()
    s0_bb_pre = bb_tuple(s0)
    s1_bb_pre = bb_tuple(s1)

    print(f"SELECTED: 1 solid for edit as s0 idx={s0_idx} vol={s0_vol_pre:.3f} bbox={s0_bb_pre}")
    print(f"SELECTED: 1 solid preserved as s1 idx={s1_idx} vol={s1_vol_pre:.3f} bbox={s1_bb_pre}")

    # --- find the two mouth faces on s0 (Y=3.175 and Y=-4.625), each with 1 inner wire of 192 edges ---
    top_y = 3.175
    bot_y = -4.625

    top_face = None
    bot_face = None
    for f in s0.Faces():
        if f.geomType() != "PLANE":
            continue
        c = f.Center()
        n = f.normalAt()
        if abs(n.y) < 0.9:
            continue
        iw = f.innerWires()
        if len(iw) != 1:
            continue
        n_inner_edges = len(iw[0].Edges())
        if close(c.y, top_y, 1e-3) and n.y > 0.9 and n_inner_edges == 192:
            top_face = f
        if close(c.y, bot_y, 1e-3) and n.y < -0.9 and n_inner_edges == 192:
            bot_face = f

    print(f"SELECTED: {1 if top_face else 0} faces for top mouth @Y={top_y}")
    if top_face:
        iw = top_face.innerWires()[0]
        print(f"  TOP MOUTH face: center={top_face.Center().toTuple()} normal={top_face.normalAt().toTuple()} inner_edges={len(iw.Edges())}")
    print(f"SELECTED: {1 if bot_face else 0} faces for bottom mouth @Y={bot_y}")
    if bot_face:
        iw = bot_face.innerWires()[0]
        print(f"  BOT MOUTH face: center={bot_face.Center().toTuple()} normal={bot_face.normalAt().toTuple()} inner_edges={len(iw.Edges())}")

    if top_face is None or bot_face is None:
        print("ERROR: Could not find both 192-edge mouth faces on s0; aborting without changes")
        return shape

    top_wire = top_face.innerWires()[0]
    bot_wire = bot_face.innerWires()[0]

    # --- Build an exact plug from: (top cap from top_wire) + (bottom cap from bot_wire) + connected cavity-wall faces between them ---
    # We will find the connected set of cavity boundary faces by adjacency starting from edges on the two mouth wires.
    # Use TopExp.MapShapesAndAncestors_s (no TopTools_*Iterator anywhere).
    from OCP.TopExp import TopExp
    from OCP.TopAbs import TopAbs_ShapeEnum
    from OCP.TopTools import TopTools_IndexedDataMapOfShapeListOfShape

    face_by_hash = {}
    for f in s0.Faces():
        face_by_hash[f.wrapped.HashCode(1000003)] = f

    top_hash = top_face.wrapped.HashCode(1000003)
    bot_hash = bot_face.wrapped.HashCode(1000003)

    edge_face_map = TopTools_IndexedDataMapOfShapeListOfShape()
    TopExp.MapShapesAndAncestors_s(
        s0.wrapped,
        TopAbs_ShapeEnum.TopAbs_EDGE,
        TopAbs_ShapeEnum.TopAbs_FACE,
        edge_face_map,
    )

    def adj_face_hashes_for_edge(edge_wrapped):
        if not edge_face_map.Contains(edge_wrapped):
            return []
        lst = edge_face_map.FindFromKey(edge_wrapped)
        # TopTools_ListOfShape is directly iterable in OCP Python
        return [sh.HashCode(1000003) for sh in lst]

    # Seed faces: all faces adjacent to any mouth edge, excluding the two mouth plane faces
    seed = set()
    mouth_edges = list(top_wire.Edges()) + list(bot_wire.Edges())
    print(f"SELECTED: {len(mouth_edges)} edges total from the two 192-edge mouth loops (expect 384)")

    for e in mouth_edges:
        for fh in adj_face_hashes_for_edge(e.wrapped):
            if fh not in (top_hash, bot_hash):
                seed.add(fh)

    print(f"SELECTED: {len(seed)} seed faces adjacent to mouth edges (excluding the two mouth plane faces)")

    # Candidate filter to avoid spilling onto exterior: limit to inner radial region and within the Y span.
    # The old flower is around R~10.5; outer envelope is at ~15.75, so r_limit=14.8 stays safely inside.
    r_limit = 14.8
    y_pad = 1e-2

    def is_candidate_cavity_face(f):
        bb = f.BoundingBox()
        rmax = max(abs(bb.xmin), abs(bb.xmax), abs(bb.zmin), abs(bb.zmax))
        if rmax > r_limit:
            return False
        if bb.ymin < bot_y - y_pad or bb.ymax > top_y + y_pad:
            return False
        return True

    # BFS over face adjacency via shared edges
    cavity_face_hashes = set()
    queue = list(seed)
    while queue:
        fh = queue.pop()
        if fh in cavity_face_hashes:
            continue
        f = face_by_hash.get(fh, None)
        if f is None:
            continue
        if not is_candidate_cavity_face(f):
            continue
        cavity_face_hashes.add(fh)
        for e in f.Edges():
            for nh in adj_face_hashes_for_edge(e.wrapped):
                if nh in (top_hash, bot_hash):
                    continue
                if nh not in cavity_face_hashes:
                    queue.append(nh)

    cavity_faces = [face_by_hash[h] for h in cavity_face_hashes]
    print(f"SELECTED: {len(cavity_faces)} faces for cavity-wall+internal-step boundary (connected, filtered) to build plug")

    # Create cap faces from the mouth wires
    cap_top = cq.Face.makeFromWires(top_wire)
    cap_bot = cq.Face.makeFromWires(bot_wire)

    # Ensure cap normals point outward from the plug: top cap +Y, bottom cap -Y
    nt = cap_top.normalAt()
    nb = cap_bot.normalAt()
    if nt.y < 0:
        cap_top = cap_top.reverse()
    if nb.y > 0:
        cap_bot = cap_bot.reverse()

    print(f"CAP TOP: center={cap_top.Center().toTuple()} normal={cap_top.normalAt().toTuple()} (expect +Y)")
    print(f"CAP BOT: center={cap_bot.Center().toTuple()} normal={cap_bot.normalAt().toTuple()} (expect -Y)")

    # Reverse cavity faces (they are outward from s0 into the cavity; for the plug they must face into s0 material)
    cavity_faces_rev = [f.reverse() for f in cavity_faces]

    # Build shell and solid plug
    plug = None
    try:
        shell0 = cq.Shell.makeShell(cavity_faces_rev + [cap_top, cap_bot])
        plug = cq.Solid.makeSolid(shell0)
    except Exception as ex:
        print(f"ERROR: plug makeSolid failed on first attempt: {ex}")
        # Retry: reverse both caps too (sometimes wire orientation makes caps inconsistent)
        try:
            cap_top2 = cap_top.reverse()
            cap_bot2 = cap_bot.reverse()
            shell1 = cq.Shell.makeShell(cavity_faces_rev + [cap_top2, cap_bot2])
            plug = cq.Solid.makeSolid(shell1)
            print("INFO: plug makeSolid succeeded on second attempt with reversed caps")
        except Exception as ex2:
            print(f"ERROR: plug makeSolid failed on second attempt: {ex2}")
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
    print(f"  prism: centered at y_mid={y_mid:.3f} mm, height={prism_h:.3f} mm, both=True")

    # Build explicit 2D hex in the XZ plane at y=y_mid.
    # Workplane plane: normal +Y, xDir +X -> 2D y axis is -Z, so use y2D=-z_world.
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
        best_edges = None
        for f in solid.Faces():
            if f.geomType() != "PLANE":
                continue
            c = f.Center()
            n = f.normalAt()
            if not close(c.y, y_target, tol=2e-3):
                continue
            if nsign > 0 and n.y < 0.9:
                continue
            if nsign < 0 and n.y > -0.9:
                continue
            iws = f.innerWires()
            if len(iws) != 1:
                continue
            w = iws[0]
            ne = len(w.Edges())
            if best is None or ne < best_edges:
                best = f
                best_edges = ne
        if best is None:
            return None, None
        return best, best.innerWires()[0]

    top_after, top_w_after = find_hex_mouth_face(s0_hex, top_y, +1)
    bot_after, bot_w_after = find_hex_mouth_face(s0_hex, bot_y, -1)

    if top_w_after:
        print(f"VERIFY TOP MOUTH: inner_edges={len(top_w_after.Edges())} (expect 6) face_center={top_after.Center().toTuple()} normal={top_after.normalAt().toTuple()}")
    else:
        print("VERIFY TOP MOUTH: FAILED to find top mouth face")

    if bot_w_after:
        print(f"VERIFY BOTTOM MOUTH: inner_edges={len(bot_w_after.Edges())} (expect 6) face_center={bot_after.Center().toTuple()} normal={bot_after.normalAt().toTuple()}")
    else:
        print("VERIFY BOTTOM MOUTH: FAILED to find bottom mouth face")

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