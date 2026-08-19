def my_cad_function(args):
    import cadquery as cq
    import math

    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input
    base = shape.val() if hasattr(shape, "val") else shape

    def rv(v, nd=3):
        return [round(v.x, nd), round(v.y, nd), round(v.z, nd)]

    # --- Pre-check: resolve referenced faces/edges on the imported compound (global indices) ---
    all_faces = list(base.Faces())
    all_edges = list(base.Edges())

    ref_face_idxs = [8, 10, 21, 23, 9, 11, 22, 28, 25, 26, 27]
    print(f"SELECTED: {len(ref_face_idxs)} faces by explicit face_idx for verification  idx={ref_face_idxs}")
    for i in ref_face_idxs:
        f = all_faces[i]
        c = f.Center()
        try:
            area = f.Area()
        except Exception:
            area = None
        n = f.normalAt()  # no args
        print(f"  face_idx={i} center={rv(c)} area={None if area is None else round(area, 3)} normal={rv(n)}")

    ref_edge_idxs = [33, 34, 35, 59, 60, 61]
    print(f"SELECTED: {len(ref_edge_idxs)} edges by explicit edge_idx for verification  idx={ref_edge_idxs}")
    for i in ref_edge_idxs:
        e = all_edges[i]
        bb = e.BoundingBox()
        print(f"  edge_idx={i} bb_z=[{round(bb.zmin,3)},{round(bb.zmax,3)}] bb_xy=([{round(bb.xmin,3)},{round(bb.ymin,3)}]..[{round(bb.xmax,3)},{round(bb.ymax,3)}])")

    # --- Work on solids: replace only s0 (solids[0]) ---
    sols = list(base.Solids())
    print(f"SELECTED: {len(sols)} solids in STEP")
    if len(sols) < 1:
        print("SELECTED: 0 solids -> no-op")
        return shape

    s0 = sols[0]
    bb0 = s0.BoundingBox()
    print(f"SELECTED: 1 solid for edit: s0  bbox=([{round(bb0.xmin,3)},{round(bb0.ymin,3)},{round(bb0.zmin,3)}]..[{round(bb0.xmax,3)},{round(bb0.ymax,3)},{round(bb0.zmax,3)}])")

    # --- Named numbers (from sub-goal) ---
    z0, z1 = 23.004, 25.004
    thickness = z1 - z0
    width = 9.8
    r_end = 4.9

    p1 = cq.Vector(-37.304, 44.606, 0)
    pm = cq.Vector(2.720, 23.179, 0)
    p2 = cq.Vector(42.743, 1.753, 0)

    axis_vec = p2 - p1
    u = cq.Vector(axis_vec.x, axis_vec.y, 0)
    u_len = u.Length
    if u_len == 0:
        print("ERROR: endpoints coincide")
        return shape
    u = cq.Vector(u.x / u_len, u.y / u_len, 0)
    v = cq.Vector(-u.y, u.x, 0)  # +90deg in XY

    target_angle = -28.16
    angle = math.degrees(math.atan2(u.y, u.x))

    # If direction is flipped by ~180deg, swap endpoints to make it close to target
    def ang_diff(a, b):
        d = (a - b + 180.0) % 360.0 - 180.0
        return abs(d)

    if ang_diff(angle, target_angle) > ang_diff(angle + 180.0 if angle <= 0 else angle - 180.0, target_angle):
        # swap
        p1, p2 = p2, p1
        axis_vec = p2 - p1
        u = cq.Vector(axis_vec.x, axis_vec.y, 0)
        u_len = u.Length
        u = cq.Vector(u.x / u_len, u.y / u_len, 0)
        v = cq.Vector(-u.y, u.x, 0)
        angle = math.degrees(math.atan2(u.y, u.x))

    print("SELF-CHECK: named geometry inputs")
    print(f"  endpoints p1={rv(p1)}  p2={rv(p2)}  mid={rv(pm)}")
    print(f"  z0={z0} z1={z1} thickness={thickness} width={width} r_end={r_end}")
    print(f"  achieved major-axis angle(deg)={round(angle, 4)}  target~{target_angle}")

    # --- Build constant-width obround (capsule) blank at exact Z thickness ---
    # Tangency points
    A1 = p1 + v * r_end
    A2 = p2 + v * r_end
    B2 = p2 - v * r_end
    B1 = p1 - v * r_end

    # Arc midpoints to force semicircles centered on endpoints
    M2 = p2 + u * r_end
    M1 = p1 - u * r_end

    print("SELF-CHECK: achieved endpoint centers for obround ends (should match hole axes)")
    print(f"  end1_center={rv(p1)} end2_center={rv(p2)}")

    wp = cq.Workplane(cq.Plane(origin=(0, 0, z0), normal=(0, 0, 1)))
    print(f"SKETCH PLANE: origin={[0,0,z0]} normal=[0,0,1]")

    blank = (
        wp.moveTo(A1.x, A1.y)
          .lineTo(A2.x, A2.y)
          .threePointArc((M2.x, M2.y), (B2.x, B2.y))
          .lineTo(B1.x, B1.y)
          .threePointArc((M1.x, M1.y), (A1.x, A1.y))
          .close()
          .extrude(thickness)
          .val()
    )

    bb_blank = blank.BoundingBox()
    print("SELF-CHECK: obround blank bbox vs required Z")
    print(f"  blank bbox z=[{round(bb_blank.zmin,3)},{round(bb_blank.zmax,3)}]  deltas=[{round(bb_blank.zmin-z0,6)},{round(bb_blank.zmax-z1,6)}]")

    # Fillet outer perimeter top/bottom edges to match existing small blend (r=0.1)
    def edges_at_z(solid, z, tol=1e-4):
        out = []
        for e in solid.Edges():
            bb = e.BoundingBox()
            if abs(bb.zmin - z) < tol and abs(bb.zmax - z) < tol:
                out.append(e)
        return out

    top_edges = edges_at_z(blank, z1)
    bot_edges = edges_at_z(blank, z0)
    perim_edges = top_edges + bot_edges
    print(f"SELECTED: {len(perim_edges)} edges for outer-perimeter fillet r=0.1 (top+bottom)  (top={len(top_edges)} bottom={len(bot_edges)})")

    blank_f = blank
    if len(perim_edges) > 0:
        try:
            blank_f = blank.fillet(0.1, perim_edges)
            print("FILLET: applied r=0.1 to selected perimeter edges")
        except Exception as ex:
            print(f"FILLET: FAILED on perimeter edges with r=0.1 -> proceeding without fillet. err={ex}")

    # --- Enlarge all three red d=5.0 bores to d=6.0 (i.e., cut r=3.0 cylinders) ---
    hole_centers = [(2.720, 23.179), (-37.304, 44.606), (42.743, 1.753)]
    print(f"SELECTED: {len(hole_centers)} hole centers for d=6.0 cut (concentric) centers={hole_centers}")

    cut_h = thickness + 4.0  # overlap for robustness
    cut_z0 = z0 - 2.0
    hole_tool = (
        cq.Workplane(cq.Plane(origin=(0, 0, cut_z0), normal=(0, 0, 1)))
          .pushPoints(hole_centers)
          .circle(3.0)
          .extrude(cut_h)
          .val()
    )

    arm_new = blank_f.cut(hole_tool)

    # --- Width / angle self-check from vertices projected onto perp axis ---
    verts = list(arm_new.Vertices())
    if len(verts) > 0:
        proj = []
        for vv in verts:
            c = vv.Center()
            proj.append(c.x * v.x + c.y * v.y)
        achieved_width = (max(proj) - min(proj))
        print(f"SELF-CHECK: achieved profile width from vertex projection = {round(achieved_width, 4)} (target {width})  delta={round(achieved_width-width,4)}")
    else:
        print("SELF-CHECK: 0 vertices found on new arm (unexpected)")

    # --- Placement self-check: new material vs old s0 (added/removed) ---
    # (We are replacing the body; still measure difference for sanity)
    try:
        added = arm_new.cut(s0)
        removed = s0.cut(arm_new)
        bb_added = added.BoundingBox()
        bb_removed = removed.BoundingBox()
        print("DIFF-CHECK: new_vs_old s0")
        print(f"  added bbox=([{round(bb_added.xmin,3)},{round(bb_added.ymin,3)},{round(bb_added.zmin,3)}]..[{round(bb_added.xmax,3)},{round(bb_added.ymax,3)},{round(bb_added.zmax,3)}])")
        print(f"  removed bbox=([{round(bb_removed.xmin,3)},{round(bb_removed.ymin,3)},{round(bb_removed.zmin,3)}]..[{round(bb_removed.xmax,3)},{round(bb_removed.ymax,3)},{round(bb_removed.zmax,3)}])")
    except Exception as ex:
        print(f"DIFF-CHECK: failed to compute added/removed solids err={ex}")

    # --- Recompound: keep all other solids untouched, replace only s0 ---
    new_sols = list(sols)
    new_sols[0] = arm_new
    out = cq.Compound.makeCompound(new_sols)

    # Final print of achieved key values
    print("FINAL REPORT:")
    print(f"  endpoint_centers={[(round(p1.x,3),round(p1.y,3)), (round(p2.x,3),round(p2.y,3))]}")
    print(f"  hole_centers={hole_centers}")
    print(f"  width={width}  r_end={r_end}  major_axis_angle_deg={round(angle,4)}")

    return out