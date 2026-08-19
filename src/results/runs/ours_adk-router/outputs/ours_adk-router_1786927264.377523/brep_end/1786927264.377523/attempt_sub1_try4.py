def my_cad_function(args):
    import cadquery as cq
    import math

    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input
    base = shape.val() if hasattr(shape, "val") else shape

    def vfmt(v):
        return f"({v.x:.3f},{v.y:.3f},{v.z:.3f})"

    def bbfmt(bb):
        return (
            f"min=({bb.xmin:.3f},{bb.ymin:.3f},{bb.zmin:.3f}) "
            f"max=({bb.xmax:.3f},{bb.ymax:.3f},{bb.zmax:.3f}) "
            f"len=({bb.xlen:.3f},{bb.ylen:.3f},{bb.zlen:.3f})"
        )

    def vec_from(t):
        return cq.Vector(float(t[0]), float(t[1]), float(t[2]))

    def unit(v):
        L = v.Length
        if L < 1e-12:
            return cq.Vector(1, 0, 0)
        return cq.Vector(v.x / L, v.y / L, v.z / L)

    def pick_xdir(n):
        n = unit(n)
        z = cq.Vector(0, 0, 1)
        y = cq.Vector(0, 1, 0)
        xdir = z.cross(n)
        if xdir.Length < 1e-6:
            xdir = y.cross(n)
        return unit(xdir)

    def make_global_clip(bbox_exact):
        # Exact measured bbox: X=0..300, Y=200..320, Z=-445..-340
        cx = (bbox_exact[0] + bbox_exact[1]) * 0.5
        cy = (bbox_exact[2] + bbox_exact[3]) * 0.5
        cz = (bbox_exact[4] + bbox_exact[5]) * 0.5
        dx = (bbox_exact[1] - bbox_exact[0])
        dy = (bbox_exact[3] - bbox_exact[2])
        dz = (bbox_exact[5] - bbox_exact[4])
        wp = cq.Workplane(cq.Plane.XY()).box(dx, dy, dz, centered=(True, True, True)).translate((cx, cy, cz))
        return wp.val()

    def make_local_clip_from_face(face, margin=30.0, min_thickness=10.0):
        bb = face.BoundingBox()
        cx, cy, cz = bb.center.x, bb.center.y, bb.center.z
        dx = max(bb.xlen + 2 * margin, min_thickness)
        dy = max(bb.ylen + 2 * margin, min_thickness)
        dz = max(bb.zlen + 2 * margin, min_thickness)
        return cq.Workplane(cq.Plane.XY()).box(dx, dy, dz, centered=(True, True, True)).translate((cx, cy, cz)).val()

    def make_inward_halfbox_from_plane_face(plane_face, extent=5000.0, depth=5000.0):
        # Use inward normal = -outward normal
        p0 = plane_face.Center()
        n_out = plane_face.normalAt()
        n_in = cq.Vector(-n_out.x, -n_out.y, -n_out.z)
        xdir = pick_xdir(n_in)
        pl = cq.Plane(origin=(p0.x, p0.y, p0.z), xDir=(xdir.x, xdir.y, xdir.z), normal=(n_in.x, n_in.y, n_in.z))
        # Box starts at the plane and extends into the solid (along +normal of this plane)
        hb = cq.Workplane(pl).box(extent, extent, depth, centered=(True, True, False)).val()
        return hb

    def face_geom_type(f):
        try:
            gt = f.geomType()
            # CadQuery returns a GeomType enum or string depending on build
            return str(gt).split(".")[-1]
        except Exception:
            return "UNKNOWN"

    def edge_hash(e):
        try:
            return int(e.hashCode())
        except Exception:
            # fallback (less stable)
            return hash((round(e.Center().x, 6), round(e.Center().y, 6), round(e.Center().z, 6), round(e.Length(), 6)))

    sols = base.Solids()
    print(f"INFO: imported solids={len(sols)}")
    if len(sols) < 1:
        print("SELECTED: 0 solids (ERROR)")
        return shape

    solid0 = sols[0]
    bb0 = solid0.BoundingBox()
    print(f"INFO: base bbox {bbfmt(bb0)}")

    # Numbers explicitly named by sub-goal (anchors / protections / clip)
    bbox_exact = (0.0, 300.0, 200.0, 320.0, -445.0, -340.0)
    bore_r = 14.1421
    bore_y, bore_z = 270.0, -400.0
    bore_x0, bore_x1 = 100.0, 300.0
    print("NUMBERS:")
    print(f"  GLOBAL CLIP bbox X={bbox_exact[0]}..{bbox_exact[1]} Y={bbox_exact[2]}..{bbox_exact[3]} Z={bbox_exact[4]}..{bbox_exact[5]}")
    print(f"  PROTECT bore: radius={bore_r} axis||+X at Y={bore_y} Z={bore_z} spanning X={bore_x0}..{bore_x1}")
    print("  PROTECT cavities: via inner opening loops on planar faces near Y=230 and X=300")

    # Build global clip solid (prevents any tool from exceeding measured envelope)
    global_clip = make_global_clip(bbox_exact)

    # Build protection solids
    # 1) Bore protection cylinder (slightly oversized and slightly overlong for safety)
    bore_len = (bore_x1 - bore_x0) + 20.0
    bore_base_x = bore_x0 - 10.0
    bore_protect = cq.Solid.makeCylinder(
        bore_r, bore_len,
        cq.Vector(bore_base_x, bore_y, bore_z),
        cq.Vector(1, 0, 0)
    )
    print(f"INFO: bore_protect bbox {bbfmt(bore_protect.BoundingBox())}")

    # 2) Cavity access protection boxes derived from faces with inner loops near Y=230 and X=300
    faces0 = solid0.Faces()
    print(f"INFO: solid faces={len(faces0)}")

    cavity_boxes = []
    cavity_face_idxs = []
    for i, f in enumerate(faces0):
        try:
            if face_geom_type(f) != "PLANE":
                continue
            wires = f.Wires()
            if len(wires) <= 1:
                continue
            c = f.Center()
            n = f.normalAt()
            # near Y=230 plane (normal ~ -Y) or near X=300 plane (normal ~ +X)
            if abs(c.y - 230.0) < 5.0 and abs(n.y + 1.0) < 0.05:
                # Build axis-aligned box that starts at Y=230 and goes inward (+Y)
                # Use inner wire bbox to size box in X,Z
                inner_edges = []
                for w in wires[1:]:
                    inner_edges.extend(list(w.Edges()))
                if not inner_edges:
                    continue
                comp = cq.Compound.makeCompound(inner_edges)
                ibb = comp.BoundingBox()
                xlen = ibb.xlen + 20.0
                zlen = ibb.zlen + 20.0
                ylen = 300.0
                cx, cz = ibb.center.x, ibb.center.z
                cy = 230.0 + ylen * 0.5
                box = cq.Workplane(cq.Plane.XY()).box(xlen, ylen, zlen, centered=(True, True, True)).translate((cx, cy, cz)).val()
                # ensure minY is at/just below 230
                cavity_boxes.append(box)
                cavity_face_idxs.append(i)
            elif abs(c.x - 300.0) < 1.0 and abs(n.x - 1.0) < 0.05:
                # Build axis-aligned box that starts at X=300 and goes inward (-X)
                inner_edges = []
                for w in wires[1:]:
                    inner_edges.extend(list(w.Edges()))
                if not inner_edges:
                    continue
                comp = cq.Compound.makeCompound(inner_edges)
                ibb = comp.BoundingBox()
                ylen = ibb.ylen + 20.0
                zlen = ibb.zlen + 20.0
                xlen = 350.0
                cy, cz = ibb.center.y, ibb.center.z
                cx = 300.0 - xlen * 0.5
                box = cq.Workplane(cq.Plane.XY()).box(xlen, ylen, zlen, centered=(True, True, True)).translate((cx, cy, cz)).val()
                cavity_boxes.append(box)
                cavity_face_idxs.append(i)
        except Exception as e:
            print(f"WARN: cavity-protect detection failed on face #{i}: {e}")

    print(f"SELECTED: {len(cavity_boxes)} faces with inner opening loops for cavity protection   idx={cavity_face_idxs}")
    cavity_protect = None
    if cavity_boxes:
        cavity_protect = cavity_boxes[0]
        for b in cavity_boxes[1:]:
            cavity_protect = cavity_protect.fuse(b)
        cavity_protect = cavity_protect.clean()
        print(f"INFO: cavity_protect bbox {bbfmt(cavity_protect.BoundingBox())}")

    # Build adjacency map (on the original solid0)
    edge_to_faces = {}
    for fi, f in enumerate(faces0):
        for e in f.Edges():
            h = edge_hash(e)
            edge_to_faces.setdefault(h, []).append(fi)

    def adjacent_planar_faces(face_idx):
        f = faces0[face_idx]
        nbr_idxs = set()
        for e in f.Edges():
            h = edge_hash(e)
            for j in edge_to_faces.get(h, []):
                if j != face_idx:
                    nbr_idxs.add(j)
        planes = []
        for j in sorted(nbr_idxs):
            fj = faces0[j]
            if face_geom_type(fj) == "PLANE":
                planes.append((j, fj))
        return planes

    # Target exterior blends / bevel strips from the geometry index
    # (use indices but verify resolved face centers/types)
    target_face_indices = [
        # partial-sweep exterior cylinders
        6, 22, 24, 30, 32,   # r30 partial sweeps
        4,                   # r35 partial sweep (cyl)
        10,                  # r10 90deg
        27,                  # r5 90deg corner
        12,                  # r2.5 90deg corner
        7,                   # r5 small (16.7deg) (still an exterior blend family)
        # exterior cones
        0, 14,
        # exterior spherical / bspline corners
        23, 25, 20,
        # planar bevel strips (oblique normals)
        1, 15, 11,
    ]

    # Resolve target faces and categorize by planar neighbor count (2-support first, trihedral later)
    resolved = []
    for idx in target_face_indices:
        if idx < 0 or idx >= len(faces0):
            print(f"SELECTED: 0 faces for target idx={idx} (out of range)")
            continue
        f = faces0[idx]
        gt = face_geom_type(f)
        c = f.Center()
        a = f.Area()
        print(f"INFO: target face #{idx} type={gt} area={a:.3f} center={vfmt(c)}")
        nbr_planes = adjacent_planar_faces(idx)
        print(f"SELECTED: {len(nbr_planes)} adjacent planar support faces for target face #{idx}   idx={[j for j,_ in nbr_planes]}")
        resolved.append((idx, f, gt, nbr_planes))

    two_support = []
    trihedral = []
    for idx, f, gt, nbr_planes in resolved:
        if gt in ("SPHERE", "BSPLINE") or len(nbr_planes) >= 3:
            trihedral.append((idx, f, gt, nbr_planes))
        else:
            two_support.append((idx, f, gt, nbr_planes))

    print(f"INFO: processing order: two-support={len(two_support)} then trihedral={len(trihedral)}")

    edited = solid0

    def build_wedge_patch(target_idx, target_face, support_planes, margin=30.0):
        # support_planes: list of (face_idx, face_obj)
        # Construct wedge as intersection of inward halfspaces of support planes
        # Then localize by target face bbox and global clip bbox.
        if len(support_planes) < 2:
            print(f"SELECTED: 0 wedges for face #{target_idx} (need >=2 planar supports)")
            return None

        # Use top-N supports: 2 for two-support, 3 for trihedral
        # Choose largest-area planes first (more likely the true supports)
        sp_sorted = sorted(support_planes, key=lambda t: t[1].Area(), reverse=True)
        use_n = 3 if (face_geom_type(target_face) in ("SPHERE", "BSPLINE") or len(sp_sorted) >= 3) else 2
        sp_use = sp_sorted[:use_n]
        print(f"SELECTED: {len(sp_use)} support planes for wedge of target face #{target_idx}   idx={[j for j,_ in sp_use]}")

        local_clip = make_local_clip_from_face(target_face, margin=margin)

        try:
            wedge = None
            for j, pf in sp_use:
                hb = make_inward_halfbox_from_plane_face(pf)
                wedge = hb if wedge is None else wedge.intersect(hb)
            # clip to local and global extents
            wedge = wedge.intersect(local_clip)
            wedge = wedge.intersect(global_clip)

            # subtract protected volumes
            wedge = wedge.cut(bore_protect)
            if cavity_protect is not None:
                wedge = wedge.cut(cavity_protect)

            wedge = wedge.clean()
            return wedge
        except Exception as e:
            print(f"ERROR: wedge construction failed for target face #{target_idx}: {e}")
            return None

    def fuse_patch(edited_solid, patch, label):
        if patch is None:
            return edited_solid
        try:
            # isolate what would actually be added
            added = patch.cut(edited_solid)
            av = added.Volume() if added is not None else 0.0
            if added is None or av < 1e-6:
                print(f"CHECK: {label} added volume ~0 (no-op) -> skipping fuse")
                return edited_solid

            abb = added.BoundingBox()
            print(f"CHECK: {label} added volume={av:.3f} mm^3")
            print(f"CHECK: {label} added bbox {bbfmt(abb)}")
            # bbox guard print vs exact envelope
            dxmin = abb.xmin - bbox_exact[0]
            dxmax = abb.xmax - bbox_exact[1]
            dymin = abb.ymin - bbox_exact[2]
            dymax = abb.ymax - bbox_exact[3]
            dzmin = abb.zmin - bbox_exact[4]
            dzmax = abb.zmax - bbox_exact[5]
            print(
                f"VERIFY: {label} added vs envelope deltas "
                f"dxmin={dxmin:+.3f} dxmax={dxmax:+.3f} "
                f"dymin={dymin:+.3f} dymax={dymax:+.3f} "
                f"dzmin={dzmin:+.3f} dzmax={dzmax:+.3f}"
            )

            out = edited_solid.fuse(added)
            out = out.clean()
            return out
        except Exception as e:
            print(f"ERROR: fuse failed for {label}: {e}")
            return edited_solid

    # Process two-support blend/bevel patches first
    for idx, tf, gt, nbr_planes in two_support:
        # margin heuristics: small blends can use smaller; large rounds use larger
        margin = 40.0
        if gt == "PLANE":
            margin = 30.0
        elif gt == "CYLINDER":
            margin = 45.0
        elif gt == "CONE":
            margin = 35.0
        wedge = build_wedge_patch(idx, tf, nbr_planes, margin=margin)
        edited = fuse_patch(edited, wedge, f"two-support patch for face#{idx} type={gt}")

    # Process trihedral patches (sphere + bsplines) after
    for idx, tf, gt, nbr_planes in trihedral:
        wedge = build_wedge_patch(idx, tf, nbr_planes, margin=50.0)
        edited = fuse_patch(edited, wedge, f"trihedral patch for face#{idx} type={gt}")

    # Final checks
    ebb = edited.BoundingBox()
    print(f"RESULT: edited bbox {bbfmt(ebb)}")
    print(
        "VERIFY: bbox vs measured exact "
        f"xmin Δ={ebb.xmin - bbox_exact[0]:+.6f} xmax Δ={ebb.xmax - bbox_exact[1]:+.6f} "
        f"ymin Δ={ebb.ymin - bbox_exact[2]:+.6f} ymax Δ={ebb.ymax - bbox_exact[3]:+.6f} "
        f"zmin Δ={ebb.zmin - bbox_exact[4]:+.6f} zmax Δ={ebb.zmax - bbox_exact[5]:+.6f}"
    )

    # Report surface mix as a coarse residual-blend indicator
    try:
        ftypes = {}
        for f in edited.Faces():
            ftypes[face_geom_type(f)] = ftypes.get(face_geom_type(f), 0) + 1
        print(f"INFO: edited face type counts: {dict(sorted(ftypes.items()))}")
    except Exception as e:
        print(f"WARN: could not compute edited face type counts: {e}")

    # Ensure single connected solid retained
    try:
        esols = edited.Solids()
        print(f"INFO: edited solids={len(esols)}")
        if len(esols) == 1:
            return edited
        out = cq.Compound.makeCompound([s for i, s in enumerate(esols)])
        return out
    except Exception as e:
        print(f"WARN: could not verify solids count: {e}")
        return edited