def my_cad_function(args):
    import cadquery as cq
    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input

    base = shape.val() if hasattr(shape, "val") else shape

    # ---------- helpers ----------
    def bb_tuple(bb):
        return (bb.xmin, bb.ymin, bb.zmin, bb.xmax, bb.ymax, bb.zmax)

    def count_free_edges(cqshape: cq.Shape):
        try:
            from OCP.TopExp import TopExp
            from OCP.TopTools import TopTools_IndexedDataMapOfShapeListOfShape
            from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE
            m = TopTools_IndexedDataMapOfShapeListOfShape()
            TopExp.MapShapesAndAncestors_s(cqshape.wrapped, TopAbs_EDGE, TopAbs_FACE, m)
            free = 0
            for i in range(1, m.Extent() + 1):
                # Edge is "free" if it has only one adjacent face
                if m.FindFromIndex(i).Extent() == 1:
                    free += 1
            return free
        except Exception as e:
            print(f"WARN: free-edge count failed: {e}")
            return None

    def maybe_repair_solid_envelope_preserving(s0: cq.Solid, tol=1e-4):
        """Attempt a very light ShapeFix on s0; reject if bbox shifts noticeably."""
        try:
            from OCP.ShapeFix import ShapeFix_Shape
            bb0 = s0.BoundingBox()
            fix = ShapeFix_Shape(s0.wrapped)
            try:
                fix.SetPrecision(tol)
            except Exception:
                pass
            fix.Perform()
            fixed = cq.Shape.cast(fix.Shape())

            # Extract a solid if needed
            fixed_solids = fixed.Solids()
            if not fixed_solids:
                print("REPAIR: ShapeFix produced no solids; keeping original s0")
                return s0
            fixed_s0 = max(fixed_solids, key=lambda ss: ss.Volume())

            bb1 = fixed_s0.BoundingBox()
            d = (
                abs(bb1.xmin - bb0.xmin), abs(bb1.ymin - bb0.ymin), abs(bb1.zmin - bb0.zmin),
                abs(bb1.xmax - bb0.xmax), abs(bb1.ymax - bb0.ymax), abs(bb1.zmax - bb0.zmax),
            )
            print(f"REPAIR: bbox delta (abs) = {d}")
            # Envelope-preserving: reject if any bound moved by >0.05mm
            if max(d) > 0.05:
                print("REPAIR: rejected (envelope shift > 0.05mm); keeping original s0")
                return s0
            print("REPAIR: accepted")
            return fixed_s0
        except Exception as e:
            print(f"REPAIR: failed ({e}); keeping original s0")
            return s0

    def ensure_single_solid(shp, label=""):
        if isinstance(shp, cq.Solid):
            return shp
        try:
            sols = shp.Solids()
        except Exception:
            sols = []
        if len(sols) == 1:
            return sols[0]
        if len(sols) > 1:
            print(f"WARN: {label} produced {len(sols)} solids; keeping largest")
            return max(sols, key=lambda ss: ss.Volume())
        print(f"WARN: {label} produced no solids; returning as-is")
        return shp

    def face_info(f):
        try:
            c = f.Center()
        except Exception:
            c = None
        try:
            n = f.normalAt()
            n = [float(n.x), float(n.y), float(n.z)]
        except Exception:
            n = None
        try:
            a = float(f.Area())
        except Exception:
            a = None
        return c, n, a

    # ---------- isolate solids and confirm target faces ----------
    sols = base.Solids()
    print(f"SELECTED: {len(sols)} solids in imported shape")
    if len(sols) < 1:
        print("SELECTED: 0 solids -> no-op")
        return shape

    s0 = sols[0]
    print(f"s0 PRE: vol={s0.Volume():.3f} bbox={bb_tuple(s0.BoundingBox())} valid={s0.isValid()}")
    fe0 = count_free_edges(s0)
    print(f"s0 PRE: free_edges={fe0}")

    # Confirm face indices #24 and #42 on the *imported* (pre-edit) compound
    all_faces = base.Faces()
    print(f"SELECTED: {len(all_faces)} faces in imported shape (compound)")
    for idx in [24, 42]:
        if idx >= len(all_faces):
            print(f"SELECTED: 0 faces for face #{idx} (index out of range)")
            continue
        f = all_faces[idx]
        c, n, a = face_info(f)
        print(f"FACE #{idx} PRE: center={c.toTuple() if c else None} normal={n} area={a}")

    # Optional localized repair only on isolated s0 (do NOT heal whole assembly)
    if (not s0.isValid()) or (fe0 not in (None, 0)):
        print("s0 PRE: attempting localized light repair (ShapeFix) on isolated s0")
        s0 = maybe_repair_solid_envelope_preserving(s0, tol=1e-4)
        print(f"s0 AFTER-REPAIR: vol={s0.Volume():.3f} bbox={bb_tuple(s0.BoundingBox())} valid={s0.isValid()}")
        feR = count_free_edges(s0)
        print(f"s0 AFTER-REPAIR: free_edges={feR}")

    v_before = s0.Volume()
    bb_before = s0.BoundingBox()

    # ---------- build slot cutters anchored to outer ±Z planes ----------
    # Named numbers / anchors
    x_center = -88.9
    slot_len = 25.4
    slot_w = 6.35
    r = 3.175
    ys = [-127.0, -101.6, -76.2, -50.8, -25.4, 0.0, 25.4, 50.8, 76.2, 101.6, 127.0]

    z_top_outer = 266.7
    z_bot_outer = -266.7
    z_top_cut0, z_top_cut1 = 257.075, 266.8
    z_bot_cut0, z_bot_cut1 = -266.8, -257.075

    print("ANCHORS: x_center=-88.9, ys=[-127..127 step 25.4], slot_len=25.4, slot_w=6.35, r=3.175")
    print(f"ANCHORS: top outer z={z_top_outer}, top cut z=[{z_top_cut0}..{z_top_cut1}]")
    print(f"ANCHORS: bot outer z={z_bot_outer}, bot cut z=[{z_bot_cut0}..{z_bot_cut1}]")

    def make_slot_prism(z0, z1, y):
        h = z1 - z0
        wp = cq.Workplane(cq.Plane(origin=(0, 0, z0), normal=(0, 0, 1)))
        tool = wp.center(x_center, y).slot2D(slot_len, slot_w, angle=0).extrude(h).val()
        return tool

    # Make cutters and verify each intersects s0 before committing booleans
    top_tools = []
    bot_tools = []

    for y in ys:
        t = make_slot_prism(z_top_cut0, z_top_cut1, y)
        inter_v = 0.0
        try:
            inter = s0.intersect(t)
            inter_v = inter.Volume() if inter and inter.Volume() is not None else 0.0
        except Exception as e:
            print(f"INTERSECT CHECK (+Z) y={y}: failed ({e})")
            inter_v = 0.0
        print(f"INTERSECT CHECK (+Z) y={y}: vol={inter_v:.6f}  tool_bbox={bb_tuple(t.BoundingBox())}")
        if inter_v > 1e-6:
            top_tools.append(t)
        else:
            print(f"WARN: skipping +Z cutter at y={y} (no material intersection)")

    for y in ys:
        t = make_slot_prism(z_bot_cut0, z_bot_cut1, y)
        inter_v = 0.0
        try:
            inter = s0.intersect(t)
            inter_v = inter.Volume() if inter and inter.Volume() is not None else 0.0
        except Exception as e:
            print(f"INTERSECT CHECK (-Z) y={y}: failed ({e})")
            inter_v = 0.0
        print(f"INTERSECT CHECK (-Z) y={y}: vol={inter_v:.6f}  tool_bbox={bb_tuple(t.BoundingBox())}")
        if inter_v > 1e-6:
            bot_tools.append(t)
        else:
            print(f"WARN: skipping -Z cutter at y={y} (no material intersection)")

    print(f"SELECTED: {len(top_tools)} cutters for +Z side")
    print(f"SELECTED: {len(bot_tools)} cutters for -Z side")

    # If selection is empty, do not silently no-op
    if len(top_tools) == 0 and len(bot_tools) == 0:
        print("SELECTED: 0 cutters total -> no-op")
        return shape

    edited = s0

    # One robust boolean per side
    if top_tools:
        top_comp = cq.Compound.makeCompound(top_tools)
        try:
            edited = ensure_single_solid(edited.cut(top_comp), label="cut +Z")
            print("BOOLEAN: performed single cut for +Z cutters")
        except Exception as e:
            print(f"ERROR: +Z cut failed ({e})")

    if bot_tools:
        bot_comp = cq.Compound.makeCompound(bot_tools)
        try:
            edited = ensure_single_solid(edited.cut(bot_comp), label="cut -Z")
            print("BOOLEAN: performed single cut for -Z cutters")
        except Exception as e:
            print(f"ERROR: -Z cut failed ({e})")

    # ---------- post checks on s0 ----------
    v_after = edited.Volume() if hasattr(edited, "Volume") else None
    removed_vol = (v_before - v_after) if (v_after is not None) else None

    print(f"s0 POST: vol={v_after:.3f} removed={removed_vol:.3f} (mm^3) valid={edited.isValid()}")
    fe1 = count_free_edges(edited)
    print(f"s0 POST: free_edges={fe1}")

    bb_after = edited.BoundingBox()
    print(f"s0 BBOX PRE : {bb_tuple(bb_before)}")
    print(f"s0 BBOX POST: {bb_tuple(bb_after)}")
    print(
        "s0 BBOX DELTA (xmin,ymin,zmin,xmax,ymax,zmax): ",
        (
            bb_after.xmin - bb_before.xmin,
            bb_after.ymin - bb_before.ymin,
            bb_after.zmin - bb_before.zmin,
            bb_after.xmax - bb_before.xmax,
            bb_after.ymax - bb_before.ymax,
            bb_after.zmax - bb_before.zmax,
        ),
    )

    # Verify slot openings count on outer ±Z planar faces by summing inner-wire counts
    def count_openings_on_plane(sol: cq.Solid, z_target, nz_sign, z_tol=0.5):
        faces = sol.Faces()
        sel = []
        for f in faces:
            try:
                n = f.normalAt()
                c = f.Center()
                if abs(c.z - z_target) <= z_tol and (n.z * nz_sign) > 0.9:
                    sel.append(f)
            except Exception:
                pass
        print(f"SELECTED: {len(sel)} planar-ish faces near z={z_target} with normal sign {nz_sign}")
        openings = 0
        for f in sel:
            try:
                wires = f.Wires()
                inner = max(0, len(wires) - 1)
                openings += inner
            except Exception:
                pass
        return openings

    top_openings = count_openings_on_plane(edited, z_top_outer, +1)
    bot_openings = count_openings_on_plane(edited, z_bot_outer, -1)
    print(f"VERIFY: estimated openings on +Z outer plane = {top_openings} (target 11)")
    print(f"VERIFY: estimated openings on -Z outer plane = {bot_openings} (target 11)")

    # ---------- recompound all solids, preserving original order ----------
    out_sols = []
    for i, s in enumerate(sols):
        out_sols.append(edited if i == 0 else s)
    out = cq.Compound.makeCompound(out_sols)

    # overall bbox / solid count
    out_base = out.val() if hasattr(out, "val") else out
    out_sols2 = out_base.Solids()
    print(f"OUT: solids={len(out_sols2)} (should remain {len(sols)})")
    print(f"OUT: bbox={bb_tuple(out_base.BoundingBox())}")

    return out