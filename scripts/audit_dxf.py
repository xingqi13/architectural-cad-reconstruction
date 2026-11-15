#!/usr/bin/env python3
"""Report 2D straight-line DXF junction candidates without editing the file."""
import argparse
from collections import Counter
import json
import math
from pathlib import Path

import ezdxf
from shapely.geometry import LineString, Point, box
from shapely.ops import nearest_points
from shapely.strtree import STRtree


def collect_segments(doc, selected):
    segments, skipped, disabled = [], Counter(), set()

    def add(a, b, entity):
        xy = (float(a[0]), float(a[1]), float(b[0]), float(b[1]))
        if not all(math.isfinite(v) for v in xy):
            skipped['nonfinite_segment'] += 1
            return
        if math.hypot(xy[2] - xy[0], xy[3] - xy[1]) < 1e-12:
            skipped['zero_length_segment'] += 1
            return
        segments.append({'geom': LineString([(xy[0], xy[1]), (xy[2], xy[3])]),
                         'handle': entity.dxf.handle, 'layer': entity.dxf.layer})

    for entity in doc.modelspace():
        layer = entity.dxf.layer
        if layer not in selected:
            continue
        layer_state = doc.layers.get(layer)
        if layer_state.is_off() or layer_state.is_frozen():
            disabled.add(layer)
            skipped['off_or_frozen_layer_entities'] += 1
            continue
        kind = entity.dxftype()
        if kind == 'LINE':
            a, b = entity.dxf.start, entity.dxf.end
            if abs(a.z) > 1e-8 or abs(b.z) > 1e-8:
                skipped['LINE_nonzero_z'] += 1
            else:
                add((a.x, a.y), (b.x, b.y), entity)
        elif kind == 'LWPOLYLINE':
            normal = entity.dxf.get('extrusion', (0, 0, 1))
            if abs(normal[0]) > 1e-8 or abs(normal[1]) > 1e-8 or abs(normal[2] - 1) > 1e-8 or abs(entity.dxf.elevation) > 1e-8:
                skipped['LWPOLYLINE_nonplanar_or_elevated'] += 1
                continue
            vertices = list(entity.get_points('xyb'))
            pairs = [(i, i + 1) for i in range(len(vertices) - 1)]
            if entity.closed and len(vertices) > 1:
                pairs.append((len(vertices) - 1, 0))
            for i, j in pairs:
                if abs(vertices[i][2]) > 1e-10:
                    skipped['LWPOLYLINE_curved_segment'] += 1
                else:
                    add(vertices[i], vertices[j], entity)
        else:
            skipped[kind] += 1
    return segments, skipped, sorted(disabled)


def reference_status(doc, path):
    refs = []
    for image in doc.objects.query('IMAGEDEF'):
        name = image.dxf.filename
        p = Path(name)
        p = p if p.is_absolute() else path.parent / p
        refs.append({'type': 'IMAGEDEF', 'name': name, 'exists': p.is_file()})
    for layout in doc.blocks:
        block = layout.block
        if block and block.dxf.get('flags', 0) & (4 | 8):
            name = block.dxf.get('xref_path', '')
            p = Path(name)
            p = p if p.is_absolute() else path.parent / p
            refs.append({'type': 'XREF', 'name': name, 'exists': bool(name) and p.is_file()})
    return refs


def audit(path, selected, unit_mm, gap_mm, axes_deg, angle_tol):
    doc = ezdxf.readfile(path)
    missing = sorted(selected - {layer.dxf.name for layer in doc.layers})
    if missing:
        raise ValueError('selected DXF layers are missing: ' + ', '.join(missing))
    segs, skipped, disabled = collect_segments(doc, selected)
    geoms = [s['geom'] for s in segs]
    tree = STRtree(geoms) if geoms else None
    eps, tolerance = .01 / unit_mm, gap_mm / unit_mm
    candidates, orthogonal, crosses, overlaps = [], [], [], []
    connected, isolated = 0, 0
    for i, row in enumerate(segs):
        g = row['geom']
        a, b = list(g.coords)
        angle = math.degrees(math.atan2(b[1] - a[1], b[0] - a[0]))
        deviation = abs(((angle - axes_deg + 45) % 90) - 45)
        if .02 < deviation <= angle_tol:
            orthogonal.append({'handle': row['handle'], 'layer': row['layer'],
                               'length_mm': round(g.length * unit_mm, 2),
                               'deviation_deg': round(deviation, 4), 'start': a, 'end': b})
        for end in (a, b):
            point = Point(end)
            hits = tree.query(box(end[0] - tolerance, end[1] - tolerance,
                                  end[0] + tolerance, end[1] + tolerance))
            neighbours = [(point.distance(geoms[int(j)]), int(j)) for j in hits if int(j) != i]
            if not neighbours:
                isolated += 1
                continue
            distance, j = min(neighbours)
            if distance <= eps:
                connected += 1
            elif distance <= tolerance:
                near = nearest_points(point, geoms[j])[1]
                candidates.append({'handle': row['handle'], 'layer': row['layer'],
                                   'endpoint': end, 'near_handle': segs[j]['handle'],
                                   'near_layer': segs[j]['layer'], 'near_point': (near.x, near.y),
                                   'distance_mm': round(distance * unit_mm, 3)})
            else:
                isolated += 1
        for j0 in tree.query(g):
            j = int(j0)
            if j <= i:
                continue
            other = geoms[j]
            if g.crosses(other):
                p = g.intersection(other)
                crosses.append({'handles': [row['handle'], segs[j]['handle']],
                                'point': (p.x, p.y)})
            else:
                overlap = g.intersection(other)
                if overlap.length > eps:
                    overlaps.append({'handles': [row['handle'], segs[j]['handle']],
                                     'overlap_mm': round(overlap.length * unit_mm, 3)})
    return {'input': str(path), 'selected_layers': sorted(selected),
            'drawing_insunits': doc.header.get('$INSUNITS', 0), 'unit_mm_supplied': unit_mm,
            'gap_tolerance_mm': gap_mm, 'reference_axes_deg': axes_deg,
            'angle_tolerance_deg': angle_tol, 'segments_checked': len(segs),
            'connected_endpoints': connected, 'isolated_endpoints_outside_tolerance': isolated,
            'near_gap_candidates': candidates, 'near_orthogonal_candidates': orthogonal,
            'crossing_candidates': crosses, 'overlap_candidates': overlaps,
            'skipped_types_or_segments': dict(skipped), 'off_or_frozen_layers': disabled,
            'image_and_xref_status': reference_status(doc, path),
            'limits': 'Straight 2D selected entities only; candidates can be intentional doors, joints or projections. No automatic repair or closure conclusion.'}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('dxf', type=Path)
    ap.add_argument('--layers', nargs='+', required=True)
    ap.add_argument('--unit-mm', type=float, required=True)
    ap.add_argument('--gap-mm', type=float, required=True)
    ap.add_argument('--axes-deg', type=float, default=0)
    ap.add_argument('--angle-tol-deg', type=float, default=2)
    ap.add_argument('--out', type=Path, required=True)
    a = ap.parse_args()
    if not a.dxf.is_file():
        ap.error('input DXF does not exist')
    if not all(math.isfinite(x) for x in (a.unit_mm, a.gap_mm, a.axes_deg, a.angle_tol_deg)) or a.unit_mm <= 0 or a.gap_mm <= .01 or not .02 < a.angle_tol_deg < 45:
        ap.error('unit-mm > 0, gap-mm > 0.01 and 0.02 < angle-tol-deg < 45 are required')
    result = audit(a.dxf, set(a.layers), a.unit_mm, a.gap_mm, a.axes_deg, a.angle_tol_deg)
    with a.out.open('x', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"Segments: {result['segments_checked']}; near gaps: {len(result['near_gap_candidates'])}; "
          f"crossings: {len(result['crossing_candidates'])}; overlaps: {len(result['overlap_candidates'])}; report: {a.out}")
    print('Candidates require architectural review; unhandled object types are listed in the report.')


if __name__ == '__main__':
    main()
