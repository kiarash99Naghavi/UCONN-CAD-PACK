def my_cad_function(args):
    import cadquery as cq
    import math

    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input
    base = shape.val() if hasattr(shape, "val") else shape

    solids = list(base.Solids())
    print(f"Loaded solids: {len(solids)}")
    if len(solids) < 2:
        print("ERROR: Expected 2 solids (housing + wheel). Returning original.")
        return shape

    s0 = solids[0]
    s1 = solids[1]

    print(f"Solid0 vol={s0.Volume():.3f} center={tuple(round(v,3) for v in s0.Center().toTuple())}")
    print(f"Solid1 vol={s1.Volume():.3f} center={tuple(round(v,3) for v in s1.Center().toTuple())}")

    # ---- Anchors explicitly listed (from prompt) ----
    fillet_r = 2.0
    target_face_idx = 3
    target_face_area = 59.212
    target_face_center = cq.Vector(27.956, 15.571, 59.222)
    target_face_n = cq.Vector(-0.024, 0.0, -1.0).normalized()
    wheel_c = cq.Vector(27.881, 22.101, 51.256)
    wheel_bbox_min = cq.Vector(25.423, 15.069, 44.238)
    wheel_bbox_max = cq.Vector(30.420, 29.133, 58.299)

    print(f"Anchors: target_face_idx={target_face_idx} face_area≈{target_face_area} face_center≈{tuple(target_face_center.toTuple())} face_n≈{tuple(round(v,6) for v in target_face_n.toTuple())}")
    print(f"Anchors: wheel_center≈{tuple(wheel_c.toTuple())}")
    print(f"Anchors: wheel_bbox≈[{tuple(wheel_bbox_min.toTuple())}..{tuple(wheel_bbox_max.toTuple())}]")
    print(f"Anchors: fillet_r={fillet_r}")

    # Resolve the target face by global index and print its properties (verification)
    all_faces = list(base.Faces())
    if target_face_idx >= len(all_faces):
        print(f"ERROR: base.Faces() has only {len(all_faces)} faces; cannot access idx {target_face_idx}. Returning original.")
        return shape

    tf = all_faces[target_face_idx]
    try:
        tf_c = tf.Center()
        tf_a = tf.Area()
        tf_n = tf.normalAt(tf_c).normalized()
        print(f"Resolved base face#{target_face_idx}: area={tf_a:.3f} center={tuple(round(v,3) for v in tf_c.toTuple())} normal={tuple(round(v,6) for v in tf_n.toTuple())}")
        print(f"  Deltas vs index: dArea={tf_a-target_face_area:+.3f} dCenter={(tf_c-target_face_center).Length():.3f} dotN={tf_n.dot(target_face_n):.6f}")
    except Exception as ex:
        print("WARN: Could not compute target face properties:", repr(ex))
        tf_c = target_face_center
        tf_n = target_face_n

    # Ensure this face is actually on solid #0 (by hash match); if not, continue anyway but select edges from s0 only.
    tf_hash = tf.hashCode()
    on_s0 = any(f.hashCode() == tf_hash for f in s0.Faces())
    on_s1 = any(f.hashCode() == tf_hash for f in s1.Faces())
    print(f"Target face hash on s0? {on_s0}  on s1? {on_s1}")

    # Primary edge selection: edges on solid #0 that lie on the target z-level and overlap the wheel XY region.
    # (Previous attempt failed because the face had no inner wire; use edge filtering instead.)
    target_z = float(tf_c.z)
    z_tol = 0.75
    pad_xy = 3.0

    def overlaps_xy(bb):
        return not (
            bb.xmax < (wheel_bbox_min.x - pad_xy) or bb.xmin > (wheel_bbox_max.x + pad_xy) or
            bb.ymax < (wheel_bbox_min.y - pad_xy) or bb.ymin > (wheel_bbox_max.y + pad_xy)
        )

    rim_edges = []
    for e in s0.Edges():
        bb = e.BoundingBox()
        # Edge must be essentially on the target plane z=target_z
        if abs(bb.zmax - target_z) <= z_tol and abs(bb.zmin - target_z) <= z_tol:
            if overlaps_xy(bb):
                rim_edges.append(e)

    # De-duplicate by hash
    rim_edges_u = []
    seen = set()
    for e in rim_edges:
        h = e.hashCode()
        if h not in seen:
            seen.add(h)
            rim_edges_u.append(e)
    rim_edges = rim_edges_u

    print(f"Selected rim edge candidates on s0: {len(rim_edges)} (target_z={target_z:.3f} z_tol={z_tol} pad_xy={pad_xy})")
    for i, e in enumerate(rim_edges[:24]):
        try:
            gt = e.geomType()
        except Exception:
            gt = "?"
        ec = e.Center()
        bb = e.BoundingBox()
        print(f"  e[{i}] type={gt} len={e.Length():.3f} center={tuple(round(v,3) for v in ec.toTuple())} bbZ=({bb.zmin:.3f},{bb.zmax:.3f})")

    # If nothing matched, broaden z tolerance slightly and try once more (still s0-only)
    if len(rim_edges) == 0:
        z_tol2 = 2.0
        rim_edges = []
        for e in s0.Edges():
            bb = e.BoundingBox()
            if abs(bb.zmax - target_z) <= z_tol2 and abs(bb.zmin - target_z) <= z_tol2 and overlaps_xy(bb):
                rim_edges.append(e)
        # de-dup
        rim_edges_u = []
        seen = set()
        for e in rim_edges:
            h = e.hashCode()
            if h not in seen:
                seen.add(h)
                rim_edges_u.append(e)
        rim_edges = rim_edges_u
        print(f"Retry selection (broader z_tol={z_tol2}): edges={len(rim_edges)}")

    if len(rim_edges) == 0:
        print("ERROR: Could not select any candidate edges for slot rim on solid #0. Returning original (no-op).")
        return shape

    # ---- Apply fillet on solid #0 only ----
    s0_mod = None
    try:
        s0_mod = s0.fillet(fillet_r, rim_edges)
        print("Fillet succeeded on candidate edge set.")
    except Exception as ex:
        print("WARN: Fillet failed on full set, trying incremental per-edge fillets:", repr(ex))
        s0_mod = s0
        ok = 0
        # Prefer longer edges first (often more stable) to establish the blend
        rim_edges_sorted = sorted(rim_edges, key=lambda ee: ee.Length(), reverse=True)
        for e in rim_edges_sorted:
            try:
                s0_mod2 = s0_mod.fillet(fillet_r, [e])
                s0_mod = s0_mod2
                ok += 1
            except Exception:
                pass
        print(f"Incremental fillet successes: {ok}/{len(rim_edges_sorted)}")
        if ok == 0:
            print("ERROR: No fillets could be applied; returning original (no-op).")
            return shape

    # ---- Verification prints ----
    dv = s0_mod.Volume() - s0.Volume()
    print(f"Solid0 volume before={s0.Volume():.3f} after={s0_mod.Volume():.3f} delta={dv:+.3f}")

    try:
        removed = s0.cut(s0_mod)  # material removed by fillet
        if hasattr(removed, "Volume"):
            print(f"Removed volume (s0 - s0_mod) = {removed.Volume():.3f}")
            try:
                rc = removed.Center()
                print(f"Removed center={tuple(round(v,3) for v in rc.toTuple())} vs wheel_center={tuple(round(v,3) for v in wheel_c.toTuple())} d={ (rc-wheel_c).Length():.3f}")
            except Exception:
                pass
    except Exception as ex:
        print("WARN: Could not compute removed volume/center:", repr(ex))

    # Solid #1 must remain unchanged
    print(f"Solid1 volume unchanged check={s1.Volume():.3f}")

    out = cq.Compound.makeCompound([s0_mod, s1])
    out_solids = list(out.Solids())
    print(f"Output solids: {len(out_solids)} volumes={[round(s.Volume(),3) for s in out_solids]}")

    return out