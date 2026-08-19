def my_cad_function(args):
    import cadquery as cq

    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input
    base = shape.val() if hasattr(shape, "val") else shape

    solids = base.Solids()
    print(f"Loaded: solids={len(solids)} faces={len(base.Faces())} edges={len(base.Edges())}")
    if len(solids) < 2:
        print("WARNING: expected 2 solids; proceeding with whatever is available")

    # Identify solids (per index: #0 is main housing, #1 is wheel)
    solid0 = solids[0]
    solid1 = solids[1] if len(solids) > 1 else None

    def vol(s):
        try:
            return s.Volume()
        except Exception:
            return None

    print(f"Body0 volume(before)={vol(solid0)}")
    if solid1 is not None:
        print(f"Body1 volume(before)={vol(solid1)}")

    # Resolve planar face #1 from the GLOBAL index and confirm it matches the provided numbers
    face_global = base.Faces()[1]
    print("Resolved global face #1:")
    print(f"  area={face_global.Area():.3f}")
    print(f"  center={tuple(round(v, 3) for v in face_global.Center().toTuple())}")

    # Find the same face object inside solid0 (so we don't accidentally target solid1)
    face0 = None
    for f in solid0.Faces():
        if f.wrapped.IsSame(face_global.wrapped):
            face0 = f
            break
    print(f"Face #1 found in body0? {face0 is not None}")

    # Target edge indices that must be part of the slot rim loop
    target_edge_idx = [25, 26, 27, 29]
    edges_global = base.Edges()
    target_edges_global = [edges_global[i] for i in target_edge_idx]

    # If face0 couldn't be found in body0 for any reason, we still try to fillet
    # the loop by finding a wire on the global face and then mapping its edges to body0.
    if face0 is None:
        face0 = face_global

    # Find the perimeter wire on face0 that contains (IsSame) any of the target edges
    wires = face0.Wires()
    print(f"Face #1 wire count={len(wires)}")

    slot_wire = None
    for wi, w in enumerate(wires):
        w_edges = w.Edges()
        hit = 0
        for te in target_edges_global:
            if any(e.wrapped.IsSame(te.wrapped) for e in w_edges):
                hit += 1
        bb = w.BoundingBox()
        print(f"  wire[{wi}] edges={len(w_edges)} hit_target_edges={hit} bbox=([{bb.xmin:.3f},{bb.ymin:.3f},{bb.zmin:.3f}]..[{bb.xmax:.3f},{bb.ymax:.3f},{bb.zmax:.3f}])")
        if hit > 0 and slot_wire is None:
            slot_wire = w

    if slot_wire is None:
        # Fallback: just fillet the explicit target edges that belong to body0
        print("WARNING: could not find a wire containing the target edges; falling back to filleting the explicit target edges on body0")
        slot_edges = []
        solid0_edges = solid0.Edges()
        for i in target_edge_idx:
            eg = edges_global[i]
            match = None
            for e0 in solid0_edges:
                if e0.wrapped.IsSame(eg.wrapped):
                    match = e0
                    break
            if match is not None:
                slot_edges.append(match)
        print(f"Fallback slot_edges in body0 count={len(slot_edges)} from target indices={target_edge_idx}")
    else:
        # Use every edge in that wire to ensure full perimeter-loop fillet propagation
        slot_edges = list(slot_wire.Edges())
        print(f"Selected slot perimeter edges from wire: count={len(slot_edges)}")

    # Verify which of the required edge indices are included (by IsSame against global edges)
    for i in target_edge_idx:
        eg = edges_global[i]
        included = any(e.wrapped.IsSame(eg.wrapped) for e in slot_edges)
        print(f"  includes global edge_idx[{i}] ? {included}")

    # Ensure we are NOT modifying body1: only fillet body0
    r = 2.0
    solid0_mod = solid0

    # Try filleting all selected perimeter edges in one operation
    try:
        if hasattr(solid0_mod, "fillet"):
            solid0_mod = solid0_mod.fillet(r, slot_edges)
        else:
            wp = cq.Workplane(obj=solid0_mod).newObject(slot_edges)
            solid0_mod = wp.fillet(r).val()
        print(f"SUCCESS: filleted body0 with R={r} on {len(slot_edges)} edges")
    except Exception as e:
        print(f"Fillet-all failed: {e}")
        # Fallback: fillet incrementally edge-by-edge, keeping successes
        kept = 0
        for ei, ed in enumerate(slot_edges):
            try:
                if hasattr(solid0_mod, "fillet"):
                    solid0_mod = solid0_mod.fillet(r, [ed])
                else:
                    wp = cq.Workplane(obj=solid0_mod).newObject([ed])
                    solid0_mod = wp.fillet(r).val()
                kept += 1
            except Exception as e2:
                print(f"  edge[{ei}] fillet failed: {e2}")
        print(f"Fallback incremental fillet kept={kept}/{len(slot_edges)} edges")

    print(f"Body0 volume(after)={vol(solid0_mod)}  delta={None if (vol(solid0_mod) is None or vol(solid0) is None) else (vol(solid0_mod)-vol(solid0))}")
    if solid1 is not None:
        print(f"Body1 volume(after, unchanged)={vol(solid1)}")

    out = cq.Compound.makeCompound([solid0_mod] + ([solid1] if solid1 is not None else []))
    # BBox check (should not grow for a fillet)
    bb0 = solid0.BoundingBox()
    bb1 = solid0_mod.BoundingBox()
    print(f"Body0 bbox(before)=([{bb0.xmin:.3f},{bb0.ymin:.3f},{bb0.zmin:.3f}]..[{bb0.xmax:.3f},{bb0.ymax:.3f},{bb0.zmax:.3f}])")
    print(f"Body0 bbox(after )=([{bb1.xmin:.3f},{bb1.ymin:.3f},{bb1.zmin:.3f}]..[{bb1.xmax:.3f},{bb1.ymax:.3f},{bb1.zmax:.3f}])")

    return out