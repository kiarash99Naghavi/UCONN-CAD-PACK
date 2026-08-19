def my_cad_function(args):
    import cadquery as cq
    shape = cq.importers.importStep(args["input_file"])
    base = shape.val() if hasattr(shape, 'val') else shape
    from OCP.BRepAdaptor import BRepAdaptor_Surface, BRepAdaptor_Curve

    def p3(v):
        try:
            vals = v.toTuple()
            return tuple(round(float(c), 6) for c in vals)
        except Exception:
            return (round(float(v.X()), 6), round(float(v.Y()), 6), round(float(v.Z()), 6))

    def dist(a, b):
        return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2) ** 0.5

    def bb_dict(s):
        bb = s.BoundingBox()
        return {
            "xmin": round(float(bb.xmin), 6),
            "ymin": round(float(bb.ymin), 6),
            "zmin": round(float(bb.zmin), 6),
            "xmax": round(float(bb.xmax), 6),
            "ymax": round(float(bb.ymax), 6),
            "zmax": round(float(bb.zmax), 6),
        }

    def edge_circle_info(edge):
        out = {"center": p3(edge.Center()), "radius": None}
        try:
            adap = BRepAdaptor_Curve(edge.wrapped)
            circ = adap.Circle()
            loc = circ.Location()
            out["center"] = (round(float(loc.X()), 6), round(float(loc.Y()), 6), round(float(loc.Z()), 6))
            out["radius"] = round(float(circ.Radius()), 6)
        except Exception:
            try:
                out["radius"] = round(float(edge.radius()), 6)
            except Exception:
                pass
        return out

    def face_cyl_info(face):
        info = {"center": p3(face.Center()), "axis": None, "radius": None, "area": round(float(face.Area()), 6)}
        try:
            adap = BRepAdaptor_Surface(face.wrapped)
            cyl = adap.Cylinder()
            d = cyl.Axis().Direction()
            info["axis"] = (round(float(d.X()), 6), round(float(d.Y()), 6), round(float(d.Z()), 6))
            info["radius"] = round(float(cyl.Radius()), 6)
        except Exception:
            pass
        return info

    solids = base.Solids()
    print(f"SELECTED: {len(solids)} solids in imported shape for edit")
    target_solid_index = 0
    target_solid = solids[target_solid_index] if len(solids) else base

    chamfer_size = 0.2
    small_hole_face_idxs = [49, 50, 51, 52]
    large_hole_face_idxs = [44, 46]
    target_face_idxs = small_hole_face_idxs + large_hole_face_idxs
    target_edge_idxs = [180, 181, 183, 184, 186, 187, 189, 190, 162, 163, 173, 174]

    print("TARGET numbers: chamfer=0.2 mm")
    print("TARGET numbers: small-hole face_idx=[49,50,51,52] axis=[0.0,1.0,0.0] edge_idx=[180,181,183,184,186,187,189,190]")
    print("TARGET numbers: large-hole face_idx=[44,46] axis=[1.0,0.0,0.0] edge_idx=[162,163,173,174]")

    orig_faces = target_solid.Faces()
    orig_edges = target_solid.Edges()

    print(f"SELECTED: {len(target_face_idxs)} faces for hole-wall verification idx={target_face_idxs}")
    face_infos = {}
    for idx in target_face_idxs:
        f = orig_faces[idx]
        info = face_cyl_info(f)
        face_infos[idx] = info
        print(f"FACE idx={idx} geom={f.geomType()} center={info['center']} area={info['area']} radius={info['radius']} axis={info['axis']}")

    print(f"SELECTED: {len(target_edge_idxs)} edges for hole rim chamfer idx={target_edge_idxs}")
    edge_infos = {}
    target_points = []
    for idx in target_edge_idxs:
        e = orig_edges[idx]
        info = edge_circle_info(e)
        edge_infos[idx] = info
        target_points.append(info["center"])
        print(f"EDGE idx={idx} geom={e.geomType()} center={info['center']} radius={info['radius']}")

    xs = [p[0] for p in target_points]
    ys = [p[1] for p in target_points]
    zs = [p[2] for p in target_points]
    target_centroid = (round(sum(xs) / len(xs), 6), round(sum(ys) / len(ys), 6), round(sum(zs) / len(zs), 6))
    print(f"TARGET mouth-center bbox: x=[{round(min(xs),6)},{round(max(xs),6)}] y=[{round(min(ys),6)},{round(max(ys),6)}] z=[{round(min(zs),6)},{round(max(zs),6)}]")
    print(f"TARGET mouth-center centroid: {target_centroid}")

    current = target_solid
    success = []
    failed = []
    for idx in target_edge_idxs:
        pt = edge_infos[idx]["center"]
        wp_sel = cq.Workplane(obj=current).edges(cq.selectors.NearestToPointSelector(pt))
        selected_edges = wp_sel.vals()
        print(f"SELECTED: {len(selected_edges)} edges for hole rim chamfer edge_idx={idx} near={pt}")
        if len(selected_edges) != 1:
            print(f"WARNING: expected 1 edge for edge_idx={idx}, got {len(selected_edges)}")
            failed.append(idx)
            continue
        try:
            current = wp_sel.chamfer(chamfer_size).val()
            success.append(idx)
            print(f"APPLIED: chamfer={chamfer_size} mm on edge_idx={idx}")
        except Exception as exc:
            print(f"FAILED: chamfer on edge_idx={idx} with error: {exc}")
            failed.append(idx)

    print(f"SELECTED: {len(success)} edges successfully chamfered for hole rim chamfer idx={success}")
    if failed:
        print(f"SELECTED: {len(failed)} edges FAILED for hole rim chamfer idx={failed}")

    out_solid = current

    try:
        removed = target_solid.cut(out_solid)
        try:
            removed_center = p3(removed.Center())
            print(f"REMOVED: chamfer material center={removed_center} bbox={bb_dict(removed)}")
            print(f"REMOVED vs target-centroid delta=({round(removed_center[0]-target_centroid[0],6)},{round(removed_center[1]-target_centroid[1],6)},{round(removed_center[2]-target_centroid[2],6)})")
        except Exception as exc:
            print(f"REMOVED: could not measure center/bbox: {exc}")
        try:
            print(f"REMOVED: volume={round(float(removed.Volume()), 6)}")
        except Exception as exc:
            print(f"REMOVED: could not measure volume: {exc}")
    except Exception as exc:
        print(f"REMOVED: could not compute removed material with base.cut(out): {exc}")

    edited_edges = out_solid.Edges()
    print(f"SELECTED: {len(edited_edges)} total edges in edited solid for sharp-rim replacement verification")
    for idx in target_edge_idxs:
        ref = edge_infos[idx]
        found = 0
        for e in edited_edges:
            if e.geomType() != "CIRCLE":
                continue
            info = edge_circle_info(e)
            if info["radius"] is None:
                continue
            if abs(info["radius"] - ref["radius"]) <= 0.005 and dist(info["center"], ref["center"]) <= 0.005:
                found += 1
        print(f"VERIFY: edge_idx={idx} original sharp rim remaining count={found} at center={ref['center']} radius={ref['radius']} expected=0")

    edited_cyl_infos = []
    for f in out_solid.Faces():
        if f.geomType() == "CYLINDER":
            try:
                edited_cyl_infos.append(face_cyl_info(f))
            except Exception:
                pass
    print(f"SELECTED: {len(edited_cyl_infos)} cylindrical faces in edited solid for hole center/axis verification")

    for idx in target_face_idxs:
        ref = face_infos[idx]
        candidates = [ci for ci in edited_cyl_infos if ci["radius"] is not None and ref["radius"] is not None and abs(ci["radius"] - ref["radius"]) <= 0.005]
        if not candidates:
            print(f"VERIFY: face_idx={idx} no matching cylindrical face found after edit")
            continue
        best = min(candidates, key=lambda ci: dist(ci["center"], ref["center"]))
        dcenter = dist(best["center"], ref["center"])
        dotabs = None
        if ref["axis"] is not None and best["axis"] is not None:
            dotabs = abs(ref["axis"][0] * best["axis"][0] + ref["axis"][1] * best["axis"][1] + ref["axis"][2] * best["axis"][2])
            dotabs = round(dotabs, 6)
        print(f"VERIFY: face_idx={idx} center_before={ref['center']} center_after={best['center']} delta_center={round(dcenter,6)} axis_before={ref['axis']} axis_after={best['axis']} |dot|={dotabs} radius_before={ref['radius']} radius_after={best['radius']}")

    if len(solids) > 1:
        rebuilt = [s for i, s in enumerate(solids) if i != target_solid_index] + [out_solid]
        out = cq.Compound.makeCompound(rebuilt)
    else:
        out = out_solid
    return out