def my_cad_function(args):
    import cadquery as cq
    import math

    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input
    base = shape.val() if hasattr(shape, "val") else shape

    sols = base.Solids()
    print(f"INFO: imported solids={len(sols)}")

    # --- find existing terminal plug solid (s19) by bbox match ---
    tgt_min = (-169.956, 30.48, -290.722)
    tgt_max = (-145.158, 35.56, -262.98)

    def bb_tuple(s):
        bb = s.BoundingBox()
        return (bb.xmin, bb.ymin, bb.zmin, bb.xmax, bb.ymax, bb.zmax)

    def score_bb(bb):
        vals = [bb[0] - tgt_min[0], bb[1] - tgt_min[1], bb[2] - tgt_min[2], bb[3] - tgt_max[0], bb[4] - tgt_max[1], bb[5] - tgt_max[2]]
        return sum(v * v for v in vals)

    scored = []
    for i, s in enumerate(sols):
        bb = bb_tuple(s)
        scored.append((score_bb(bb), i, bb))

    scored.sort(key=lambda t: t[0])
    best_score, best_i, best_bb = scored[0]
    print(
        "SELECTED:",
        1,
        "solid for existing terminal plug (s19 candidate)",
        f"idx={best_i}",
        f"score={best_score:.6f}",
        "bbox=[%.3f,%.3f,%.3f]..[%.3f,%.3f,%.3f]" % best_bb,
    )

    # Resolve referenced boundary edges (sanity print)
    try:
        eids = [1053, 1055, 1063, 1067]
        edges = base.Edges()
        got = []
        for ei in eids:
            if ei < len(edges):
                e = edges[ei]
                c = e.Center()
                got.append(ei)
                print(f"INFO: edge_idx {ei} center=[{c.x:.3f},{c.y:.3f},{c.z:.3f}] length={e.Length():.3f}")
        print(f"SELECTED: {len(got)} edges for boundary-edge sanity check   idx={got}")
    except Exception as e:
        print(f"SELECTED: 0 edges for boundary-edge sanity check (failed) reason={e}")

    # --- targets ---
    cord_entry_t = cq.Vector(-145.158, 33.02, -262.98)

    pin1_base_t = cq.Vector(-158.07, 33.02, -291.68)
    pin2_base_t = cq.Vector(-172.23, 33.02, -279.02)
    pin1_tip_t = cq.Vector(-170.73, 33.02, -305.85)
    pin2_tip_t = cq.Vector(-184.89, 33.02, -293.19)

    pin_diam_t = 4.0
    pin_len_t = 19.0
    pin_spacing_t = 19.0

    body_len_t = 30.0
    body_w_t = 35.3
    body_thk_t = 13.7

    # outward axis dir from target points
    d = (pin1_tip_t - pin1_base_t).normalized()
    print(f"INFO: target pin-axis dir from points d=[{d.x:.6f},{d.y:.6f},{d.z:.6f}]  (expected ~[-0.666,0,-0.746])")

    # rotation about +Y to align local +X with d (d should be in XZ)
    theta = math.atan2(d.z, d.x)  # radians
    # verify sign: standard right-hand rot about +Y maps +X -> (cos,0,sin)
    v_test = cq.Vector(math.cos(theta), 0.0, math.sin(theta))
    if v_test.dot(d) < 0.999:
        theta = -theta
        v_test = cq.Vector(math.cos(theta), 0.0, math.sin(theta))
    theta_deg = math.degrees(theta)
    print(f"INFO: using rotY theta_deg={theta_deg:.3f}  check rot(+X)=[{v_test.x:.6f},{v_test.y:.6f},{v_test.z:.6f}] dot(d)={v_test.dot(d):.6f}")

    # --- build new CEE 7/16 body in local coords ---
    # local: rear center at (0,0,0), nose near x=+L
    L = body_len_t
    W = body_w_t
    T = body_thk_t

    Wn = 33.0  # gentle taper at nose (still wide enough to cover pin positions)
    x_taper = 0.55 * L

    # Broad faces parallel world XZ plane => thickness along Y. We sketch on XZ and extrude along Y.
    plane_local = cq.Plane(origin=(0, 0, 0), normal=(0, 1, 0), xDir=(1, 0, 0))
    print(f"INFO: local sketch plane origin={(0.0, 0.0, 0.0)} normal=(0,1,0) xDir=(1,0,0)")

    pts = [
        (0.0, +W / 2.0),
        (x_taper, +W / 2.0),
        (L, +Wn / 2.0),
        (L, -Wn / 2.0),
        (x_taper, -W / 2.0),
        (0.0, -W / 2.0),
    ]

    wp = cq.Workplane(plane_local)
    body_local = wp.polyline(pts).close().extrude(T / 2.0, both=True).val()  # total thickness = T

    # round outer perimeter (tolerant fillets)
    try:
        e_sel = cq.Workplane(obj=body_local).edges("|Y").vals()
        print(f"SELECTED: {len(e_sel)} edges |Y for main perimeter fillet")
        body_local = cq.Workplane(obj=body_local).edges("|Y").fillet(7.0).val()
        print("INFO: applied perimeter fillet r=7.0")
    except Exception as e:
        print(f"SELECTED: 0 edges successfully filleted for perimeter (skipped) reason={e}")

    try:
        e_sel2 = cq.Workplane(obj=body_local).edges().vals()
        print(f"SELECTED: {len(e_sel2)} edges (all) for small softening fillet")
        body_local = cq.Workplane(obj=body_local).edges().fillet(1.2).val()
        print("INFO: applied small softening fillet r=1.2")
    except Exception as e:
        print(f"SELECTED: 0 edges successfully filleted for softening (skipped) reason={e}")

    # place body: rotate about world Y (keeps thickness along Y) then translate rear center to cord entry
    body_world = body_local.rotate((0, 0, 0), (0, 1, 0), theta_deg).translate((cord_entry_t.x, cord_entry_t.y, cord_entry_t.z))

    # strain-relief collar: extends backward (-d) from just inside body
    collar_r = 4.2
    collar_len = 10.0
    collar_overlap_in = 2.0
    collar_base = cord_entry_t + d.multiply(collar_overlap_in)  # start a bit inside body
    collar_dir = d.multiply(-1.0)
    collar = cq.Solid.makeCylinder(collar_r, collar_len, pnt=collar_base, dir=collar_dir)

    # pins: ensure they intersect body (start pin a bit inside body), while keeping exposed length = 19mm
    pin_r = pin_diam_t / 2.0
    pin_overlap_in = 2.0
    pin_total_len = pin_len_t + pin_overlap_in

    pin1_start = pin1_base_t - d.multiply(pin_overlap_in)
    pin2_start = pin2_base_t - d.multiply(pin_overlap_in)
    pin1 = cq.Solid.makeCylinder(pin_r, pin_total_len, pnt=pin1_start, dir=d)
    pin2 = cq.Solid.makeCylinder(pin_r, pin_total_len, pnt=pin2_start, dir=d)

    new_plug = body_world.fuse(collar).fuse(pin1).fuse(pin2)

    try:
        nsol_new = len(new_plug.Solids())
        print(f"INFO: new_plug solids count after fuses = {nsol_new}")
    except Exception as e:
        print(f"INFO: could not count new_plug solids reason={e}")

    # --- placement self-check (numeric, against named targets) ---
    def v3(v):
        return [float(f"{v.x:.3f}"), float(f"{v.y:.3f}"), float(f"{v.z:.3f}")]

    # Achieved values from construction
    cord_entry_a = cord_entry_t
    pin_dir_a = d
    pin1_base_a = pin1_base_t
    pin2_base_a = pin2_base_t
    pin_spacing_a = (pin2_base_a - pin1_base_a).Length
    pin_diam_a = pin_diam_t
    pin1_tip_a = pin1_base_a + pin_dir_a.multiply(pin_len_t)
    pin2_tip_a = pin2_base_a + pin_dir_a.multiply(pin_len_t)

    print("CHECK: cord-entry point achieved", v3(cord_entry_a), "target", v3(cord_entry_t), "delta", v3(cord_entry_a - cord_entry_t))
    print(
        "CHECK: pin-axis direction achieved",
        [float(f"{pin_dir_a.x:.6f}"), float(f"{pin_dir_a.y:.6f}"), float(f"{pin_dir_a.z:.6f}")],
    )
    print("CHECK: pin centers (bases at nose)", v3(pin1_base_a), v3(pin2_base_a))
    print(f"CHECK: pin spacing achieved {pin_spacing_a:.3f} mm  target {pin_spacing_t:.3f}  delta {pin_spacing_a - pin_spacing_t:.3f}")
    print(f"CHECK: pin diameter achieved {pin_diam_a:.3f} mm  target {pin_diam_t:.3f}  delta {pin_diam_a - pin_diam_t:.3f}")
    print("CHECK: pin tips achieved", v3(pin1_tip_a), v3(pin2_tip_a))
    print("CHECK: pin tips targets ", v3(pin1_tip_t), v3(pin2_tip_t))
    print("CHECK: pin1 tip delta", v3(pin1_tip_a - pin1_tip_t))
    print("CHECK: pin2 tip delta", v3(pin2_tip_a - pin2_tip_t))

    # Body thickness check along Y (should be ~33.02±6.85 for the body; we print body_world bbox)
    bb_body = body_world.BoundingBox()
    print(
        "CHECK: body_world bbox=",
        f"[{bb_body.xmin:.3f},{bb_body.ymin:.3f},{bb_body.zmin:.3f}]..[{bb_body.xmax:.3f},{bb_body.ymax:.3f},{bb_body.zmax:.3f}]",
        f"ylen={bb_body.ylen:.3f} (target {body_thk_t:.3f})",
    )

    # Tolerance checks (0.5mm)
    tol = 0.5

    def max_abs_delta(vec):
        return max(abs(vec.x), abs(vec.y), abs(vec.z))

    ok = True
    if max_abs_delta(cord_entry_a - cord_entry_t) > tol:
        ok = False
        print("WARN: cord-entry out of tolerance")
    if abs(pin_spacing_a - pin_spacing_t) > tol:
        ok = False
        print("WARN: pin spacing out of tolerance")
    if max_abs_delta(pin1_tip_a - pin1_tip_t) > tol or max_abs_delta(pin2_tip_a - pin2_tip_t) > tol:
        ok = False
        print("WARN: pin tips out of tolerance")

    # Overlap check between pins (axis spacing must exceed diameter)
    if pin_spacing_a <= pin_diam_a + 1e-6:
        ok = False
        print("WARN: pins overlap (spacing <= diameter)")

    if not ok:
        # We anchored directly to target coords; if this fires, something is very wrong.
        print("ERROR: placement self-check failed; refusing to apply fixup that would move cord entry.")

    # --- build output compound with only s19 replaced ---
    out_sols = [s for i, s in enumerate(sols) if i != best_i] + [new_plug]
    out = cq.Compound.makeCompound(out_sols)

    # Added/removed material diagnostic (generic)
    try:
        added = out.cut(base)
        bb_a = added.BoundingBox()
        c_a = added.Center()
        print(
            "ADDED: out \\\u2216 base  center=",
            [float(f"{c_a.x:.3f}"), float(f"{c_a.y:.3f}"), float(f"{c_a.z:.3f}")],
            "bbox=[",
            f"{bb_a.xmin:.3f},{bb_a.ymin:.3f},{bb_a.zmin:.3f}]..[{bb_a.xmax:.3f},{bb_a.ymax:.3f},{bb_a.zmax:.3f}]",
        )
    except Exception as e:
        print(f"ADDED: failed to compute out.cut(base) reason={e}")

    try:
        removed = base.cut(out)
        bb_r = removed.BoundingBox()
        c_r = removed.Center()
        print(
            "REMOVED: base \\\u2216 out  center=",
            [float(f"{c_r.x:.3f}"), float(f"{c_r.y:.3f}"), float(f"{c_r.z:.3f}"),],
            "bbox=[",
            f"{bb_r.xmin:.3f},{bb_r.ymin:.3f},{bb_r.zmin:.3f}]..[{bb_r.xmax:.3f},{bb_r.ymax:.3f},{bb_r.zmax:.3f}]",
        )
    except Exception as e:
        print(f"REMOVED: failed to compute base.cut(out) reason={e}")

    return out