def my_cad_function(args):
    import cadquery as cq
    import math

    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_Cylinder, GeomAbs_Torus

    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input
    base = shape.val() if hasattr(shape, "val") else shape

    # --- Numbers explicitly named by the sub-goal ---
    named_centers_r10 = [
        (-226.62, -348.409, 583.515),
        (-173.62, -362.126, 532.32),
        (-173.62, -481.183, 87.995),
        (-226.62, -494.9, 36.8),
        (-886.62, -494.9, 36.8),
        (-939.62, -481.183, 87.995),
        (-939.62, -362.126, 532.32),
        (-886.62, -348.409, 583.515),
    ]
    target_bbox_min = (-949.62, -506.698, 26.8)
    target_bbox_max = (-163.62, -338.409, 595.312)
    print("NAMED: r10 loop corroboration centers:")
    for c in named_centers_r10:
        print("  ", c)
    print("NAMED: bbox min", target_bbox_min, "max", target_bbox_max)

    faces = base.Faces()
    edges = base.Edges()
    print(f"INFO: base solids={len(base.Solids())} faces={len(faces)} edges={len(edges)}")
    bb0 = base.BoundingBox()
    print("INFO: base bbox min=", (bb0.xmin, bb0.ymin, bb0.zmin), "max=", (bb0.xmax, bb0.ymax, bb0.zmax))

    # Indices from the provided geometry index
    cyl10_face_idx = [36, 38, 40, 42]
    tor10_face_idx_guess = [35, 37, 39, 41]  # the 4 largest torus faces listed

    # --- Helpers ---
    def v_from_gpdir(d):
        return cq.Vector(float(d.X()), float(d.Y()), float(d.Z()))

    def v_from_gppnt(p):
        return cq.Vector(float(p.X()), float(p.Y()), float(p.Z()))

    def face_adaptor(f):
        return BRepAdaptor_Surface(f.wrapped, True)

    def get_uv_bounds(ad):
        u1 = float(ad.FirstUParameter())
        u2 = float(ad.LastUParameter())
        v1 = float(ad.FirstVParameter())
        v2 = float(ad.LastVParameter())
        # unwrap if needed
        if u2 < u1:
            u2 += 2.0 * math.pi
        if v2 < v1:
            v2 += 2.0 * math.pi
        return u1, u2, v1, v2

    def sector_annulus_profile(wp, r_out, r_in, ang):
        # Sector in WP XY, centered at origin, starting at angle 0.
        # Uses threePointArc for stability.
        if ang <= 1e-6:
            raise ValueError("Angle too small")
        a2 = ang / 2.0
        p0o = (r_out, 0.0)
        pmo = (r_out * math.cos(a2), r_out * math.sin(a2))
        p1o = (r_out * math.cos(ang), r_out * math.sin(ang))
        p1i = (r_in * math.cos(ang), r_in * math.sin(ang))
        pmi = (r_in * math.cos(a2), r_in * math.sin(a2))
        p0i = (r_in, 0.0)
        return (
            wp.moveTo(*p0o)
              .threePointArc(pmo, p1o)
              .lineTo(*p1i)
              .threePointArc(pmi, p0i)
              .close()
        )

    def annular_sector_between_radii_on_cylinder(face, r_big=10.0, r_small=2.0):
        ad = face_adaptor(face)
        if ad.GetType() != GeomAbs_Cylinder:
            raise ValueError("Not a cylinder")
        cyl = ad.Cylinder()
        pos = cyl.Position()  # gp_Ax3
        loc = v_from_gppnt(pos.Location())
        xdir = v_from_gpdir(pos.XDirection())
        ydir = v_from_gpdir(pos.YDirection())
        zdir = v_from_gpdir(pos.Direction())

        u1, u2, v1, v2 = get_uv_bounds(ad)
        du = u2 - u1
        h = v2 - v1
        if h < 0:
            h = -h
            v1, v2 = v2, v1
            zdir = -zdir

        # rotate local xDir to align sector start with u1
        xrot = (xdir * math.cos(u1) + ydir * math.sin(u1)).normalized()

        origin = loc + zdir * v1
        pln = cq.Plane(origin=origin.toTuple(), normal=zdir.toTuple(), xDir=xrot.toTuple())
        wp = cq.Workplane(pln)
        prof = sector_annulus_profile(wp, r_big, r_small, du)
        solid = prof.extrude(h)
        return solid, {"u1": u1, "u2": u2, "v1": v1, "v2": v2, "du": du, "h": h}

    def annular_sector_between_minor_radii_on_torus(face, r_big=10.0, r_small=2.0):
        ad = face_adaptor(face)
        if ad.GetType() != GeomAbs_Torus:
            raise ValueError("Not a torus")
        tor = ad.Torus()
        pos = tor.Position()  # gp_Ax3
        loc = v_from_gppnt(pos.Location())
        xdir = v_from_gpdir(pos.XDirection())
        ydir = v_from_gpdir(pos.YDirection())
        zdir = v_from_gpdir(pos.Direction())
        R = float(tor.MajorRadius())
        rmin = float(tor.MinorRadius())

        u1, u2, v1, v2 = get_uv_bounds(ad)
        du = u2 - u1
        dv = v2 - v1

        # Meridian plane at U = u1: radial direction rotated in XY about axis
        radial = (xdir * math.cos(u1) + ydir * math.sin(u1)).normalized()
        # Plane contains axis (zdir) and radial; normal is perpendicular to both
        nrm = zdir.cross(radial).normalized()

        # In this plane: x axis = radial, y axis = axis direction
        pln = cq.Plane(origin=loc.toTuple(), normal=nrm.toTuple(), xDir=radial.toTuple())
        wp = cq.Workplane(pln)

        vm = (v1 + v2) * 0.5

        # Points are in (radial, axis) coordinates. Minor circle center at (R,0).
        p0o = (R + r_big * math.cos(v1), r_big * math.sin(v1))
        pmo = (R + r_big * math.cos(vm), r_big * math.sin(vm))
        p1o = (R + r_big * math.cos(v2), r_big * math.sin(v2))

        p1i = (R + r_small * math.cos(v2), r_small * math.sin(v2))
        pmi = (R + r_small * math.cos(vm), r_small * math.sin(vm))
        p0i = (R + r_small * math.cos(v1), r_small * math.sin(v1))

        prof = (
            wp.moveTo(*p0o)
              .threePointArc(pmo, p1o)
              .lineTo(*p1i)
              .threePointArc(pmi, p0i)
              .close()
        )

        axis_start = loc.toTuple()
        axis_end = (loc + zdir).toTuple()
        solid = prof.revolve(math.degrees(du), axis_start, axis_end)
        return solid, {"R": R, "rmin": rmin, "u1": u1, "u2": u2, "v1": v1, "v2": v2, "du": du, "dv": dv}

    # --- Resolve and validate target faces ---
    cyl_faces = []
    for i in cyl10_face_idx:
        f = faces[i]
        c = f.Center()
        a = f.Area()
        ad = face_adaptor(f)
        print(f"CHECK: cyl candidate face_idx={i} center={c.toTuple()} area={a:.3f} geomType={ad.GetType()}")
        if ad.GetType() == GeomAbs_Cylinder:
            cyl = ad.Cylinder()
            print(f"  -> cylinder r={float(cyl.Radius()):.6f}")
        cyl_faces.append(f)
    print(f"SELECTED: {len(cyl_faces)} faces for r=10 cylindrical blend members idx={cyl10_face_idx}")

    tor_faces = []
    for i in tor10_face_idx_guess:
        f = faces[i]
        c = f.Center()
        a = f.Area()
        ad = face_adaptor(f)
        print(f"CHECK: tor candidate face_idx={i} center={c.toTuple()} area={a:.3f} geomType={ad.GetType()}")
        if ad.GetType() == GeomAbs_Torus:
            tor = ad.Torus()
            print(f"  -> torus R={float(tor.MajorRadius()):.6f} r={float(tor.MinorRadius()):.6f}")
            tor_faces.append(f)
        else:
            print("  -> NOT a torus; skipping")
    print(f"SELECTED: {len(tor_faces)} faces for r=10 toroidal corner blend members idx={tor10_face_idx_guess}")

    if len(cyl_faces) != 4 or len(tor_faces) != 4:
        print("WARNING: Did not resolve expected 4 cylinder + 4 torus faces for r10 loop; no-op avoided by still attempting with what was found.")

    # --- Build 8 finite additive fillers ---
    fillers = []

    for k, f in enumerate(cyl_faces):
        try:
            solid, meta = annular_sector_between_radii_on_cylinder(f, 10.0, 2.0)
            fillers.append(solid)
            bb = solid.BoundingBox()
            print(
                f"BUILT: cyl filler {k} du(deg)={math.degrees(meta['du']):.3f} h={meta['h']:.3f} "
                f"bbox=(({bb.xmin:.3f},{bb.ymin:.3f},{bb.zmin:.3f})->({bb.xmax:.3f},{bb.ymax:.3f},{bb.zmax:.3f}))"
            )
        except Exception as e:
            print(f"FAILED: cyl filler {k} due to {e}")

    for k, f in enumerate(tor_faces):
        try:
            solid, meta = annular_sector_between_minor_radii_on_torus(f, 10.0, 2.0)
            fillers.append(solid)
            bb = solid.BoundingBox()
            print(
                f"BUILT: tor filler {k} du(deg)={math.degrees(meta['du']):.3f} dv(deg)={math.degrees(meta['dv']):.3f} "
                f"R={meta['R']:.3f} rmin={meta['rmin']:.3f} "
                f"bbox=(({bb.xmin:.3f},{bb.ymin:.3f},{bb.zmin:.3f})->({bb.xmax:.3f},{bb.ymax:.3f},{bb.zmax:.3f}))"
            )
        except Exception as e:
            print(f"FAILED: tor filler {k} due to {e}")

    print(f"SELECTED: {len(fillers)} additive filler solids for union (expected 8)")
    if len(fillers) == 0:
        print("SELECTED: 0 fillers => returning input unchanged (would be a no-op)")
        return shape

    # --- Boolean-union fillers with unchanged imported solid ---
    out = base
    for i, tool in enumerate(fillers):
        try:
            out = out.fuse(tool)
            print(f"FUSE: succeeded for filler {i}")
        except Exception as e:
            print(f"FUSE: FAILED for filler {i} due to {e}")

    # refine only coincident seams / splitters
    try:
        out = out.removeSplitter()
        print("REFINE: removeSplitter() applied")
    except Exception as e:
        print("REFINE: removeSplitter() failed:", e)

    # --- Placement self-check: isolate added material ---
    try:
        added = out.cut(base)
        bbA = added.BoundingBox()
        cA = added.Center()
        print("SELF-CHECK: added material center=", cA.toTuple())
        print(
            "SELF-CHECK: added bbox min=", (bbA.xmin, bbA.ymin, bbA.zmin),
            "max=", (bbA.xmax, bbA.ymax, bbA.zmax),
            "size=", (bbA.xlen, bbA.ylen, bbA.zlen),
        )
    except Exception as e:
        print("SELF-CHECK: could not compute added=out.cut(base):", e)

    # --- Validate bbox preservation ---
    bb1 = out.BoundingBox()
    out_min = (bb1.xmin, bb1.ymin, bb1.zmin)
    out_max = (bb1.xmax, bb1.ymax, bb1.zmax)
    print("CHECK: out bbox min=", out_min, "max=", out_max)
    print(
        "CHECK: bbox delta min=",
        (out_min[0] - target_bbox_min[0], out_min[1] - target_bbox_min[1], out_min[2] - target_bbox_min[2]),
        "delta max=",
        (out_max[0] - target_bbox_max[0], out_max[1] - target_bbox_max[1], out_max[2] - target_bbox_max[2]),
    )

    # --- Confirm one valid solid ---
    sols = out.Solids()
    print("CHECK: out solids=", len(sols))

    # --- Confirm no remaining r=10 cylindrical or toroidal faces on boundary ---
    rem_cyl10 = []
    rem_tor10 = []
    for fi, f in enumerate(out.Faces()):
        ad = face_adaptor(f)
        t = ad.GetType()
        if t == GeomAbs_Cylinder:
            cyl = ad.Cylinder()
            r = float(cyl.Radius())
            if abs(r - 10.0) < 0.05:
                # check if it's a 90-degree patch (blend-like)
                u1, u2, v1, v2 = get_uv_bounds(ad)
                if abs((u2 - u1) - (math.pi / 2.0)) < 0.05:
                    rem_cyl10.append(fi)
        elif t == GeomAbs_Torus:
            tor = ad.Torus()
            r = float(tor.MinorRadius())
            if abs(r - 10.0) < 0.05:
                # likely the corner torus family
                rem_tor10.append(fi)

    print(f"CHECK: remaining boundary r=10 cylindrical blend faces: {len(rem_cyl10)} idx={rem_cyl10}")
    print(f"CHECK: remaining boundary r=10 toroidal faces: {len(rem_tor10)} idx={rem_tor10}")

    return out