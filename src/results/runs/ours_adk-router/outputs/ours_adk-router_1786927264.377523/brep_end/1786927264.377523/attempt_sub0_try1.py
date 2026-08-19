def my_cad_function(args):
    import cadquery as cq

    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input
    base = shape.val() if hasattr(shape, "val") else shape

    sols = base.Solids()
    print(f"INFO: imported solids={len(sols)}")
    if len(sols) < 1:
        print("SELECTED: 0 solids (ERROR)")
        return shape

    solid = sols[0]
    bb0 = solid.BoundingBox()
    print(f"INFO: base bbox zmin={bb0.zmin} zmax={bb0.zmax}")

    faces = solid.Faces()
    print(f"INFO: solid faces={len(faces)}")

    # Resolve by face index per instructions
    try:
        f12 = faces[12]
        f34 = faces[34]
    except Exception as e:
        print(f"ERROR: could not access face indices 12 and/or 34: {e}")
        return shape

    c12, c34 = f12.Center(), f34.Center()
    print(
        "INFO: resolved face #12 "
        f"area={f12.Area():.3f} center=({c12.x:.3f},{c12.y:.3f},{c12.z:.3f}) normal={f12.normalAt()}"
    )
    print(
        "INFO: resolved face #34 "
        f"area={f34.Area():.3f} center=({c34.x:.3f},{c34.y:.3f},{c34.z:.3f}) normal={f34.normalAt()}"
    )
    print("SELECTED: 2 faces for Z-level reference/correction   idx=[12, 34]")

    # Numbers named by the sub-goal
    z_from = -450.0
    z_to = -445.0
    dz = z_to - z_from  # +5.0mm
    print(f"INFO: target move face #34 from Z={z_from} to Z={z_to} (dz={dz})")

    # Build a cut prism by extruding face #34 upward +Z by 5mm (removes the 5mm protrusion)
    cut_vec = cq.Vector(0, 0, dz)
    try:
        tool = cq.Solid.extrudeLinear(f34, cut_vec)
        print("SELECTED: 1 solid tool for protrusion removal (extruded from face #34)")
    except Exception as e:
        print(f"ERROR: failed to extrude face #34 into cut tool: {e}
")
        return shape

    # Apply cut to only the owning solid
    try:
        edited = solid.cut(tool)
    except Exception as e:
        print(f"ERROR: boolean cut failed: {e}")
        return shape

    # Self-check: isolate removed material
    removed = solid.cut(edited)
    rbb = removed.BoundingBox()
    ebb = edited.BoundingBox()

    print(
        "CHECK: removed bbox "
        f"zmin={rbb.zmin:.3f} zmax={rbb.zmax:.3f} (expected approx {z_from}..{z_to})"
    )
    print(
        "CHECK: edited bbox "
        f"zmin={ebb.zmin:.3f} zmax={ebb.zmax:.3f} (expected zmin={z_to})"
    )
    print(f"CHECK: zmin delta vs target = {ebb.zmin - z_to:.6f} mm")

    # Recompound if multi-solid (keep others untouched)
    if len(sols) == 1:
        return edited

    out = cq.Compound.makeCompound([s for i, s in enumerate(sols) if i != 0] + [edited])
    return out