"""Web UI for custom_sign.scad.

Stateless: each request renders into a temp directory, streams the STL back
and deletes everything. Nothing is kept between requests.
"""
import asyncio, json, os, re, shutil, struct, subprocess, tempfile
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from PIL import Image, UnidentifiedImageError

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
ASSET_VERSION = hashlib.sha1((HERE / 'static' / 'app.js').read_bytes()).hexdigest()[:10]


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
    return HTMLResponse(html.replace('__FONTS__', json.dumps(FONTS)).replace('__ASSET_VERSION__', ASSET_VERSION),
                        headers={'Cache-Control': 'no-cache'})


@app.post('/generate')
async def generate(
    lines: str = Form(''),
    font: str = Form('Nimbus Sans Narrow:style=Bold'),
    text_size: float = Form(10.35),
    line_gap: float = Form(3.0),
    show_icon: bool = Form(False),
    icon_h: float = Form(30.0),
    gap_icon_text: float = Form(3.0),
    icon_flip: bool = Form(False),
    width: float = Form(180.0),
    corner_r: float = Form(10.8),
    base_t: float = Form(3.0),
    raise_t: float = Form(1.0),
    ring_inset: float = Form(4.2),
    ring_w: float = Form(3.7),
    inner_pad: float = Form(4.0),
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
    if hole_rows not in ('top', 'bottom', 'top+bottom', 'middle'):
        raise HTTPException(400, 'hole rows must be top, bottom, top+bottom or middle')
    if fit not in ('shrink', 'widen', 'none'):
        raise HTTPException(400, 'fit must be shrink, widen or none')
    if csk_d and csk_d < hole_d:
        raise HTTPException(400, 'countersink diameter must be 0 or at least the hole diameter')
    if 2 * (ring_inset + ring_w + inner_pad) >= width - 10:
        raise HTTPException(400, 'border and padding leave no room for content')

    # Fit the text: OpenSCAD cannot measure text, so measure the lines with a quick
    # text-only render first, then shrink the size or widen the plate as asked.
    margin = ring_inset + ring_w + inner_pad
    fit_note = ''
    if fit != 'none':
        widths = await measure_lines(text_lines, font, text_size)
        widest = max(widths)
        inner_w = width - 2 * margin
        if widest > inner_w:
            if fit == 'shrink':
                text_size = round(text_size * inner_w / widest * 0.995, 2)
                fit_note = f'text size reduced to {text_size:g} so the longest line fits'
            else:
                width = round(widest / 0.995 + 2 * margin, 1)
                fit_note = f'plate widened to {width:g} mm to fit the longest line'

    defs = {
        'lines': '[' + ','.join(scad_str(l) for l in text_lines) + ']',
        'font': scad_str(font),
        'text_size': num('text size', text_size, 1, 100),
        'line_gap': num('line gap', line_gap, 0, 50),
        'show_icon': 'true' if show_icon else 'false',
        'icon_h': num('icon height', icon_h, 1, 300),
        'gap_icon_text': num('icon gap', gap_icon_text, 0, 50),
        'icon_flip': 'true' if icon_flip else 'false',
        'width': num('width', width, 20, 400),
        'corner_r': num('corner radius', corner_r, 0, 50),
        'base_t': num('base thickness', base_t, 0.4, 20),
        'raise_t': num('raised height', raise_t, 0.2, 10),
        'ring_inset': num('ring inset', ring_inset, 0, 30),
        'ring_w': num('ring width', ring_w, 0, 30),
        'inner_pad': num('inner padding', inner_pad, 0, 30),
        'hole_x': parse_holes(hole_x, width),
        'hole_rows': scad_str(hole_rows),
        'hole_d': num('hole diameter', hole_d, 0.5, 20),
        'csk_d': num('countersink diameter', csk_d, 0, 30),
        'part': '"both"',
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

        out = work / 'custom_sign.stl'
        cmd = [OPENSCAD, '-o', str(out)]
        for k, v in defs.items():
            cmd += ['-D', f'{k}={v}']
        cmd.append(str(SCAD))
        r = await run(cmd, RENDER_TIMEOUT)
        if r.returncode != 0 or not out.exists():
            err = [l for l in r.stderr.splitlines() if 'ERROR' in l or 'WARNING' in l]
            raise HTTPException(500, 'OpenSCAD failed: ' + ('; '.join(err) or r.stderr.strip()[-500:]))
        warnings = [l.replace('WARNING: ', '') for l in r.stderr.splitlines() if l.startswith('WARNING')]
        m = re.search(r'ECHO: "plate ([\d.]+) x ([\d.]+) mm', r.stderr)
        size = f'{float(m.group(1)):.1f} x {float(m.group(2)):.1f} mm' if m else ''
        headers = {
            'Content-Disposition': 'attachment; filename="custom_sign.stl"',
            'X-Plate-Size': size,
            'X-Text-Size': f'{text_size:g}',
            'X-Fit': fit_note,
            'X-Warnings': json.dumps(warnings),
            'Access-Control-Expose-Headers': 'X-Plate-Size, X-Text-Size, X-Fit, X-Warnings',
        }
        return Response(out.read_bytes(), media_type='model/stl', headers=headers)
    finally:
        shutil.rmtree(work, ignore_errors=True)


async def measure_lines(lines, font, text_size):
    """Width in mm of each text line as OpenSCAD will render it: extrude every line
    at a known y offset, export binary STL, and read the x extent per band."""
    step = 1000.0
    scad = ''.join(f'translate([0, {i * step}]) linear_extrude(1) text({scad_str(l)}, size={text_size!r}, font={scad_str(font)}, halign="left", valign="baseline", $fn=16);\n'
                   for i, l in enumerate(lines))
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
    lo = [float('inf')] * len(lines); hi = [float('-inf')] * len(lines)
    for t in range(n):
        base = 84 + t * 50 + 12                      # skip normal
        for v in range(3):
            x, y, _ = struct.unpack_from('<fff', data, base + v * 12)
            i = int(round(y / step))
            if 0 <= i < len(lines):
                lo[i] = min(lo[i], x); hi[i] = max(hi[i], x)
    return [max(0.0, h - l) if h > l else 0.0 for l, h in zip(lo, hi)]


async def run(cmd, timeout):
    proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        so, se = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        raise HTTPException(504, f'{Path(cmd[0]).name} took longer than {timeout}s')
    return subprocess.CompletedProcess(cmd, proc.returncode, so.decode(errors='replace'), se.decode(errors='replace'))


@app.get('/healthz')
def healthz():
    return {'ok': True, 'fonts': len(FONTS)}
