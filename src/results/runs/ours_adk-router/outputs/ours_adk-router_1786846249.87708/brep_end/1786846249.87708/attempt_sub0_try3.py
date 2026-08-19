def my_cad_function(args):
    import cadquery as cq
    import math

    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input
    base = shape.val() if hasattr(shape, "val") else shape

    # --- helpers ---
    def bb_str(bb):
        return f"([{bb.xmin:.3f},{bb.ymin:.3f},{bb.zmin:.3f}]..[{bb.xmax:.3f},{bb.ymax:.3f},{bb.zmax:.3f}])"

    def v3(v):
        return [round(v.x, 3), round(v.y, 3), round(v.z, 3)]

    def dir_parallel_y(d, tol=1e-3):
        # d can be cq.Vector or gp_Dir-like
        try:
            dx, dy, dz = float(d.x), float(d.y), float(d.z)
        except Exception:
            dx, dy, dz = float(d.X()), float(d.Y()), float(d.Z())
        # parallel to +/-Y
        return abs(abs(dy) - 1.0) < tol and abs(dx) < tol and abs(dz) < tol

    def circle_geom_center(edge):
        """True circle center for a CIRCLE edge, including partial arcs."""
        from OCP.BRepAdaptor import BRepAdaptor_Curve
        from OCP.GeomAbs import GeomAbs_Circle
        c = BRepAdaptor_Curve(edge.wrapped)
        if c.GetType() != GeomAbs_Circle:
            return None
        circ = c.Circle()
        p = circ.Location()
        return cq.Vector(p.X(), p.Y(), p.Z())

    def cyl_axis_xz(face):
        from OCP.BRepAdaptor import BRepAdaptor_Surface
        from OCP.GeomAbs import GeomAbs_Cylinder
        s = BRepAdaptor_Surface(face.wrapped)
        if s.GetType() != GeomAbs_Cylinder:
            return None
        cyl = s.Cylinder()
        ax = cyl.Axis()
        d = ax.Direction()
        if not dir_parallel_y(d, tol=2e-3):
            return None
        p = ax.Location()
        return float(p.X()), float(p.Z()), float(cyl.Radius())

    def uniq_pts_xz(pts, tol=0.25):
        out = []
        for (x, z) in pts:
            found = False
            for (xx, zz) in out:
                if math.hypot(x - xx, z - zz) <= tol:
                    found = True
                    break
            if not found:
                out.append((x, z))
        return out

    def probe_perimeter_inside(solid, x, z, y_outer, y_inner, r=2.5, y_off=0.25, n=12):
        """Return (ok, n_fail_outer, n_fail_inner). ok=True means all samples are inside solid."""
        fails_outer = 0
        fails_inner = 0
        for i in range(n):
            ang = (2.0 * math.pi) * (i / n)
            px = x + (r - 0.05) * math.cos(ang)
            pz = z + (r - 0.05) * math.sin(ang)
            # sample slightly inside each face
            p_outer = cq.Vector(px, y_outer - y_off, pz)
            p_inner = cq.Vector(px, y_inner + y_off, pz)
            try:
                if not solid.isInside(p_outer, 1e-6):
                    fails_outer += 1
                if not solid.isInside(p_inner, 1e-6):
                    fails_inner += 1
            except Exception:
                # If isInside isn't available in this build, be conservative and treat as failure
                return (False, n, n)
        return (fails_outer == 0 and fails_inner == 0, fails_outer, fails_inner)

    def build_cyl_tool(points_xz, y_min, y_max, r, margin=3.0):
        h = (y_max - y_min) + 2.0 * margin
        y0 = y_min - margin
        cyls = []
        for (x, z) in points_xz:
            cyls.append(cq.Solid.makeCylinder(r, h, cq.Vector(x, y0, z), cq.Vector(0, 1, 0)))
        return cq.Compound.makeCompound(cyls) if cyls else None

    def mouth_coverage_ratio(solid, x, z, y_plane, r=2.5, tol_xyz=0.6):
        """Sum arc-length coverage of all r circle edges at a given plane near (x,z)."""
        target = cq.Vector(x, y_plane, z)
        total_len = 0.0
        for e in solid.Edges():
            try:
                if e.geomType() != "CIRCLE":
                    continue
                if abs(e.radius() - r) > 0.08:
                    continue
                c = circle_geom_center(e)
                if c is None:
                    continue
                if abs(c.y - y_plane) > tol_xyz:
                    continue
                if math.hypot(c.x - x, c.z - z) > tol_xyz:
                    continue
                total_len += float(e.Length())
            except Exception:
                continue
        full = 2.0 * math.pi * r
        return total_len / full if full > 0 else 0.0

    # --- targets from prompt ---
    x_list = [-34.757, -9.7685, 22.7315]
    z_list = [-10.746, 0.504, 11.754]
    z_center = 0.504

    print("TARGETS:")
    print(f"  x_list={x_list}")
    print(f"  z_list={z_list}")
    print(f"  shift rule: +/-5mm in Z toward z_center={z_center}")

    solids = list(base.Solids())
    print(f"Imported solids: {len(solids)}")
    if len(solids) < 6:
        print("ERROR: Expected at least 6 solids to access #4 and #5")
        return shape

    # Platforms are specified as solids #4 and #5
    platforms = [(4, solids[4]), (5, solids[5])]

    new_solids = solids[:]

    for (si, s) in platforms:
        bb = s.BoundingBox()
        y_min, y_max = bb.ymin, bb.ymax
        y_mid = 0.5 * (y_min + y_max)
        print("\n--- PLATFORM ---")
        print(f"Solid#{si} bbox={bb_str(bb)}")
        print(f"  y_inner=y_min={y_min:.3f}, y_outer=y_max={y_max:.3f}, y_mid={y_mid:.3f}")

        # 1) Find existing r=2.5 cylindrical faces (Y-axis) near the 3x3 grid (incl. +/-5 Z variants)
        candidate_axes = []
        for f in s.Faces():
            try:
                if f.geomType() != "CYLINDER":
                    continue
                ax = cyl_axis_xz(f)
                if ax is None:
                    continue
                x_ax, z_ax, r_ax = ax
                if abs(r_ax - 2.5) > 0.08:
                    continue

                # near any of our grid (allow +/-5 Z from each z target)
                near_x = min(abs(x_ax - xt) for xt in x_list) < 1.5
                if not near_x:
                    continue
                near_z = False
                for zt in z_list:
                    if abs(z_ax - zt) < 1.5:
                        near_z = True
                    if abs(z_ax - (zt + (5.0 if zt < z_center else (-5.0 if zt > z_center else 0.0)))) < 1.5:
                        near_z = True
                if not near_z:
                    continue

                candidate_axes.append((x_ax, z_ax))
            except Exception:
                continue

        candidate_axes = uniq_pts_xz(candidate_axes, tol=0.35)
        print(f"  Detected existing Y-axis r~2.5 hole axes near grid: {len(candidate_axes)}")
        for (x_ax, z_ax) in sorted(candidate_axes):
            print(f"    axis approx at (x,z)=({x_ax:.3f},{z_ax:.3f})")

        # 2) Plug those existing pattern holes (so we can rebuild cleanly)
        plug_r = 2.55
        plugs = build_cyl_tool(candidate_axes, y_min, y_max, plug_r, margin=3.0)
        if plugs is None:
            print("  WARNING: no plugs created (could not detect existing pattern holes); proceeding to cut anyway")
            s_filled = s
        else:
            try:
                s_filled = s.union(plugs)
                added = s_filled.cut(s)
                print(f"  Plug added volume={added.Volume():.3f} mm^3  added_bbox={bb_str(added.BoundingBox())}")
            except Exception as ex:
                print(f"  WARNING: plug union failed: {ex}; proceeding without fill")
                s_filled = s

        # 3) Decide final hole centers using perimeter-in-solid probing; shift by +/-5 in Z toward z_center if needed
        final_pts = []
        shifts = 0
        for x in x_list:
            for z0 in z_list:
                ok0, fo0, fi0 = probe_perimeter_inside(s_filled, x, z0, y_max, y_min, r=2.5)
                z_use = z0
                if not ok0:
                    # primary mandated shift direction toward z_center
                    if abs(z0 - z_center) < 1e-6:
                        # center row: try +5 then -5 (still toward center is undefined)
                        z_cand = [z0 + 5.0, z0 - 5.0]
                    else:
                        dz = -5.0 if z0 > z_center else 5.0
                        z_cand = [z0 + dz]
                    picked = False
                    for z1 in z_cand:
                        ok1, fo1, fi1 = probe_perimeter_inside(s_filled, x, z1, y_max, y_min, r=2.5)
                        if ok1:
                            z_use = z1
                            shifts += 1
                            picked = True
                            print(f"  shift: (x,z)=({x:.3f},{z0:.3f}) -> z={z_use:.3f}  (probe failed outer/inner={fo0}/{fi0})")
                            break
                    if not picked:
                        # keep original but log it; will still cut (best effort)
                        print(f"  WARNING: probe failed and shift did not resolve for (x,z)=({x:.3f},{z0:.3f}); keeping z={z0:.3f}  (probe failed outer/inner={fo0}/{fi0})")
                final_pts.append((x, z_use))

        print(f"  Final hole points: {len(final_pts)} (shifted={shifts})")
        for (x, z) in final_pts:
            print(f"    (x,y_mid,z)=({x:.3f},{y_mid:.3f},{z:.3f})")

        # 4) Re-cut clean through holes
        cut_tool = build_cyl_tool(final_pts, y_min, y_max, 2.5, margin=3.0)
        if cut_tool is None:
            print("  ERROR: no cut tool created")
            s_new = s
        else:
            s_new = s_filled.cut(cut_tool)

        # 5) QA checks
        try:
            removed = s_filled.cut(s_new)
            bbrem = removed.BoundingBox()
            print(f"  Removed volume (platform#{si})={removed.Volume():.3f} mm^3  removed_bbox={bb_str(bbrem)}")
            print(f"  Removed y_range=[{bbrem.ymin:.3f},{bbrem.ymax:.3f}] vs platform [{y_min:.3f},{y_max:.3f}]")
        except Exception as ex:
            print(f"  WARNING: could not compute removed volume for platform#{si}: {ex}")

        # Verify full circular mouths at both planes for each intended hole
        print("  Mouth coverage check (sum arc length / full circumference) at y_min and y_max:")
        for (x, z) in final_pts:
            cov_out = mouth_coverage_ratio(s_new, x, z, y_max, r=2.5)
            cov_in = mouth_coverage_ratio(s_new, x, z, y_min, r=2.5)
            print(f"    (x,z)=({x:.3f},{z:.3f})  cov@y_max={cov_out:.3f}  cov@y_min={cov_in:.3f}")

        new_solids[si] = s_new

    out = cq.Compound.makeCompound(new_solids)

    # global removed vs incoming
    try:
        removed_total = base.cut(out)
        bb = removed_total.BoundingBox()
        print("\nTOTAL REMOVED (base - out):")
        print(f"  volume={removed_total.Volume():.3f} mm^3")
        print(f"  bbox={bb_str(bb)}")
        print(f"  center={v3(removed_total.Center())}")
    except Exception as ex:
        print(f"WARNING: could not compute total removed: {ex}")

    return out