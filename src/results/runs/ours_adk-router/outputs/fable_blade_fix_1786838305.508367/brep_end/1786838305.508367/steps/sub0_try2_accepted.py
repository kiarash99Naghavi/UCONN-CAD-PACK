def my_cad_function(args):
    import cadquery as cq
    import math

    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input
    base = shape.val() if hasattr(shape, "val") else shape

    # --- Numbers explicitly named by the sub-goal ---
    R = 6.35  # mm (0.635 cm)
    face_idx_a = 14
    face_idx_b = 21
    end_face_idxs = (40, 44)
    print(f"Target fillet radius R = {R} mm")
    print(f"Target sharp edge: intersection of planar faces #{face_idx_a} and #{face_idx_b}; should terminate naturally into end faces {end_face_idxs}")

    faces = base.Faces()
    print(f"Base entity counts: faces={len(faces)}, edges={len(base.Edges())}, solids={len(base.Solids())}")

    fA = faces[face_idx_a]
    fB = faces[face_idx_b]
    print(f"Resolved Face #{face_idx_a}: area={fA.Area():.3f}, center={[round(x,3) for x in fA.Center().toTuple()]}")
    print(f"Resolved Face #{face_idx_b}: area={fB.Area():.3f}, center={[round(x,3) for x in fB.Center().toTuple()]}")

    # Find owning solid (STEP import is a compound of 3 solids)
    solids = base.Solids()
    owner = None
    for si, s in enumerate(solids):
        sf = s.Faces()
        hasA = any(ff.wrapped.IsSame(fA.wrapped) for ff in sf)
        hasB = any(ff.wrapped.IsSame(fB.wrapped) for ff in sf)
        if hasA and hasB:
            owner = s
            owner_index = si
            break
    if owner is None:
        owner = base
        owner_index = None
        print("WARNING: Could not isolate owning solid containing both faces; will operate on whole base shape.")
    else:
        print(f"Owning solid index in compound: {owner_index}")

    # Resolve the faces on the owner to keep topology consistent
    ofA = None
    ofB = None
    for ff in owner.Faces():
        if ff.wrapped.IsSame(fA.wrapped):
            ofA = ff
        if ff.wrapped.IsSame(fB.wrapped):
            ofB = ff
    if ofA is None or ofB is None:
        ofA, ofB = fA, fB
        print("WARNING: Could not re-resolve both faces on owner; using faces from base for edge matching.")

    # Shared edge between the two faces
    shared = []
    eAs = ofA.Edges()
    eBs = ofB.Edges()
    for e1 in eAs:
        for e2 in eBs:
            if e1.wrapped.IsSame(e2.wrapped):
                shared.append(e1)
                break

    print(f"Shared edge candidates between Face #{face_idx_a} and Face #{face_idx_b}: {len(shared)}")
    for i, e in enumerate(shared):
        c = e.Center().toTuple()
        print(f"  cand[{i}]: length={e.Length():.3f} center={[round(c[0],3), round(c[1],3), round(c[2],3)]}")

    if not shared:
        print("ERROR: No shared edge found; returning input unchanged (cannot apply edit).")
        return shape

    edge = max(shared, key=lambda ee: ee.Length())
    P = edge.positionAt(0.5)
    t = edge.tangentAt(0.5).normalized()
    L = edge.Length()
    print(f"Selected target edge: length={L:.3f} midpoint={[round(x,3) for x in P.toTuple()]} tangent={[round(x,3) for x in t.toTuple()]}")

    # Compute outward normals from the two adjacent planar faces at P
    n1 = ofA.normalAt(P).normalized()
    n2 = ofB.normalAt(P).normalized()
    print(f"Face normals at edge midpoint: n1={[round(x,4) for x in n1.toTuple()]}, n2={[round(x,4) for x in n2.toTuple()]}")

    # Convexity / bisector direction (outward) and axis point for the fillet cylinder
    b = (n1 + n2)
    if b.Length < 1e-9:
        print("ERROR: Face normals nearly opposite; cannot build bisector cylinder. Returning input unchanged.")
        return shape
    b = b.normalized()

    # Convexity probe
    eps = 0.2
    inside_minus = owner.isInside(P - b * eps)
    inside_plus = owner.isInside(P + b * eps)
    print(f"Convexity probe: isInside(P - b*eps)={inside_minus}, isInside(P + b*eps)={inside_plus} (expect True/False for convex external edge)")

    denom = b.dot(n1)
    if abs(denom) < 1e-9:
        print("ERROR: b.dot(n1) ~ 0; cannot locate cylinder axis. Returning input unchanged.")
        return shape

    C = P - b * (R / denom)  # cylinder axis point (centerline) for fillet
    print(f"Computed cylinder axis point C={[round(x,3) for x in C.toTuple()]}  (R={R} mm)")

    # Build two inward slabs of thickness R from each face, then their intersection gives the local corner chunk.
    def make_slab(outward_n):
        inward = (-outward_n).normalized()
        ref = cq.Vector(0, 0, 1)
        if abs(ref.dot(inward)) > 0.9:
            ref = cq.Vector(1, 0, 0)
        xdir = ref.cross(inward).normalized()
        pl = cq.Plane(P, xdir, inward)
        BIG = 2000.0
        return cq.Workplane(pl).rect(BIG, BIG).extrude(R).val()

    slab1 = make_slab(n1)
    slab2 = make_slab(n2)

    try:
        corner = slab1.intersect(slab2).intersect(owner)
    except Exception as ex:
        print(f"ERROR: Failed building corner intersection: {ex}")
        return shape

    try:
        corner_vol = corner.Volume() if hasattr(corner, "Volume") else 0.0
        print(f"Corner chunk: volume={corner_vol:.3f} mm^3")
    except Exception as ex:
        print(f"Corner chunk volume check failed: {ex}")

    # Cylinder along the edge direction, long enough to cover the full edge and naturally terminate at ends.
    extra = max(5.0, 2 * R)
    height = L + 2 * extra
    cyl_base = C - t * (height / 2.0)
    cylinder = cq.Solid.makeCylinder(R, height, cyl_base, t)

    # cutter = (corner chunk) minus (cylinder) ; removing cutter from owner produces the fillet
    try:
        cutter = corner.cut(cylinder)
    except Exception as ex:
        print(f"ERROR: Failed creating cutter (corner - cylinder): {ex}")
        return shape

    try:
        cutter_vol = cutter.Volume() if hasattr(cutter, "Volume") else 0.0
        print(f"Cutter chunk (to remove): volume={cutter_vol:.3f} mm^3")
    except Exception as ex:
        print(f"Cutter volume check failed: {ex}")

    # Apply cut
    try:
        out_owner = owner.cut(cutter)
    except Exception as ex:
        print(f"ERROR: Final cut failed: {ex}")
        return shape

    # Self-check: removed material and its location
    try:
        removed = owner.cut(out_owner)
        rem_vol = removed.Volume() if hasattr(removed, "Volume") else 0.0
        print(f"Removed material (owner - out_owner): vol={rem_vol:.3f} mm^3")
        if rem_vol > 1e-6:
            bb = removed.BoundingBox()
            cc = removed.Center().toTuple()
            print(f"  removed.Center={[round(x,3) for x in cc]}")
            print(f"  removed.BBox min={[round(bb.xmin,3), round(bb.ymin,3), round(bb.zmin,3)]} max={[round(bb.xmax,3), round(bb.ymax,3), round(bb.zmax,3)]}")
            pm = P.toTuple()
            print(f"  target edge midpoint P={[round(x,3) for x in pm]} delta_center={[round(cc[i]-pm[i],3) for i in range(3)]}")
        else:
            print("WARNING: Removed volume is ~0; edit may have failed (no-op).")
    except Exception as ex:
        print(f"Removed material check failed: {ex}")

    # Recombine with other solids unchanged if we isolated an owner solid
    if owner is base:
        return out_owner

    new_solids = []
    replaced = False
    for s in solids:
        if s.wrapped.IsSame(owner.wrapped):
            new_solids.append(out_owner)
            replaced = True
        else:
            new_solids.append(s)
    if not replaced:
        print("WARNING: Owner solid replacement failed; returning modified owner only.")
        return out_owner

    out = cq.Compound.makeCompound(new_solids)
    print(f"Output solids recombined: {len(new_solids)}")
    return out