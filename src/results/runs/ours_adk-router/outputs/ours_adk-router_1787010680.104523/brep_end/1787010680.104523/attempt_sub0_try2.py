def my_cad_function(args):
    import cadquery as cq
    from math import sqrt

    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input

    base = shape.val() if hasattr(shape, "val") else shape
    solids = base.Solids()
    print(f"INFO: imported solids={len(solids)}")
    if len(solids) != 1:
        print("ERROR: expected exactly 1 solid in input; aborting without changes")
        return shape

    solid = solids[0]
    print(f"INFO: base faces={len(solid.Faces())} edges={len(solid.Edges())}")

    # --- helpers ---
    def v(x, y=None, z=None):
        if isinstance(x, cq.Vector):
            return x
        if y is None and z is None and hasattr(x, "__len__"):
            return cq.Vector(float(x[0]), float(x[1]), float(x[2]))
        return cq.Vector(float(x), float(y), float(z))

    def vec_to_list(u):
        return [round(u.x, 6), round(u.y, 6), round(u.z, 6)]

    def make_aabb_from_bb(bb, pad=0.0):
        cx, cy, cz = bb.center.x, bb.center.y, bb.center.z
        sx, sy, sz = bb.xlen + 2 * pad, bb.ylen + 2 * pad, bb.zlen + 2 * pad
        box = cq.Solid.makeBox(sx, sy, sz, pnt=cq.Vector(-sx / 2, -sy / 2, -sz / 2))
        return box.translate(cq.Vector(cx, cy, cz))

    def make_bbox_box(shape_bb):
        sx, sy, sz = shape_bb.xlen, shape_bb.ylen, shape_bb.zlen
        cx, cy, cz = shape_bb.center.x, shape_bb.center.y, shape_bb.center.z
        box = cq.Solid.makeBox(sx, sy, sz, pnt=cq.Vector(-sx / 2, -sy / 2, -sz / 2))
        return box.translate(cq.Vector(cx, cy, cz))

    def safe_xdir_for_normal(n):
        n = n.normalized()
        ref = cq.Vector(1, 0, 0)
        if abs(n.dot(ref)) > 0.9:
            ref = cq.Vector(0, 1, 0)
        xd = ref.cross(n)
        if xd.Length < 1e-12:
            xd = cq.Vector(0, 0, 1).cross(n)
        return xd.normalized()

    def make_inward_slab(plane_point, outward_normal, thickness, size):
        # Half-space approximation: points within 'thickness' in the inward direction.
        # Outward normals are given by face.normalAt(), so inward is -outward.
        n_in = (-outward_normal).normalized()
        xd = safe_xdir_for_normal(n_in)
        pl = cq.Plane(origin=plane_point.toTuple(), normal=n_in.toTuple(), xDir=xd.toTuple())
        slab = cq.Workplane(pl).rect(size, size, centered=True).extrude(thickness, combine=False).val()
        return slab

    def point_on_intersection_line_of_two_planes(p1, n1, p2, n2, ref=None):
        # Planes: n1·x=c1 and n2·x=c2 where c1=n1·p1, c2=n2·p2
        n1u, n2u = n1.normalized(), n2.normalized()
        d = n1u.cross(n2u)
        d2 = d.dot(d)
        if d2 < 1e-18:
            raise ValueError("Planes nearly parallel; no stable intersection line")
        c1 = n1u.dot(p1)
        c2 = n2u.dot(p2)
        # x0 = ( c1*(n2×d) + c2*(d×n1) ) / |d|^2
        x0 = (n2u.cross(d) * c1 + d.cross(n1u) * c2) * (1.0 / d2)
        if ref is None:
            return x0, d.normalized()
        # closest point on line to ref
        t = d.dot(ref - x0) / d2
        return x0 + d * t, d.normalized()

    # --- constants from sub-goal ---
    r_old = 63.0
    r_new = 50.0
    axis_target = cq.Vector(0.0, 0.966, -0.259).normalized()
    exp_min = (-949.62, -506.698, 26.8)
    exp_max = (-163.62, -338.409, 595.312)

    print(f"INFO: target radii: old={r_old} new={r_new}")
    print(f"INFO: named axis_dir={vec_to_list(axis_target)}")
    print(f"INFO: expected bbox min={list(exp_min)} max={list(exp_max)}")

    # Face indices to modify, and the two adjacent planar faces that define each corner
    # (from the index / previous diagnostics)
    corner_specs = [
        (21, 22, 43),
        (23, 22, 24),
        (44, 43, 49),
        (48, 24, 49),
    ]

    faces0 = solid.Faces()
    planars0 = {i: faces0[i] for i in [22, 24, 43, 49]}

    green_faces = []
    for fi, p1i, p2i in corner_specs:
        f = faces0[fi]
        green_faces.append((fi, f, p1i, planars0[p1i], p2i, planars0[p2i]))
    print(f"SELECTED: {len(green_faces)} faces for GREEN R63 corner family  idx={[fi for fi, *_ in green_faces]}")

    base_bb = solid.BoundingBox()
    bbox_box = make_bbox_box(base_bb)
    print(
        "INFO: base bbox min="
        f"[{round(base_bb.xmin,3)},{round(base_bb.ymin,3)},{round(base_bb.zmin,3)}] max="
        f"[{round(base_bb.xmax,3)},{round(base_bb.ymax,3)},{round(base_bb.zmax,3)}]"
    )

    # --- main edit: for each of the four corners, add back (old_cut - new_cut) then cut new fillet ---
    # overlap eps to ensure fuse does not leave separate solids due to coincident boundaries
    overlap_eps = 0.3

    for (fi, gf, p1i, p1f, p2i, p2f) in green_faces:
        gcen = gf.Center()
        gbb = gf.BoundingBox()
        local = make_aabb_from_bb(gbb, pad=12.0)
        local = local.intersect(bbox_box)  # hard-stop: do not affect outside the original bbox

        print(
            f"INFO: corner face#{fi} center={[round(gcen.x,3),round(gcen.y,3),round(gcen.z,3)]} "
            f"local_bb=[{round(local.BoundingBox().xmin,3)},{round(local.BoundingBox().ymin,3)},{round(local.BoundingBox().zmin,3)}] "
            f"to [{round(local.BoundingBox().xmax,3)},{round(local.BoundingBox().ymax,3)},{round(local.BoundingBox().zmax,3)}]"
        )

        n1_out = p1f.normalAt().normalized()
        n2_out = p2f.normalAt().normalized()
        p1 = p1f.Center()
        p2 = p2f.Center()
        print(
            f"SELECTED: 2 planar faces for corner#{fi} definition  idx=[{p1i},{p2i}] "
            f"n1_out={vec_to_list(n1_out)} n2_out={vec_to_list(n2_out)}"
        )

        # Build inward slabs to clip the corner region to the correct quadrant
        try:
            slab_size = max(800.0, base_bb.xlen + base_bb.ylen + base_bb.zlen)
            slab_thk = 400.0
            slab1 = make_inward_slab(p1, n1_out, thickness=slab_thk, size=slab_size)
            slab2 = make_inward_slab(p2, n2_out, thickness=slab_thk, size=slab_size)
            wedge = slab1.intersect(slab2).intersect(local)
            wbb = wedge.BoundingBox()
            print(
                f"INFO: wedge for corner#{fi} bb=[{round(wbb.xmin,3)},{round(wbb.ymin,3)},{round(wbb.zmin,3)}] "
                f"to [{round(wbb.xmax,3)},{round(wbb.ymax,3)},{round(wbb.zmax,3)}]"
            )
        except Exception as e:
            print(f"ERROR: failed building wedge for corner#{fi}: {e}")
            continue

        # Compute axis lines for old and new radii from offset planes
        try:
            # Offset plane points inward by radius: p_off = p - n_out * r
            p1_old = p1 - n1_out * r_old
            p2_old = p2 - n2_out * r_old
            axis_old_pt, d_old = point_on_intersection_line_of_two_planes(p1_old, n1_out, p2_old, n2_out, ref=gcen)

            p1_new = p1 - n1_out * r_new
            p2_new = p2 - n2_out * r_new
            axis_new_pt, d_new = point_on_intersection_line_of_two_planes(p1_new, n1_out, p2_new, n2_out, ref=gcen)

            # sanity: direction should align to named family axis (sign irrelevant)
            align = abs(d_new.dot(axis_target))
            print(
                f"CHECK: corner#{fi} axis_dir={vec_to_list(d_new)} align_to_named_axis={round(align,6)} "
                f"axis_old_pt={[round(axis_old_pt.x,3),round(axis_old_pt.y,3),round(axis_old_pt.z,3)]} "
                f"axis_new_pt={[round(axis_new_pt.x,3),round(axis_new_pt.y,3),round(axis_new_pt.z,3)]}"
            )
        except Exception as e:
            print(f"ERROR: failed computing axis for corner#{fi}: {e}")
            continue

        # Build cylinders long enough to cover the local region, then clip by wedge
        try:
            lbb = local.BoundingBox()
            diag = sqrt(lbb.xlen * lbb.xlen + lbb.ylen * lbb.ylen + lbb.zlen * lbb.zlen)
            height = max(400.0, diag + 200.0)

            base_old = axis_old_pt - d_old * (height / 2.0)
            cyl_old = cq.Solid.makeCylinder(r_old + overlap_eps, height, pnt=base_old, dir=d_old)

            base_new = axis_new_pt - d_new * (height / 2.0)
            cyl_new = cq.Solid.makeCylinder(r_new, height, pnt=base_new, dir=d_new)

            cut_old = cyl_old.intersect(wedge)
            cut_new = cyl_new.intersect(wedge)

            cobb = cut_old.BoundingBox()
            cnbb = cut_new.BoundingBox()
            print(
                f"INFO: corner#{fi} cut_old(bb)=[{round(cobb.xmin,3)},{round(cobb.ymin,3)},{round(cobb.zmin,3)}]"
                f"..[{round(cobb.xmax,3)},{round(cobb.ymax,3)},{round(cobb.zmax,3)}]  "
                f"cut_new(bb)=[{round(cnbb.xmin,3)},{round(cnbb.ymin,3)},{round(cnbb.zmin,3)}]"
                f"..[{round(cnbb.xmax,3)},{round(cnbb.ymax,3)},{round(cnbb.zmax,3)}]"
            )

            addback = cut_old.cut(cut_new)
            abb = addback.BoundingBox()
            print(
                f"CHECK: corner#{fi} addback bb=[{round(abb.xmin,3)},{round(abb.ymin,3)},{round(abb.zmin,3)}]"
                f"..[{round(abb.xmax,3)},{round(abb.ymax,3)},{round(abb.zmax,3)}]"
            )
        except Exception as e:
            print(f"ERROR: failed building tools for corner#{fi}: {e}")
            continue

        # Apply: fuse addback (restores material), then cut new R50 fillet
        try:
            before_solids = len(solid.Solids())
            solid = solid.fuse(addback)
            after_fuse_solids = len(solid.Solids())
            print(f"APPLIED: corner#{fi} fuse addback  solids_before={before_solids} solids_after={after_fuse_solids}")

            before_solids2 = len(solid.Solids())
            solid = solid.cut(cut_new)
            after_cut_solids = len(solid.Solids())
            print(f"APPLIED: corner#{fi} cut new R50  solids_before={before_solids2} solids_after={after_cut_solids}")

            if after_cut_solids != 1:
                print(f"WARNING: corner#{fi} operation resulted in {after_cut_solids} solids (should remain 1)")
        except Exception as e:
            print(f"ERROR: boolean apply failed for corner#{fi}: {e}")
            continue

    # --- Verification: bbox must remain exactly, and R63 corners must now be R50 ---
    out_bb = solid.BoundingBox()
    print(
        "VERIFY: output bbox min="
        f"[{round(out_bb.xmin,3)},{round(out_bb.ymin,3)},{round(out_bb.zmin,3)}] "
        "max="
        f"[{round(out_bb.xmax,3)},{round(out_bb.ymax,3)},{round(out_bb.zmax,3)}]"
    )
    print(
        "VERIFY: expected bbox min="
        f"{list(exp_min)} max={list(exp_max)}  "
        "dmin="
        f"[{round(out_bb.xmin-exp_min[0],6)},{round(out_bb.ymin-exp_min[1],6)},{round(out_bb.zmin-exp_min[2],6)}] "
        "dmax="
        f"[{round(out_bb.xmax-exp_max[0],6)},{round(out_bb.ymax-exp_max[1],6)},{round(out_bb.zmax-exp_max[2],6)}]"
    )

    print(f"VERIFY: output solids={len(solid.Solids())}")

    # radius verification near the 4 original green face centers
    try:
        from OCP.BRepAdaptor import BRepAdaptor_Surface
        from OCP.GeomAbs import GeomAbs_Cylinder

        # reference points from the original input indices (use the original face centers)
        ref_centers = {
            21: v([-186.513, -495.427, 50.289]),
            23: v([-926.727, -495.427, 50.289]),
            44: v([-186.513, -355.609, 572.096]),
            48: v([-926.727, -355.609, 572.096]),
        }

        out_faces = solid.Faces()
        print(f"INFO: output faces={len(out_faces)} edges={len(solid.Edges())}")

        hits50 = 0
        hits63 = 0
        for fi, ref in ref_centers.items():
            best50 = None
            best50_d = 1e99
            best63 = None
            best63_d = 1e99
            for f in out_faces:
                ad = BRepAdaptor_Surface(f.wrapped)
                if ad.GetType() != GeomAbs_Cylinder:
                    continue
                r = float(ad.Cylinder().Radius())
                c = f.Center()
                d = (c - ref).Length
                if abs(r - 50.0) < 0.6 and d < best50_d:
                    best50_d = d
                    best50 = (r, c)
                if abs(r - 63.0) < 0.6 and d < best63_d:
                    best63_d = d
                    best63 = (r, c)

            if best50 is None:
                print(f"VERIFY: ref corner face#{fi} -> found 0 nearby CYL faces with R~50")
            else:
                r, c = best50
                print(
                    f"VERIFY: ref corner face#{fi} -> nearest CYL R50: R={round(r,3)} center="
                    f"[{round(c.x,3)},{round(c.y,3)},{round(c.z,3)}] dist={round(best50_d,3)}"
                )
                hits50 += 1

            if best63 is not None and best63_d < 8.0:
                r, c = best63
                print(
                    f"VERIFY: ref corner face#{fi} -> WARNING nearby CYL R63 still present: R={round(r,3)} center="
                    f"[{round(c.x,3)},{round(c.y,3)},{round(c.z,3)}] dist={round(best63_d,3)}"
                )
                hits63 += 1
            else:
                print(f"VERIFY: ref corner face#{fi} -> no nearby CYL R63 within 8mm (good)")

        print(f"VERIFY: matched {hits50}/4 corners to CYL R~50")
        print(f"VERIFY: corners with nearby leftover R~63 cylinders (should be 0): {hits63}")

    except Exception as e:
        print(f"WARNING: radius verification skipped/failed: {e}")

    return solid