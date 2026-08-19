def my_cad_function(args):
    import cadquery as cq
    from math import sqrt

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

    def geom_type(face):
        try:
            return face.geomType()
        except Exception:
            return "UNKNOWN"

    def safe_clean(s):
        if s is None:
            return None
        try:
            return s.clean()
        except Exception:
            return s

    def is_inside(sol, p, tol=1e-6):
        try:
            return sol.isInside(p, tol, True)
        except Exception:
            # If isInside isn't available, fail open (do not flip normals)
            return False

    def inward_normal_for_face(sol, f, eps=0.5):
        c = f.Center()
        n = f.normalAt()
        # outward normal is n; inward should be the direction that goes inside the solid
        p_in = cq.Vector(c.x - n.x * eps, c.y - n.y * eps, c.z - n.z * eps)
        if is_inside(sol, p_in, tol=1e-4):
            inward = cq.Vector(-n.x, -n.y, -n.z)
            flipped = False
        else:
            inward = cq.Vector(n.x, n.y, n.z)
            flipped = True
        # normalize
        L = inward.Length
        if L > 0:
            inward = cq.Vector(inward.x / L, inward.y / L, inward.z / L)
        return inward, flipped

    def pick_xdir(n):
        # pick a stable x-direction not parallel to n
        ax = cq.Vector(1, 0, 0)
        ay = cq.Vector(0, 1, 0)
        az = cq.Vector(0, 0, 1)
        # choose the axis least aligned with n
        dots = [abs(n.dot(ax)), abs(n.dot(ay)), abs(n.dot(az))]
        ref = [ax, ay, az][dots.index(min(dots))]
        xdir = ref.cross(n)
        L = xdir.Length
        if L < 1e-9:
            xdir = cq.Vector(1, 0, 0)
        else:
            xdir = cq.Vector(xdir.x / L, xdir.y / L, xdir.z / L)
        return xdir

    def make_halfspace_box(origin, inward_n, big=2500.0, depth=2500.0):
        # Represents the (finite) halfspace slab: points with inward_n·(x-origin) in [0, depth]
        xdir = pick_xdir(inward_n)
        pl = cq.Plane(origin=(origin.x, origin.y, origin.z), normal=(inward_n.x, inward_n.y, inward_n.z), xDir=(xdir.x, xdir.y, xdir.z))
        # non-centered in +Z so the plane itself is the "bottom" (z=0)
        return cq.Workplane(pl).box(big, big, depth, centered=(True, True, False)).val()

    def make_axis_aligned_clip_box(x0, x1, y0, y1, z0, z1):
        cx, cy, cz = (x0 + x1) * 0.5, (y0 + y1) * 0.5, (z0 + z1) * 0.5
        sx, sy, sz = (x1 - x0), (y1 - y0), (z1 - z0)
        return cq.Workplane(cq.Plane.XY()).box(sx, sy, sz, centered=(True, True, True)).translate((cx, cy, cz)).val()

    def clamp(v, a, b):
        return max(a, min(b, v))

    # ---- Solid extraction ----
    sols = base.Solids()
    print(f"INFO: imported solids={len(sols)}")
    if len(sols) < 1:
        print("SELECTED: 0 solids (ERROR)")
        return shape

    solid = sols[0]
    bb0 = solid.BoundingBox()
    vol0 = solid.Volume()
    print(f"INFO: base bbox {bbfmt(bb0)}")
    print(f"INFO: base volume={vol0:.3f} mm^3")

    # ---- Numbers named by the sub-goal (print them) ----
    gx0, gx1 = 0.0, 300.0
    gy0, gy1 = 200.0, 320.0
    gz0, gz1 = -445.0, -340.0
    bore_r = 14.1421
    bore_y, bore_z = 270.0, -400.0
    bore_x0, bore_x1 = 100.0, 300.0
    print("NUMBERS:")
    print(f"  GLOBAL bbox X={gx0}..{gx1} Y={gy0}..{gy1} Z={gz0}..{gz1}")
    print(f"  PROTECT bore: r={bore_r} axis||X at Y={bore_y} Z={bore_z} spanning X={bore_x0}..{bore_x1}")
    print("  PROTECT cavities: via inner opening loops on planar faces near Y=230 and X=300")

    # ---- Global clip to measured envelope ----
    global_clip = make_axis_aligned_clip_box(gx0, gx1, gy0, gy1, gz0, gz1)
    print(f"INFO: global_clip bbox {bbfmt(global_clip.BoundingBox())} (should match measured envelope)")

    # ---- Protection solids ----
    # Bore protect: cylinder along +X, spanning slightly beyond given range
    bore_margin = 0.35
    bore_len = (bore_x1 - bore_x0) + 20.0
    bore_start = bore_x0 - 10.0
    bore_plane = cq.Plane(origin=(bore_start, bore_y, bore_z), normal=(1, 0, 0))
    bore_protect = cq.Workplane(bore_plane).circle(bore_r + bore_margin).extrude(bore_len).val()
    print(f"INFO: bore_protect bbox {bbfmt(bore_protect.BoundingBox())}")

    # Cavity protect: find faces with inner wires, build protect boxes over their inner-loop bboxes
    faces = solid.Faces()
    cavity_faces = []
    for i, f in enumerate(faces):
        try:
            ws = f.Wires()
            if len(ws) > 1:
                cavity_faces.append((i, f, ws[1:]))
        except Exception:
            pass
    print(f"SELECTED: {len(cavity_faces)} faces with inner opening loops for cavity protection   idx={[i for i,_,_ in cavity_faces]}")

    cavity_protects = []
    for (fi, ff, inner_ws) in cavity_faces:
        n = ff.normalAt()
        c = ff.Center()
        print(f"INFO: cavity face #{fi} has inner_loops={len(inner_ws)} center={vfmt(c)} normal(out)={vfmt(n)}")
        # union bbox of inner wires
        bbs = [w.BoundingBox() for w in inner_ws]
        xmin = min(bb.xmin for bb in bbs)
        xmax = max(bb.xmax for bb in bbs)
        ymin = min(bb.ymin for bb in bbs)
        ymax = max(bb.ymax for bb in bbs)
        zmin = min(bb.zmin for bb in bbs)
        zmax = max(bb.zmax for bb in bbs)
        pad = 3.0
        xmin -= pad; xmax += pad
        ymin -= pad; ymax += pad
        zmin -= pad; zmax += pad

        # Extend into the part along inward direction (axis-aligned, clamped to global envelope)
        inward, flipped = inward_normal_for_face(solid, ff, eps=0.5)
        # choose dominant axis for extension
        ax = max([(abs(inward.x), 'x'), (abs(inward.y), 'y'), (abs(inward.z), 'z')])[1]
        ext = 600.0
        if ax == 'x':
            if inward.x > 0:
                xmax = clamp(xmax + ext, gx0 - 1000, gx1 + 1000)
            else:
                xmin = clamp(xmin - ext, gx0 - 1000, gx1 + 1000)
        elif ax == 'y':
            if inward.y > 0:
                ymax = clamp(ymax + ext, gy0 - 1000, gy1 + 1000)
            else:
                ymin = clamp(ymin - ext, gy0 - 1000, gy1 + 1000)
        else:
            if inward.z > 0:
                zmax = clamp(zmax + ext, gz0 - 1000, gz1 + 1000)
            else:
                zmin = clamp(zmin - ext, gz0 - 1000, gz1 + 1000)

        prot = make_axis_aligned_clip_box(xmin, xmax, ymin, ymax, zmin, zmax)
        cavity_protects.append(prot)
        print(f"INFO: cavity_protect for face#{fi} bbox {bbfmt(prot.BoundingBox())} inward={vfmt(inward)} flipped_outward={flipped}")

    # Combine protections
    protect = bore_protect
    for p in cavity_protects:
        try:
            protect = safe_clean(protect.fuse(p))
        except Exception:
            pass

    # ---- Build adjacency map (edge->faces) ----
    edge_to_faces = {}
    for fi, f in enumerate(faces):
        for e in f.Edges():
            try:
                k = e.hashCode()
            except Exception:
                # fallback: use bounding box signature
                bb = e.BoundingBox()
                k = (round(bb.xmin, 4), round(bb.ymin, 4), round(bb.zmin, 4), round(bb.xmax, 4), round(bb.ymax, 4), round(bb.zmax, 4))
            edge_to_faces.setdefault(k, []).append(fi)

    def adjacent_face_indices(target_face_idx):
        f = faces[target_face_idx]
        nbrs = set()
        for e in f.Edges():
            try:
                k = e.hashCode()
            except Exception:
                bb = e.BoundingBox()
                k = (round(bb.xmin, 4), round(bb.ymin, 4), round(bb.zmin, 4), round(bb.xmax, 4), round(bb.ymax, 4), round(bb.zmax, 4))
            for j in edge_to_faces.get(k, []):
                if j != target_face_idx:
                    nbrs.add(j)
        return sorted(nbrs)

    def shared_edge_length_score(fi_a, fi_b):
        fa, fb = faces[fi_a], faces[fi_b]
        # compare edge hash codes
        ea = set()
        for e in fa.Edges():
            try:
                ea.add(e.hashCode())
            except Exception:
                bb = e.BoundingBox()
                ea.add((round(bb.xmin, 4), round(bb.ymin, 4), round(bb.zmin, 4), round(bb.xmax, 4), round(bb.ymax, 4), round(bb.zmax, 4)))
        score = 0.0
        for e in fb.Edges():
            try:
                k = e.hashCode()
            except Exception:
                bb = e.BoundingBox()
                k = (round(bb.xmin, 4), round(bb.ymin, 4), round(bb.zmin, 4), round(bb.xmax, 4), round(bb.ymax, 4), round(bb.zmax, 4))
            if k in ea:
                try:
                    score += e.Length()
                except Exception:
                    pass
        return score

    # ---- Target faces (from prompt) ----
    target_face_indices = [
        6, 22, 24, 30, 32,      # R30 partial-sweep exterior cylinders
        4,                      # R35 partial-sweep exterior cylinder
        10,                     # R10 90deg corner blend
        27,                     # R5 90deg corner blend near [98.183,318.183,-392.5]
        12,                     # R2.5 90deg corner blend near [299.086,251.114,-390.457]
        0, 14,                  # exterior cones
        23,                     # exterior spherical corner
        25, 20,                 # exterior BSPLINE corners
        1, 15, 11               # planar bevel strips with oblique normals
    ]

    # Sanity print targets
    selected_targets = []
    for idx in target_face_indices:
        if idx < 0 or idx >= len(faces):
            continue
        f = faces[idx]
        gt = geom_type(f)
        selected_targets.append((idx, f, gt))
        print(f"INFO: target face #{idx} type={gt} area={f.Area():.3f} center={vfmt(f.Center())}")
    print(f"SELECTED: {len(selected_targets)} target faces total for exterior de-blending")

    # Partition processing order: two-support first, trihedral later
    two_support_first = []
    trihedral_later = []
    for idx, f, gt in selected_targets:
        if gt in ("SPHERE", "BSPLINE"):
            trihedral_later.append((idx, f, gt))
        else:
            two_support_first.append((idx, f, gt))
    print(f"INFO: processing order two-support={len(two_support_first)} trihedral={len(trihedral_later)}")

    # helper: collect best planar supports
    cavity_face_indices = set(i for i, _, _ in cavity_faces)

    def collect_support_planes(tidx, needed=2):
        nbrs = adjacent_face_indices(tidx)
        scored = []
        for j in nbrs:
            if j in cavity_face_indices:
                # don't use cavity faces as supports; they're protected
                continue
            fj = faces[j]
            if geom_type(fj) != "PLANE":
                continue
            sc = shared_edge_length_score(tidx, j)
            if sc <= 1e-6:
                continue
            scored.append((sc, j, fj))
        scored.sort(reverse=True, key=lambda t: t[0])
        out = [(j, fj, sc) for (sc, j, fj) in scored[:needed]]
        return out

    # tool making: wedge = intersection of inward halfspace boxes, then local clip, then global clip
    def build_wedge_from_supports(tface, supports, local_clip):
        hs = []
        for (pi, pf, sc) in supports:
            inward, flipped = inward_normal_for_face(solid, pf, eps=0.5)
            hs_box = make_halfspace_box(pf.Center(), inward, big=2500.0, depth=2500.0)
            hs.append((pi, pf, hs_box, inward, flipped, sc))
        if not hs:
            return None, []
        w = hs[0][2]
        for k in range(1, len(hs)):
            try:
                w = safe_clean(w.intersect(hs[k][2]))
            except Exception as e:
                print(f"ERROR: halfspace intersection failed at k={k}: {e}")
                return None, hs
        try:
            w = safe_clean(w.intersect(local_clip))
            w = safe_clean(w.intersect(global_clip))
        except Exception as e:
            print(f"ERROR: wedge clip failed: {e}")
            return None, hs
        return w, hs

    def make_local_clip_for_face(f, pad):
        bb = f.BoundingBox()
        x0, x1 = bb.xmin - pad, bb.xmax + pad
        y0, y1 = bb.ymin - pad, bb.ymax + pad
        z0, z1 = bb.zmin - pad, bb.zmax + pad
        # also clamp within a generous band around global envelope, but DO NOT exceed measured envelope after intersect
        # (global_clip will clamp it anyway)
        return make_axis_aligned_clip_box(x0, x1, y0, y1, z0, z1)

    edited = solid
    total_added = 0.0
    applied = 0

    def apply_patch_for_target(face_idx, face_obj, gt, needed_supports):
        nonlocal edited, total_added, applied

        supports = collect_support_planes(face_idx, needed=needed_supports)
        print(f"SELECTED: {len(supports)} planar support faces for target face#{face_idx} type={gt}   idx={[i for i,_,_ in supports]}")
        for (pi, pf, sc) in supports:
            nn = pf.normalAt()
            inward, flipped = inward_normal_for_face(solid, pf, eps=0.5)
            print(f"  SUPPORT: face#{pi} shared_edge_len_score={sc:.3f} center={vfmt(pf.Center())} n(out)={vfmt(nn)} inward={vfmt(inward)} flipped_outward={flipped}")

        if len(supports) < needed_supports:
            print(f"WARN: insufficient planar supports for face#{face_idx} need={needed_supports} have={len(supports)} -> skipping")
            return

        # pad based on face size (avoid tiny local clip that misses the missing corner)
        fbb = face_obj.BoundingBox()
        diag = sqrt(fbb.xlen * fbb.xlen + fbb.ylen * fbb.ylen + fbb.zlen * fbb.zlen)
        pad = max(8.0, min(35.0, 0.45 * diag + 4.0))
        local_clip = make_local_clip_for_face(face_obj, pad=pad)
        print(f"INFO: local_clip for face#{face_idx} pad={pad:.3f} bbox {bbfmt(local_clip.BoundingBox())}")

        wedge, hsinfo = build_wedge_from_supports(face_obj, supports, local_clip)
        if wedge is None:
            print(f"SELECTED: 0 wedge solids for face#{face_idx} (build failed)")
            return

        # Remove protected regions from wedge so we don't touch bore/cavity
        try:
            wedge2 = safe_clean(wedge.cut(protect))
        except Exception as e:
            print(f"WARN: wedge.cut(protect) failed for face#{face_idx}: {e}")
            wedge2 = wedge

        # Compute added material = wedge region that is NOT already inside the solid
        try:
            add_shape = safe_clean(wedge2.cut(edited))
            add_vol = add_shape.Volume() if add_shape is not None else 0.0
        except Exception as e:
            print(f"ERROR: computing add_shape failed for face#{face_idx}: {e}")
            return

        print(f"CHECK: face#{face_idx} add_vol(candidate)={add_vol:.3f} mm^3")
        if add_shape is None or add_vol < 1e-4:
            print(f"CHECK: face#{face_idx} add_vol ~0 -> no-op, skipping")
            return

        # Guard against runaway tools: should be localized
        if add_vol > 400000.0:
            print(f"WARN: face#{face_idx} add_vol {add_vol:.3f} too large for localized patch -> skipping")
            return

        # Final clip to measured envelope and protection
        try:
            add_shape = safe_clean(add_shape.intersect(global_clip))
            add_shape = safe_clean(add_shape.cut(protect))
        except Exception as e:
            print(f"WARN: post-clip/protect failed for face#{face_idx}: {e}")

        abb = add_shape.BoundingBox()
        print(f"CHECK: face#{face_idx} added bbox {bbfmt(abb)}")
        print(
            "VERIFY: added vs measured envelope deltas "
            f"dxmin={abb.xmin - gx0:+.3f} dxmax={abb.xmax - gx1:+.3f} "
            f"dymin={abb.ymin - gy0:+.3f} dymax={abb.ymax - gy1:+.3f} "
            f"dzmin={abb.zmin - gz0:+.3f} dzmax={abb.zmax - gz1:+.3f}"
        )

        # Apply union
        try:
            edited2 = safe_clean(edited.fuse(add_shape))
        except Exception as e:
            print(f"ERROR: fuse failed for face#{face_idx}: {e}")
            return

        # Self-check isolate what was added
        try:
            added_iso = safe_clean(edited2.cut(edited))
            bb_added_iso = added_iso.BoundingBox()
            vol_added_iso = added_iso.Volume()
            print(f"SELF-CHECK: added_iso vol={vol_added_iso:.3f} bbox {bbfmt(bb_added_iso)}")
        except Exception as e:
            print(f"WARN: could not isolate added material for face#{face_idx}: {e}")

        edited = edited2
        total_added += add_vol
        applied += 1
        print(f"APPLIED: fuse corner/chamfer replacement patch for face#{face_idx} type={gt} add_vol={add_vol:.3f}")

    # ---- Apply two-support patches first ----
    for (idx, f, gt) in two_support_first:
        # cone treated as two-support chamfer; planar bevel treated as two-support
        apply_patch_for_target(idx, f, gt, needed_supports=2)

    # ---- Apply trihedral patches (sphere/bspline) ----
    for (idx, f, gt) in trihedral_later:
        apply_patch_for_target(idx, f, gt, needed_supports=3)

    # ---- Final diagnostics ----
    ebb = edited.BoundingBox()
    vole = edited.Volume()
    print(f"RESULT: edited bbox {bbfmt(ebb)}")
    print(
        "VERIFY: bbox vs measured exact "
        f"xmin Δ={ebb.xmin - gx0:+.6f} xmax Δ={ebb.xmax - gx1:+.6f} "
        f"ymin Δ={ebb.ymin - gy0:+.6f} ymax Δ={ebb.ymax - gy1:+.6f} "
        f"zmin Δ={ebb.zmin - gz0:+.6f} zmax Δ={ebb.zmax - gz1:+.6f}"
    )
    print(f"RESULT: volume before={vol0:.3f} after={vole:.3f} delta={vole - vol0:+.3f} mm^3")
    print(f"RESULT: total_added_requested={total_added:.3f} patches_applied={applied}")

    # Surface-type counts to help spot residual exterior blends
    try:
        ftypes = {}
        for f in edited.Faces():
            gt = geom_type(f)
            ftypes[gt] = ftypes.get(gt, 0) + 1
        print(f"INFO: edited face type counts: {dict(sorted(ftypes.items()))}")
    except Exception as e:
        print(f"WARN: could not compute edited face type counts: {e}")

    # Ensure single connected solid
    try:
        esols = edited.Solids()
        print(f"INFO: edited solids={len(esols)}")
        if len(esols) == 1:
            return esols[0]
        # If multiple solids appear, keep the largest (should not happen; but avoid returning a no-op compound)
        esols = sorted(esols, key=lambda s: s.Volume(), reverse=True)
        print(f"WARN: multiple solids after booleans; keeping largest vol={esols[0].Volume():.3f}")
        return esols[0]
    except Exception as e:
        print(f"WARN: could not verify solids count: {e}")
        return edited