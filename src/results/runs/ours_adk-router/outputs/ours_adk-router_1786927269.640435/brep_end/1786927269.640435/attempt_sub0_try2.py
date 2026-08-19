def my_cad_function(args):
    import cadquery as cq
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.GeomAbs import GeomAbs_Circle

    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input
    base = shape.val() if hasattr(shape, "val") else shape

    print(f"INPUT: solids={len(base.Solids())} faces={len(base.Faces())} edges={len(base.Edges())}")

    # --- Helpers (true circle centers for partial arcs) ---
    def circle_center_radius(edge):
        c = BRepAdaptor_Curve(edge.wrapped)
        if c.GetType() != GeomAbs_Circle:
            return None
        circ = c.Circle()
        loc = circ.Location()
        return (float(loc.X()), float(loc.Y()), float(loc.Z()), float(circ.Radius()))

    def measure_outputs(sol, floor_z_target=-120.0, arc_center_z_target=-125.0, jaw_x_target=10.0):
        faces = sol.Faces()
        edges = sol.Edges()

        # Floor face (planar, normal ~ -Z, center.z near target)
        floor_cands = []
        for i, f in enumerate(faces):
            if f.geomType() != "PLANE":
                continue
            try:
                n = f.normalAt()
            except Exception:
                continue
            if abs(n.x) < 1e-3 and abs(n.y) < 1e-3 and n.z < -0.999:
                c = f.Center()
                if abs(c.z - floor_z_target) < 1.0:
                    floor_cands.append((f.Area(), i, c.z, c.x, c.y))
        floor_cands.sort(reverse=True, key=lambda t: t[0])
        if floor_cands:
            area, idx, zc, xc, yc = floor_cands[0]
            floor_z = zc
            print(f"SELECTED: {len(floor_cands)} planar -Z faces near z={floor_z_target} for jaw floor; taking idx={idx} area={area:.3f} center=({xc:.3f},{yc:.3f},{zc:.3f})")
        else:
            floor_z = None
            print(f"SELECTED: 0 planar -Z faces near z={floor_z_target} for jaw floor  <-- BUG")

        # Corner arc circle centers (r=5) near (±5,*,arc_center_z_target)
        corner_centers = []
        for ei, e in enumerate(edges):
            cr = circle_center_radius(e)
            if cr is None:
                continue
            x, y, z, r = cr
            if abs(r - 5.0) > 0.05:
                continue
            if abs(abs(x) - 5.0) < 0.6 and abs(z - arc_center_z_target) < 0.6 and (abs(y - 0.0) < 0.35 or abs(y - 15.0) < 0.35):
                corner_centers.append((round(x, 3), round(y, 3), round(z, 3)))
        # de-dup
        cc_uniq = []
        seen = set()
        for p in corner_centers:
            if p not in seen:
                seen.add(p)
                cc_uniq.append(p)
        print(f"SELECTED: {len(cc_uniq)} r=5 circle-edge centers near (±5,*,{arc_center_z_target}) for throat corners")
        if cc_uniq:
            pos = [p for p in cc_uniq if p[0] > 0]
            neg = [p for p in cc_uniq if p[0] < 0]
            print(f"  corner_centers(+x)={pos}")
            print(f"  corner_centers(-x)={neg}")

        # Jaw faces: planar, normal ~ ±X, near x=±10
        jaw_cands = []
        for i, f in enumerate(faces):
            if f.geomType() != "PLANE":
                continue
            try:
                n = f.normalAt()
            except Exception:
                continue
            if abs(abs(n.x) - 1.0) < 1e-3 and abs(n.y) < 1e-3 and abs(n.z) < 1e-3:
                c = f.Center()
                # Focus on the jaw region in Z to avoid handle side faces
                if c.z < -115.0 and abs(abs(c.x) - jaw_x_target) < 1.0 and f.Area() > 200.0:
                    jaw_cands.append((c.x, i, f.Area(), c.z))
        jaw_cands.sort(key=lambda t: t[0])
        if len(jaw_cands) >= 2:
            left_x, left_i, left_a, left_z = jaw_cands[0]
            right_x, right_i, right_a, right_z = jaw_cands[-1]
            spacing = right_x - left_x
            print(f"SELECTED: {len(jaw_cands)} planar ±X faces in jaw region near x=±{jaw_x_target}; using idx=[{left_i},{right_i}] x=[{left_x:.6f},{right_x:.6f}] spacing={spacing:.6f}")
        else:
            spacing = None
            print(f"SELECTED: {len(jaw_cands)} planar ±X faces in jaw region near x=±{jaw_x_target}  <-- BUG")

        # Any remaining planar face near z=-110 in the jaw region (should be gone)
        z110 = []
        for i, f in enumerate(faces):
            if f.geomType() != "PLANE":
                continue
            c = f.Center()
            if abs(c.z - (-110.0)) < 0.25 and abs(c.x) < 15.0 and 0.0 <= c.y <= 15.0:
                z110.append(i)
        print(f"SELECTED: {len(z110)} planar faces with center.z≈-110 in jaw region (should be 0). idx={z110}")

        def fmt(v):
            return "None" if v is None else f"{v:.6f}"

        print(f"ACHIEVED: floor_z={fmt(floor_z)} target=-120.000000 delta={(floor_z + 120.0) if floor_z is not None else 'None'}")
        print(f"ACHIEVED: jaw_spacing={fmt(spacing)} target=20.000000 delta={(spacing - 20.0) if spacing is not None else 'None'}")

        return floor_z, cc_uniq, spacing, z110

    # --- Build the correct jaw cutout tool (absolute coordinates) ---
    def build_jaw_tool(floor_z=-120.0, arc_center_z=-125.0, z_mouth=-155.0):
        # quarter-circle midpoints for radius=5 arcs
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
            .translate((0.0, -0.5, 0.0))  # cover Y: -0.5..15.5
        )
        return tool

    # --- Rebuild the jaw region: fill (to add material back in), then re-cut correct U jaw ---
    target_floor_z = -120.0
    target_arc_center_z = -125.0
    target_spacing = 20.0

    out = base
    # Two passes: second pass enlarges the fill block if any residual over-wide cavity remains
    for it in range(2):
        plug_x = 26.0 + 10.0 * it   # must cover prior wrong cavity width reaching x=±11
        plug_y = 15.0
        plug_zmin = -150.0
        plug_zmax = -108.0          # extends above the old z=-110 throat remnants to eliminate them
        plug_h = plug_zmax - plug_zmin
        plug_center = (0.0, 7.5, (plug_zmin + plug_zmax) / 2.0)

        plug = (
            cq.Workplane(cq.Plane.XY())
            .box(plug_x, plug_y, plug_h, centered=(True, True, True))
            .val()
            .translate(plug_center)
        )

        tool = build_jaw_tool(floor_z=target_floor_z, arc_center_z=target_arc_center_z, z_mouth=-155.0)

        print(f"\nEDIT ITER {it}: plug box x={plug_x:.3f} y={plug_y:.3f} z[{plug_zmin:.3f},{plug_zmax:.3f}] center={plug_center}")
        bbp = plug.BoundingBox()
        print(f"  plug bbox x[{bbp.xmin:.3f},{bbp.xmax:.3f}] y[{bbp.ymin:.3f},{bbp.ymax:.3f}] z[{bbp.zmin:.3f},{bbp.zmax:.3f}]")
        bbt = tool.BoundingBox()
        print(f"  tool bbox x[{bbt.xmin:.3f},{bbt.xmax:.3f}] y[{bbt.ymin:.3f},{bbt.ymax:.3f}] z[{bbt.zmin:.3f},{bbt.zmax:.3f}] (jaw faces must be x=±10)")

        filled = base.fuse(plug)
        out = filled.cut(tool)

        # Self-check isolate change
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

        floor_z_meas, cc_uniq, spacing, z110 = measure_outputs(out, target_floor_z, target_arc_center_z, 10.0)

        # Evaluate against requirements
        ok = True
        if floor_z_meas is None or abs(floor_z_meas - target_floor_z) > 0.05:
            ok = False
        if spacing is None or abs(spacing - target_spacing) > 0.05:
            ok = False
        has_pos = any(p[0] > 0 and abs(p[2] - target_arc_center_z) < 0.2 for p in cc_uniq)
        has_neg = any(p[0] < 0 and abs(p[2] - target_arc_center_z) < 0.2 for p in cc_uniq)
        if not (has_pos and has_neg):
            ok = False
        if len(z110) > 0:
            ok = False

        if ok:
            print("SELF-CHECK: PASS (floor z=-120, corner centers near z=-125 at x=±5, jaw spacing=20, and no z=-110 throat plane remnants)")
            break
        else:
            print("SELF-CHECK: FAIL -> will retry with larger plug to ensure the over-wide cavity is fully filled before re-cut")

    return out
"
}