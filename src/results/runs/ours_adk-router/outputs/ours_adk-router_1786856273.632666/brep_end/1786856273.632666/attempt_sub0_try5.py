def my_cad_function(args):
    import cadquery as cq

    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input
    base = shape.val() if hasattr(shape, "val") else shape

    # --- Verify we are on the expected input ---
    solids = base.Solids()
    print("Imported solids:", len(solids))
    if len(solids) < 2:
        print("ERROR: expected 2 solids; returning unmodified shape")
        return shape

    s0 = solids[0]
    s1 = solids[1]
    bb0 = s0.BoundingBox()
    bb1 = s1.BoundingBox()
    print("SOLID#0 vol=", s0.Volume(), "bbox=", (bb0.xmin, bb0.ymin, bb0.zmin, bb0.xmax, bb0.ymax, bb0.zmax))
    print("SOLID#1 vol=", s1.Volume(), "bbox=", (bb1.xmin, bb1.ymin, bb1.zmin, bb1.xmax, bb1.ymax, bb1.zmax))

    # Anchor checks against provided geometry index (global face indices)
    faces = base.Faces()
    if len(faces) > 2:
        f_front_small = faces[1]  # planar face at y=3.175 with area ~372.76
        c1 = f_front_small.Center()
        try:
            n1 = f_front_small.normalAt(c1)
        except Exception:
            n1 = None
        print("Face#1 (expected small front plane @ y=3.175) area=", f_front_small.Area(), "center=", (c1.x, c1.y, c1.z), "normal=", (n1.x, n1.y, n1.z) if n1 else None)

        f_outer_cyl = faces[2]  # cylindrical r=15.75 family count=1
        c2 = f_outer_cyl.Center()
        gt2 = None
        try:
            gt2 = f_outer_cyl.geomType()
        except Exception:
            pass
        print("Face#2 (expected outer cyl r=15.75) geomType=", gt2, "area=", f_outer_cyl.Area(), "center=", (c2.x, c2.y, c2.z))

    # --- Chamfer parameters (named explicitly in the sub-goal) ---
    y_bot = 2.175
    y_top = 3.175
    r_at_y_bot = 15.75
    r_at_y_top = 14.75
    print("Chamfer spec: y=", y_bot, "..", y_top, " cone radii=", r_at_y_bot, "@y_bot and", r_at_y_top, "@y_top")

    # --- Build an ANNULAR cutter: (large cylinder segment) - (coaxial frustum)
    # This yields only the outer wedge region, preventing unintended enlargement of the inner bore.
    eps = 0.01
    h = (y_top - y_bot) + 2 * eps
    p0 = cq.Vector(0, y_bot - eps, 0)
    axis = cq.Vector(0, 1, 0)

    cyl_r = 16.5  # slightly larger than r=15.75 to avoid coincident outer surfaces
    cyl = cq.Solid.makeCylinder(cyl_r, h, p0, axis)
    frustum = cq.Solid.makeCone(r_at_y_bot, r_at_y_top, h, p0, axis)
    cutter = cyl.cut(frustum)

    bbc = cutter.BoundingBox()
    print("Cutter bbox=", (bbc.xmin, bbc.ymin, bbc.zmin, bbc.xmax, bbc.ymax, bbc.zmax))
    print("Cutter y-extents vs target:",
          "ymin", bbc.ymin, "(delta", bbc.ymin - y_bot, ")",
          "ymax", bbc.ymax, "(delta", bbc.ymax - y_top, ")")

    # --- Apply ONLY to SOLID #0 ---
    s0_mod = s0.cut(cutter)

    # Self-check removed material location
    removed = s0.cut(s0_mod)
    try:
        bbr = removed.BoundingBox()
        cr = removed.Center()
        print("Removed vol=", removed.Volume())
        print("Removed center=", (cr.x, cr.y, cr.z))
        print("Removed bbox=", (bbr.xmin, bbr.ymin, bbr.zmin, bbr.xmax, bbr.ymax, bbr.zmax))
        print("Removed y-extents (should lie near y=2.175..3.175):", bbr.ymin, "..", bbr.ymax)
    except Exception as e:
        print("Removed-material diagnostics failed:", e)

    # Ensure SOLID #0 stayed as a single solid
    s0_mod_solids = s0_mod.Solids() if hasattr(s0_mod, "Solids") else []
    print("SOLID#0 modified solids count:", len(s0_mod_solids) if s0_mod_solids else "(n/a)")

    # Keep SOLID #1 byte-identical by reusing the original s1 object
    print("SOLID#1 (unchanged) vol check:", s1.Volume())

    out = cq.Compound.makeCompound([s0_mod, s1])
    return out