#!/usr/bin/env python3
"""Trace a black-on-white image (a silhouette, logo, icon) into an SVG for custom_sign.scad.

    python3 trace_silhouette.py image.png icon.svg
    python3 trace_silhouette.py image.png icon.svg --crop 150,135,300,245

Dark pixels become the shape. Use --invert for a light shape on dark.
Use --crop to pick the shape out of a larger image (left,top,right,bottom
in source pixels). The output size doesn't matter: the .scad resizes the SVG
to icon_h and centres it.

Needs Pillow and the potrace command (apk add potrace / dnf install potrace).
"""
import argparse, os, shutil, subprocess, sys, tempfile
from PIL import Image, ImageFilter

ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
ap.add_argument('src'); ap.add_argument('out')
ap.add_argument('--crop', help='left,top,right,bottom in source pixels')
ap.add_argument('--threshold', type=float, default=128, help='0-255, pixels darker than this are the shape')
ap.add_argument('--invert', action='store_true', help='shape is light on dark')
ap.add_argument('--upscale', type=int, default=4, help='upsample factor before tracing (smoother curves on small images)')
ap.add_argument('--blur', type=float, default=0.8, help='gaussian blur in upscaled pixels (anti-alias the edge)')
ap.add_argument('--smooth', type=float, default=0.0, help='potrace corner rounding, 0-1.334. 0 (default) gives polygons; OpenSCAD 2021 turns curves into thousands of segments, so leave at 0 unless the shape needs it')
ap.add_argument('--min-area', type=int, default=20, help='drop specks below this many (upscaled) pixels')
ap.add_argument('--height', type=float, default=30.0, help='nominal output height in mm')
a = ap.parse_args()

potrace = shutil.which('potrace')
if not potrace:
    sys.exit('potrace not found on PATH - install it (apk add potrace / dnf install potrace)')

im = Image.open(a.src)
if im.mode in ('RGBA', 'LA', 'P'):                      # transparent background counts as white
    im = Image.alpha_composite(Image.new('RGBA', im.size, 'white'), im.convert('RGBA'))
im = im.convert('L')
if a.crop:
    im = im.crop(tuple(int(v) for v in a.crop.split(',')))
if a.invert:
    im = im.point(lambda v: 255 - v)
up = max(1, a.upscale)
im = im.resize((im.width * up, im.height * up), Image.LANCZOS)
if a.blur > 0:
    im = im.filter(ImageFilter.GaussianBlur(a.blur))
bitmap = im.point(lambda v: 0 if v < a.threshold else 255, mode='1')   # black = shape, as potrace expects

with tempfile.TemporaryDirectory() as td:
    pbm = os.path.join(td, 'in.pbm')
    bitmap.save(pbm)
    # -s svg, -k blacklevel already applied, -t speck size, -a corner rounding, -O curve tolerance,
    # -W/-H output size in mm (keeps aspect), -u units per mm for path precision
    cmd = [potrace, '-s', '-o', a.out, '-t', str(a.min_area), '-a', str(a.smooth), '-O', '0.2',
           '-H', f'{a.height}mm', '-u', '100', pbm]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit('potrace failed: ' + r.stderr.strip())

svg = open(a.out).read()
if '<path' not in svg:
    sys.exit('no shape found - check --threshold / --invert')
print(f'{a.out}: {im.width / (im.height / a.height):.1f} x {a.height:.1f} mm nominal, {svg.count("<path")} path(s)')
