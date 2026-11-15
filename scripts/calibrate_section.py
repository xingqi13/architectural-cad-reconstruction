#!/usr/bin/env python3
"""Interpolate candidate heights from independently checked section controls."""
import argparse
import bisect
import json
import math
from pathlib import Path


def finite(value, field):
    x = float(value)
    if not math.isfinite(x):
        raise ValueError(f'{field} must be finite')
    return x


def calibrate(spec):
    ref = finite(spec['reference_x_px'], 'reference_x_px')
    slope = finite(spec['scan_slope_px_per_px'], 'scan_slope_px_per_px')
    levels = sorted([{'y_px': finite(a['y_px'], 'level.y_px'),
                      'level_mm': finite(a['level_mm'], 'level.level_mm'),
                      'source': str(a.get('source', ''))} for a in spec['levels']],
                    key=lambda a: a['y_px'])
    if len(levels) < 2 or any(a['y_px'] >= b['y_px'] or a['level_mm'] <= b['level_mm']
                              for a, b in zip(levels, levels[1:])):
        raise ValueError('levels need at least two distinct y positions with strictly decreasing height')
    ys = [a['y_px'] for a in levels]
    result = []
    for item in spec.get('samples', []):
        x = finite(item['x_px'], 'sample.x_px')
        y = finite(item['y_px'], 'sample.y_px')
        corrected = y - slope * (x - ref)
        item_result = {'name': str(item.get('name', '')), 'corrected_y_px': corrected}
        if corrected < ys[0] or corrected > ys[-1]:
            item_result.update({'estimated_mm': None, 'anchors': None, 'status': 'outside_control_range'})
        else:
            index = max(0, min(len(ys) - 2, bisect.bisect_right(ys, corrected) - 1))
            lo, hi = levels[index:index + 2]
            t = (corrected - lo['y_px']) / (hi['y_px'] - lo['y_px'])
            value = lo['level_mm'] + t * (hi['level_mm'] - lo['level_mm'])
            item_result.update({'estimated_mm': round(value, 2), 'anchors': [lo, hi],
                                'status': 'interpolated_candidate'})
        result.append(item_result)
    return {'controls': {'reference_x_px': ref, 'scan_slope_px_per_px': slope, 'levels': levels},
            'samples': result,
            'limits': 'Image interpolation only; anchors, correspondences and the physical height require independent checking.'}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('controls', type=Path)
    ap.add_argument('--out', type=Path, required=True)
    a = ap.parse_args()
    with a.controls.open(encoding='utf-8-sig') as f:
        result = calibrate(json.load(f))
    with a.out.open('x', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"Candidate samples: {len(result['samples'])}; no extrapolation; report: {a.out}")


if __name__ == '__main__':
    main()
