"""Web UI for custom_sign.scad.

Stateless: each request renders into a temp directory, streams the STL back
and deletes everything. Nothing is kept between requests.
"""
import asyncio, json, math, os, re, shutil, struct, subprocess, tempfile
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from PIL import Image, UnidentifiedImageError

from .threemf import orca_project

ROOT = Path(os.environ.get('SIGN_ROOT', Path(__file__).resolve().parent.parent))
SCAD = ROOT / 'custom_sign.scad'
DEFAULT_ICON = ROOT / 'icon.svg'
TRACER = ROOT / 'trace_silhouette.py'
PYTHON = os.environ.get('SIGN_PYTHON', 'python3')
OPENSCAD = os.environ.get('OPENSCAD', 'openscad')
RENDER_TIMEOUT = int(os.environ.get('RENDER_TIMEOUT', '180'))
MAX_UPLOAD = 10 * 1024 * 1024
HERE = Path(__file__).resolve().parent

app = FastAPI(title='Printed sign generator')


class NoCacheStatic(StaticFiles):
    """Browsers must revalidate static files, so a rebuilt container never serves a stale app.js."""
    def file_response(self, *args, **kwargs):
        resp = super().file_response(*args, **kwargs)
        resp.headers['Cache-Control'] = 'no-cache'
        return resp


app.mount('/static', NoCacheStatic(directory=HERE / 'static'), name='static')
# Version tag for the script URL, from the file's own hash, so a changed app.js is always refetched.
import hashlib
from html import escape as html_escape
ASSET_VERSION = hashlib.sha1((HERE / 'static' / 'app.js').read_bytes()).hexdigest()[:10]
# Baked in by the Dockerfile from --build-arg GIT_SHA / GIT_REF as two lines of
# /app/VERSION (commit, then tag); missing or empty means a dev build.
def _version():
    try:
        commit, ref = ((ROOT / 'VERSION').read_text().splitlines() + ['', ''])[:2]
    except OSError:
        return '', ''
    return (commit.strip() if re.fullmatch(r'[0-9a-f]{7,40}', commit.strip()) else ''), ref.strip()
COMMIT, REF = _version()
REPO_URL = 'https://github.com/thurcombe/printed_sign_generator'
VERSION_HTML = (f'<a href="{REPO_URL}/commit/{COMMIT}" title="{COMMIT}">{html_escape(REF) + " " if REF else ""}{COMMIT[:7]}</a>'
                if COMMIT else 'dev build')


def installed_fonts():
    try:
        out = subprocess.run(['fc-list', ':', 'family', 'style'], capture_output=True, text=True, timeout=20).stdout
    except (OSError, subprocess.TimeoutExpired):
        return []
    fonts = set()
    for line in out.splitlines():
        if ':' not in line:
            continue
        fam, _, style = line.partition(':')
        fam = fam.split(',')[0].strip()
        style = style.replace('style=', '').split(',')[0].strip()
        if fam and not fam.startswith('.'):
            fonts.add(f'{fam}:style={style}' if style else fam)
    return sorted(fonts)


FONTS = installed_fonts()


def scad_str(s: str) -> str:
    if re.search(r'[\x00-\x08\x0b-\x1f\x7f]', s):
        raise HTTPException(400, 'control characters are not allowed in text')
    return '"' + s.replace('\\', '\\\\').replace('"', '\\"') + '"'


def num(name: str, value: float, lo: float, hi: float) -> str:
    if not (lo <= value <= hi):
        raise HTTPException(400, f'{name} must be between {lo} and {hi}')
    return repr(float(value))


def parse_holes(s: str, width: float):
    s = s.strip()
    if not s:
        return '[]'
    try:
        xs = [float(v) for v in re.split(r'[,\s]+', s) if v]
    except ValueError:
        raise HTTPException(400, 'hole positions must be numbers separated by commas')
    for x in xs:
        if not (0 < x < width):
            raise HTTPException(400, f'hole position {x} is outside the plate width')
    return '[' + ','.join(repr(x) for x in xs) + ']'


@app.get('/', response_class=HTMLResponse)
def index():
    html = (HERE / 'static' / 'index.html').read_text()
    return HTMLResponse(html.replace('__FONTS__', json.dumps(FONTS)).replace('__ASSET_VERSION__', ASSET_VERSION)
                            .replace('__VERSION__', VERSION_HTML),
                        headers={'Cache-Control': 'no-cache'})


@app.post('/generate')
async def generate(
    request: Request,
    lines: str = Form(''),
    font: str = Form('Nimbus Sans Narrow:style=Bold'),
    text_size: float = Form(10.35),
    line_gap: float = Form(3.0),
    show_icon: bool = Form(False),
    icon_h: float = Form(30.0),
    gap_icon_text: float = Form(3.0),
    icon_flip: bool = Form(False),
    shape: str = Form('rectangle'),
    width: float = Form(180.0),
    diameter: float = Form(180.0),
    corner_r: float = Form(10.8),
    style: str = Form('raised'),
    base_t: float = Form(3.0),
    raise_t: float = Form(1.0),
    ring_inset: float = Form(4.2),
    ring_w: float = Form(3.7),
    inner_pad: float = Form(4.0),
    anchor: str = Form('centre'),
    content_shift: float = Form(0.0),
    icon_shift: float = Form(0.0),
    text_shift: float = Form(0.0),
    hole_x: str = Form(''),   # empty string = no holes (FastAPI treats '' as missing, so no other default)
    hole_rows: str = Form('top'),
    fit: str = Form('shrink'),
    hole_d: float = Form(4.2),
    csk_d: float = Form(8.2),
    threshold: float = Form(128),
    invert: bool = Form(False),
    icon: Optional[UploadFile] = File(None),
):
    """Render, but stop as soon as the browser gives up on the request (the page
    aborts a render when the settings change again), so abandoned OpenSCAD
    processes do not pile up."""
    work = asyncio.ensure_future(_generate(lines=lines, font=font, text_size=text_size, line_gap=line_gap, show_icon=show_icon, icon_h=icon_h, gap_icon_text=gap_icon_text, icon_flip=icon_flip, shape=shape, width=width, diameter=diameter, corner_r=corner_r, style=style, base_t=base_t, raise_t=raise_t, ring_inset=ring_inset, ring_w=ring_w, inner_pad=inner_pad, anchor=anchor, content_shift=content_shift, icon_shift=icon_shift, text_shift=text_shift, hole_x=hole_x, hole_rows=hole_rows, fit=fit, hole_d=hole_d, csk_d=csk_d, threshold=threshold, invert=invert, icon=icon))
    async def gone():
        while not await request.is_disconnected():
            await asyncio.sleep(0.25)
    watch = asyncio.ensure_future(gone())
    done, _ = await asyncio.wait({work, watch}, return_when=asyncio.FIRST_COMPLETED)
    if work in done:
        watch.cancel()
        return work.result()
    work.cancel()
    try:
        await work
    except asyncio.CancelledError:
        pass
    raise HTTPException(499, 'client went away')


async def _generate(
    lines: str = Form(''),
    font: str = Form('Nimbus Sans Narrow:style=Bold'),
    text_size: float = Form(10.35),
    line_gap: float = Form(3.0),
    show_icon: bool = Form(False),
    icon_h: float = Form(30.0),
    gap_icon_text: float = Form(3.0),
    icon_flip: bool = Form(False),
    shape: str = Form('rectangle'),
    width: float = Form(180.0),
    diameter: float = Form(180.0),
    corner_r: float = Form(10.8),
    style: str = Form('raised'),
    base_t: float = Form(3.0),
    raise_t: float = Form(1.0),
    ring_inset: float = Form(4.2),
    ring_w: float = Form(3.7),
    inner_pad: float = Form(4.0),
    anchor: str = Form('centre'),
    content_shift: float = Form(0.0),
    icon_shift: float = Form(0.0),
    text_shift: float = Form(0.0),
    hole_x: str = Form(''),   # empty string = no holes (FastAPI treats '' as missing, so no other default)
    hole_rows: str = Form('top'),
    fit: str = Form('shrink'),
    hole_d: float = Form(4.2),
    csk_d: float = Form(8.2),
    threshold: float = Form(128),
    invert: bool = Form(False),
    icon: Optional[UploadFile] = File(None),
):
    text_lines = [l.rstrip() for l in lines.splitlines() if l.strip()]
    if not text_lines:
        raise HTTPException(400, 'at least one line of text is needed')
    if len(text_lines) > 10:
        raise HTTPException(400, 'at most 10 lines of text')
    if len(font) > 100 or not re.fullmatch(r'[\w .:=,\-]+', font):
        raise HTTPException(400, 'font name has unexpected characters')
    if hole_rows not in ('none', 'top', 'bottom', 'top+bottom', 'middle'):
        raise HTTPException(400, 'hole rows must be top, bottom, top+bottom or middle')
    if shape not in ('rectangle', 'round', 'triangle'):
        raise HTTPException(400, 'shape must be rectangle, round or triangle')
    if anchor not in ('centre', 'top', 'bottom'):
        raise HTTPException(400, 'anchor must be centre, top or bottom')
    if style not in ('raised', 'flush'):
        raise HTTPException(400, 'style must be raised or flush')
    if style == 'flush' and raise_t >= base_t:
        raise HTTPException(400, 'flush style needs the inlay depth (raised height) smaller than the base thickness')
    if fit not in ('shrink', 'widen', 'none'):
        raise HTTPException(400, 'fit must be shrink, widen or none')
    if csk_d and csk_d < hole_d:
        raise HTTPException(400, 'countersink diameter must be 0 or at least the hole diameter')
    margin = ring_inset + ring_w + inner_pad
    if 2 * margin >= (diameter if shape == 'round' else width) - 10:
        raise HTTPException(400, 'border and padding leave no room for content')

    defs = {
        'lines': '[' + ','.join(scad_str(l) for l in text_lines) + ']',
        'font': scad_str(font),
        'text_size': num('text size', text_size, 1, 100),
        'line_gap': num('line gap', line_gap, 0, 50),
        'show_icon': 'true' if show_icon else 'false',
        'icon_h': num('icon height', icon_h, 1, 300),
        'gap_icon_text': num('icon gap', gap_icon_text, 0, 50),
        'icon_flip': 'true' if icon_flip else 'false',
        'shape': scad_str(shape),
        'width': num('width', width, 20, 400),
        'diameter': num('diameter', diameter, 20, 400),
        'corner_r': num('corner radius', corner_r, 0, 50),
        'style': scad_str(style),
        'base_t': num('base thickness', base_t, 0.4, 20),
        'raise_t': num('raised height', raise_t, 0.2, 10),
        'ring_inset': num('ring inset', ring_inset, 0, 30),
        'ring_w': num('ring width', ring_w, 0, 30),
        'inner_pad': num('inner padding', inner_pad, 0, 30),
        'content_shift': num('content shift', content_shift, -200, 200),
        'icon_shift': num('icon shift', icon_shift, -200, 200),
        'text_shift': num('text shift', text_shift, -200, 200),
        'hole_x': '[]' if shape != 'rectangle' or hole_rows in ('none', 'middle') else parse_holes(hole_x, width),
        'hole_rows': scad_str(hole_rows),
        'hole_d': num('hole diameter', hole_d, 0.5, 20),
        'csk_d': num('countersink diameter', csk_d, 0, 30),
    }

    work = Path(tempfile.mkdtemp(prefix='sign-'))
    try:
        icon_path = work / 'icon.svg'
        if icon is not None and icon.filename:
            data = await icon.read()
            if len(data) > MAX_UPLOAD:
                raise HTTPException(413, 'icon file is larger than 10 MB')
            if icon.filename.lower().endswith('.svg') or data.lstrip()[:5].lower() in (b'<?xml', b'<svg ', b'<svg>'):
                if b'<svg' not in data[:4096].lower():
                    raise HTTPException(400, 'that does not look like an SVG file')
                icon_path.write_bytes(data)
            else:
                src = work / 'upload.img'
                src.write_bytes(data)
                try:
                    with Image.open(src) as im:
                        im.verify()
                except (UnidentifiedImageError, OSError):
                    raise HTTPException(400, 'icon must be a PNG/JPEG/GIF/BMP image or an SVG')
                cmd = [PYTHON, '-I', str(TRACER), str(src), str(icon_path), '--threshold', num('threshold', threshold, 0, 255)]
                if invert:
                    cmd.append('--invert')
                r = await run(cmd, 120)
                if r.returncode != 0:
                    raise HTTPException(400, 'tracing the icon failed: ' + (r.stderr or r.stdout).strip().splitlines()[-1])
        else:
            shutil.copy(DEFAULT_ICON, icon_path)
        defs['icon_file'] = scad_str(str(icon_path))

        # Fit the text: OpenSCAD cannot measure text, so measure the lines (and the
        # icon) with a quick flat render first, then shrink the text or enlarge the
        # plate as asked.
        notes = []
        if fit != 'none' or shape in ('round', 'triangle'):
            ink = await measure_lines(text_lines, font, text_size, icon_path if show_icon else None, icon_h)
            icon_ink = ink.pop() if show_icon else None
            widths = [m['w'] for m in ink]
            if shape in ('round', 'triangle'):
                # Lay the content out as the SCAD file does, balance it on its ink centroid,
                # then place the block: centred (balanced), or slid to the top or bottom as
                # far as everything still fits. Text scales with its size; bands and the icon
                # do not, so the fit searches are done on the measured outlines.
                layout = dict(n=len(text_lines), line_gap=line_gap, hole_rows=hole_rows, hole_d=hole_d, csk_d=csk_d,
                              gap_icon_text=gap_icon_text, icon_h=icon_h if show_icon else None, icon=icon_ink,
                              icon_shift=icon_shift, text_shift=text_shift)
                measured_size = text_size
                def at(size):
                    k = size / measured_size
                    return content_boxes(size, [dict(w=m['w'] * k, area=m['area'] * k * k, cy=m['cy'] * k) for m in ink],
                                         content_shift, **layout)
                def area(dim):   # the content area for a diameter (round) or base width (triangle)
                    return RoundArea(dim, margin) if shape == 'round' else TriArea(dim, corner_r, margin)
                def fits(size, dim):
                    boxes, balanced = at(size)
                    ar = area(dim)
                    return ar.contains(boxes, balanced) if anchor == 'centre' else ar.fit_interval(boxes) is not None
                dim = diameter if shape == 'round' else width
                dim_name = 'diameter' if shape == 'round' else 'width'
                shape_name = 'circle' if shape == 'round' else 'triangle'
                if not fits(text_size, dim) and fit != 'none':
                    if fit == 'shrink':
                        # The balance point moves as the text shrinks, so fitting is not
                        # monotonic in the size: scan down for the largest size that fits,
                        # then bisect up to the next size that does not.
                        steps = int((text_size - 0.5) / 0.1)
                        lo = next((text_size - i * 0.1 for i in range(steps + 1) if fits(text_size - i * 0.1, dim)), None)
                        if lo is None:
                            boxes, balanced = at(0.5)
                            s0 = area(dim).place(boxes, balanced, anchor, content_shift)[0]
                            raise HTTPException(400, f'nothing fits a {shape_name} of this {dim_name} even with the text at its smallest: '
                                                     + ', '.join(area(dim).misfits(boxes, s0))
                                                     + '. Change the nudges, the icon height or gap, or the hole rows.')
                        hi = min(lo + 0.1, text_size)
                        for _ in range(30):
                            mid = (lo + hi) / 2
                            if fits(mid, dim): lo = mid
                            else: hi = mid
                        text_size = math.floor(lo * 100) / 100       # round down so it still fits
                        notes.append(f'text size reduced to {text_size:g} so the content fits the {shape_name}')
                    else:
                        lo, hi = dim, dim
                        while not fits(text_size, hi):
                            hi *= 1.5
                            if hi > 2000:
                                raise HTTPException(400, f'the content does not fit any sensible {shape_name}')
                        for _ in range(40):
                            mid = (lo + hi) / 2
                            if fits(text_size, mid): hi = mid
                            else: lo = mid
                        dim = round(hi + 0.05, 1)
                        if shape == 'round': diameter = dim
                        else: width = dim
                        notes.append(f'{dim_name} increased to {dim:g} mm to fit the content')
                boxes, balanced = at(text_size)
                shift, how = area(dim).place(boxes, balanced, anchor, content_shift)
                if how:
                    notes.append(how)
                defs['content_shift'] = num('content shift', round(shift, 2), -400, 400)
            elif fit != 'none':
                widest = max(widths)
                inner_w = width - 2 * margin
                if widest > inner_w:
                    if fit == 'shrink':
                        text_size = round(text_size * inner_w / widest * 0.995, 2)
                        notes.append(f'text size reduced to {text_size:g} so the longest line fits')
                    else:
                        width = round(widest / 0.995 + 2 * margin, 1)
                        notes.append(f'plate widened to {width:g} mm to fit the longest line')
            defs['text_size'] = num('text size', text_size, 1, 100)
            defs['width'] = num('width', width, 20, 400)
            defs['diameter'] = num('diameter', diameter, 20, 400)

        async def render(part):
            out = work / f'{part}.stl'
            cmd = [OPENSCAD, '--export-format', 'binstl', '-o', str(out)]
            for k, v in {**defs, 'part': scad_str(part)}.items():
                cmd += ['-D', f'{k}={v}']
            cmd.append(str(SCAD))
            r = await run(cmd, RENDER_TIMEOUT)
            if r.returncode != 0 or not out.exists():
                err = [l for l in r.stderr.splitlines() if 'ERROR' in l or 'WARNING' in l]
                raise HTTPException(500, 'OpenSCAD failed: ' + ('; '.join(err) or r.stderr.strip()[-500:]))
            return out.read_bytes(), r.stderr

        # Render the plate and the black features as two bodies. They go out twice in one
        # response: as two shells in one STL, and as an OrcaSlicer / Bambu Studio project
        # (.3mf) with the plate on filament 1 and the features on filament 2, so a
        # two-colour print needs no manual splitting or painting in the slicer.
        (base_stl, log), (top_stl, _) = await asyncio.gather(render('base'), render('raised'))
        stl, inlay_from = join_stl(base_stl, top_stl)
        project = orca_project([('plate', base_stl, 1), ('inlay' if style == 'flush' else 'raised', top_stl, 2)])
        warnings = [l.replace('WARNING: ', '') for l in log.splitlines() if l.startswith('WARNING')]
        m = re.search(r'ECHO: "plate ([\d.]+) x ([\d.]+) mm', log)
        size = f'{float(m.group(1)):.1f} x {float(m.group(2)):.1f} mm' if m else ''
        headers = {
            'X-Plate-Size': size,
            'X-Text-Size': f'{text_size:g}',
            'X-Fit': '; '.join(notes),
            'X-Inlay-From': str(inlay_from),        # first triangle of the second shell in the STL
            'X-STL-Bytes': str(len(stl)),           # the STL comes first, the .3mf project follows
            'X-Warnings': json.dumps(warnings),
            'Access-Control-Expose-Headers': 'X-Plate-Size, X-Text-Size, X-Fit, X-Inlay-From, X-STL-Bytes, X-Warnings',
        }
        return Response(stl + project, media_type='application/octet-stream', headers=headers)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def content_boxes(text_size, lines, user_shift, n, line_gap, hole_rows, hole_d, csk_d, gap_icon_text, icon_h, icon,
                  icon_shift=0.0, text_shift=0.0):
    """Lay the content of a round or triangular sign out exactly as custom_sign.scad
    does (hole bands, icon, text lines, box centred on the shape's centre, icon and
    text nudged by their own shifts). Returns the boxes as (name, width, y_top,
    y_bottom) relative to the shape's centre with the block unshifted, and the block
    shift that puts the ink centroid on the centre plus the user's own shift. The
    centroid is taken before the icon and text nudges, so a nudge moves only what it
    names and never the rest of the block."""
    cap_h = text_size * 1.024
    band_h = max(hole_d, csk_d) + 2
    want_top = hole_rows in ('top', 'top+bottom')
    want_bot = hole_rows in ('bottom', 'top+bottom')
    top_band = band_h + gap_icon_text if want_top else 0
    bot_band = band_h + line_gap if want_bot else 0
    content_h = top_band + (icon_h + gap_icon_text if icon_h else 0) + n * cap_h + max(0, n - 1) * line_gap + bot_band
    top = content_h / 2
    boxes = []                                    # (name, width, y_top, y_bottom) relative to the centre
    ink = []                                      # (area, centroid y) before the nudges
    if want_top:
        boxes.append(('top hole', max(hole_d, csk_d), top, top - band_h))
    y = top - top_band
    if icon_h:
        boxes.append(('icon', icon['w'], y + icon_shift, y + icon_shift - icon_h)); ink.append((icon['area'], y - icon_h / 2 + icon['cy']))
        y -= icon_h + gap_icon_text
    for i, m in enumerate(lines):
        name = 'text' if n == 1 else f'text line {i + 1}'
        boxes.append((name, m['w'], y + text_shift, y + text_shift - cap_h)); ink.append((m['area'], y - cap_h + m['cy']))   # cy is relative to the baseline
        y -= cap_h + line_gap
    if want_bot:
        boxes.append(('bottom hole', max(hole_d, csk_d), -top + band_h, -top))
    total = sum(a for a, _ in ink)
    centroid = sum(a * cy for a, cy in ink) / total if total else 0.0
    return boxes, -centroid + user_shift


def shifted(boxes, s):
    return [(name, w, y1 + s, y2 + s) for name, w, y1, y2 in boxes]


class ContentArea:
    """The region of a sign the content may occupy, relative to the shape's centre.
    Subclasses give inside(x, y) (true when at least tol inside), the vertical
    extent (lo, hi) of the region, and the exact anchor positions used by the
    SCAD file when nothing fits."""
    tol = 0.3

    def contains(self, boxes, s):
        return all(self.inside(x, y) for _, w, y1, y2 in shifted(boxes, s) for x in (-w / 2, w / 2) for y in (y1, y2))

    def misfits(self, boxes, s):
        """What sticks out at block shift s, as phrases for an error message."""
        lo, hi = self.extent()
        out = []
        for name, w, y1, y2 in shifted(boxes, s):
            if all(self.inside(x, y) for x in (-w / 2, w / 2) for y in (y1, y2)):
                continue
            where = ('runs off the top' if y1 > hi else 'runs off the bottom' if y2 < lo
                     else f'is too wide ({w:.0f} mm) where it sits')
            out.append(f'the {name} {where}')
        return out

    def fit_interval(self, boxes):
        """Range of block shifts at which the content fits (it is an interval, the
        region being convex), or None. Scanned at 1 mm, then the ends bisected."""
        lo, hi = self.extent()
        span = hi - lo
        found = next((s for s in (lo + i * 1.0 for i in range(int(span) + 1)) if self.contains(boxes, s)), None)
        if found is None:
            return None
        def edge(inside, outside):
            for _ in range(20):
                mid = (inside + outside) / 2
                if self.contains(boxes, mid): inside = mid
                else: outside = mid
            return inside
        return edge(found, lo - 1), edge(found, hi + 1)

    def place(self, boxes, balanced, anchor, user_shift):
        """Block shift for the anchor, and a note for the user."""
        iv = self.fit_interval(boxes)
        if anchor == 'centre':
            s = balanced
            how = f'content moved {abs(s - user_shift):.1f} mm {"up" if s > user_shift else "down"} to balance its visual weight' \
                  if abs(s - user_shift) >= 0.05 else ''
            return s, how
        if iv is None:                            # nothing fits (fit mode none): sit on the edge like the SCAD file does
            lo, hi = self.extent()
            s = (lo - min(y2 for *_, y2 in boxes) if anchor == 'bottom' else hi - max(y1 for _, _, y1, _ in boxes)) + user_shift
            return s, f'content anchored at the {anchor} (it does not fit, so it overhangs the border)'
        s = (iv[0] if anchor == 'bottom' else iv[1]) + user_shift
        return s, f'content anchored at the {anchor}, {abs(s):.1f} mm {"above" if s > 0 else "below"} the centre'


class RoundArea(ContentArea):
    def __init__(self, diameter, margin):
        self.r = diameter / 2 - margin

    def inside(self, x, y):
        return math.hypot(x, y) <= self.r - self.tol

    def extent(self):
        return -self.r, self.r


class TriArea(ContentArea):
    """Content area of a rounded equilateral triangle of the given base width: the
    plate outline inset by margin, built as custom_sign.scad builds it (the sharp
    triangle inset by max(corner_r, margin), then rounded by max(corner_r - margin, 0)),
    relative to the centroid."""
    def __init__(self, width, corner_r, margin):
        ins, self.rd = max(corner_r, margin), max(corner_r - margin, 0)
        side = width - 2 * math.sqrt(3) * ins
        tri_h = width * math.sqrt(3) / 2
        base = ins - tri_h / 3                    # y of the inset triangle's base, relative to the centroid
        self.ok = side > 0
        self.verts = [(-side / 2, base), (side / 2, base), (0, base + side * math.sqrt(3) / 2)]

    def inside(self, px, py):
        if not self.ok:
            return False
        inside, best = True, float('inf')
        for i in range(3):
            (ax, ay), (bx, by) = self.verts[i], self.verts[(i + 1) % 3]
            ex, ey = bx - ax, by - ay
            if ex * (py - ay) - ey * (px - ax) < 0:   # vertices run anticlockwise, so inside is to the left
                inside = False
            t = max(0.0, min(1.0, ((px - ax) * ex + (py - ay) * ey) / (ex * ex + ey * ey)))
            best = min(best, math.hypot(px - (ax + t * ex), py - (ay + t * ey)))
        return (-best if inside else best) <= self.rd - self.tol

    def extent(self):
        return self.verts[0][1], self.verts[2][1] + self.rd


def join_stl(a: bytes, b: bytes):
    """Concatenate two binary STLs into one with two shells. Returns the file and
    the index of the first triangle that came from the second file."""
    na = struct.unpack_from('<I', a, 80)[0]
    nb = struct.unpack_from('<I', b, 80)[0]
    body = a[84:84 + na * 50] + b[84:84 + nb * 50]
    return b'custom_sign'.ljust(80, b'\0') + struct.pack('<I', na + nb) + body, na


async def measure_lines(lines, font, text_size, icon=None, icon_h=0.0):
    """Measure each text line as OpenSCAD will render it: extrude every line at a
    known y offset, export binary STL, and read the top faces per band. Returns a
    dict per line with w (width), area (ink area) and cy (ink centroid above the
    baseline). With an icon, the same for it at icon_h tall is appended, cy then
    relative to the icon's centre."""
    # One extrusion of a 2D union: several top-level 3D objects would be unioned
    # with CGAL on export, which takes a minute for ten lines; 2D goes through
    # Clipper and is instant.
    step = 1000.0
    scad = ''.join(f'  translate([0, {i * step}]) text({scad_str(l)}, size={text_size!r}, font={scad_str(font)}, halign="left", valign="baseline", $fn=16);\n'
                   for i, l in enumerate(lines))
    if icon is not None:
        scad += f'  translate([0, {len(lines) * step}]) resize([0, {icon_h!r}], auto=true) import({scad_str(str(icon))}, center=true);\n'
        lines = list(lines) + ['']
    scad = 'linear_extrude(1) union() {\n' + scad + '}\n'
    work = Path(tempfile.mkdtemp(prefix='measure-'))
    try:
        src, out = work / 'm.scad', work / 'm.stl'
        src.write_text(scad)
        r = await run([OPENSCAD, '--export-format', 'binstl', '-o', str(out), str(src)], 60)
        if r.returncode != 0 or not out.exists():
            raise HTTPException(500, 'measuring the text failed: ' + r.stderr.strip()[-300:])
        data = out.read_bytes()
    finally:
        shutil.rmtree(work, ignore_errors=True)
    n = struct.unpack_from('<I', data, 80)[0]
    k = len(lines)
    lo = [float('inf')] * k; hi = [float('-inf')] * k; area = [0.0] * k; moment = [0.0] * k
    for t in range(n):
        base = 84 + t * 50 + 12                      # skip normal
        v = [struct.unpack_from('<fff', data, base + j * 12) for j in range(3)]
        i = int(round(v[0][1] / step))
        if not 0 <= i < k:
            continue
        for x, y, _ in v:
            lo[i] = min(lo[i], x); hi[i] = max(hi[i], x)
        if all(abs(p[2] - 1.0) < 1e-4 for p in v):  # top face: counts for area and centroid
            (x0, y0, _), (x1, y1, _), (x2, y2, _) = v
            a = abs((x1 - x0) * (y2 - y0) - (x2 - x0) * (y1 - y0)) / 2
            area[i] += a; moment[i] += a * ((y0 + y1 + y2) / 3 - i * step)
    return [dict(w=max(0.0, h - l) if h > l else 0.0, area=a, cy=m / a if a else 0.0)
            for l, h, a, m in zip(lo, hi, area, moment)]


async def run(cmd, timeout):
    proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        so, se = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        raise HTTPException(504, f'{Path(cmd[0]).name} took longer than {timeout}s')
    except asyncio.CancelledError:                # the request was abandoned: take the process with it
        proc.kill()
        await proc.wait()
        raise
    return subprocess.CompletedProcess(cmd, proc.returncode, so.decode(errors='replace'), se.decode(errors='replace'))


@app.get('/healthz')
def healthz():
    return {'ok': True, 'fonts': len(FONTS), 'commit': COMMIT or None, 'ref': REF or None}
