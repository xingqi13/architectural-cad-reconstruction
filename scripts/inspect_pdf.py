#!/usr/bin/env python3
"""Read-only PDF page inventory; classification is a routing hint, not proof."""
import argparse
import json
from pathlib import Path

import fitz


def inspect(path):
    pages = []
    with fitz.open(path) as doc:
        if doc.needs_pass:
            raise ValueError('PDF requires a password; no credentials are requested or modified')
        for number, page in enumerate(doc, 1):
            rect = page.rect
            text_chars = len(page.get_text('text').strip())
            images = page.get_image_info(xrefs=True)
            coverage_sum = 0.0
            for item in images:
                region = fitz.Rect(item['bbox']) & rect
                if not region.is_empty:
                    coverage_sum += region.get_area() / max(rect.get_area(), 1)
            paths = len(page.get_drawings())
            if coverage_sum >= .7 and text_chars < 20 and paths < 8:
                kind = 'likely_scanned_image'
            elif paths >= 10 and coverage_sum < .7:
                kind = 'likely_vector_or_mixed'
            else:
                kind = 'mixed_or_unclear'
            pages.append({'page': number, 'width_pt': round(rect.width, 2),
                          'height_pt': round(rect.height, 2), 'rotation': page.rotation,
                          'extracted_text_chars': text_chars, 'displayed_images': len(images),
                          'image_coverage_sum_approx': round(coverage_sum, 4),
                          'vector_path_count': paths, 'routing_hint': kind})
    return {'input': str(path), 'page_count': len(pages), 'pages': pages,
            'limits': 'Coverage may be overestimated by overlapping images; content, units, scale and dimensions require visual/source checks.'}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('pdf', type=Path)
    ap.add_argument('--out', type=Path, required=True, help='new JSON report path')
    a = ap.parse_args()
    if not a.pdf.is_file():
        ap.error('input PDF does not exist')
    result = inspect(a.pdf)
    with a.out.open('x', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"PDF pages inspected: {result['page_count']}; routing hints only; report: {a.out}")


if __name__ == '__main__':
    main()
