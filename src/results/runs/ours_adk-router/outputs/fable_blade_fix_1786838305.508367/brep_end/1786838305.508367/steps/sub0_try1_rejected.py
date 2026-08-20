def my_cad_function(args):
    import cadquery as cq

    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input
    base = shape.val() if hasattr(shape, "val") else shape

    # --- Numbers explicitly named by the sub-goal ---
    R = 6.35  # mm (0.635 cm)
    face_idx_a = 14
    face_idx_b = 21
    end_face_idxs = (40, 44)
    print(f"Target fillet radius R = {R} mm (0.635 cm)")
    print(f"Target faces: #{face_idx_a} meets #{face_idx_b}; termination into end faces {end_face_idxs}")

    faces = base.Faces()
    edges = base.Edges()
    print(f"Base entity counts: faces={len(faces)}, edges={len(edges)}, solids={len(base.Solids())}")

    f14 = faces[face_idx_a]
    f21 = faces[face_idx_b]
    print(f"Resolved Face #{face_idx_a}: area={f14.Area():.3f}, center={list(map(lambda x: round(x,3), f14.Center().toTuple()))}")
    print(f"Resolved Face #{face_idx_b}: area={f21.Area():.3f}, center={list(map(lambda x: round(x,3), f21.Center().toTuple()))}")

    # Find owning solid (important since STEP import is a compound of 3 solids)
    solids = base.Solids()
    owner = None
    for si, s in enumerate(solids):
        sf = s.Faces()
        has14 = any(ff.wrapped.IsSame(f14.wrapped) for ff in sf)
        has21 = any(ff.wrapped.IsSame(f21.wrapped) for ff in sf)
        if has14 and has21:
            owner = s
            print(f"Owning solid index in compound: {si}")
            break
    if owner is None:
        # Fallback: if faces are not found together (shouldn't happen), try using the whole base
        owner = base
        print("WARNING: Could not isolate owning solid containing both faces; will attempt fillet on whole shape.")

    # Re-resolve the faces from the owner's face list to ensure topological consistency for edge matching
    owner_faces = owner.Faces()
    of14 = None
    of21 = None
    for ff in owner_faces:
        if ff.wrapped.IsSame(f14.wrapped):
            of14 = ff
        if ff.wrapped.IsSame(f21.wrapped):
            of21 = ff
    if of14 is None or of21 is None:
        print("WARNING: Could not re-resolve both faces on the owning shape; using originally resolved faces for edge matching.")
        of14, of21 = f14, f21

    # Find shared edge(s) between the two faces
    shared = []
    e14s = of14.Edges()
    e21s = of21.Edges()
    for e1 in e14s:
        for e2 in e21s:
            if e1.wrapped.IsSame(e2.wrapped):
                shared.append(e1)
                break

    print(f"Shared edge candidates between Face #{face_idx_a} and Face #{face_idx_b}: {len(shared)}")
    for i, e in enumerate(shared):
        c = e.Center().toTuple()
        print(f"  cand[{i}]: length={e.Length():.3f} center={[round(c[0],3), round(c[1],3), round(c[2],3)]}")

    if not shared:
        print("ERROR: No shared edge found; no change applied.")
        return shape

    # Choose the longest shared edge (the 'long blade edge')
    target_edge = max(shared, key=lambda ee: ee.Length())
    tec = target_edge.Center().toTuple()
    print(f"Selected target edge: length={target_edge.Length():.3f} center={[round(tec[0],3), round(tec[1],3), round(tec[2],3)]}")

    # Apply fillet only to that edge
    try:
        filleted_owner = owner.fillet(R, [target_edge])
        print("Fillet operation: SUCCESS")
    except Exception as ex:
        print(f"Fillet operation: FAILED with exception: {ex}")
        return shape

    # Self-check: material removed by fillet (approximate) and location
    try:
        removed = owner.cut(filleted_owner)
        if removed and removed.Volume() > 1e-6:
            bb = removed.BoundingBox()
            cc = removed.Center().toTuple()
            print(f"Removed material (owner - filleted_owner): vol={removed.Volume():.3f} mm^3")
            print(f"  removed.Center={list(map(lambda x: round(x,3), cc))}")
            print(f"  removed.BBox min={[round(bb.xmin,3), round(bb.ymin,3), round(bb.zmin,3)]} max={[round(bb.xmax,3), round(bb.ymax,3), round(bb.zmax,3)]}")
        else:
            print("Removed material check: none/too small to measure")
    except Exception as ex:
        print(f"Removed material check: skipped due to exception: {ex}")

    # Recombine with other solids unchanged if we isolated an owner solid
    if owner is base:
        return filleted_owner

    new_solids = []
    replaced = False
    for s in solids:
        if s.wrapped.IsSame(owner.wrapped):
            new_solids.append(filleted_owner)
            replaced = True
        else:
            new_solids.append(s)
    if not replaced:
        print("WARNING: Owner solid replacement failed; returning filleted owner only.")
        return filleted_owner

    out = cq.Compound.makeCompound(new_solids)
    print(f"Output solids recombined: {len(new_solids)}")
    return out