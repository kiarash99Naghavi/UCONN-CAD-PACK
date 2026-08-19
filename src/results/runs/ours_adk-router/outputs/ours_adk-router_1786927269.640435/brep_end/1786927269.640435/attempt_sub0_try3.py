def my_cad_function(args):
    import cadquery as cq
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.GeomAbs import GeomAbs_Circle

    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input
    base = shape.val() if hasattr(shape, "val") else shape

    print("--- INPUT SUMMARY ---")
    print(f"INPUT: solids={len(base.Solids())} faces={len(base.Faces())} edges={len(base.Edges())}")
    bb0 = base.BoundingBox()
    print(f"INPUT BBOX: x[{bb0.xmin:.3f},{bb0.xmax:.3f}] y[{bb0.ymin:.3f},{bb0.ymax:.3f}] z[{bb0.zmin:.3f},{bb0.zmax:.3f}]")

    def circle_center_radius(edge):
        try:
            c = BRepAdaptor_Curve(edge.wrapped)
            if c.GetType() != GeomAbs_Circle:
                return None
            circ = c.Circle()
            loc = circ.Location()
            return (float(loc.X()), float(loc.Y()), float(loc.Z()), float(circ.Radius()))
        except Exception:
            return None

    def uniq_pts(pts, nd=3):
        seen = set()
        out = []
        for p in pts:
            k = tuple(round(v, nd) for v in p)
            if k not in seen:
                seen.add(k)
                out.append(k)
        return out

    def preselect_report(sol):
        faces = sol.Faces()
        edges = sol.Edges()

        # Existing r=5 edges around jaw throat corners (currently near z=-115 in the provided index)
        jaw_r5 = []
        jaw_r5_idx = []
        for ei, e in enumerate(edges):
            cr = circle_center_radius(e)
            if not cr:
                continue
            x, y, z, r = cr
            if abs(r - 5.0) > 0.05:
                continue
            if abs(abs(x) - 5.0) < 0.75 and z < -90.0 and z > -140.0 and (abs(y - 0.0) < 0.5 or abs(y - 15.0) < 0.5):
                jaw_r5.append((x, y, z))
                jaw_r5_idx.append(ei)
        jaw_r5u = uniq_pts(jaw_r5, nd=3)
        print(f"SELECTED: {len(jaw_r5u)} r=5 circle edges near jaw-corner zone (|x|≈5, z∈[-140,-90]) idx(sample)={jaw_r5_idx[:12]}")
        if jaw_r5u:
            print(f"  matched circle centers (unique)={jaw_r5u}")

        # Existing jaw planar side faces (try to catch wrong ±11 ones if present)
        jaw_planes = []
        for i, f in enumerate(faces):
            if f.geomType() != "PLANE":
                continue
            n = f.normalAt()
            c = f.Center()
            if abs(abs(n.x) - 1.0) < 1e-3 and abs(n.y) < 1e-3 and abs(n.z) < 1e-3:
                if c.z < -120.0 and c.z > -150.5 and abs(c.x) > 8.0 and abs(c.x) < 14.0 and f.Area() > 200.0:
                    jaw_planes.append((c.x, i, f.Area(), c.z))
        jaw_planes.sort(key=lambda t: t[0])
        print(f"SELECTED: {len(jaw_planes)} planar ±X faces in jaw depth zone (possible jaw side walls)")
        if jaw_planes:
            print("  candidates (x, idx, area, cz) = " + ", ".join([f"({x:.3f},{i},{a:.1f},{cz:.1f})" for x, i, a, cz in jaw_planes[:8]]))

        # Any plane at z≈-110 in jaw region (should be eliminated as a cavity floor remnant)
        z110 = []
        for i, f in enumerate(faces):
            if f.geomType() != "PLANE":
                continue
            n = f.normalAt()
            c = f.Center()
            if n.z < -0.999 and abs(c.z - (-110.0)) < 0.35 and abs(c.x) < 12.0 and 0.0 <= c.y <= 15.0:
                z110.append(i)
        print(f"SELECTED: {len(z110)} planar -Z faces with center.z≈-110 in jaw region (should be 0 after fix). idx={z110}")

    def measure_outputs(sol, floor_z_target=-120.0, arc_center_z_target=-125.0, jaw_x_target=10.0):
        faces = sol.Faces()
        edges = sol.Edges()

        # Floor face: planar, normal ~ -Z, and near target z, with X extent roughly 10mm
        floor_cands = []
        for i, f in enumerate(faces):
            if f.geomType() != "PLANE":
                continue
            n = f.normalAt()
            if n.z > -0.999:
                continue
            c = f.Center()
            if c.z < -100.0 and c.z > -140.0 and abs(c.z - floor_z_target) < 4.0 and abs(c.x) < 10.0 and 0.0 <= c.y <= 15.0:
                bb = f.BoundingBox()
                # Jaw floor should be ~10mm wide in X and 15mm in Y; be permissive
                floor_cands.append((abs(c.z - floor_z_target), f.Area(), i, c, (bb.xlen, bb.ylen, bb.zlen)))
        floor_cands.sort(key=lambda t: (t[0], -t[1]))
        if floor_cands:
            dz, area, idx, c, dims = floor_cands[0]
            print(f"SELECTED: {len(floor_cands)} planar -Z faces near z={floor_z_target} for floor; taking idx={idx} center=({c.x:.3f},{c.y:.3f},{c.z:.3f}) area={area:.3f} dims(x,y,z)=({dims[0]:.3f},{dims[1]:.3f},{dims[2]:.6f})")
            floor_z = c.z
        else:
            print(f"SELECTED: 0 planar -Z faces near z={floor_z_target} for floor  <-- BUG")
            floor_z = None

        # Corner arc centers: r=5 circle edges with centers near x=±5 and z near target
        corner = []
        for ei, e in enumerate(edges):
            cr = circle_center_radius(e)
            if not cr:
                continue
            x, y, z, r = cr
            if abs(r - 5.0) > 0.05:
                continue
            if abs(abs(x) - 5.0) < 0.35 and abs(z - arc_center_z_target) < 0.35 and (abs(y - 0.0) < 0.5 or abs(y - 15.0) < 0.5):
                corner.append((x, y, z))
        corner_u = uniq_pts(corner, nd=3)
        print(f"SELECTED: {len(corner_u)} r=5 circle edges near corner centers (x≈±5, z≈{arc_center_z_target})")
        if corner_u:
            print(f"  corner circle centers found={corner_u}")

        # Jaw spacing: planar faces normal ~ ±X in jaw depth zone
        jaw_cands = []
        for i, f in enumerate(faces):
            if f.geomType() != "PLANE":
                continue
            n = f.normalAt()
            if abs(abs(n.x) - 1.0) > 1e-3 or abs(n.y) > 1e-3 or abs(n.z) > 1e-3:
                continue
            c = f.Center()
            # focus on jaw region
            if c.z < -120.0 and c.z > -150.5 and f.Area() > 250.0 and abs(c.x) > 8.0 and abs(c.x) < 14.0:
                jaw_cands.append((c.x, i, f.Area(), c.z))
        jaw_cands.sort(key=lambda t: t[0])
        if len(jaw_cands) >= 2:
            left_x, left_i, left_a, left_cz = jaw_cands[0]
            right_x, right_i, right_a, right_cz = jaw_cands[-1]
            spacing = right_x - left_x
            print(f"SELECTED: {len(jaw_cands)} planar ±X faces in jaw zone; using idx=[{left_i},{right_i}] x=[{left_x:.6f},{right_x:.6f}] spacing={spacing:.6f}")
        else:
            spacing = None
            left_x = right_x = None
            print(f"SELECTED: {len(jaw_cands)} planar ±X faces in jaw zone  <-- BUG")

        # Any remaining planar face near z=-110 in jaw region (should be gone)
        z110 = []
        for i, f in enumerate(faces):
            if f.geomType() != "PLANE":
                continue
            n = f.normalAt()
            c = f.Center()
            if n.z < -0.999 and abs(c.z - (-110.0)) < 0.35 and abs(c.x) < 12.0 and 0.0 <= c.y <= 15.0:
                z110.append(i)
        print(f"SELECTED: {len(z110)} planar -Z faces with center.z≈-110 in jaw region (should be 0). idx={z110}")

        def fmt(v):
            return "None" if v is None else f"{v:.6f}"

        print(f"ACHIEVED: floor_z={fmt(floor_z)} target=-120.000000 delta={(floor_z - floor_z_target) if floor_z is not None else 'None'}")
        if corner_u:
            pos = [p for p in corner_u if p[0] > 0]
            neg = [p for p in corner_u if p[0] < 0]
            print(f"ACHIEVED: corner_centers(+x)={pos} target approx (5,*,{arc_center_z_target})")
            print(f"ACHIEVED: corner_centers(-x)={neg} target approx (-5,*,{arc_center_z_target})")
        else:
            print("ACHIEVED: corner_centers=NONE (expected r=5 arc edge centers)")
        print(f"ACHIEVED: jaw_spacing={fmt(spacing)} target=20.000000 delta={(spacing - 20.0) if spacing is not None else 'None'}")

        return floor_z, corner_u, spacing, z110

    def build_jaw_tool(floor_z=-120.0, arc_center_z=-125.0, z_mouth=-155.0):
        # quarter-circle midpoints for radius=5 arcs
        r = 5.0
        mid_dx = r * (2 ** 0.5) / 2.0
        mid_dz = r * (2 ** 0.5) / 2.0
        mid_r = (5.0 + mid_dx, arc_center_z + mid_dz)
        mid_l = (-5.0 - mid_dx, arc_center_z + mid_dz)

        wp_xz = cq.Workplane(cq.Plane(origin=(0, 0, 0), normal=(0, 1, 0), xDir=(1, 0, 0)))
        tool = (
            wp_xz
            .moveTo(-10.0, z_mouth)
            .lineTo(10.0, z_mouth)
            .lineTo(10.0, arc_center_z)
            .threePointArc(mid_r, (5.0, floor_z))
            .lineTo(-5.0, floor_z)
            .threePointArc(mid_l, (-10.0, arc_center_z))
            .lineTo(-10.0, z_mouth)
            .close()
            .extrude(16.0)
            .val()
            .translate((0.0, -0.5, 0.0))  # cover Y: -0.5..15.5 robustly
        )
        return tool

    # --- REPORT WHAT WE MATCHED BEFORE MODIFYING ---
    print("\n--- PRESELECTION ON CURRENT (FLAWED) GEOMETRY ---")
    preselect_report(base)

    # --- TARGETS ---
    target_floor_z = -120.0
    target_arc_center_z = -125.0
    target_spacing = 20.0

    # Build the correct jaw tool once
    tool = build_jaw_tool(floor_z=target_floor_z, arc_center_z=target_arc_center_z, z_mouth=-155.0)
    bbt = tool.BoundingBox()
    print("\n--- TOOL (new correct jaw cutout) ---")
    print(f"TOOL bbox: x[{bbt.xmin:.3f},{bbt.xmax:.3f}] y[{bbt.ymin:.3f},{bbt.ymax:.3f}] z[{bbt.zmin:.3f},{bbt.zmax:.3f}] (jaw faces intended at x=±10, floor z=-120, arc centers z=-125)")

    out = base
    # Iteratively enlarge the fill plug if any remnants (e.g. over-wide ±11 walls) survive
    for it in range(3):
        plug_x = 42.0 + 12.0 * it   # must cover prior wrong cavity width reaching x≈±11
        plug_y = 15.0
        plug_zmin = -150.0
        plug_zmax = -104.0          # extends above -110 remnants but stays well below other features (~-96)
        plug_h = plug_zmax - plug_zmin
        plug_center = (0.0, 7.5, (plug_zmin + plug_zmax) / 2.0)

        plug = (
            cq.Workplane(cq.Plane.XY())
            .box(plug_x, plug_y, plug_h, centered=(True, True, True))
            .val()
            .translate(plug_center)
        )

        print(f"\nEDIT ITER {it}: plug box x={plug_x:.3f} y={plug_y:.3f} z[{plug_zmin:.3f},{plug_zmax:.3f}] center={plug_center}")
        bbp = plug.BoundingBox()
        print(f"  PLUG bbox x[{bbp.xmin:.3f},{bbp.xmax:.3f}] y[{bbp.ymin:.3f},{bbp.ymax:.3f}] z[{bbp.zmin:.3f},{bbp.zmax:.3f}]")

        filled = base.fuse(plug)
        out = filled.cut(tool)

        # Change isolation
        try:
            added = out.cut(base)
            bb = added.BoundingBox()
            cc = added.Center()
            print(
                f"ADDED (out.cut(base)): center=({cc.x:.3f},{cc.y:.3f},{cc.z:.3f}) "
                f"bbox x[{bb.xmin:.3f},{bb.xmax:.3f}] y[{bb.ymin:.3f},{bb.ymax:.3f}] z[{bb.zmin:.3f},{bb.zmax:.3f}]"
            )
        except Exception as e:
            print(f"ADDED isolation failed: {e}")
        try:
            removed = base.cut(out)
            bb = removed.BoundingBox()
            cc = removed.Center()
            print(
                f"REMOVED (base.cut(out)): center=({cc.x:.3f},{cc.y:.3f},{cc.z:.3f}) "
                f"bbox x[{bb.xmin:.3f},{bb.xmax:.3f}] y[{bb.ymin:.3f},{bb.ymax:.3f}] z[{bb.zmin:.3f},{bb.zmax:.3f}]"
            )
        except Exception as e:
            print(f"REMOVED isolation failed: {e}")

        # Required self-check numbers
        floor_z_meas, corner_u, spacing, z110 = measure_outputs(
            out,
            floor_z_target=target_floor_z,
            arc_center_z_target=target_arc_center_z,
            jaw_x_target=10.0,
        )

        has_pos = any(p[0] > 0 and abs(p[2] - target_arc_center_z) < 0.35 for p in corner_u)
        has_neg = any(p[0] < 0 and abs(p[2] - target_arc_center_z) < 0.35 for p in corner_u)

        ok = True
        if floor_z_meas is None or abs(floor_z_meas - target_floor_z) > 0.05:
            ok = False
        if spacing is None or abs(spacing - target_spacing) > 0.05:
            ok = False
        if not (has_pos and has_neg):
            ok = False
        if len(z110) > 0:
            ok = False

        if ok:
            print("SELF-CHECK: PASS (floor z=-120, r=5 corner centers near z=-125 at x=±5, jaw spacing=20, and no z=-110 throat remnants)")
            break
        else:
            print("SELF-CHECK: FAIL -> retrying with larger plug to fully overwrite any remaining over-wide / mispositioned cavity surfaces before re-cut")

    # Final sanity print
    bb1 = out.BoundingBox()
    print("\n--- OUTPUT SUMMARY ---")
    print(f"OUTPUT: solids={len(out.Solids())} faces={len(out.Faces())} edges={len(out.Edges())}")
    print(f"OUTPUT BBOX: x[{bb1.xmin:.3f},{bb1.xmax:.3f}] y[{bb1.ymin:.3f},{bb1.ymax:.3f}] z[{bb1.zmin:.3f},{bb1.zmax:.3f}]")

    return out