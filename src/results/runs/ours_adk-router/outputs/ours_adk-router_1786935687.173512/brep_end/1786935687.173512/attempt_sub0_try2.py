def my_cad_function(args):
    import cadquery as cq
    import math

    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input
    base = shape.val() if hasattr(shape, "val") else shape

    solids = base.Solids()
    print(f"SELECTED: {len(solids)} solids in imported shape")
    if len(solids) != 1:
        print("ERROR: expected exactly 1 solid; returning original shape")
        return shape
    solid = solids[0]

    # ----------------------------
    # Targets named by sub-goal
    # ----------------------------
    pivot_t = cq.Vector(67.3, 0.0, 9.3)
    tip_t = cq.Vector(72.75, 0.0, 15.0)
    print(f"TARGET pivot  = [{pivot_t.x:.3f},{pivot_t.y:.3f},{pivot_t.z:.3f}] mm")
    print(f"TARGET tip    = [{tip_t.x:.3f},{tip_t.y:.3f},{tip_t.z:.3f}] mm")
    print("TARGET gate thickness band Y = [-3.0, +3.0] mm")
    print("TARGET pivot-pin axis = world +Y [0,1,0]")

    d = tip_t - pivot_t
    L = math.sqrt(d.x**2 + d.z**2)
    theta = math.degrees(math.atan2(d.z, d.x))
    print(f"INFO: gate vector d=[{d.x:.3f},{d.y:.3f},{d.z:.3f}]  L={L:.3f} mm  theta={theta:.3f} deg")

    # ----------------------------
    # Diagnostics: find existing (wrong) pivot-pin circular edges near pivot
    # (edges may be partial arcs; get TRUE circle center via adaptor)
    # ----------------------------
    try:
        from OCP.BRepAdaptor import BRepAdaptor_Curve
        from OCP.GeomAbs import GeomAbs_Circle

        cand = []
        for i, e in enumerate(solid.Edges()):
            try:
                ad = BRepAdaptor_Curve(e.wrapped)
                if ad.GetType() != GeomAbs_Circle:
                    continue
                circ = ad.Circle()
                r = float(circ.Radius())
                c = circ.Location().TranslationPart()
                cx, cy, cz = float(c.X()), float(c.Y()), float(c.Z())
                if abs(r - 1.0) < 0.15 and abs(cx - 67.3) < 1.5 and abs(cz - 9.3) < 2.0:
                    cand.append((i, r, cx, cy, cz))
            except Exception:
                continue

        print(f"SELECTED: {len(cand)} circular edges (r~1.0) near pivot for existing-pin detection  idx={[c[0] for c in cand]}")
        for (i, r, cx, cy, cz) in cand[:12]:
            print(f"  MATCH edge#{i}: r={r:.3f} true_center=[{cx:.3f},{cy:.3f},{cz:.3f}]")
    except Exception as e:
        print(f"SELECTED: 0 circular edges for existing-pin detection (ERROR: {e})")

    # ----------------------------
    # Build an approximation of the OLD (flawed) feature and REMOVE it
    # (then we re-add corrected geometry)
    # ----------------------------
    hook_thk_old = 6.0
    gate_w_old = 2.5
    pin_r_old = 1.0
    pin_len_old = 7.0          # was y=-3.5..+3.5 (too wide)
    anchor_len_old = 6.0
    anchor_depth_old = 5.0     # drove z too low

    def build_old_feature():
        # gate: local x 0..L, y -3..+3, z -2.5..0
        gate = (
            cq.Workplane(cq.Plane.XY())
            .box(L, hook_thk_old, gate_w_old, centered=(False, True, False))
            .translate((0, 0, -gate_w_old))
            .val()
        )
        anchor = (
            cq.Workplane(cq.Plane.XY())
            .box(anchor_len_old, hook_thk_old, anchor_depth_old, centered=(False, True, False))
            .translate((-anchor_len_old, 0, -anchor_depth_old))
            .val()
        )
        gate_plus = gate.fuse(anchor)

        gate_world = gate_plus.rotate((0, 0, 0), (0, 1, 0), -theta).translate((pivot_t.x, pivot_t.y, pivot_t.z))

        pin_base = cq.Vector(pivot_t.x, pivot_t.y - pin_len_old / 2.0, pivot_t.z)
        pin = cq.Solid.makeCylinder(pin_r_old, pin_len_old, pnt=pin_base, dir=cq.Vector(0, 1, 0))
        return gate_world.fuse(pin)

    old_feat = build_old_feature()
    oldbb = old_feat.BoundingBox()
    print(
        "INFO: rebuilt OLD feature tool bbox "
        f"x[{oldbb.xmin:.3f},{oldbb.xmax:.3f}] y[{oldbb.ymin:.3f},{oldbb.ymax:.3f}] z[{oldbb.zmin:.3f},{oldbb.zmax:.3f}]"
    )

    v0 = solid.Volume()
    try:
        solid_wo_old = solid.cut(old_feat)
        v1 = solid_wo_old.Volume()
        print(f"INFO: cut-out OLD feature approx removed volume = {v0 - v1:.3f} mm^3 (base vol before={v0:.3f}, after={v1:.3f})")
    except Exception as e:
        print(f"ERROR: failed to cut out old feature (will proceed without removal) (ERROR: {e})")
        solid_wo_old = solid

    # ----------------------------
    # Build the CORRECTED gate + pivot-pin
    # Fixes:
    #  - Y thickness strictly within [-3,+3]
    #  - pin axis along world Y
    #  - compact Z: avoid deep anchor; keep min Z near pivot region
    # ----------------------------
    gate_w = 2.5
    hook_thk = 6.0  # y=-3..+3
    pin_r = 1.0
    pin_len = 6.0   # y=-3..+3 (flush)

    # Slight extra length to guarantee visible tip contact/overlap
    tip_overlap = 0.8
    L_ext = L + tip_overlap

    # Gate body in local coordinates:
    #  - local +X is the span direction (later rotated about world Y by -theta)
    #  - local +Y is world Y (hook thickness band)
    #  - local +Z is thickness, biased DOWN so zmax stays within part z=15
    gate_local = (
        cq.Workplane(cq.Plane.XY())
        .box(L_ext, hook_thk, gate_w, centered=(False, True, False))
        .translate((0, 0, -gate_w))  # local z: -2.5..0
        .val()
    )

    # Compact pivot lug to ensure fusion, but NOT deep in Z
    lug_len = 2.2
    lug_local = (
        cq.Workplane(cq.Plane.XY())
        .box(lug_len, hook_thk, gate_w, centered=(False, True, False))
        .translate((-lug_len, 0, -gate_w))
        .val()
    )

    gate_plus = gate_local.fuse(lug_local)
    gate_world = gate_plus.rotate((0, 0, 0), (0, 1, 0), -theta).translate((pivot_t.x, pivot_t.y, pivot_t.z))

    # Pivot pin: ensure axis is world Y
    pin_base = cq.Vector(pivot_t.x, pivot_t.y - pin_len / 2.0, pivot_t.z)
    pin = cq.Solid.makeCylinder(pin_r, pin_len, pnt=pin_base, dir=cq.Vector(0, 1, 0))

    new_feat = gate_world.fuse(pin)

    # ----------------------------
    # Placement self-check: achieved pivot and tip-contact coordinates
    # ----------------------------
    def rotY(v, ang_deg):
        a = math.radians(ang_deg)
        ca, sa = math.cos(a), math.sin(a)
        # standard Y-rotation
        return cq.Vector(v.x * ca + v.z * sa, v.y, -v.x * sa + v.z * ca)

    pivot_local = cq.Vector(0, 0, 0)
    tip_local = cq.Vector(L, 0, 0)  # check against the nominal tip point, not the overlap extension

    pivot_world_chk = rotY(pivot_local, -theta) + pivot_t
    tip_world_chk = rotY(tip_local, -theta) + pivot_t

    dp = pivot_world_chk - pivot_t
    dt = tip_world_chk - tip_t

    print(
        "ACHIEVED (corrected gate construction): pivot_world="
        f"[{pivot_world_chk.x:.3f},{pivot_world_chk.y:.3f},{pivot_world_chk.z:.3f}] "
        f"delta=[{dp.x:.3f},{dp.y:.3f},{dp.z:.3f}]"
    )
    print(
        "ACHIEVED (corrected gate construction): tip_world  ="
        f"[{tip_world_chk.x:.3f},{tip_world_chk.y:.3f},{tip_world_chk.z:.3f}] "
        f"delta=[{dt.x:.3f},{dt.y:.3f},{dt.z:.3f}]"
    )

    # If tip miss is large (shouldn't), correct by translating the whole new feature
    corr = cq.Vector(-dp.x, -dp.y, -dp.z)
    if max(abs(dp.x), abs(dp.y), abs(dp.z)) > 1.0 or max(abs(dt.x), abs(dt.y), abs(dt.z)) > 1.0:
        print(f"WARNING: placement miss >1mm; applying correction translate=[{corr.x:.3f},{corr.y:.3f},{corr.z:.3f}]")
        new_feat = new_feat.translate((corr.x, corr.y, corr.z))
        pivot_t = pivot_t + corr
        tip_t = tip_t + corr

    # Fuse corrected feature onto (old-feature-removed) solid
    out = solid_wo_old.fuse(new_feat)

    # ----------------------------
    # Added-geometry isolation and constraints checks
    # ----------------------------
    try:
        added = out.cut(solid_wo_old)
        abb = added.BoundingBox()
        ac = added.Center()
        print(f"ADDED(corrected): center=[{ac.x:.3f},{ac.y:.3f},{ac.z:.3f}]")
        print(
            "ADDED(corrected): bbox "
            f"x[{abb.xmin:.3f},{abb.xmax:.3f}] "
            f"y[{abb.ymin:.3f},{abb.ymax:.3f}] "
            f"z[{abb.zmin:.3f},{abb.zmax:.3f}]"
        )
        print(
            "CHECK: added Y-span should be within [-3,+3] -> "
            f"ymin={abb.ymin:.3f}, ymax={abb.ymax:.3f}"
        )
        print(
            "CHECK: added Z-min should be compact near pivot (~>=7-8mm preferred) -> "
            f"zmin={abb.zmin:.3f} (pivot z={pivot_t.z:.3f})"
        )
    except Exception as e:
        print(f"ADDED(corrected): could not isolate added geometry (ERROR: {e})")

    # Pin endpoint print to prove world-Y axis and Y band
    pin_y0 = pivot_t.y - pin_len / 2.0
    pin_y1 = pivot_t.y + pin_len / 2.0
    print(
        "PIN(corrected): axis=[0,1,0] endpoints="
        f"([{pivot_t.x:.3f},{pin_y0:.3f},{pivot_t.z:.3f}] -> [{pivot_t.x:.3f},{pin_y1:.3f},{pivot_t.z:.3f}])"
    )

    # Outer bbox check
    obb = out.BoundingBox()
    ibb = solid.BoundingBox()
    print(
        "BBOX(before) "
        f"min[{ibb.xmin:.3f},{ibb.ymin:.3f},{ibb.zmin:.3f}] max[{ibb.xmax:.3f},{ibb.ymax:.3f},{ibb.zmax:.3f}]"
    )
    print(
        "BBOX(after)  "
        f"min[{obb.xmin:.3f},{obb.ymin:.3f},{obb.zmin:.3f}] max[{obb.xmax:.3f},{obb.ymax:.3f},{obb.zmax:.3f}]"
    )

    # Solidity count
    try:
        out_sols = out.Solids()
        print(f"RESULT: solids after correction = {len(out_sols)}")
    except Exception as e:
        print(f"RESULT: could not count solids (ERROR: {e})")

    # Final achieved named coordinates (as requested)
    print(
        "FINAL pivot target ~[67.3,0.0,9.3], used="
        f"[{pivot_t.x:.3f},{pivot_t.y:.3f},{pivot_t.z:.3f}]"
    )
    print(
        "FINAL tip contact ~[72.75,0.0,15.0], achieved="
        f"[{tip_world_chk.x:.3f},{tip_world_chk.y:.3f},{tip_world_chk.z:.3f}]"
    )

    return out