def my_cad_function(args):
    import cadquery as cq
    from math import sqrt

    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input
    base = shape.val() if hasattr(shape, "val") else shape

    solids = list(base.Solids())
    print(f"Loaded solids: {len(solids)}")
    if len(solids) < 2:
        print("ERROR: Expected 2 solids (housing + scroll wheel). Returning original shape.")
        return shape

    s0 = solids[0]  # housing
    s1 = solids[1]  # scroll wheel body (must remain unchanged)

    print(f"Solid0 vol={s0.Volume():.3f} center={tuple(round(v,3) for v in s0.Center().toTuple())}")
    print(f"Solid1 vol={s1.Volume():.3f} center={tuple(round(v,3) for v in s1.Center().toTuple())}")

    # --- anchors from prompt (must list) ---
    target_n = cq.Vector(-0.024, 0.0, -1.0)
    target_n = target_n.normalized()
    target_area = 59.212
    target_c = cq.Vector(27.956, 15.571, 59.222)
    wheel_c = cq.Vector(27.881, 22.101, 51.256)
    wheel_bbox_min = cq.Vector(25.423, 15.069, 44.238)
    wheel_bbox_max = cq.Vector(30.420, 29.133, 58.299)
    fillet_r = 2.0
    print(f"Anchors: face_n≈{tuple(round(v,6) for v in target_n.toTuple())}, face_area≈{target_area}, face_center≈{tuple(target_c.toTuple())}")
    print(f"Anchors: wheel_center≈{tuple(wheel_c.toTuple())}")
    print(f"Anchors: wheel_bbox≈[{tuple(wheel_bbox_min.toTuple())}..{tuple(wheel_bbox_max.toTuple())}]")
    print(f"Anchors: fillet_r={fillet_r}")

    # --- Step 2: find the exterior/top planar face ON SOLID #0 only ---
    def vdist(a, b):
        d = a.sub(b)
        return sqrt(d.x*d.x + d.y*d.y + d.z*d.z)

    best = None
    best_score = 1e9
    for i, f in enumerate(s0.Faces()):
        try:
            if hasattr(f, "geomType") and f.geomType() != "PLANE":
                continue
        except Exception:
            pass

        try:
            fc = f.Center()
            fn = f.normalAt(fc).normalized()
            fa = f.Area()
        except Exception:
            continue

        # direction match (want same direction as target_n)
        dot = fn.dot(target_n)
        # Use a combined score: center proximity + area diff + normal mismatch
        score = (vdist(fc, target_c) * 1.0) + (abs(fa - target_area) * 2.0) + ((1.0 - dot) * 1000.0)
        if score < best_score:
            best_score = score
            best = (i, f, fc, fn, fa, dot)

    if best is None:
        print("ERROR: Could not find any planar face on solid #0. Returning original.")
        return shape

    face_i, top_face, fc, fn, fa, dot = best
    print(f"Chosen s0 planar face idx_in_s0={face_i} area={fa:.3f} center={tuple(round(v,3) for v in fc.toTuple())} normal={tuple(round(v,6) for v in fn.toTuple())} dot_to_target={dot:.6f} score={best_score:.3f}")

    # Extract wires on this face
    wires = list(top_face.Wires())
    print(f"Top face wires: {len(wires)}")
    if len(wires) < 2:
        print("ERROR: Top face has no inner wires; cannot identify slot rim. Returning original.")
        return shape

    # Determine outer wire by largest bbox diagonal (robust)
    def bbox_diag(w):
        bb = w.BoundingBox()
        dx = bb.xlen
        dy = bb.ylen
        dz = bb.zlen
        return sqrt(dx*dx + dy*dy + dz*dz)

    wire_diags = [(j, w, bbox_diag(w)) for j, w in enumerate(wires)]
    wire_diags.sort(key=lambda t: t[2], reverse=True)
    outer_j = wire_diags[0][0]
    print("Wire bbox diags (desc):", [(j, round(d,3)) for j, _, d in wire_diags])
    print(f"Outer wire assumed idx={outer_j}")

    # Choose inner wire nearest to scroll wheel centroid and overlapping wheel bbox region
    def bbox_intersects(bb, mn, mx, pad=1.0):
        mnx, mny, mnz = mn.x - pad, mn.y - pad, mn.z - pad
        mxx, mxy, mxz = mx.x + pad, mx.y + pad, mx.z + pad
        return not (bb.xmax < mnx or bb.xmin > mxx or bb.ymax < mny or bb.ymin > mxy or bb.zmax < mnz or bb.zmin > mxz)

    inner_candidates = []
    for j, w in enumerate(wires):
        if j == outer_j:
            continue
        bb = w.BoundingBox()
        wc = cq.Vector((bb.xmin + bb.xmax)/2.0, (bb.ymin + bb.ymax)/2.0, (bb.zmin + bb.zmax)/2.0)
        d = vdist(wc, wheel_c)
        hit = bbox_intersects(bb, wheel_bbox_min, wheel_bbox_max, pad=1.5)
        inner_candidates.append((j, w, d, hit, bb, wc))

    inner_candidates.sort(key=lambda t: (not t[3], t[2]))  # prefer intersects=True, then nearest
    print("Inner wire candidates (idx, dist_to_wheel, bbox_intersects):",
          [(j, round(d,3), bool(hit)) for j, _, d, hit, _, _ in inner_candidates])

    if not inner_candidates:
        print("ERROR: No inner wire candidates. Returning original.")
        return shape

    slot_wire_j, slot_wire, dmin, hit, slot_bb, slot_wc = inner_candidates[0]
    print(f"Chosen slot inner wire idx={slot_wire_j} center≈{tuple(round(v,3) for v in slot_wc.toTuple())} dist_to_wheel={dmin:.3f} intersects_wheel_bbox={bool(hit)}")
    print(f"Slot wire bbox: xmin={slot_bb.xmin:.3f} xmax={slot_bb.xmax:.3f} ymin={slot_bb.ymin:.3f} ymax={slot_bb.ymax:.3f} zmin={slot_bb.zmin:.3f} zmax={slot_bb.zmax:.3f}")

    rim_edges = list(slot_wire.Edges())
    print(f"Rim edges in chosen wire: {len(rim_edges)}")
    for k, e in enumerate(rim_edges[:12]):
        ec = e.Center()
        try:
            et = e.geomType()
        except Exception:
            et = "?"
        print(f"  edge[{k}] type={et} center={tuple(round(v,3) for v in ec.toTuple())} length={e.Length():.3f}")

    if len(rim_edges) == 0:
        print("ERROR: Chosen wire has 0 edges. Returning original.")
        return shape

    # --- Step 3: Apply fillet on solid #0 only ---
    s0_mod = None
    try:
        s0_mod = s0.fillet(fillet_r, rim_edges)
        print("Fillet succeeded on full rim edge loop.")
    except Exception as ex:
        print("ERROR: Fillet failed on full rim loop:", repr(ex))
        # Fallback (Step 4) is non-trivial in CQ; attempt an alternate robust selection by bbox and retry once.
        try:
            pad = 0.25
            mn = (slot_bb.xmin - pad, slot_bb.ymin - pad, slot_bb.zmin - pad)
            mx = (slot_bb.xmax + pad, slot_bb.ymax + pad, slot_bb.zmax + pad)
            cand = [e for e in s0.Edges() if e.BoundingBox().xmax >= mn[0] and e.BoundingBox().xmin <= mx[0]
                    and e.BoundingBox().ymax >= mn[1] and e.BoundingBox().ymin <= mx[1]
                    and e.BoundingBox().zmax >= mn[2] and e.BoundingBox().zmin <= mx[2]]
            print(f"Fallback edge gather by slot bbox: {len(cand)} edges")
            s0_mod = s0.fillet(fillet_r, cand)
            print("Fallback fillet succeeded with bbox-gathered edge set.")
        except Exception as ex2:
            print("ERROR: Fallback fillet also failed:", repr(ex2))
            print("Returning original shape (no-op) to avoid unintended edits.")
            return shape

    # --- Verification: solid #1 unchanged; recombine as compound ---
    print(f"Solid0 volume before={s0.Volume():.3f} after={s0_mod.Volume():.3f} delta={s0_mod.Volume()-s0.Volume():.3f}")
    print(f"Solid1 volume unchanged check (pre)={s1.Volume():.3f}")

    out = cq.Compound.makeCompound([s0_mod, s1])
    # Post-check: ensure we still have two solids
    out_solids = list(out.Solids())
    print(f"Output solids: {len(out_solids)} volumes={[round(s.Volume(),3) for s in out_solids]}")

    return out