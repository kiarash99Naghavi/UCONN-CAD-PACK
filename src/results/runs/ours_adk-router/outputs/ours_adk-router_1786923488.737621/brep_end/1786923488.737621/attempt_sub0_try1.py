def my_cad_function(args):
    import cadquery as cq

    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input
    base = shape.val() if hasattr(shape, "val") else shape

    # --- Resolve/verify indexed entities (diagnostic only; not used for boolean) ---
    faces = base.Faces()
    edges = base.Edges()
    print(f"BASE: solids={len(base.Solids())} faces={len(faces)} edges={len(edges)}")

    for fi in [3, 4]:
        f = faces[fi]
        c = f.Center()
        n = f.normalAt()
        print(f"VERIFY: face #{fi} center={[round(c.x,3), round(c.y,3), round(c.z,3)]} normal={[round(n.x,3), round(n.y,3), round(n.z,3)]} area={round(f.Area(),3)}")

    bore_edge_ids = [2, 4]
    sel_edges = []
    for ei in bore_edge_ids:
        e = edges[ei]
        sel_edges.append(e)
        gt = e.geomType()
        r = None
        try:
            r = e.radius()
        except Exception:
            r = None
        # arcCenter() is preferred for true circle center; fall back to Center()
        try:
            cc = e.arcCenter()
            cc_src = "arcCenter"
        except Exception:
            cc = e.Center()
            cc_src = "Center(centroid)"
        ec = e.Center()
        print(
            f"VERIFY: edge_idx[{ei}] geomType={gt} radius={None if r is None else round(r,3)} "
            f"{cc_src}={[round(cc.x,3), round(cc.y,3), round(cc.z,3)]} centroid={[round(ec.x,3), round(ec.y,3), round(ec.z,3)]}"
        )
    print(f"SELECTED: {len(sel_edges)} edges for bore mouths idx={bore_edge_ids}")

    # --- Targets ---
    target_center = cq.Vector(-68.25, 0.0, 7.5)
    target_axis = cq.Vector(0.0, 1.0, 0.0)
    target_diam = 6.0
    target_rad = target_diam / 2.0
    target_len = 31.1
    target_ymin = -15.55
    target_ymax = 15.55

    print("TARGETS:")
    print(f"  center={[-68.25, 0.0, 7.5]}")
    print(f"  axis={[0.0, 1.0, 0.0]}")
    print(f"  diameter={target_diam} length={target_len}")
    print(f"  Y endpoints={target_ymin} .. {target_ymax}")

    # --- Build the pin as a SEPARATE solid (do not fuse) ---
    base_point = target_center - target_axis.normalized().multiply(target_len / 2.0)
    pin = cq.Solid.makeCylinder(
        target_rad,
        target_len,
        pnt=base_point,
        dir=target_axis,
        angleDegrees=360,
    )

    # --- Placement self-check (and correct in same attempt if needed) ---
    bb = pin.BoundingBox()
    achieved_center = pin.Center()
    achieved_diam = 2.0 * target_rad
    achieved_axis = target_axis.normalized()
    achieved_ymin = bb.ymin
    achieved_ymax = bb.ymax

    print("ACHIEVED (pre-correction):")
    print(f"  center={[round(achieved_center.x,6), round(achieved_center.y,6), round(achieved_center.z,6)]}")
    print(f"  diameter={round(achieved_diam,6)}")
    print(f"  axis={[round(achieved_axis.x,6), round(achieved_axis.y,6), round(achieved_axis.z,6)]}")
    print(f"  Y endpoints={round(achieved_ymin,6)} .. {round(achieved_ymax,6)}")

    dc = target_center - achieved_center
    dymin = target_ymin - achieved_ymin
    dymax = target_ymax - achieved_ymax

    print("DELTAS (target - achieved):")
    print(f"  dCenter={[round(dc.x,6), round(dc.y,6), round(dc.z,6)]}")
    print(f"  dYmin={round(dymin,6)} dYmax={round(dymax,6)}")

    # If any mismatch beyond tiny tolerance, translate the pin to match the target center.
    tol = 1e-4
    if abs(dc.x) > tol or abs(dc.y) > tol or abs(dc.z) > tol:
        pin = pin.translate(dc)
        bb = pin.BoundingBox()
        achieved_center = pin.Center()
        achieved_ymin = bb.ymin
        achieved_ymax = bb.ymax
        print("CORRECTION: translated pin to match target center")
        print("ACHIEVED (post-correction):")
        print(f"  center={[round(achieved_center.x,6), round(achieved_center.y,6), round(achieved_center.z,6)]}")
        print(f"  Y endpoints={round(achieved_ymin,6)} .. {round(achieved_ymax,6)}")

    # Added material isolation (distinct solid => added == pin)
    added = pin
    abb = added.BoundingBox()
    ac = added.Center()
    print("ADDED SOLID (pin):")
    print(f"  Center={[round(ac.x,6), round(ac.y,6), round(ac.z,6)]}")
    print(
        f"  BBox x[{round(abb.xmin,3)},{round(abb.xmax,3)}] "
        f"y[{round(abb.ymin,3)},{round(abb.ymax,3)}] "
        f"z[{round(abb.zmin,3)},{round(abb.zmax,3)}]"
    )

    # Return an assembly so the pin remains a distinct solid from the host part
    asm = cq.Assembly()
    asm.add(base, name="host_part")
    asm.add(pin, name="pin_6x31p1")
    return asm