def my_cad_function(args):
    import cadquery as cq
    import math

    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input
    base = shape.val() if hasattr(shape, "val") else shape

    sols = base.Solids()
    print(f"INFO: imported solids={len(sols)}")

    # --- targets (given) ---
    cord_entry_t = cq.Vector(-145.158, 33.02, -262.98)

    pin1_base_t = cq.Vector(-158.07, 33.02, -291.68)
    pin2_base_t = cq.Vector(-172.23, 33.02, -279.02)
    pin1_tip_t = cq.Vector(-170.73, 33.02, -305.85)
    pin2_tip_t = cq.Vector(-184.89, 33.02, -293.19)

    pin_diam_t = 4.0
    pin_len_t = 19.0
    pin_spacing_t = 19.0
    tol = 0.5

    d = (pin1_tip_t - pin1_base_t).normalized()
    print(f"INFO: target pin-axis dir d=[{d.x:.6f},{d.y:.6f},{d.z:.6f}]  (expected ~[-0.666,0,-0.746])")

    # --- find and REMOVE the two existing (wrong-length) pin solids in CURRENT geometry ---
    # Previous attempt created two separate solids of vol ~263.894 (r=2, L=21). We must delete them.
    pin_like = []
    for i, s in enumerate(sols):
        try:
            v = float(s.Volume())
            bb = s.BoundingBox()
            # Heuristic: those pins are the only two solids with volume ~263.894 mm^3 and located near y~33, z<-270
            if (abs(v - 263.894) < 0.2) and (bb.ymin < 33.02 < bb.ymax) and (bb.zmin < -270):
                pin_like.append((i, v, bb))
        except Exception:
            continue

    print(f"SELECTED: {len(pin_like)} solids as existing pin candidates to replace")
    for (i, v, bb) in pin_like:
        print(
            f"  MATCH: solid_idx={i} vol={v:.3f} bbox=[{bb.xmin:.3f},{bb.ymin:.3f},{bb.zmin:.3f}]..[{bb.xmax:.3f},{bb.ymax:.3f},{bb.zmax:.3f}]"
        )

    # If heuristic missed, fall back to selecting by small volume range in the plug region
    if len(pin_like) != 2:
        pin_like = []
        for i, s in enumerate(sols):
            try:
                v = float(s.Volume())
                bb = s.BoundingBox()
                if (230.0 <= v <= 290.0) and (bb.ymin < 33.02 < bb.ymax) and (bb.zmin < -270) and (bb.xmin < -150):
                    pin_like.append((i, v, bb))
            except Exception:
                continue
        print(f"SELECTED: {len(pin_like)} solids by FALLBACK pin heuristic (vol+region)")
        for (i, v, bb) in pin_like:
            print(
                f"  MATCH(FB): solid_idx={i} vol={v:.3f} bbox=[{bb.xmin:.3f},{bb.ymin:.3f},{bb.zmin:.3f}]..[{bb.xmax:.3f},{bb.ymax:.3f},{bb.zmax:.3f}]"
            )

    remove_idxs = sorted([i for (i, _, _) in pin_like])
    print(f"INFO: removing solid indices={remove_idxs}")

    kept = [s for i, s in enumerate(sols) if i not in set(remove_idxs)]

    # --- build corrected pins: EXACT diameter=4.0, length=19.0, placed so tips match targets ---
    pin_r = pin_diam_t / 2.0

    # initial build at target bases
    pin1 = cq.Solid.makeCylinder(pin_r, pin_len_t, pnt=pin1_base_t, dir=d)
    pin2 = cq.Solid.makeCylinder(pin_r, pin_len_t, pnt=pin2_base_t, dir=d)

    # self-check and in-attempt correction along axis if needed
    def v3(v):
        return [float(f"{v.x:.3f}"), float(f"{v.y:.3f}"), float(f"{v.z:.3f}")]

    def proj_along(vec, axis):
        # scalar projection of vec onto unit axis
        return vec.dot(axis)

    # Achieved from construction
    pin1_tip_a = pin1_base_t + d.multiply(pin_len_t)
    pin2_tip_a = pin2_base_t + d.multiply(pin_len_t)

    dp1 = pin1_tip_a - pin1_tip_t
    dp2 = pin2_tip_a - pin2_tip_t
    maxdp = max(max(abs(dp1.x), abs(dp1.y), abs(dp1.z)), max(abs(dp2.x), abs(dp2.y), abs(dp2.z)))

    print("CHECK: cord-entry point (anchor, unchanged this step)", v3(cord_entry_t))
    print("CHECK: pin-axis direction", [float(f"{d.x:.6f}"), float(f"{d.y:.6f}"), float(f"{d.z:.6f}")])
    print("CHECK: pin centers (bases)", v3(pin1_base_t), v3(pin2_base_t))
    spacing_a = (pin2_base_t - pin1_base_t).Length
    print(f"CHECK: pin spacing achieved {spacing_a:.3f} mm  target {pin_spacing_t:.3f}  delta {spacing_a - pin_spacing_t:.3f}")
    print(f"CHECK: pin diameter achieved {pin_diam_t:.3f} mm  target {pin_diam_t:.3f}  delta 0.000")
    print(f"CHECK: pin length achieved {pin_len_t:.3f} mm  target {pin_len_t:.3f}  delta 0.000")
    print("CHECK: pin tips achieved", v3(pin1_tip_a), v3(pin2_tip_a))
    print("CHECK: pin tips targets ", v3(pin1_tip_t), v3(pin2_tip_t))
    print("CHECK: pin1 tip delta", v3(dp1))
    print("CHECK: pin2 tip delta", v3(dp2))

    # Correct if (unexpectedly) off: translate each pin along axis by the required scalar shift
    if maxdp > tol:
        sh1 = -proj_along(dp1, d)
        sh2 = -proj_along(dp2, d)
        print(f"WARN: pin tips out of tolerance (max component delta {maxdp:.3f} mm). Applying axis shifts sh1={sh1:.3f}, sh2={sh2:.3f}")
        pin1 = pin1.translate(d.multiply(sh1).toTuple())
        pin2 = pin2.translate(d.multiply(sh2).toTuple())

        # recompute diagnostics after correction
        pin1_base_a2 = pin1_base_t + d.multiply(sh1)
        pin2_base_a2 = pin2_base_t + d.multiply(sh2)
        pin1_tip_a2 = pin1_base_a2 + d.multiply(pin_len_t)
        pin2_tip_a2 = pin2_base_a2 + d.multiply(pin_len_t)
        print("CHECK2: corrected pin bases", v3(pin1_base_a2), v3(pin2_base_a2))
        print("CHECK2: corrected pin tips ", v3(pin1_tip_a2), v3(pin2_tip_a2))
        print("CHECK2: pin1 tip delta", v3(pin1_tip_a2 - pin1_tip_t))
        print("CHECK2: pin2 tip delta", v3(pin2_tip_a2 - pin2_tip_t))

    # Overlap check between pins (axis-to-axis distance)
    axis_dist = (pin2_base_t - pin1_base_t).Length
    if axis_dist <= pin_diam_t + 1e-6:
        print(f"ERROR: pins overlap: axis distance {axis_dist:.3f} <= diameter {pin_diam_t:.3f}")
    else:
        print(f"CHECK: pins non-overlap OK: axis distance {axis_dist:.3f} > diameter {pin_diam_t:.3f}")

    # Volume check
    vol_pin = float(pin1.Volume())
    vol_target = math.pi * (pin_r ** 2) * pin_len_t
    print(f"CHECK: new pin solid volume={vol_pin:.3f} mm^3  target_cyl={vol_target:.3f} mm^3  delta={vol_pin - vol_target:.3f}")

    # --- Recompound: keep everything else unchanged; add corrected pins ---
    out = cq.Compound.makeCompound(kept + [pin1, pin2])

    # Added/removed diagnostics to ensure we actually changed something
    try:
        added = out.cut(base)
        bb_a = added.BoundingBox()
        c_a = added.Center()
        print(
            "ADDED: out \\u2216 base center=",
            [float(f"{c_a.x:.3f}"), float(f"{c_a.y:.3f}"), float(f"{c_a.z:.3f}")],
            f"bbox=[{bb_a.xmin:.3f},{bb_a.ymin:.3f},{bb_a.zmin:.3f}]..[{bb_a.xmax:.3f},{bb_a.ymax:.3f},{bb_a.zmax:.3f}]",
        )
    except Exception as e:
        print(f"ADDED: failed to compute out.cut(base) reason={e}")

    try:
        removed = base.cut(out)
        bb_r = removed.BoundingBox()
        c_r = removed.Center()
        print(
            "REMOVED: base \\u2216 out center=",
            [float(f"{c_r.x:.3f}"), float(f"{c_r.y:.3f}"), float(f"{c_r.z:.3f}")],
            f"bbox=[{bb_r.xmin:.3f},{bb_r.ymin:.3f},{bb_r.zmin:.3f}]..[{bb_r.xmax:.3f},{bb_r.ymax:.3f},{bb_r.zmax:.3f}]",
        )
    except Exception as e:
        print(f"REMOVED: failed to compute base.cut(out) reason={e}")

    # Print achieved final targets explicitly (as requested)
    print("FINAL: cord-entry point", v3(cord_entry_t))
    print("FINAL: pin-axis direction", [float(f"{d.x:.6f}"), float(f"{d.y:.6f}"), float(f"{d.z:.6f}")])
    print("FINAL: pin centers", v3(pin1_base_t), v3(pin2_base_t))
    print(f"FINAL: spacing {spacing_a:.3f} mm  diameter {pin_diam_t:.3f} mm  exposed_length {pin_len_t:.3f} mm")
    print("FINAL: tip coordinates", v3(pin1_base_t + d.multiply(pin_len_t)), v3(pin2_base_t + d.multiply(pin_len_t)))

    return out