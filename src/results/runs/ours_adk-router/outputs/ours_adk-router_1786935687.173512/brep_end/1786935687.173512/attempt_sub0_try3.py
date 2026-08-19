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

    # Targets named by sub-goal
    pivot_t = cq.Vector(67.3, 0.0, 9.3)
    tip_t = cq.Vector(72.75, 0.0, 15.0)
    print(f"TARGET pivot  = [{pivot_t.x:.3f},{pivot_t.y:.3f},{pivot_t.z:.3f}] mm")
    print(f"TARGET tip    = [{tip_t.x:.3f},{tip_t.y:.3f},{tip_t.z:.3f}] mm")

    d = tip_t - pivot_t
    L = math.sqrt(d.x**2 + d.z**2)
    theta = math.degrees(math.atan2(d.z, d.x))
    print(f"INFO: gate vector d=[{d.x:.3f},{d.y:.3f},{d.z:.3f}]  L={L:.3f} mm  theta={theta:.3f} deg")

    # ---------- DIAGNOSTICS: find existing pin-like cylinders near pivot ----------
    cyl_faces = [f for f in solid.Faces() if f.geomType() == "CYLINDER"]
    print(f"INFO: solid has {len(cyl_faces)} cylindrical faces")

    # Use OCP adaptor to read cylinder axis/radius robustly
    misoriented_pin_faces = []
    yaxis = cq.Vector(0, 1, 0)
    try:
        from OCP.BRepAdaptor import BRepAdaptor_Surface
        from OCP.GeomAbs import GeomAbs_SurfaceType

        for i, f in enumerate(cyl_faces):
            try:
                ad = BRepAdaptor_Surface(f.wrapped, True)
                if ad.GetType() != GeomAbs_SurfaceType.GeomAbs_Cylinder:
                    continue
                cyl = ad.Cylinder()
                r = float(cyl.Radius())
                ax = cyl.Axis().Direction()
                axis_v = cq.Vector(float(ax.X()), float(ax.Y()), float(ax.Z()))
                c = f.Center()
                # focus on small cylinders around the intended pivot area
                if abs(r - 1.0) < 0.35 and (c - pivot_t).Length < 8.0:
                    align = abs(axis_v.normalized().dot(yaxis))
                    print(
                        "MATCH: cyl-face near pivot  idx={}  r={:.3f}  center=[{:.3f},{:.3f},{:.3f}]  axis=[{:.3f},{:.3f},{:.3f}]  |dot(Y)|={:.3f}".format(
                            i, r, c.x, c.y, c.z, axis_v.x, axis_v.y, axis_v.z, align
                        )
                    )
                    if align < 0.90:
                        misoriented_pin_faces.append((f, axis_v, r, c))
            except Exception as e:
                pass
    except Exception as e:
        print(f"WARNING: could not use OCP adaptor to read cylinder axes (ERROR: {e})")

    print(f"SELECTED: {len(misoriented_pin_faces)} misoriented small-cylinder faces near pivot (candidate wrong pin)")

    out = solid

    # ---------- FIX 1: trim ANY locking-feature overhang beyond Y=-3..+3 near hook end ----------
    # Keep trim localized to +X hook area.
    # (This should shave the old pin length from y=±3.5 down to y=±3.0 without harming the hook body,
    # because the hook family thickness here is intended to be 6 mm.)
    trim_xmin, trim_xmax = 60.0, 76.0
    trim_zmin, trim_zmax = 0.0, 15.5

    pos_y_tool = cq.Solid.makeBox(
        trim_xmax - trim_xmin,
        30.0,
        trim_zmax - trim_zmin,
        pnt=(trim_xmin, 3.0, trim_zmin),
    )
    neg_y_tool = cq.Solid.makeBox(
        trim_xmax - trim_xmin,
        30.0,
        trim_zmax - trim_zmin,
        pnt=(trim_xmin, -33.0, trim_zmin),
    )
    # Intersections are printed as a sanity check (large intersection means we're also cutting base material)
    try:
        iv1 = out.intersect(pos_y_tool).Volume()
    except Exception:
        iv1 = 0.0
    try:
        iv2 = out.intersect(neg_y_tool).Volume()
    except Exception:
        iv2 = 0.0
    print(f"INFO: Y-trim tool intersection vols: +Ytool={iv1:.3f}  -Ytool={iv2:.3f} (mm^3)")

    out = out.cut(pos_y_tool)
    out = out.cut(neg_y_tool)

    # ---------- FIX 2: remove the too-deep anchor/tab below the intended 2.5mm gate thickness ----------
    # Build a deep-cut tool in the SAME local coords used for the prior gate build:
    # local z should be limited to [-2.5..0]. Remove anything below z=-2.55 (local).
    gate_w = 2.5

    deep_cut_local = cq.Solid.makeBox(
        L + 20.0,  # x
        12.0,      # y
        60.0 - (gate_w + 0.05),  # z extent from -60..-(gate_w+0.05)
        pnt=(-10.0, -6.0, -60.0),
    )
    # rotate around +Y by -theta and translate to pivot
    deep_cut_world = deep_cut_local.rotate((0, 0, 0), (0, 1, 0), -theta).translate((pivot_t.x, pivot_t.y, pivot_t.z))

    try:
        iv3 = out.intersect(deep_cut_world).Volume()
    except Exception:
        iv3 = 0.0
    print(f"INFO: deep-cut(tool for local z<-{gate_w+0.05:.2f}) intersection vol={iv3:.3f} (mm^3)")

    out = out.cut(deep_cut_world)

    # ---------- FIX 3: if any small pin-like cylinder is NOT along Y, remove it locally ----------
    # Limit removal to a very small neighborhood around the pivot to avoid harming the hook.
    pivot_neighborhood = cq.Solid.makeBox(22.0, 10.0, 12.0, pnt=(pivot_t.x - 11.0, -5.0, pivot_t.z - 6.0))

    if len(misoriented_pin_faces) > 0:
        removed_any = 0
        for (f, axis_v, r, c) in misoriented_pin_faces:
            try:
                axis_n = axis_v.normalized()
                # long cylinder, then clip to pivot neighborhood
                cut_cyl = cq.Solid.makeCylinder(r + 0.35, 40.0, pnt=(c - axis_n * 20.0).toTuple(), dir=axis_n)
                cut_tool = cut_cyl.intersect(pivot_neighborhood)
                out = out.cut(cut_tool)
                removed_any += 1
            except Exception as e:
                print(f"WARNING: failed cutting misoriented-pin candidate (ERROR: {e})")
        print(f"SELECTED: {removed_any} misoriented-pin candidates were cut away (localized)")
    else:
        print("SELECTED: 0 misoriented-pin candidates to cut (will still rebuild/overwrite correct Y-pin)")

    # ---------- ADD: rebuild a correct transverse pivot pin (and compact knuckle) along measured Y axis ----------
    # Keep within Y=-3..+3 as requested.
    pin_r = 1.0
    pin_len = 6.0
    pin_base = cq.Vector(pivot_t.x, -pin_len / 2.0, pivot_t.z)
    pin = cq.Solid.makeCylinder(pin_r, pin_len, pnt=pin_base.toTuple(), dir=cq.Vector(0, 1, 0))

    # Add a compact knuckle boss around the pin to ensure visible, sturdy pivot form (still within thickness band)
    knuckle_r = 1.8
    knuckle = cq.Solid.makeCylinder(knuckle_r, pin_len, pnt=pin_base.toTuple(), dir=cq.Vector(0, 1, 0))

    # Add a small tip-contact pad aligned with the gate direction to ensure throat visibly blocked at the tip.
    # Local box near the tip end: x in [L-1.2, L+0.6], y in [-3,3], z in [-2.5,0]
    tip_pad_local = cq.Solid.makeBox(
        1.8,
        6.0,
        2.5,
        pnt=(L - 1.2, -3.0, -2.5),
    )
    tip_pad_world = tip_pad_local.rotate((0, 0, 0), (0, 1, 0), -theta).translate((pivot_t.x, pivot_t.y, pivot_t.z))

    # Fuse additions
    additions = pin.fuse(knuckle).fuse(tip_pad_world)

    # Placement self-check (named pivot/tip points by construction)
    def rotY(v, ang_deg):
        a = math.radians(ang_deg)
        ca, sa = math.cos(a), math.sin(a)
        return cq.Vector(v.x * ca + v.z * sa, v.y, -v.x * sa + v.z * ca)

    pivot_local = cq.Vector(0, 0, 0)
    tip_local = cq.Vector(L, 0, 0)
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

    # Fuse final
    out2 = out.fuse(additions)

    # Added-geometry isolation and self-check
    try:
        added = out2.cut(solid)
        abb = added.BoundingBox()
        ac = added.Center()
        print(f"ADDED: center=[{ac.x:.3f},{ac.y:.3f},{ac.z:.3f}]")
        print(f"ADDED: bbox x[{abb.xmin:.3f},{abb.xmax:.3f}] y[{abb.ymin:.3f},{abb.ymax:.3f}] z[{abb.zmin:.3f},{abb.zmax:.3f}]")
    except Exception as e:
        print(f"ADDED: could not isolate added geometry (ERROR: {e})")

    # Print volume delta vs input of THIS attempt
    try:
        dv = out2.Volume() - solid.Volume()
        print(f"DELTA volume (out - in) = {dv:.3f} mm^3")
    except Exception as e:
        print(f"DELTA volume: could not compute (ERROR: {e})")

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

    # Check resulting solidity count
    try:
        out_sols = out2.Solids()
        print(f"RESULT: solids after edits = {len(out_sols)}")
    except Exception as e:
        print(f"RESULT: could not count solids (ERROR: {e})")

    return out2