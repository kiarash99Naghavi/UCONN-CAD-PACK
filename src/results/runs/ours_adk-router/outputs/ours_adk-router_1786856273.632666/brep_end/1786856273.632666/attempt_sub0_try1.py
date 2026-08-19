def my_cad_function(args):
    import cadquery as cq

    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input
    base = shape.val() if hasattr(shape, "val") else shape

    # --- numbers explicitly named by sub-goal ---
    y_front = 3.175
    edge_idx_target = 0
    face_idx_front = 496
    face_idx_cyl = 2
    r_edge_named = 15.25
    r_cyl_named = 15.75
    chamfer_size = 1.0

    print("TARGET NUMBERS:")
    print(f"  y_front={y_front}")
    print(f"  edge_idx_target={edge_idx_target} (named r={r_edge_named})")
    print(f"  face_idx_front={face_idx_front} (front plane)")
    print(f"  face_idx_cyl={face_idx_cyl} (central cylindrical)")
    print(f"  r_cyl_named={r_cyl_named}")
    print(f"  chamfer_size={chamfer_size}")

    # --- resolve indexed entities and verify against the provided geometry index ---
    all_edges = base.Edges()
    all_faces = base.Faces()
    print(f"Entity counts: faces={len(all_faces)} edges={len(all_edges)} solids={len(base.Solids())}")

    if edge_idx_target >= len(all_edges) or face_idx_front >= len(all_faces) or face_idx_cyl >= len(all_faces):
        print("ERROR: Provided indices exceed entity counts; returning original shape")
        return shape

    edge0 = all_edges[edge_idx_target]
    f_front = all_faces[face_idx_front]
    f_cyl = all_faces[face_idx_cyl]

    def pnt_tuple(v):
        t = v.toTuple() if hasattr(v, "toTuple") else tuple(v)
        return (float(t[0]), float(t[1]), float(t[2]))

    def circle_data(e):
        try:
            if hasattr(e, "geomType") and e.geomType() != "CIRCLE":
                return None
            c = e.Center()
            r = e.radius()
            cx, cy, cz = pnt_tuple(c)
            return (cx, cy, cz, float(r))
        except Exception:
            return None

    e0_cd = circle_data(edge0)
    print("Resolved entities (index-check):")
    print(f"  face#{face_idx_front}: area={f_front.Area():.3f} center={tuple(round(x,6) for x in pnt_tuple(f_front.Center()))}")
    try:
        n = f_front.normalAt()
        print(f"    normal={tuple(round(x,6) for x in pnt_tuple(n))}")
    except Exception as ex:
        print("    normalAt failed:", ex)

    print(f"  face#{face_idx_cyl}: area={f_cyl.Area():.3f} center={tuple(round(x,6) for x in pnt_tuple(f_cyl.Center()))}")
    try:
        # Face radius helper sometimes exists for cylinders
        if hasattr(f_cyl, "radius"):
            print(f"    face.radius()={f_cyl.radius():.6f}")
    except Exception as ex:
        print("    face.radius() failed:", ex)

    if e0_cd:
        cx, cy, cz, r0 = e0_cd
        print(f"  edge#{edge_idx_target}: CIRCLE center={tuple(round(x,6) for x in (cx,cy,cz))} r={r0:.6f}")
        print(f"    delta_y_to_front={cy - y_front:.6f}")
    else:
        print(f"  edge#{edge_idx_target}: not detected as circle via cq Edge API (continuing anyway)")

    # --- find which solid contains the target edge (compound has 2 solids) ---
    solids = list(base.Solids())
    target_solid = None
    edge0_h = None
    try:
        edge0_h = edge0.hashCode()
    except Exception:
        try:
            edge0_h = edge0.wrapped.HashCode(2147483647)
        except Exception:
            edge0_h = None

    for si, s in enumerate(solids):
        found = False
        for e in s.Edges():
            try:
                eh = e.hashCode()
            except Exception:
                try:
                    eh = e.wrapped.HashCode(2147483647)
                except Exception:
                    continue
            if edge0_h is not None and eh == edge0_h:
                found = True
                break
        if found:
            target_solid = s
            print(f"Target edge belongs to solid index #{si} (of {len(solids)})")
            break

    if target_solid is None:
        # Fallback: pick the solid whose bbox includes the edge center
        print("WARNING: Could not locate solid by edge hash; falling back to nearest solid by edge center")
        if e0_cd:
            pt = cq.Vector(e0_cd[0], e0_cd[1], e0_cd[2])
            best = None
            for si, s in enumerate(solids):
                bb = s.BoundingBox()
                cx_bb = 0.5 * (bb.xmin + bb.xmax)
                cy_bb = 0.5 * (bb.ymin + bb.ymax)
                cz_bb = 0.5 * (bb.zmin + bb.zmax)
                d2 = (cx_bb - pt.x) ** 2 + (cy_bb - pt.y) ** 2 + (cz_bb - pt.z) ** 2
                if best is None or d2 < best[0]:
                    best = (d2, si, s)
            if best:
                target_solid = best[2]
                print(f"Fallback chose solid #{best[1]} by bbox-center distance")

    if target_solid is None:
        print("ERROR: No target solid found; returning original")
        return shape

    other_solids = [s for s in solids if not s.wrapped.IsSame(target_solid.wrapped)]

    # --- determine fillet height by finding the nearby r~15.75 circle edge just below the front plane ---
    # The index hints one such edge is at y=2.675 for r=15.75 (0.5mm below y_front).
    best_y = None
    best_info = None
    for e in target_solid.Edges():
        cd = circle_data(e)
        if not cd:
            continue
        cx, cy, cz, r = cd
        if abs(cx) > 0.2 or abs(cz) > 0.2:
            continue
        if abs(r - r_cyl_named) > 0.2:
            continue
        if cy >= y_front - 1e-6:
            continue
        # prefer the one closest to the front plane
        d = abs((y_front - 0.5) - cy)
        if best_info is None or d < best_info[0]:
            best_info = (d, e, cd)
            best_y = cy

    if best_y is None:
        fill_depth = 0.55  # safe default around the expected 0.5mm fillet
        print("WARNING: Could not find the cyl/fillet circle edge below front; using default fill_depth=0.55")
    else:
        fill_depth = (y_front - best_y) + 0.02  # small epsilon to ensure full cover
        print("Found likely cyl/fillet junction circle below front:")
        _, e_b, (cx, cy, cz, r) = best_info
        print(f"  center={tuple(round(x,6) for x in (cx,cy,cz))} r={r:.6f} y={cy:.6f}")
        print(f"  computed fill_depth=(y_front - y_edge)+0.02 = {fill_depth:.6f}")

    # --- heal to a sharp edge by unioning an annular ring that restores the missing corner volume ---
    # Use the NAMED radii: inner=15.25 (edge_idx 0), outer=15.75 (cyl face #2)
    r_inner = r_edge_named
    r_outer = r_cyl_named

    if not (r_outer > r_inner):
        print("ERROR: r_outer not greater than r_inner; cannot build annular heal ring. Returning original")
        return shape

    wp_front = cq.Workplane(cq.Plane(origin=(0, y_front, 0), normal=(0, 1, 0), xDir=(1, 0, 0)))
    heal_ring = wp_front.circle(r_outer).circle(r_inner).extrude(-fill_depth).val()

    healed = target_solid.fuse(heal_ring)

    # --- find the NEW sharp rim edge at the front plane (y=3.175) and radius ~15.75 ---
    rim_candidates = []
    for e in healed.Edges():
        cd = circle_data(e)
        if not cd:
            continue
        cx, cy, cz, r = cd
        if abs(cx) > 0.2 or abs(cz) > 0.2:
            continue
        if abs(cy - y_front) > 0.05:
            continue
        if abs(r - r_outer) > 0.2:
            continue
        rim_candidates.append((abs(r - r_outer) + abs(cy - y_front), e, cd))

    print(f"Rim candidates on healed solid near front: {len(rim_candidates)}")
    rim_edge = None
    if rim_candidates:
        rim_candidates.sort(key=lambda t: t[0])
        _, rim_edge, (cx, cy, cz, r) = rim_candidates[0]
        print("Selected front rim edge for chamfer:")
        print(f"  center={tuple(round(x,6) for x in (cx,cy,cz))} r={r:.6f} delta_y={cy - y_front:.6f}")
    else:
        print("WARNING: No suitable front rim edge found; returning healed-without-chamfer")

    # --- Apply 1.0 mm chamfer on that rim edge only ---
    chamfered = healed
    if rim_edge is not None:
        try:
            rim_h = rim_edge.hashCode()
        except Exception:
            try:
                rim_h = rim_edge.wrapped.HashCode(2147483647)
            except Exception:
                rim_h = None

        class _SameHashEdgeSelector(cq.selectors.Selector):
            def __init__(self, h):
                self._h = h
            def filter(self, objectList):
                out = []
                for o in objectList:
                    try:
                        oh = o.hashCode()
                    except Exception:
                        try:
                            oh = o.wrapped.HashCode(2147483647)
                        except Exception:
                            continue
                    if self._h is not None and oh == self._h:
                        out.append(o)
                return out

        try:
            wp = cq.Workplane().newObject([healed])
            wp2 = wp.edges(_SameHashEdgeSelector(rim_h)).chamfer(chamfer_size)
            chamfered = wp2.val()
            print("Chamfer applied on selected front rim edge.")
        except Exception as ex:
            print("WARNING: Chamfer failed; returning healed solid without chamfer:", ex)
            chamfered = healed

    # --- Reassemble compound with other solids unchanged ---
    if other_solids:
        result = cq.Compound.makeCompound([chamfered] + other_solids)
    else:
        result = chamfered

    # --- Placement self-check (added material and removed material) ---
    try:
        removed = target_solid.cut(chamfered)
        added = chamfered.cut(target_solid)
        print("SELF-CHECK (edited solid diff):")
        print(f"  removed.Volume={removed.Volume():.6f}  added.Volume={added.Volume():.6f}")
        if added.Volume() > 1e-6:
            ac = pnt_tuple(added.Center())
            bb = added.BoundingBox()
            print(f"  added.Center={tuple(round(x,6) for x in ac)}")
            print(f"  added.BBox=(xmin={bb.xmin:.6f}, ymin={bb.ymin:.6f}, zmin={bb.zmin:.6f}, xmax={bb.xmax:.6f}, ymax={bb.ymax:.6f}, zmax={bb.zmax:.6f})")
            print(f"  added delta_y_to_front = {ac[1] - y_front:.6f} mm")
        if removed.Volume() > 1e-6:
            rc = pnt_tuple(removed.Center())
            bb = removed.BoundingBox()
            print(f"  removed.Center={tuple(round(x,6) for x in rc)}")
            print(f"  removed.BBox=(xmin={bb.xmin:.6f}, ymin={bb.ymin:.6f}, zmin={bb.zmin:.6f}, xmax={bb.xmax:.6f}, ymax={bb.ymax:.6f}, zmax={bb.zmax:.6f})")
            print(f"  removed delta_y_to_front = {rc[1] - y_front:.6f} mm")
    except Exception as ex:
        print("WARNING: self-check boolean diff failed:", ex)

    return result