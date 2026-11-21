"""Generate a small editable CAD plan from explicit wall and opening data.

The demo is synthetic. This script does not interpret scans or infer dimensions.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import subprocess
import time
from collections import Counter
from pathlib import Path

import ezdxf
from ezdxf import units
from shapely import STRtree
from shapely.geometry import LineString, MultiPolygon, Point, Polygon
from shapely.ops import polygonize_full, unary_union

EPS = 0.01  # millimetres; detection only, never silently snap source geometry
EXACT = 1e-7
EVIDENCE = {"source", "estimated", "demo"}


def point(value, label):
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ValueError(f"{label}: expected a two-coordinate point")
    p = tuple(float(x) for x in value)
    if not all(math.isfinite(x) for x in p):
        raise ValueError(f"{label}: coordinates must be finite")
    return p


def positive(value, label):
    value = float(value)
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{label}: expected a positive finite number")
    return value


def require_evidence(item):
    if item.get("evidence") not in EVIDENCE:
        raise ValueError(f"{item.get('id', item.get('text', '?'))}: missing evidence category")


def prepare(data):
    if data.get("units") != "mm" or data.get("purpose") not in {"demo", "field_review"}:
        raise ValueError("Specify units=mm and purpose=demo or field_review")
    if data["purpose"] == "field_review":
        for group in ("walls", "openings", "room_labels"):
            if any(item.get("evidence") == "demo" for item in data.get(group, [])):
                raise ValueError("Demo geometry cannot be presented as field-review data")
    raw_walls = data.get("walls")
    if not isinstance(raw_walls, list) or not raw_walls:
        raise ValueError("At least one wall is required")
    walls = {}
    for raw in raw_walls:
        require_evidence(raw)
        ident = str(raw["id"])
        if ident in walls:
            raise ValueError(f"Duplicate wall id: {ident}")
        a, b = point(raw["start"], ident), point(raw["end"], ident)
        line = LineString([a, b])
        if line.length <= EPS:
            raise ValueError(f"{ident}: wall is too short")
        alignment = raw.get("alignment")
        if alignment not in {"orthogonal", "as_drawn"}:
            raise ValueError(f"{ident}: specify orthogonal or as_drawn")
        if alignment == "orthogonal" and abs(a[0]-b[0]) > EXACT and abs(a[1]-b[1]) > EXACT:
            raise ValueError(f"{ident}: non-orthogonal wall; do not silently rotate it")
        width = positive(raw["thickness"], ident)
        if line.length <= width:
            raise ValueError(f"{ident}: wall is not longer than its thickness")
        walls[ident] = {"axis": line, "a": a, "b": b, "width": width, "evidence": raw["evidence"]}

    ids = list(walls)
    axis_tree = STRtree([walls[name]["axis"] for name in ids])
    for i, name in enumerate(ids):
        for j in axis_tree.query(walls[name]["axis"], predicate="intersects"):
            j = int(j)
            if j <= i:
                continue
            other = ids[j]
            overlap = walls[name]["axis"].intersection(walls[other]["axis"])
            if overlap.length > EPS:
                raise ValueError(f"{name}/{other}: overlapping wall axes require review")
    for wall in walls.values():
        wall["solid"] = wall["axis"].buffer(wall["width"]/2, cap_style="square", join_style="mitre")
    solid_tree = STRtree([walls[name]["solid"] for name in ids])

    axes = unary_union([w["axis"] for w in walls.values()])
    faces, cuts, dangles, invalid = polygonize_full(axes)
    face_list = list(faces.geoms)
    face_tree = STRtree(face_list)
    expected = data.get("expected_spaces")
    if expected is not None and len(faces.geoms) != int(expected):
        raise ValueError(f"Expected {expected} enclosed centerline spaces, found {len(faces.geoms)}")
    if expected is not None and (not cuts.is_empty or not dangles.is_empty or not invalid.is_empty):
        raise ValueError("Unclosed or dangling centerline segments require review")

    before = unary_union([w["solid"] for w in walls.values()])
    if not before.is_valid:
        raise ValueError("Wall union is invalid")
    cuts_list, openings = [], []
    opening_ids = set()
    for raw in data.get("openings", []):
        require_evidence(raw)
        ident = str(raw["id"])
        if ident in opening_ids:
            raise ValueError(f"Duplicate opening id: {ident}")
        opening_ids.add(ident)
        host = walls.get(raw["wall"])
        if host is None:
            raise ValueError(f"{ident}: host wall does not exist")
        kind = raw.get("kind")
        if kind not in {"door", "window"}:
            raise ValueError(f"{ident}: kind must be door or window")
        width = positive(raw["width"], ident)
        center = point(raw["center"], ident)
        axis = host["axis"]
        projected = axis.project(Point(center))
        if axis.distance(Point(center)) > EXACT:
            raise ValueError(f"{ident}: center is off the host axis")
        if projected-width/2 <= EPS or projected+width/2 >= axis.length-EPS:
            raise ValueError(f"{ident}: opening reaches a wall end")
        tx = (host["b"][0]-host["a"][0])/axis.length
        ty = (host["b"][1]-host["a"][1])/axis.length
        nx, ny = -ty, tx
        sx, sy = center[0]-tx*width/2, center[1]-ty*width/2
        ex, ey = center[0]+tx*width/2, center[1]+ty*width/2
        depth = host["width"]/2 + 2.0
        cutter = Polygon([(sx+nx*depth, sy+ny*depth), (ex+nx*depth, ey+ny*depth),
                          (ex-nx*depth, ey-ny*depth), (sx-nx*depth, sy-ny*depth)])
        candidates = [ids[int(j)] for j in solid_tree.query(cutter, predicate="intersects")]
        others = unary_union([walls[key]["solid"] for key in candidates if key != raw["wall"]])
        if not others.is_empty and cutter.intersection(others).area > EPS:
            raise ValueError(f"{ident}: opening also cuts another wall; review the junction")
        if any(cutter.intersection(c).area > EPS for c in cuts_list):
            raise ValueError(f"{ident}: openings overlap")
        if kind == "door" and raw.get("swing") not in {"left", "right"}:
            raise ValueError(f"{ident}: door swing must be explicit")
        cuts_list.append(cutter)
        openings.append({"id": ident, "kind": kind, "wall": raw["wall"], "width": width,
                         "center": center, "start": (sx, sy), "tangent": (tx, ty), "normal": (nx, ny),
                         "swing": raw.get("swing"), "evidence": raw["evidence"],
                         "host_width": host["width"]})
    after = before.difference(unary_union(cuts_list)) if cuts_list else before
    if not after.is_valid:
        raise ValueError("Wall geometry after openings is invalid")
    for o in openings:
        x, y = o["center"]; nx, ny = o["normal"]; depth = o["host_width"]/2 + 1
        probe = LineString([(x-nx*depth, y-ny*depth), (x+nx*depth, y+ny*depth)])
        if probe.intersection(after).length > EPS:
            raise ValueError(f"{o['id']}: material remains across the opening")
    flow_links = []
    for o in openings:
        if o["kind"] != "door":
            continue
        x, y = o["center"]; nx, ny = o["normal"]
        offset = o["host_width"]/2 + 1.0
        sides = []
        for sign in (-1, 1):
            pt = Point(x+sign*nx*offset, y+sign*ny*offset)
            match = [int(i)+1 for i in face_tree.query(pt, predicate="within")]
            sides.append(match[0] if len(match) == 1 else "outside_or_unresolved")
        flow_links.append({"door": o["id"], "candidate_sides": sides})
    # An exterior entrance is a source assertion, not inferred from geometry alone.
    # Walkability here only means a chain of modeled door gaps between centerline faces.
    access = None
    if "outside_access_doors" in data:
        entry_ids = data["outside_access_doors"]
        if not isinstance(entry_ids, list) or not entry_ids:
            raise ValueError("outside_access_doors must be a nonempty list when supplied")
        by_id = {link["door"]: link["candidate_sides"] for link in flow_links}
        if len(set(entry_ids)) != len(entry_ids) or any(x not in by_id for x in entry_ids):
            raise ValueError("outside_access_doors contains an unknown or duplicate door")
        adjacency = {i+1: set() for i in range(len(face_list))}
        for sides in by_id.values():
            if all(isinstance(x, int) for x in sides) and sides[0] != sides[1]:
                adjacency[sides[0]].add(sides[1])
                adjacency[sides[1]].add(sides[0])
        roots = set()
        for ident in entry_ids:
            sides = by_id[ident]
            inside = [x for x in sides if isinstance(x, int)]
            if len(inside) != 1 or "outside_or_unresolved" not in sides:
                raise ValueError(f"{ident}: supplied entrance does not have one modeled interior side")
            roots.add(inside[0])
        reached = set(roots)
        stack = list(roots)
        while stack:
            for nxt in adjacency[stack.pop()]:
                if nxt not in reached:
                    reached.add(nxt)
                    stack.append(nxt)
        access = {"source_asserted_entry_doors": entry_ids, "reachable_spaces": len(reached),
                  "unreachable_space_ids": sorted(set(adjacency)-reached)}
    labels = data.get("room_labels", [])
    for label in labels:
        require_evidence(label)
        p = Point(point(label["point"], "room label"))
        if len(face_tree.query(p, predicate="within")) != 1 or before.contains(p):
            raise ValueError(f"{label['text']}: label is not inside a centerline space")
    return {"walls": walls, "wall_before": before, "wall_after": after,
            "openings": openings, "spaces": faces, "labels": labels,
            "cuts": cuts, "dangles": dangles, "invalid": invalid, "flow_links": flow_links,
            "access": access}


def polygons(geom):
    if isinstance(geom, Polygon):
        return [geom]
    if isinstance(geom, MultiPolygon):
        return list(geom.geoms)
    return [g for g in getattr(geom, "geoms", []) if isinstance(g, Polygon)]


def build_dxf(data, model, path):
    doc = ezdxf.new("R2018")
    doc.units = units.MM
    for name, color in (("A-WALL", 7), ("A-DOOR", 2), ("A-WINDOW", 4), ("A-ANNO", 7), ("A-NOTE", 2)):
        doc.layers.new(name, dxfattribs={"color": color})
    msp = doc.modelspace()
    for poly in polygons(model["wall_after"]):
        for ring in [poly.exterior, *poly.interiors]:
            msp.add_lwpolyline(list(ring.coords)[:-1], close=True, dxfattribs={"layer": "A-WALL"})
    for o in model["openings"]:
        width = o["width"]
        token = repr(width).replace(".", "_").replace("+", "P").replace("-", "M")
        if o["kind"] == "window":
            token += "_" + repr(o["host_width"]).replace(".", "_").replace("+", "P").replace("-", "M")
        name = f"{o['kind'].upper()}_{token}_{o['swing'] or 'NA'}"
        if name not in doc.blocks:
            block = doc.blocks.new(name)
            layer = "A-DOOR" if o["kind"] == "door" else "A-WINDOW"
            if o["kind"] == "door":
                side = 1 if o["swing"] == "left" else -1
                block.add_line((0, 0), (0, side*width), dxfattribs={"layer": layer})
                start, end = (0, 90) if side == 1 else (270, 360)
                block.add_arc((0, 0), width, start, end, dxfattribs={"layer": layer})
            else:
                # Symbolic glazing lines, not a claim about real frame details.
                spacing = min(25.0, o["host_width"]/4)
                for y in (-spacing, spacing):
                    block.add_line((0, y), (width, y), dxfattribs={"layer": layer})
                for x in (0, width):
                    block.add_line((x, -spacing), (x, spacing), dxfattribs={"layer": layer})
        tx, ty = o["tangent"]
        rotation = math.degrees(math.atan2(ty, tx))
        layer = "A-DOOR" if o["kind"] == "door" else "A-WINDOW"
        msp.add_blockref(name, o["start"], dxfattribs={"rotation": rotation, "layer": layer})
        x, y = o["center"]; nx, ny = o["normal"]
        text = o["id"] + (" VERIFY" if o["evidence"] == "estimated" else "")
        msp.add_text(text, dxfattribs={"insert": (x+nx*130, y+ny*130), "height": 100, "layer": "A-ANNO"})
    for label in model["labels"]:
        text = str(label["text"]) + (" VERIFY" if label["evidence"] == "estimated" else "")
        msp.add_text(text, dxfattribs={"insert": point(label["point"], "label"), "height": 160, "layer": "A-ANNO"})
    for ident, wall in model["walls"].items():
        if wall["evidence"] != "estimated":
            continue
        a, b, axis = wall["a"], wall["b"], wall["axis"]
        nx, ny = -(b[1]-a[1])/axis.length, (b[0]-a[0])/axis.length
        mid = axis.interpolate(0.5, normalized=True)
        offset = wall["width"]/2 + 120
        msp.add_text(f"{ident} EST VERIFY", dxfattribs={
            "insert": (mid.x+nx*offset, mid.y+ny*offset), "height": 100, "layer": "A-NOTE"})
    minx, miny, _, _ = model["wall_before"].bounds
    banner = "DEMO ONLY - NOT PROJECT DIMENSIONS" if data["purpose"] == "demo" else "FIELD REVIEW - VERIFY UNCERTAIN DATA"
    msp.add_text(banner, dxfattribs={"insert": (minx, miny-600), "height": 180, "layer": "A-NOTE"})
    if data["purpose"] == "field_review" and any(w["evidence"] == "estimated" for w in model["walls"].values()):
        msp.add_text("ESTIMATED WALLS - FIELD VERIFY", dxfattribs={"insert": (minx, miny-850), "height": 120, "layer": "A-NOTE"})
    auditor = doc.audit()
    if auditor.has_errors:
        raise ValueError(f"DXF audit failed: {len(auditor.errors)} errors")
    doc.saveas(path)
    reopened = ezdxf.readfile(path)
    check = reopened.audit()
    if check.has_errors or reopened.units != units.MM:
        raise ValueError("DXF reopen audit or units check failed")
    return doc, Counter(e.dxftype() for e in reopened.modelspace())


def render(doc, path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from ezdxf.addons.drawing import Frontend, RenderContext
    from ezdxf.addons.drawing.matplotlib import MatplotlibBackend
    fig = plt.figure(figsize=(12, 8), dpi=150)
    ax = fig.add_axes((0, 0, 1, 1))
    Frontend(RenderContext(doc), MatplotlibBackend(ax)).draw_layout(doc.modelspace(), finalize=True)
    fig.savefig(path, dpi=150)
    plt.close(fig)


def decode_console(raw):
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16", errors="replace")
    if raw[:500].count(b"\x00") > 30:
        return raw.decode("utf-16-le", errors="replace")
    return raw.decode("gb18030", errors="replace")


def run_console(exe, drawing, script):
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    done = subprocess.run([str(exe), "/i", str(drawing), "/s", str(script)],
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          timeout=55, creationflags=flags, check=False)
    output = decode_console(done.stdout)
    if done.returncode != 0:
        raise RuntimeError(f"AutoCAD background exited {done.returncode}: {output[-1800:]}")
    return output


def convert_and_verify(exe, dxf, outdir, workdir, dxf_counts):
    dwg = outdir / "plan.dwg"
    save_script = workdir / "convert.scr"
    verify_script = workdir / "verify.scr"
    if dwg.exists() or save_script.exists() or verify_script.exists():
        raise FileExistsError("DWG or background scripts already exist")
    # AutoCAD suppresses file dialogs while a script/AutoLISP program is active.
    # Do not alter the registry-backed FILEDIA system variable.
    save_script.write_text('(command "_.SAVEAS" "2018" (strcat (getvar "DWGPREFIX") "plan.dwg"))\n'
                           '(princ "\\nCAD_BATCH_SAVE_DONE\\n")\n'
                           '(command "_.QUIT")\n', encoding="ascii")
    save_log = run_console(exe, dxf, save_script)
    if "CAD_BATCH_SAVE_DONE" not in save_log or not dwg.is_file() or dwg.stat().st_size < 1000:
        raise RuntimeError("AutoCAD did not confirm a nonempty DWG save")
    verify_script.write_text('(setq s (ssget "_X" \'((0 . "INSERT") (410 . "Model"))))\n'
                             '(princ (strcat "\\nCADVERIFY_INSERT=" (itoa (if s (sslength s) 0))))\n'
                             '(setq s (ssget "_X" \'((0 . "LWPOLYLINE") (410 . "Model"))))\n'
                             '(princ (strcat "\\nCADVERIFY_WALLPOLY=" (itoa (if s (sslength s) 0))))\n'
                             '(princ (strcat "\\nCADVERIFY_UNITS=" (itoa (getvar "INSUNITS"))))\n'
                             '(princ "\\nCADVERIFY_DONE\\n")\n'
                             '(command "_.QUIT")\n', encoding="ascii")
    verify_log = run_console(exe, dwg, verify_script)
    def value(key):
        found = re.findall(r"CADVERIFY_" + key + r"=(\d+)", verify_log)
        if not found:
            raise RuntimeError(f"Missing DWG verification marker {key}")
        return int(found[-1])
    observed = {"inserts": value("INSERT"), "wall_polylines": value("WALLPOLY"), "units": value("UNITS")}
    if "CADVERIFY_DONE" not in verify_log or observed != {
        "inserts": dxf_counts["INSERT"], "wall_polylines": dxf_counts["LWPOLYLINE"], "units": 4
    }:
        raise RuntimeError(f"DWG reopen check mismatch: {observed}")
    return dwg, observed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--work", type=Path, help="Separate directory for AutoCAD scratch scripts")
    parser.add_argument("--accoreconsole", type=Path, help="Optional AutoCAD core console executable")
    args = parser.parse_args()
    start = time.perf_counter()
    data = json.loads(args.input.read_text(encoding="utf-8"))
    model = prepare(data)
    prepared = time.perf_counter()
    args.output.mkdir(parents=True, exist_ok=True)
    names = ["plan.dxf", "plan.png", "result.json"]
    if args.accoreconsole:
        names += ["plan.dwg"]
    if any((args.output / n).exists() for n in names):
        raise FileExistsError("A planned output exists; refusing to overwrite")
    doc, counts = build_dxf(data, model, args.output / "plan.dxf")
    render(doc, args.output / "plan.png")
    drafted = time.perf_counter()
    dwg, verified = None, None
    if args.accoreconsole:
        workdir = args.work or (args.output / "_work")
        workdir.mkdir(parents=True, exist_ok=True)
        dwg, verified = convert_and_verify(args.accoreconsole, args.output / "plan.dxf", args.output, workdir, counts)
    finish = time.perf_counter()
    result = {
        "purpose": data["purpose"], "units": "mm", "source_wall_segments": len(model["walls"]),
        "centerline_spaces": len(model["spaces"].geoms),
        "openings": len(model["openings"]), "entity_counts_dxf": dict(counts),
        "door_candidate_connections": model["flow_links"],
        "door_access_graph": model["access"],
        "centerline_dangles": len(model["dangles"].geoms), "centerline_cuts": len(model["cuts"].geoms),
        "estimated_wall_ids": [name for name, wall in model["walls"].items() if wall["evidence"] == "estimated"],
        "estimated_opening_ids": [o["id"] for o in model["openings"] if o["evidence"] == "estimated"],
        "wall_union_valid": model["wall_before"].is_valid,
        "wall_after_openings_valid": model["wall_after"].is_valid,
        "opening_probes_clear": True, "dwg_reopen": verified,
        "seconds": {"geometry": round(prepared-start, 3), "dxf_and_preview": round(drafted-prepared, 3),
                    "dwg_conversion_and_reopen": round(finish-drafted, 3), "total": round(finish-start, 3)},
        "limits": ["Synthetic demo unless source data is supplied and reviewed",
                   "Straight wall segments only; not Tianzheng smart objects",
                   "Centerline spaces and door connections are candidates, not architectural or site acceptance",
                   "Access graph only considers modeled doors; it is not an egress or fire-safety check",
                   "Windows are symbolic; columns, stairs and dimensions are not generated"],
    }
    (args.output / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    access_summary = None if model["access"] is None else {
        "reachable_spaces": model["access"]["reachable_spaces"],
        "unreachable_spaces": len(model["access"]["unreachable_space_ids"]),
    }
    print(json.dumps({"purpose": result["purpose"], "source_wall_segments": result["source_wall_segments"],
                      "centerline_spaces": result["centerline_spaces"], "openings": result["openings"],
                      "door_access_graph": access_summary, "dwg_reopen": verified,
                      "seconds": result["seconds"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
