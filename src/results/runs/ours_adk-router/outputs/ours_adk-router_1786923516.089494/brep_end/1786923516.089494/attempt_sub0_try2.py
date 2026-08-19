def my_cad_function(args):
    import cadquery as cq
    import math
    from OCP.BRepAdaptor import BRepAdaptor_Curve

    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input

    base = shape.val() if hasattr(shape, "val") else shape
    sols = base.Solids()
    if len(sols) != 1:
        print(f"WARNING: expected 1 solid, found {len(sols)}; editing solid[0] only")
    solid = sols[0]

    edges0 = solid.Edges()
    faces0 = solid.Faces()

    # --- Resolve/verify face #10 ---
    try:
        f10 = faces0[10]
        c10 = f10.Center()
        a10 = f10.Area()
        n10 = f10.normalAt()
        print(f"FACE#10: area={a10:.3f} center={[round(c10.x,3), round(c10.y,3), round(c10.z,3)]} normal={[round(n10.x,3), round(n10.y,3), round(n10.z,3)]}")
    except Exception as e:
        print(f"ERROR: could not resolve face #10: {e}")
        return shape

    # Named numbers / constraints
    fillet_r = 5.0
    z_plane = -340.0
    X0, X1 = 0.0, 100.0
    Y0, Y1 = 200.0, 320.0
    print("NAMED NUMBERS:")
    print(f"  z_plane={z_plane}")
    print(f"  region X={X0}..{X1}, Y={Y0}..{Y1}")
    print(f"  fillet radius r={fillet_r}")

    target_edge_idx = [39, 41, 43, 45]

    # Region limiter box (only to LIMIT removals, never to anchor)
    zmin_reg = z_plane - (fillet_r + 2.0)
    zmax_reg = z_plane + 0.2
    region_box = cq.Solid.makeBox(
        X1 - X0,
        Y1 - Y0,
        zmax_reg - zmin_reg,
        pnt=cq.Vector(X0, Y0, zmin_reg),
    )

    def _unwrap_angles(angles):
        """unwrap list of angles (rad) into a continuous sequence"""
        if not angles:
            return []
        out = [angles[0]]
        for a in angles[1:]:
            prev = out[-1]
            da = a - (prev % (2 * math.pi))
            while da > math.pi:
                da -= 2 * math.pi
            while da < -math.pi:
                da += 2 * math.pi
            out.append(prev + da)
        return out

    def _sector_solid(cx, cy, zmin, zmax, a0, a1, Rout, npts=60):
        # Build a pie-sector prism that covers angles a0..a1
        if a1 < a0:
            a0, a1 = a1, a0
        pts = [(cx, cy)]
        for i in range(npts + 1):
            a = a0 + (a1 - a0) * (i / npts)
            pts.append((cx + Rout * math.cos(a), cy + Rout * math.sin(a)))
        wp = cq.Workplane(cq.Plane(origin=(0, 0, zmin), normal=(0, 0, 1)))
        return wp.polyline(pts).close().extrude(zmax - zmin).val()

    def _edge_circle_params(e):
        ad = BRepAdaptor_Curve(e.wrapped)
        circ = ad.Circle()  # will throw if not circular
        loc = circ.Location()
        ax = circ.Axis().Direction()
        C = cq.Vector(loc.X(), loc.Y(), loc.Z())
        D = cq.Vector(ax.X(), ax.Y(), ax.Z()).normalized()
        R = circ.Radius()
        return C, D, R

    def _safe_is_inside(s, p):
        try:
            return bool(s.isInside(p))
        except Exception:
            # Fallback: if kernel doesn't support isInside reliably, assume False
            return False

    # Precompute edge descriptors from the ORIGINAL solid so topology changes won't break targeting
    edge_desc = []
    for i in target_edge_idx:
        if i < 0 or i >= len(edges0):
            print(f"EDGE#{i}: out of range 0..{len(edges0)-1}")
            continue
        e = edges0[i]
        bb = e.BoundingBox()
        ec = e.Center()
        on_z340 = (abs(bb.zmin - z_plane) < 1e-6 and abs(bb.zmax - z_plane) < 1e-6)
        in_region = (bb.xmax >= X0 and bb.xmin <= X1 and bb.ymax >= Y0 and bb.ymin <= Y1)
        print(
            f"EDGE#{i}: len={e.Length():.3f} center={[round(ec.x,3), round(ec.y,3), round(ec.z,3)]} "
            f"bb=({bb.xmin:.3f},{bb.ymin:.3f},{bb.zmin:.3f})-({bb.xmax:.3f},{bb.ymax:.3f},{bb.zmax:.3f}) "
            f"on_z340={on_z340} in_regionXY={in_region}"
        )
        if not (on_z340 and in_region):
            continue

        # Circle params
        try:
            C, D, R = _edge_circle_params(e)
        except Exception as ex:
            print(f"EDGE#{i}: NOT circular (cannot use torus/sphere approach): {ex}")
            continue

        # Edge angle span around circle center (assumes circle lies in XY plane with ~Z axis)
        # Sample points along edge to robustly determine angular range
        samples = [k / 32 for k in range(33)]
        angs = []
        for t in samples:
            p = e.positionAt(t)
            angs.append(math.atan2(p.y - C.y, p.x - C.x))
        angs_u = _unwrap_angles(angs)
        a0, a1 = angs_u[0], angs_u[-1]
        sweep_deg = abs((a1 - a0) * 180.0 / math.pi)

        edge_desc.append(
            {
                "idx": i,
                "C": C,
                "D": D,
                "R": float(R),
                "a0": float(a0),
                "a1": float(a1),
                "sweep_deg": float(sweep_deg),
                "bb": bb,
                "pm": e.positionAt(0.5),
            }
        )

    print(f"SELECTED: {len(edge_desc)} edges for boolean r=5.0 fillet on bulb perimeter @ face#10  idx={[d['idx'] for d in edge_desc]}")
    if len(edge_desc) == 0:
        print("SELECTED: 0 edges -> would be NO-OP; returning input unchanged")
        return shape

    # --- Apply boolean fillet approximation using torus/sphere keep-volume within a tight wedge band ---
    before_bb = solid.BoundingBox()
    before_vol = solid.Volume()

    out = solid
    total_removed = 0.0

    for k, d in enumerate(edge_desc):
        idx = d["idx"]
        C = d["C"]
        D = d["D"]
        R = d["R"]
        a0 = d["a0"]
        a1 = d["a1"]
        pm = d["pm"]

        print(
            f"EDGE#{idx} params: circle_center={[round(C.x,3), round(C.y,3), round(C.z,3)]} "
            f"axis={[round(D.x,3), round(D.y,3), round(D.z,3)]} R={R:.4f} "
            f"sweep~{d['sweep_deg']:.2f}deg"
        )

        # We expect circle in plane z=-340 with ~Z axis
        if abs(abs(D.z) - 1.0) > 1e-2:
            print(f"SKIP EDGE#{idx}: circle axis not ~Z (|Dz|={abs(D.z):.3f})")
            continue

        # Determine direction into material from the top plane (assume face#10 outward normal +Z)
        z_out = cq.Vector(0, 0, 1)
        # Probe slightly below/above z_plane at edge midpoint
        p_below = cq.Vector(pm.x, pm.y, pm.z) - z_out * 0.5
        p_above = cq.Vector(pm.x, pm.y, pm.z) + z_out * 0.5
        inside_below = _safe_is_inside(out, p_below)
        inside_above = _safe_is_inside(out, p_above)
        into_z = -z_out if inside_below and not inside_above else (-z_out if inside_below else z_out)
        print(f"  into_z={['{:.3f}'.format(into_z.x),'{:.3f}'.format(into_z.y),'{:.3f}'.format(into_z.z)]}  probe_inside_below={inside_below} probe_inside_above={inside_above}")

        # Determine which side of the circle is solid: towards center or away from center
        radial = cq.Vector(pm.x - C.x, pm.y - C.y, 0)
        if radial.Length < 1e-6:
            print(f"SKIP EDGE#{idx}: radial vector too small")
            continue
        radial = radial.normalized()
        p_toward_center = cq.Vector(pm.x, pm.y, pm.z) - radial * 0.5
        p_away_center = cq.Vector(pm.x, pm.y, pm.z) + radial * 0.5
        inside_toward = _safe_is_inside(out, p_toward_center)
        inside_away = _safe_is_inside(out, p_away_center)
        # If ambiguous, default to inside-toward (common for external corner rounds)
        solid_toward_center = inside_toward or (not inside_away)
        print(f"  radial_dir={[round(radial.x,3), round(radial.y,3), round(radial.z,3)]}  inside_toward_center={inside_toward} inside_away_center={inside_away} => solid_toward_center={solid_toward_center}")

        r = fillet_r
        # Band radii for the affected wedge volume (within r of the cylinder surface)
        if solid_toward_center:
            outerR = R
            innerR = max(0.0, R - r)
        else:
            outerR = R + r
            innerR = R

        # z slab that limits the change to within r of the top plane, into the material
        z_top = z_plane + 0.1
        z_bot = z_plane - (r + 0.6)
        if into_z.z > 0:
            # material is above plane (unlikely for this part), flip slab
            z_bot, z_top = z_plane - 0.1, z_plane + (r + 0.6)

        # Sector prism to limit angular range (also helps avoid affecting other circular features)
        Rout = outerR + 3.0 * r + 2.0
        sector = _sector_solid(C.x, C.y, z_bot - 2.0, z_top + 2.0, a0, a1, Rout, npts=80)

        # Cylinder shell representing within-r band near the cylindrical side surface
        height = (z_top - z_bot) + 4.0
        cyl_base = cq.Vector(C.x, C.y, z_bot - 2.0)
        outer_cyl = cq.Solid.makeCylinder(outerR, height, cyl_base, cq.Vector(0, 0, 1))
        if innerR > 1e-6:
            inner_cyl = cq.Solid.makeCylinder(innerR, height, cyl_base, cq.Vector(0, 0, 1))
            shell = outer_cyl.cut(inner_cyl)
        else:
            shell = outer_cyl

        # Slab near the top plane
        slab = cq.Solid.makeBox(
            2 * Rout,
            2 * Rout,
            (z_top - z_bot) + 4.0,
            pnt=cq.Vector(C.x - Rout, C.y - Rout, z_bot - 2.0),
        )

        # Wedge region potentially affected by fillet
        wedge = shell.intersect(slab).intersect(sector)

        # Keep volume: torus (or sphere for R<=r) representing the desired fillet envelope
        # For the canonical cylinder-plane fillet, torus center is at z_plane - r, major radius = R - r (for solid inside cylinder)
        z0 = z_plane - r
        if solid_toward_center:
            major = max(0.0, R - r)
        else:
            # if solid is outside the cylinder, the fillet centerline is at radius R + r
            major = R + r

        if major <= 1e-6:
            keep = cq.Solid.makeSphere(r, pnt=cq.Vector(C.x, C.y, z0))
        else:
            keep = cq.Solid.makeTorus(major, r, pnt=cq.Vector(C.x, C.y, z0), dir=cq.Vector(0, 0, 1))

        keep = keep.intersect(slab).intersect(sector)

        # Remove only the part of wedge that lies OUTSIDE the keep volume
        to_remove = wedge.cut(keep)
        # Limit to requested XY bulb region and vicinity of top plane
        to_remove = to_remove.intersect(region_box)
        # Ensure we only remove what actually intersects the current solid
        to_remove = to_remove.intersect(out)

        v_w = wedge.Volume() if wedge else 0.0
        v_k = keep.Volume() if keep else 0.0
        v_r = to_remove.Volume() if to_remove else 0.0
        print(f"  V(wedge)={v_w:.3f}  V(keep)={v_k:.3f}  V(remove)={v_r:.3f}")
        if v_r <= 1e-6:
            print(f"  NOTE: EDGE#{idx} produced ~0 removal; skipping cut")
            continue

        out2 = out.cut(to_remove)
        dv = out.Volume() - out2.Volume()
        total_removed += dv
        # Where did it come off?
        removed_shape = out.cut(out2)
        rc = removed_shape.Center()
        print(
            f"  CUT EDGE#{idx}: removed dV={dv:.3f}  removed.Center={[round(rc.x,3), round(rc.y,3), round(rc.z,3)]}  "
            f"ref_edge_mid={[round(pm.x,3), round(pm.y,3), round(pm.z,3)]}"
        )
        out = out2

    after_bb = out.BoundingBox()
    after_vol = out.Volume()

    print(
        "BBOX BEFORE: "
        f"min={[round(before_bb.xmin,3), round(before_bb.ymin,3), round(before_bb.zmin,3)]} "
        f"max={[round(before_bb.xmax,3), round(before_bb.ymax,3), round(before_bb.zmax,3)]}"
    )
    print(
        "BBOX AFTER : "
        f"min={[round(after_bb.xmin,3), round(after_bb.ymin,3), round(after_bb.zmin,3)]} "
        f"max={[round(after_bb.xmax,3), round(after_bb.ymax,3), round(after_bb.zmax,3)]}"
    )
    print(
        "BBOX DELTA : "
        f"dmin={[round(after_bb.xmin-before_bb.xmin,6), round(after_bb.ymin-before_bb.ymin,6), round(after_bb.zmin-before_bb.zmin,6)]} "
        f"dmax={[round(after_bb.xmax-before_bb.xmax,6), round(after_bb.ymax-before_bb.ymax,6), round(after_bb.zmax-before_bb.zmax,6)]}"
    )
    print(f"VOLUME BEFORE={before_vol:.3f}  AFTER={after_vol:.3f}  dV={before_vol-after_vol:.3f}  total_removed_accum={total_removed:.3f}")

    return out