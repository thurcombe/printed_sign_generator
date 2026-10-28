#!/usr/bin/env python3
"""Trace a black-on-white image (a silhouette, logo, icon) into an SVG for custom_sign.scad.

    ./.venv/bin/python trace_silhouette.py image.png icon.svg
    ./.venv/bin/python trace_silhouette.py image.png icon.svg --crop 150,135,300,245

Dark pixels become the shape. Use --invert for a white shape on dark.
Use --crop to pick the silhouette out of a larger image (left,top,right,bottom
in source pixels). The output size doesn't matter: the .scad resizes the SVG
to icon_h and centres it.
"""
import argparse, sys
import numpy as np
from PIL import Image
from scipy import ndimage
from skimage import measure
from shapely.geometry import Polygon
from shapely.ops import unary_union

ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
ap.add_argument('src'); ap.add_argument('out')
ap.add_argument('--crop', help='left,top,right,bottom in source pixels')
ap.add_argument('--threshold', type=float, default=128, help='0-255, pixels darker than this are the shape')
ap.add_argument('--invert', action='store_true', help='shape is light on dark')
ap.add_argument('--upscale', type=int, default=8, help='upsample factor before tracing (smoother curves)')
ap.add_argument('--blur', type=float, default=1.5, help='gaussian blur in upscaled pixels (anti-alias edge)')
ap.add_argument('--simplify', type=float, default=0.06, help='simplify tolerance in output mm')
ap.add_argument('--min-area', type=float, default=0.3, help='drop specks smaller than this (mm^2 at --height)')
ap.add_argument('--height', type=float, default=30.0, help='nominal output height in mm')
a = ap.parse_args()

im = Image.open(a.src)
if im.mode in ('RGBA', 'LA', 'P'):                      # transparent background counts as white
    im = Image.alpha_composite(Image.new('RGBA', im.size, 'white'), im.convert('RGBA'))
im = im.convert('L')
if a.crop:
    im = im.crop(tuple(int(v) for v in a.crop.split(',')))
up = a.upscale
hi = im.resize((im.width * up, im.height * up), Image.LANCZOS)
arr = ndimage.gaussian_filter(np.asarray(hi).astype(float), a.blur)
mask = (arr > a.threshold) if a.invert else (arr < a.threshold)
px_per_mm = (im.height * up) / a.height                 # so the result is --height mm tall

mask = np.pad(mask, 1)
rings = []
for c in measure.find_contours(mask.astype(float), 0.5):
    p = Polygon([((x - 1) / px_per_mm, (y - 1) / px_per_mm) for y, x in c])   # y down, like SVG
    if p.is_valid and p.area > 0.01:
        rings.append(p)
if not rings:
    sys.exit('no shape found - check --threshold / --invert')
rings.sort(key=lambda p: p.area, reverse=True)
outers, holes = [], {}
for i, p in enumerate(rings):
    cont = [j for j in range(i) if rings[j].contains(p.representative_point())]
    if len(cont) % 2 == 0: outers.append(i); holes[i] = []
    else: holes[cont[-1]].append(p)
g = unary_union([Polygon(rings[i].exterior.coords, [h.exterior.coords for h in holes[i]]) for i in outers])
g = g.buffer(-0.08).buffer(0.08).simplify(a.simplify)   # knock off single-pixel spurs, thin out points
polys = [p for p in (g.geoms if hasattr(g, 'geoms') else [g]) if p.area >= a.min_area]
if not polys:
    sys.exit('only specks found - lower --min-area or check the image')

minx, miny, maxx, maxy = unary_union(polys).bounds
w, h = maxx - minx, maxy - miny
def ring_d(coords):
    return 'M ' + ' L '.join(f'{x - minx:.3f},{y - miny:.3f}' for x, y in coords[:-1]) + ' Z'
paths = []
for p in polys:
    paths.append(' '.join([ring_d(list(p.exterior.coords))] + [ring_d(list(r.coords)) for r in p.interiors]))
with open(a.out, 'w') as f:
    f.write(f'<svg xmlns="http://www.w3.org/2000/svg" width="{w:.3f}mm" height="{h:.3f}mm" viewBox="0 0 {w:.3f} {h:.3f}">\n')
    for d in paths:
        f.write(f'  <path d="{d}" fill="#000" fill-rule="evenodd"/>\n')
    f.write('</svg>\n')
print(f'{a.out}: {w:.1f} x {h:.1f} mm, {len(polys)} shape(s), {sum(len(p.interiors) for p in polys)} hole(s), '
      f'{sum(len(p.exterior.coords) + sum(len(r.coords) for r in p.interiors) for p in polys)} points')
