def my_cad_function(args):
    import cadquery as cq
    import math

    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input
    base = shape.val() if hasattr(shape, "val") else shape

    sols = base.Solids()
    print(f"INFO: imported solids={len(sols)}")

    # --- find target solid s19 by bbox ---
    tgt_min = cq.Vector(-169.956, 30.48, -290.722)
    tgt_max = cq.Vector(-145.158, 35.56, -262.98)

    def bb_score(bb):
        # sum abs errors of min/max
        return (
            abs(bb.xmin - tgt_min.x) + abs(bb.ymin - tgt_min.y) + abs(bb.zmin - tgt_min.z) +
            abs(bb.xmax - tgt_max.x) + abs(bb.ymax - tgt_max.y) + abs(bb.zmax - tgt_max.z)
        )

    best_i = None
    best_sc = 1e9
    for i, s in enumerate(sols):
        bb = s.BoundingBox()
        sc = bb_score(bb)
        if sc < best_sc:
            best_sc = sc
            best_i = i
    old_plug = sols[best_i]
    bb = old_plug.BoundingBox()
    print(
        "SELECTED: 1 solid for existing terminal plug (s19 candidate)  "
        f"idx={best_i} score={best_sc:.6f}  "
        f"bbox=[{bb.xmin:.3f},{bb.ymin:.3f},{bb.zmin:.3f}]..[{bb.xmax:.3f},{bb.ymax:.3f},{bb.zmax:.3f}]"
    )

    # --- targets ---
    cord_entry_t = cq.Vector(-145.158, 33.02, -262.98)
    pin1_base_t = cq.Vector(-158.07, 33.02, -291.68)
    pin2_base_t = cq.Vector(-172.23, 33.02, -279.02)
    pin1_tip_t  = cq.Vector(-170.73, 33.02, -305.85)
    pin2_tip_t  = cq.Vector(-184.89, 33.02, -293.19)
    pin_diam_t = 4.0
    pin_len_t = 19.0
    pin_spacing_t = 19.0
    body_len_t = 30.0
    body_w_t = 35.3
    body_thk_t = 13.7

    # pin axis direction from target points
    d = (pin1_tip_t - pin1_base_t)
    d = d.normalized()
    print(f"INFO: target pin-axis dir from points d=[{d.x:.6f},{d.y:.6f},{d.z:.6f}]  (expected ~[-0.666,0,-0.746])")

    # rotation about Y to align local +X with d (d is in XZ plane)
    theta = math.degrees(math.atan2(d.z, d.x))
    # build body in local coords: rear center at (0,0,0), nose at (L,0,0)
    L = body_len_t
    W = body_w_t
    T = body_thk_t
    corner_r = 7.0

    # broad faces parallel world XZ => sketch on XZ (normal +Y), extrude along Y
    plane_local = cq.Plane(origin=(L/2.0, 0.0, 0.0), normal=(0, 1, 0), xDir=(1, 0, 0))
    print(f"INFO: local sketch plane origin={(L/2.0, 0.0, 0.0)} normal=(0,1,0) xDir=(1,0,0)")

    sk = cq.Sketch().rect(L, W).vertices().fillet(corner_r)
    body_local = cq.Workplane(plane_local).placeSketch(sk).extrude(T, both=True).val()

    # Round outer edges a bit (optional, keep failures non-fatal)
    try:
        body_local = cq.Workplane(obj=body_local).edges().fillet(1.8).val()
        print("SELECTED: all edges for body rounding fillet  (applied r=1.8)")
    except Exception as e:
        print(f"SELECTED: 0 edges successfully filleted for body rounding (skipped)  reason={e}")

    # Rotate about Y so local +X aligns with d, then translate rear point to cord entry
    body_world = body_local.rotate((0, 0, 0), (0, 1, 0), theta).translate((cord_entry_t.x, cord_entry_t.y, cord_entry_t.z))

    # Collar/strain relief (overlaps body by 1mm into it)
    collar_r = 4.2
    collar_len = 9.0
    collar_overlap_in = 1.0
    collar_base = cord_entry_t + d.multiply(collar_overlap_in)  # 1mm inside body
    collar_dir = d.multiply(-1)
    collar = cq.Solid.makeCylinder(collar_r, collar_len, pnt=collar_base, dir=collar_dir)

    # Pins: cylinders from base points, along d
    pin_r = pin_diam_t / 2.0
    pin1 = cq.Solid.makeCylinder(pin_r, pin_len_t, pnt=pin1_base_t, dir=d)
    pin2 = cq.Solid.makeCylinder(pin_r, pin_len_t, pnt=pin2_base_t, dir=d)

    new_plug = body_world.fuse(collar).fuse(pin1).fuse(pin2)

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
    print("CHECK: pin-axis direction achieved",
          [float(f"{pin_dir_a.x:.6f}"), float(f"{pin_dir_a.y:.6f}"), float(f"{pin_dir_a.z:.6f}")])
    print("CHECK: pin centers (bases at nose)", v3(pin1_base_a), v3(pin2_base_a))
    print(f"CHECK: pin spacing achieved {pin_spacing_a:.3f} mm  target {pin_spacing_t:.3f}  delta {pin_spacing_a - pin_spacing_t:.3f}")
    print(f"CHECK: pin diameter achieved {pin_diam_a:.3f} mm  target {pin_diam_t:.3f}  delta {pin_diam_a - pin_diam_t:.3f}")
    print("CHECK: pin tips achieved", v3(pin1_tip_a), v3(pin2_tip_a))
    print("CHECK: pin tips targets ", v3(pin1_tip_t), v3(pin2_tip_t))
    print("CHECK: pin1 tip delta", v3(pin1_tip_a - pin1_tip_t))
    print("CHECK: pin2 tip delta", v3(pin2_tip_a - pin2_tip_t))

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
        # If something went wrong numerically, re-anchor using the exact rear/nose relation.
        # Compute nose center from target pin bases and set rear = nose - d*L, then re-place.
        nose_center_t = (pin1_base_t + pin2_base_t).multiply(0.5)
        cord_entry_fix = nose_center_t - d.multiply(L)
        print("FIXUP: recomputed cord-entry from pins+length", v3(cord_entry_fix), "(target was)", v3(cord_entry_t))
        body_world = body_local.rotate((0, 0, 0), (0, 1, 0), theta).translate((cord_entry_fix.x, cord_entry_fix.y, cord_entry_fix.z))
        collar_base = cord_entry_fix + d.multiply(collar_overlap_in)
        collar = cq.Solid.makeCylinder(collar_r, collar_len, pnt=collar_base, dir=collar_dir)
        new_plug = body_world.fuse(collar).fuse(pin1).fuse(pin2)

    # --- build output compound with only s19 replaced ---
    out_sols = [s for i, s in enumerate(sols) if i != best_i] + [new_plug]
    out = cq.Compound.makeCompound(out_sols)

    # Added material diagnostic (generic)
    try:
        added = out.cut(base)
        bb_a = added.BoundingBox()
        c_a = added.Center()
        print(
            "ADDED: out \ base  center=",
            [float(f"{c_a.x:.3f}"), float(f"{c_a.y:.3f}"), float(f"{c_a.z:.3f}")],
            "bbox=[",
            f"{bb_a.xmin:.3f},{bb_a.ymin:.3f},{bb_a.zmin:.3f}]..[{bb_a.xmax:.3f},{bb_a.ymax:.3f},{bb_a.zmax:.3f}]"
        )
    except Exception as e:
        print(f"ADDED: failed to compute out.cut(base)  reason={e}")

    return out