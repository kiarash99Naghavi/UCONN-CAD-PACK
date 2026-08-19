def my_cad_function(args):
    import cadquery as cq
    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input

    base = shape.val() if hasattr(shape, "val") else shape

    # --- Resolve and print the referenced faces to verify indices match the provided geometry index ---
    try:
        f23 = base.Faces()[23]
        c23 = f23.Center()
        n23 = f23.normalAt()  # IMPORTANT: no (u,v)
        print("face#23 center=", (round(c23.x, 3), round(c23.y, 3), round(c23.z, 3)),
              " normal=", (round(n23.x, 6), round(n23.y, 6), round(n23.z, 6)),
              " area=", round(f23.Area(), 3))
    except Exception as e:
        print("Failed to resolve face#23:", e)
        f23 = None

    try:
        f52 = base.Faces()[52]
        c52 = f52.Center()
        n52 = f52.normalAt()  # IMPORTANT: no (u,v)
        print("face#52 center=", (round(c52.x, 3), round(c52.y, 3), round(c52.z, 3)),
              " normal=", (round(n52.x, 6), round(n52.y, 6), round(n52.z, 6)),
              " area=", round(f52.Area(), 3))
    except Exception as e:
        print("Failed to resolve face#52:", e)
        f52 = None

    # --- Named numbers from the sub-goal ---
    r = 9.0
    d = 18.0
    slot_center_x, slot_center_y = 113.0, 55.0
    z_floor = -10.0
    z_exit = -42.0
    end_offset = 15.0  # y=40 and 70 -> centered at 55
    depth_nominal = z_exit - z_floor  # negative
    print("Named numbers: r=", r, " d=", d,
          " slot_center=", (slot_center_x, slot_center_y),
          " z_floor=", z_floor, " z_exit=", z_exit,
          " depth_nominal=", depth_nominal)

    # Build cutter on absolute plane z=-10 (normal +Z), cut direction is -Z.
    overlap = 0.5
    cut_depth = abs(depth_nominal) + overlap  # positive distance to extrude in -Z

    plane = cq.Plane(origin=(0.0, 0.0, z_floor), normal=(0.0, 0.0, 1.0))
    print("Sketch plane origin:", plane.origin.toTuple(), " normal:", plane.zDir.toTuple())

    # Existing slot outline: explicit obround made from 2 semicircles (r=9) and 2 tangent lines
    # Local coordinates (after centering to (113,55)):
    #   top circle center (0, +15), bottom circle center (0, -15)
    # Start at rightmost point of top circle, go CCW around top, down left side, around bottom, up right side.
    cutter_wp = (
        cq.Workplane(plane)
        .center(slot_center_x, slot_center_y)
        .moveTo(r, end_offset)
        .radiusArc((-r, end_offset), r)
        .lineTo(-r, -end_offset)
        .radiusArc((r, -end_offset), r)
        .lineTo(r, end_offset)
        .close()
        .extrude(-cut_depth)
    )

    cutter = cutter_wp.val()
    bb = cutter.BoundingBox()
    cc = cutter.Center()
    print("Cutter center:", (round(cc.x, 3), round(cc.y, 3), round(cc.z, 3)))
    print("Cutter bbox zmax (should be ~-10):", round(bb.zmax, 3), " dz=", round(bb.zmax - z_floor, 3))
    print("Cutter bbox zmin (should be <= -42):", round(bb.zmin, 3), " vs z_exit=", z_exit, " delta=", round(bb.zmin - z_exit, 3))

    # Apply cut
    result = base.cut(cutter)

    # Self-check: isolate removed material
    removed = base.cut(result)
    rbb = removed.BoundingBox()
    rc = removed.Center()
    print("Removed center:", (round(rc.x, 3), round(rc.y, 3), round(rc.z, 3)))
    print("Removed bbox z range:", (round(rbb.zmin, 3), round(rbb.zmax, 3)), " expected approx [<=-42, >=-10]")

    # Sanity-check: look for any remaining planar stop-faces around z=-10 and z=-32 with area ~794 (the old slot floor faces)
    def count_planar_near(solid, z_target, nz_sign, area_target=794.469, z_tol=0.2, area_tol=5.0):
        cnt = 0
        for f in solid.Faces():
            try:
                if f.geomType() != "PLANE":
                    continue
                c = f.Center()
                if abs(c.z - z_target) > z_tol:
                    continue
                n = f.normalAt()  # IMPORTANT: no (u,v)
                if nz_sign > 0 and n.z < 0.9:
                    continue
                if nz_sign < 0 and n.z > -0.9:
                    continue
                if abs(f.Area() - area_target) > area_tol:
                    continue
                cnt += 1
            except Exception:
                pass
        return cnt

    cnt_zm10 = count_planar_near(result, -10.0, nz_sign=+1)
    cnt_zm32 = count_planar_near(result, -32.0, nz_sign=-1)
    print("Post-cut planar stop-face matches near z=-10 (n~+Z, area~794):", cnt_zm10)
    print("Post-cut planar stop-face matches near z=-32 (n~-Z, area~794):", cnt_zm32)

    return result