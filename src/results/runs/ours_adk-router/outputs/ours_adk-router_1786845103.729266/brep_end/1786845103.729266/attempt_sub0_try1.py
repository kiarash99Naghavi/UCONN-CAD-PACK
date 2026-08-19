def my_cad_function(args):
    import cadquery as cq
    from OCP.gp import gp_Vec
    from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism

    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input
    base = shape.val() if hasattr(shape, "val") else shape

    # --- Named targets (from prompt) ---
    z_old = 432.0
    z_new = 482.0
    dz = z_new - z_old  # 50.0
    axis_x, axis_y = -149.0, 202.0
    face_end_idx = 529
    face_outer_cyl_idx = 528
    face_inner_cyl_idx = 530

    print("TARGETS:")
    print(f"  lever axis through x={axis_x}, y={axis_y}")
    print(f"  move end cap from z={z_old} to z={z_new} (dz={dz})")
    print(f"  face indices: endcap#{face_end_idx}, cyl_outer#{face_outer_cyl_idx} (r=14.62), cyl_other#{face_inner_cyl_idx} (r=7.07)")

    faces = base.Faces()
    f_end = faces[face_end_idx]
    f_outer = faces[face_outer_cyl_idx]
    f_inner = faces[face_inner_cyl_idx]

    def _pnt(v):
        return [round(v.x, 3), round(v.y, 3), round(v.z, 3)]

    print("RESOLVED FACE CHECKS (should match geometry index):")
    print(f"  face#{face_end_idx} center={_pnt(f_end.Center())} area={round(f_end.Area(), 3)}")
    print(f"  face#{face_outer_cyl_idx} center={_pnt(f_outer.Center())} area={round(f_outer.Area(), 3)}")
    print(f"  face#{face_inner_cyl_idx} center={_pnt(f_inner.Center())} area={round(f_inner.Area(), 3)}")

    # Find which solid owns the end cap face
    solids = list(base.Solids())
    owner_idx = None
    for si, s in enumerate(solids):
        for ff in s.Faces():
            if ff.wrapped.IsSame(f_end.wrapped):
                owner_idx = si
                break
        if owner_idx is not None:
            break
    print(f"Owning solid for face#{face_end_idx}: {owner_idx} (of {len(solids)} solids)")
    if owner_idx is None:
        print("ERROR: could not find a solid containing the target end-cap face; returning original shape")
        return shape

    lever_solid = solids[owner_idx]

    # Optional adjacency sanity: check if f_end shares an edge with the referenced cylinders
    def _shares_edge(fa, fb):
        ea = fa.Edges()
        eb = fb.Edges()
        for e1 in ea:
            for e2 in eb:
                if e1.wrapped.IsSame(e2.wrapped):
                    return True
        return False

    print("Adjacency (shared-edge) checks:")
    print(f"  endcap#{face_end_idx} shares edge with outer_cyl#{face_outer_cyl_idx}: {_shares_edge(f_end, f_outer)}")
    print(f"  endcap#{face_end_idx} shares edge with other_cyl#{face_inner_cyl_idx}: {_shares_edge(f_end, f_inner)}")

    # Build the extension by extruding the existing end-cap face +Z by 50mm.
    # This preserves coaxial alignment and any inner loops in the face (if present).
    prism_shape = BRepPrimAPI_MakePrism(f_end.wrapped, gp_Vec(0.0, 0.0, float(dz))).Shape()
    ext = cq.Shape(prism_shape)

    # Fuse extension into the lever solid only
    new_lever = lever_solid.fuse(ext)

    # Rebuild overall compound with the modified solid in place
    new_solids = []
    for i, s in enumerate(solids):
        new_solids.append(new_lever if i == owner_idx else s)
    out = cq.Compound.makeCompound(new_solids)

    # --- Placement / added-material self-check ---
    try:
        added = out.cut(base)
        bb = added.BoundingBox()
        c = added.Center()
        print("ADDED MATERIAL CHECK:")
        print(f"  added center = {_pnt(c)}")
        print(f"  added bbox zmin={round(bb.zmin, 3)} zmax={round(bb.zmax, 3)} (target zmax={z_new})")
        print(f"  zmax delta = {round(bb.zmax - z_new, 3)}")
    except Exception as e:
        print("ADDED MATERIAL CHECK failed:", e)

    # Also print new overall bbox max Z to confirm the front moved to ~482
    bb_out = out.BoundingBox()
    print(f"OUT bbox zmax={round(bb_out.zmax, 3)} (expected ~{z_new})")

    return out