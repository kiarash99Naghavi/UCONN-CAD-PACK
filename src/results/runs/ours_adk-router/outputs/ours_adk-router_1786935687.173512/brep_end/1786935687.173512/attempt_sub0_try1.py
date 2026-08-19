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

    # --- Resolve and print referenced faces (index check) ---
    faces = base.Faces()
    print(f"INFO: base has {len(faces)} faces")
    for idx in [7, 8, 10, 13, 17]:
        try:
            f = faces[idx]
            c = f.Center()
            print(f"SELECTED: 1 face #{idx} for hook-throat reference  center=[{c.x:.3f},{c.y:.3f},{c.z:.3f}] area={f.Area():.3f}")
        except Exception as e:
            print(f"SELECTED: 0 faces for face #{idx} (ERROR: {e})")

    # --- Targets named by sub-goal ---
    pivot_t = cq.Vector(67.3, 0.0, 9.3)
    tip_t = cq.Vector(72.75, 0.0, 15.0)
    print(f"TARGET pivot  = [{pivot_t.x:.3f},{pivot_t.y:.3f},{pivot_t.z:.3f}] mm")
    print(f"TARGET tip    = [{tip_t.x:.3f},{tip_t.y:.3f},{tip_t.z:.3f}] mm")

    d = tip_t - pivot_t
    L = math.sqrt(d.x**2 + d.z**2)
    theta = math.degrees(math.atan2(d.z, d.x))  # desired direction in XZ
    print(f"INFO: gate vector d=[{d.x:.3f},{d.y:.3f},{d.z:.3f}]  L={L:.3f} mm  theta={theta:.3f} deg")

    # Local gate definition:
    # - length along +X from x=0..L
    # - thickness across hook (Y) is 6.0 => y=-3..+3
    # - gate width is 2.5, biased DOWN in local Z so max local z=0 (keeps within z<=15)
    gate_w = 2.5
    hook_thk = 6.0

    # build in local coords so that the point (0,0,0) is on the *top* surface at the pivot end
    gate_local_wp = (
        cq.Workplane(cq.Plane.XY())
        .rect(L, hook_thk, centered=(False, True))
        .extrude(gate_w)
        .translate((0, 0, -gate_w))  # z: -gate_w..0
    )
    gate_local = gate_local_wp.val()

    # Add a compact pivot anchor tab to guarantee fusion with the hook body near the pivot
    # (extends behind pivot in -X and down in -Z, still inside original bbox)
    def build_feature(anchor_len, anchor_depth):
        anchor_wp = (
            cq.Workplane(cq.Plane.XY())
            .rect(anchor_len, hook_thk, centered=(False, True))
            .extrude(anchor_depth)
            .translate((-anchor_len, 0, -anchor_depth))  # x: -anchor_len..0, z: -anchor_depth..0
        )
        anchor = anchor_wp.val()

        gate_plus = gate_local.fuse(anchor)

        # rotate around +Y to align +X with d (CadQuery +theta rotates +X toward -Z, so use -theta)
        gate_world = gate_plus.rotate((0, 0, 0), (0, 1, 0), -theta).translate((pivot_t.x, pivot_t.y, pivot_t.z))

        # Pivot pin (simple transverse cylinder) along measured Y axis [0,1,0]
        pin_r = 1.0
        pin_len = 7.0  # slight protrusion beyond 6.0 mm thickness, still within bbox
        pin_base = cq.Vector(pivot_t.x, pivot_t.y - pin_len / 2.0, pivot_t.z)
        pin = cq.Solid.makeCylinder(pin_r, pin_len, pnt=pin_base, dir=cq.Vector(0, 1, 0))

        feature = gate_world.fuse(pin)
        return feature, gate_world, pin

    # placement self-check (compute where our intended local pivot/tip land)
    def rotY(v, ang_deg):
        a = math.radians(ang_deg)
        ca, sa = math.cos(a), math.sin(a)
        return cq.Vector(v.x * ca + v.z * sa, v.y, -v.x * sa + v.z * ca)

    pivot_local = cq.Vector(0, 0, 0)
    tip_local = cq.Vector(L, 0, 0)
    # apply same rotation (-theta) then translation
    pivot_world_chk = rotY(pivot_local, -theta) + pivot_t
    tip_world_chk = rotY(tip_local, -theta) + pivot_t
    dp = pivot_world_chk - pivot_t
    dt = tip_world_chk - tip_t
    print(
        "ACHIEVED (by construction): pivot_world=[{:.3f},{:.3f},{:.3f}]  delta=[{:.3f},{:.3f},{:.3f}]".format(
            pivot_world_chk.x, pivot_world_chk.y, pivot_world_chk.z, dp.x, dp.y, dp.z
        )
    )
    print(
        "ACHIEVED (by construction): tip_world  =[{:.3f},{:.3f},{:.3f}]  delta=[{:.3f},{:.3f},{:.3f}]".format(
            tip_world_chk.x, tip_world_chk.y, tip_world_chk.z, dt.x, dt.y, dt.z
        )
    )

    # If we somehow drifted (shouldn't), correct by translation of the whole gate feature
    corr = cq.Vector(-dp.x, -dp.y, -dp.z)
    if max(abs(dp.x), abs(dp.y), abs(dp.z)) > 1.0:
        print(f"WARNING: pivot miss >1mm; applying correction translate {corr.toTuple()}")
        pivot_t = pivot_t + corr
        tip_t = tip_t + corr

    # Try a small anchor first, then a larger one if we don't intersect the base solid
    attempts = [(3.0, 3.0), (6.0, 5.0)]
    chosen = None
    for i, (alen, adep) in enumerate(attempts):
        feature, gate_world, pin = build_feature(alen, adep)
        try:
            inter = solid.intersect(feature)
            inter_vol = inter.Volume() if inter is not None else 0.0
        except Exception as e:
            inter_vol = 0.0
            print(f"INFO: attempt {i} intersection check failed: {e}")
        fbb = feature.BoundingBox()
        print(
            f"INFO: attempt {i} feature bbox x[{fbb.xmin:.3f},{fbb.xmax:.3f}] y[{fbb.ymin:.3f},{fbb.ymax:.3f}] z[{fbb.zmin:.3f},{fbb.zmax:.3f}]  inter_vol={inter_vol:.6f}"
        )
        if inter_vol > 1e-3:
            chosen = (feature, gate_world, pin, (alen, adep), inter_vol)
            break

    if chosen is None:
        print("WARNING: feature appears disjoint from base after both attempts; proceeding anyway (may create multi-solid result).")
        feature, gate_world, pin = build_feature(attempts[-1][0], attempts[-1][1])
        chosen = (feature, gate_world, pin, attempts[-1], 0.0)

    feature, gate_world, pin, (alen, adep), inter_vol = chosen
    print(f"SELECTED: gate feature built with anchor_len={alen:.3f} anchor_depth={adep:.3f}; intersection volume with base={inter_vol:.6f}")

    # Fuse onto the single body
    out = solid.fuse(feature)

    # Added-geometry isolation and self-check
    try:
        added = out.cut(solid)
        abb = added.BoundingBox()
        ac = added.Center()
        print(f"ADDED: center=[{ac.x:.3f},{ac.y:.3f},{ac.z:.3f}]")
        print(f"ADDED: bbox x[{abb.xmin:.3f},{abb.xmax:.3f}] y[{abb.ymin:.3f},{abb.ymax:.3f}] z[{abb.zmin:.3f},{abb.zmax:.3f}]")
    except Exception as e:
        print(f"ADDED: could not isolate added geometry (ERROR: {e})")

    # Check resulting solidity count
    try:
        out_sols = out.Solids()
        print(f"RESULT: solids after fuse = {len(out_sols)}")
    except Exception as e:
        print(f"RESULT: could not count solids (ERROR: {e})")

    # Final achieved named coordinates (again)
    print(
        "FINAL pivot target ~[67.3,0.0,9.3], used=[{:.3f},{:.3f},{:.3f}]".format(
            pivot_t.x, pivot_t.y, pivot_t.z
        )
    )
    print(
        "FINAL tip contact ~[72.75,0.0,15.0], achieved=[{:.3f},{:.3f},{:.3f}]".format(
            tip_world_chk.x, tip_world_chk.y, tip_world_chk.z
        )
    )

    return out