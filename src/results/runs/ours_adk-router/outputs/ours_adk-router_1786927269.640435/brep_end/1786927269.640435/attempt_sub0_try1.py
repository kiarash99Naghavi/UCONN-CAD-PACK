def my_cad_function(args):
    import cadquery as cq
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.GeomAbs import GeomAbs_Circle

    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input
    base = shape.val() if hasattr(shape, "val") else shape

    # --- Resolve and verify indexed faces from the provided geometry index ---
    faces = base.Faces()
    edges = base.Edges()
    print(f"INPUT: faces={len(faces)} edges={len(edges)}")

    def f_info(i):
        f = faces[i]
        c = f.Center()
        try:
            n = f.normalAt()
            n3 = (round(n.x, 6), round(n.y, 6), round(n.z, 6))
        except Exception:
            n3 = None
        a = f.Area()
        print(f"RESOLVED: face #{i} area={a:.3f} center=({c.x:.3f},{c.y:.3f},{c.z:.3f}) normal={n3}")
        return f

    # Named by sub-goal
    _f18 = f_info(18)  # floor of jaw cutout
    _f17 = f_info(17)  # corner cylinder
    _f19 = f_info(19)  # corner cylinder
    _f20 = f_info(20)  # jaw face x=-10 plane
    _f22 = f_info(22)  # jaw face x=+10 plane

    print("SELECTED: 1 face idx=[18] for reference jaw floor")
    print("SELECTED: 2 faces idx=[17,19] for reference corner cylinders")
    print("SELECTED: 2 faces idx=[20,22] for reference jaw spacing")

    # --- Helpers to measure circle centers/radii of edges (true circle centers) ---
    def circle_center_radius(edge):
        c = BRepAdaptor_Curve(edge.wrapped)
        if c.GetType() != GeomAbs_Circle:
            return None
        circ = c.Circle()
        loc = circ.Location()
        return (loc.X(), loc.Y(), loc.Z(), circ.Radius())

    def measure_outputs(sol, floor_z_target, arc_center_z_target, jaw_spacing_target):
        # Floor face: plane normal ~ -Z, z near target
        floor_candidates = []
        for i, f in enumerate(sol.Faces()):
            if f.geomType() != "PLANE":
                continue
            n = f.normalAt()
            c = f.Center()
            if abs(n.x) < 1e-3 and abs(n.y) < 1e-3 and n.z < -0.999:
                if abs(c.z - floor_z_target) < 2.0:
                    floor_candidates.append((f.Area(), i, f, c, n))
        floor_candidates.sort(reverse=True, key=lambda t: t[0])
        if floor_candidates:
            area, idx, f, c, n = floor_candidates[0]
            print(f"SELECTED: {len(floor_candidates)} planar -Z faces near z={floor_z_target} for new floor; taking idx={idx} area={area:.3f}")
            floor_z = c.z
        else:
            print(f"SELECTED: 0 planar -Z faces near z={floor_z_target} for new floor  <-- BUG")
            floor_z = None

        # Corner arc centers: find r=5 circular edges at x=±5, z near target
        corner_centers = []
        for ei, e in enumerate(sol.Edges()):
            cr = circle_center_radius(e)
            if cr is None:
                continue
            x, y, z, r = cr
            if abs(r - 5.0) > 0.05:
                continue
            if abs(abs(x) - 5.0) < 0.25 and abs(z - arc_center_z_target) < 0.25 and (abs(y - 0.0) < 0.25 or abs(y - 15.0) < 0.25):
                corner_centers.append((x, y, z))
        # de-dup with rounding
        cc_uniq = []
        seen = set()
        for x, y, z in corner_centers:
            key = (round(x, 3), round(y, 3), round(z, 3))
            if key not in seen:
                seen.add(key)
                cc_uniq.append(key)
        print(f"SELECTED: {len(cc_uniq)} r=5 circular edges near corner centers (±5,*,{arc_center_z_target})")
        if cc_uniq:
            print(f"  corner circle centers found={cc_uniq}")

        # Jaw spacing: planar faces normal ~ ±X at x≈±10
        jaw_faces = []
        for i, f in enumerate(sol.Faces()):
            if f.geomType() != "PLANE":
                continue
            n = f.normalAt()
            c = f.Center()
            if abs(abs(n.x) - 1.0) < 1e-3 and abs(n.y) < 1e-3 and abs(n.z) < 1e-3:
                if abs(abs(c.x) - 10.0) < 0.25:
                    jaw_faces.append((c.x, i, f, c, n))
        jaw_faces.sort(key=lambda t: t[0])
        if len(jaw_faces) >= 2:
            left_x = jaw_faces[0][0]
            right_x = jaw_faces[-1][0]
            spacing = right_x - left_x
            idxs = [jaw_faces[0][1], jaw_faces[-1][1]]
            print(f"SELECTED: {len(jaw_faces)} planar ±X faces near x=±10 for jaw; using idx={idxs} => spacing={spacing:.6f}")
        else:
            spacing = None
            print(f"SELECTED: {len(jaw_faces)} planar ±X faces near x=±10 for jaw  <-- BUG")

        # Print required achieved numbers
        def fmt(v):
            return "None" if v is None else f"{v:.6f}"
        print(f"ACHIEVED: floor_z={fmt(floor_z)} target=-120.000000 delta={(floor_z - floor_z_target) if floor_z is not None else 'None'}")
        if cc_uniq:
            # summarize x=+5 and x=-5 at either y
            pos = [p for p in cc_uniq if p[0] > 0]
            neg = [p for p in cc_uniq if p[0] < 0]
            print(f"ACHIEVED: corner_centers(+x)={pos} target approx (5,*,{arc_center_z_target})")
            print(f"ACHIEVED: corner_centers(-x)={neg} target approx (-5,*,{arc_center_z_target})")
        else:
            print("ACHIEVED: corner_centers=NONE (expected r=5 arc edge centers)")
        print(f"ACHIEVED: jaw_spacing={fmt(spacing)} target=20.000000 delta={(spacing - jaw_spacing_target) if spacing is not None else 'None'}")

        return floor_z, cc_uniq, spacing

    # --- Perform the edit: fill old throat region, then re-cut new shallower cutout with new fillets ---
    old_floor_z = -110.0
    target_floor_z = -120.0
    target_arc_center_z = -125.0
    target_jaw_spacing = 20.0

    # correction loop (should converge in 1 pass)
    adj = 0.0
    out = base
    for it in range(2):
        floor_z = target_floor_z + adj
        arc_center_z = floor_z - 5.0

        # Plug: overfill the jaw cutout throat region from arc_center_z .. old_floor_z
        plug_zmin = arc_center_z
        plug_zmax = old_floor_z
        plug_h = plug_zmax - plug_zmin
        plug_center = (0.0, 7.5, (plug_zmin + plug_zmax) / 2.0)
        plug = (
            cq.Workplane(cq.Plane.XY())
            .box(22.0, 15.0, plug_h, centered=(True, True, True))
            .val()
            .translate(plug_center)
        )

        # New cutout tool: U slot to z=floor_z with r=5 throat corners centered at z=arc_center_z
        # Profile in XZ on plane normal +Y at y=0; then extrude through thickness.
        z_mouth = -155.0  # extend beyond actual mouth at z=-150 for robust cut
        # arc mid point at 45deg for radius=5 quarter circle
        mid_dx = 5.0 * (2 ** 0.5) / 2.0
        mid_dz = 5.0 * (2 ** 0.5) / 2.0
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
            .translate((0.0, -0.5, 0.0))
        )

        print(f"\nEDIT ITER {it}: building plug/tool with floor_z={floor_z:.6f} arc_center_z={arc_center_z:.6f}")
        print(f"  plug: zmin={plug_zmin:.6f} zmax={plug_zmax:.6f} h={plug_h:.6f} center={plug_center}")
        print(f"  tool: mouth_z={z_mouth:.6f} floor_z={floor_z:.6f} arc_center_z={arc_center_z:.6f} (extrude Y -0.5..15.5)")

        filled = base.fuse(plug)
        out = filled.cut(tool)

        # Isolate added material for self-check
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

        floor_z_meas, cc_uniq, spacing = measure_outputs(out, target_floor_z, target_arc_center_z, target_jaw_spacing)

        # Correction if needed
        ok = True
        if floor_z_meas is None or abs(floor_z_meas - target_floor_z) > 0.1:
            ok = False
        if spacing is None or abs(spacing - target_jaw_spacing) > 0.05:
            ok = False
        # corner centers check (require at least one at +x and one at -x)
        has_pos = any(p[0] > 0 and abs(p[2] - target_arc_center_z) < 0.2 for p in cc_uniq)
        has_neg = any(p[0] < 0 and abs(p[2] - target_arc_center_z) < 0.2 for p in cc_uniq)
        if not (has_pos and has_neg):
            ok = False

        if ok:
            print("SELF-CHECK: PASS (floor z, corner centers, and jaw spacing within tolerance)")
            break

        # Adjust only based on floor measurement (dominant), retry
        if floor_z_meas is not None:
            delta = (target_floor_z - floor_z_meas)
            adj += delta
            print(f"SELF-CHECK: FAIL -> applying Z correction adj += {delta:.6f} (new adj={adj:.6f})")
        else:
            print("SELF-CHECK: FAIL -> floor not found; no correction applied")

    return out