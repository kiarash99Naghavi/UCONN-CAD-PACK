def my_cad_function(args):
    import cadquery as cq
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Defeaturing

    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input
    base = shape.val() if hasattr(shape, "val") else shape

    def vfmt(v):
        return f"({v.x:.3f},{v.y:.3f},{v.z:.3f})"

    preserve_r = 14.1421
    rtol = 0.15
    print("NUMBERS:")
    print(f"  preserve bore wall: cylindrical face radius r={preserve_r:.4f} (tolerance ±{rtol:.3f})")
    print("  preserve bore opening: circular edges with same r")

    sols = base.Solids()
    print(f"INFO: imported solids={len(sols)}")
    if not sols:
        print("SELECTED: 0 solids for editing (ERROR)")
        return shape

    solid = sols[0]
    bb0 = solid.BoundingBox()
    print(
        "INFO: base bbox "
        f"min=({bb0.xmin:.3f},{bb0.ymin:.3f},{bb0.zmin:.3f}) "
        f"max=({bb0.xmax:.3f},{bb0.ymax:.3f},{bb0.zmax:.3f})"
    )

    faces = solid.Faces()
    edges = solid.Edges()
    print(f"INFO: solid faces={len(faces)} edges={len(edges)}")

    # Face indices named by sub-goal (may include some out-of-range; skip safely)
    # cylindrical blend faces: 2,6,9,23,25,27,28,29,35,36,37
    # conical chamfer faces: 0,4
    # BSPLINE corner faces: 21,22
    # spherical corner faces: 30,33
    target_face_indices = [
        2, 6, 9, 23, 25, 27, 28, 29, 35, 36, 37,
        0, 4,
        21, 22,
        30, 33,
    ]

    # Resolve faces by index and decide which to remove (skip preserved bore wall)
    resolved = []
    skipped = []
    for idx in target_face_indices:
        if idx < 0 or idx >= len(faces):
            print(f"INFO: face idx {idx} out of range for current solid (faces={len(faces)}); skipping")
            continue
        f = faces[idx]
        gt = f.geomType()
        c = f.Center()
        area = f.Area()
        rad = None
        try:
            if gt in ("CYLINDER", "CONE", "SPHERE"):
                rad = f.radius()
        except Exception:
            rad = None

        # Preserve true full-sweep bore wall by radius/area heuristic
        if gt == "CYLINDER" and rad is not None and abs(rad - preserve_r) <= rtol and area > 5000:
            skipped.append(idx)
            print(
                f"INFO: SKIP preserve candidate bore wall face #{idx} "
                f"geom={gt} r={rad:.4f} area={area:.1f} center={vfmt(c)}"
            )
            continue

        resolved.append((idx, f, gt, area, c, rad))
        print(
            f"INFO: target face #{idx} geom={gt} area={area:.1f} center={vfmt(c)}"
            + (f" r={rad:.4f}" if rad is not None else "")
        )

    print(f"SELECTED: {len(resolved)} faces for defeaturing (remove blends/chamfers)")
    if skipped:
        print(f"SELECTED: {len(skipped)} faces skipped to preserve bore wall   idx={skipped}")

    # Pre-check: witness the bore opening circular edges (r ~ 14.1421)
    def find_bore_edges(shp):
        out = []
        for i, e in enumerate(shp.Edges()):
            if e.geomType() != "CIRCLE":
                continue
            try:
                r = e.radius()
            except Exception:
                continue
            if abs(r - preserve_r) <= rtol:
                out.append(i)
        return out

    bore_edges_before = find_bore_edges(solid)
    print(
        f"SELECTED: {len(bore_edges_before)} circular edges with r≈{preserve_r:.4f}±{rtol:.3f} (bore witness) "
        f"before idx={(bore_edges_before[:40] + (["..."] if len(bore_edges_before) > 40 else []))}"
    )

    v0 = solid.Volume()
    print(f"CHECK: volume before defeaturing={v0:.3f} mm^3")

    def run_defeature(cur_solid, face_list):
        # Returns cq.Shape (solid) or None
        try:
            d = BRepAlgoAPI_Defeaturing()
            if hasattr(d, "SetShape"):
                d.SetShape(cur_solid.wrapped)
            else:
                print("ERROR: BRepAlgoAPI_Defeaturing has no SetShape() in this environment")
                return None

            add_ok = 0
            for ff in face_list:
                try:
                    d.AddFaceToRemove(ff.wrapped)
                    add_ok += 1
                except Exception as e:
                    print(f"WARN: AddFaceToRemove failed for a face: {e}")

            print(f"SELECTED: {add_ok} faces added to defeaturing operator")
            d.Build()
            if hasattr(d, "IsDone"):
                try:
                    done = d.IsDone()
                    print(f"INFO: defeaturing IsDone()={done}")
                except Exception:
                    pass
            res = d.Shape()
            out = cq.Shape.cast(res)
            return out
        except Exception as e:
            print(f"ERROR: defeaturing exception: {e}")
            return None

    # Attempt 1: all at once
    faces_to_remove = [t[1] for t in resolved]
    edited = None
    if faces_to_remove:
        print("INFO: defeaturing attempt 1 (all target faces at once)")
        edited = run_defeature(solid, faces_to_remove)

    # If failed, do iterative per-face
    if edited is None:
        print("WARN: defeaturing attempt 1 failed; trying iterative per-face defeaturing")
        cur = solid
        successes = 0
        for (idx, f, gt, area, c, rad) in resolved:
            out = run_defeature(cur, [f])
            if out is None:
                print(f"FAIL: defeaturing single face #{idx} (geom={gt})")
                continue
            try:
                dv = out.Volume() - cur.Volume()
            except Exception:
                dv = 0.0
            print(f"OK: defeatured face #{idx} (geom={gt})  dV={dv:+.3f} mm^3")
            cur = out
            successes += 1
        edited = cur
        print(f"INFO: iterative defeaturing successes={successes} of targets={len(resolved)}")

    # Hard rule: do NOT return input unchanged silently.
    # If edited equals solid (no face removed), still return edited (it is at least the attempted result).
    try:
        v1 = edited.Volume()
        print(f"CHECK: volume after defeaturing={v1:.3f} mm^3  delta={v1 - v0:+.3f}")
    except Exception as e:
        print(f"WARN: could not compute volume after defeaturing: {e}")

    try:
        bb1 = edited.BoundingBox()
        print(
            "CHECK: edited bbox "
            f"min=({bb1.xmin:.3f},{bb1.ymin:.3f},{bb1.zmin:.3f}) "
            f"max=({bb1.xmax:.3f},{bb1.ymax:.3f},{bb1.zmax:.3f})"
        )
    except Exception as e:
        print(f"WARN: could not compute edited bbox: {e}")

    # Post-check: bore witness edges still present
    try:
        bore_edges_after = find_bore_edges(edited)
        print(
            f"SELECTED: {len(bore_edges_after)} circular edges with r≈{preserve_r:.4f}±{rtol:.3f} (bore witness) "
            f"after idx={(bore_edges_after[:40] + (["..."] if len(bore_edges_after) > 40 else []))}"
        )
    except Exception as e:
        print(f"WARN: could not scan bore witness edges after: {e}")

    if len(sols) == 1:
        return edited

    out = cq.Compound.makeCompound([s for i, s in enumerate(sols) if i != 0] + [edited])
    return out