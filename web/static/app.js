// Form submit -> POST /generate -> STL blob -> download link + three.js preview.
const form = document.getElementById('f');
const status = document.getElementById('status');
const dl = document.getElementById('download');
const dl3 = document.getElementById('download3mf');
const sizeEl = document.getElementById('size');
const warnEl = document.getElementById('warnings');
const fitEl2 = document.getElementById('fitnote');
const go = document.getElementById('go');
const dlBtn = dl.querySelector('button'), dl3Btn = dl3.querySelector('button');
let viewer = null;
let rendered = false;
let plate = null;   // [w, h] of the last render in mm

// Build plate check: the sign fits if it fits in either orientation.
const bedX = document.getElementById('bed_x'), bedY = document.getElementById('bed_y'), fitEl = document.getElementById('fit');
function checkFit() {
  const bx = parseFloat(bedX.value), by = parseFloat(bedY.value);
  if (!plate || !(bx > 0) || !(by > 0)) { fitEl.textContent = ''; fitEl.className = ''; return; }
  const [w, h] = plate;
  const fits = (w <= bx && h <= by) || (w <= by && h <= bx);
  fitEl.textContent = fits ? 'Fits the build plate.' : `Too big for the build plate: sign is ${w} x ${h} mm.`;
  fitEl.className = fits ? 'ok' : 'bad';
}
bedX.addEventListener('input', checkFit);
bedY.addEventListener('input', checkFit);

function setDownload(enabled) {
  for (const [a, b] of [[dl, dlBtn], [dl3, dl3Btn]]) {
    b.disabled = !enabled;
    a.classList.toggle('disabled', !enabled);
    a.setAttribute('aria-disabled', String(!enabled));
  }
}

// Render automatically on changes: off by default, remembered per browser. A
// change (a field left, a select or checkbox toggled) starts a render after a
// short pause; typing in a field only marks the preview stale until it is left.
const auto = document.getElementById('auto');
try { auto.checked = localStorage.getItem('autoRender') === '1'; } catch {}
auto.addEventListener('change', () => {
  try { localStorage.setItem('autoRender', auto.checked ? '1' : '0'); } catch {}
  if (auto.checked && !rendered) render();
});
let autoTimer = null;
function scheduleAuto() {
  if (!auto.checked) return;
  clearTimeout(autoTimer);
  autoTimer = setTimeout(render, 400);
}

// Any change after a render makes the preview and download stale until re-rendered.
function markStale() {
  if (!rendered) return;
  rendered = false;
  setDownload(false);
  status.textContent = auto.checked ? 'Settings changed. Rendering when you leave the field.'
                                    : 'Settings changed since the last render. Press Render to update the preview and download.';
  status.className = 'stale';
  document.getElementById('viewer').classList.add('stale');
  plate = null; checkFit();
}
form.addEventListener('input', markStale);
form.addEventListener('change', (e) => { markStale(); scheduleAuto(); });

// Show only the fields that apply to the chosen shape.
const shapeSel = form.elements.shape;
function applyShape() {
  const shape = shapeSel.value;
  document.querySelectorAll('[data-shapes]').forEach(el => {
    el.style.display = el.dataset.shapes.split(' ').includes(shape) ? '' : 'none';
  });
}
shapeSel.addEventListener('change', applyShape);
applyShape();

form.addEventListener('submit', (e) => { e.preventDefault(); clearTimeout(autoTimer); render(); });

// One render in flight at a time: a newer one aborts the older request (the
// server then kills its OpenSCAD processes) and only the newest result is shown.
let inflight = null;
let renderSeq = 0;
const stage = document.getElementById('stage');
async function render() {
  if (inflight) inflight.abort();
  const ctl = inflight = new AbortController();
  const seq = ++renderSeq;
  go.disabled = true;
  rendered = false;
  setDownload(false);
  status.textContent = 'Rendering, this takes a few seconds...';
  status.className = '';
  stage.classList.add('rendering');
  document.getElementById('viewer').classList.add('stale');
  sizeEl.textContent = ''; warnEl.textContent = ''; fitEl2.textContent = '';
  const fd = new FormData(form);
  try {
    const r = await fetch('/generate', { method: 'POST', body: fd, signal: ctl.signal });
    if (!r.ok) {
      let msg = r.statusText;
      try { const d = (await r.json()).detail; msg = typeof d === 'string' ? d : JSON.stringify(d); } catch {}
      throw new Error(msg);
    }
    // The body is the STL followed by the Orca/Bambu project; X-STL-Bytes says where one ends.
    const buf = await r.arrayBuffer();
    if (seq !== renderSeq) return;                 // superseded while downloading
    const n = parseInt(r.headers.get('X-STL-Bytes') || '', 10) || buf.byteLength;
    const stl = buf.slice(0, n);
    if (dl.href) URL.revokeObjectURL(dl.href);
    if (dl3.href) URL.revokeObjectURL(dl3.href);
    dl.href = URL.createObjectURL(new Blob([stl], { type: 'model/stl' }));
    dl3.href = URL.createObjectURL(new Blob([buf.slice(n)], { type: 'model/3mf' }));
    rendered = true;
    setDownload(true);
    document.getElementById('viewer').classList.remove('stale');
    const size = r.headers.get('X-Plate-Size');
    sizeEl.textContent = size ? `Sign ${size}` : '';
    const m = /([\d.]+) x ([\d.]+)/.exec(size || '');
    plate = m ? [parseFloat(m[1]), parseFloat(m[2])] : null;
    checkFit();
    const fitNote = r.headers.get('X-Fit');
    fitEl2.textContent = fitNote ? `Note: ${fitNote}.` : '';
    const warns = JSON.parse(r.headers.get('X-Warnings') || '[]');
    warnEl.textContent = warns.join(' | ');
    status.textContent = 'Rendered. Download is up to date with the settings.';
    await preview(stl, parseInt(r.headers.get('X-Inlay-From') || '', 10));
  } catch (err) {
    if (seq !== renderSeq) return;                 // aborted by a newer render; that one reports
    status.textContent = err.message;
    status.className = 'err';
  } finally {
    if (seq === renderSeq) {
      inflight = null;
      go.disabled = false;
      stage.classList.remove('rendering');
    }
  }
}

// inlayFrom: the index of the first triangle of the black shell (NaN falls back to colouring by height).
async function preview(buf, inlayFrom) {
  let THREE, STLLoader, OrbitControls;
  try {
    THREE = await import('three');
    ({ STLLoader } = await import('three/addons/loaders/STLLoader.js'));
    ({ OrbitControls } = await import('three/addons/controls/OrbitControls.js'));
  } catch {
    status.textContent = 'Rendered. (3D preview unavailable: could not load three.js from the CDN.)';
    return;
  }
  const canvas = document.getElementById('viewer');
  if (!viewer) {
    const renderer = new THREE.WebGLRenderer({ canvas, antialias: true });
    const scene = new THREE.Scene();
    scene.background = new THREE.Color(0x2a2a2a);
    const camera = new THREE.PerspectiveCamera(35, 16 / 9, 1, 5000);
    const controls = new OrbitControls(camera, canvas);
    scene.add(new THREE.HemisphereLight(0xffffff, 0x444444, 1.2));
    const key = new THREE.DirectionalLight(0xffffff, 1.5); key.position.set(1, -1, 2); scene.add(key);
    viewer = { renderer, scene, camera, controls, mesh: null };
    const resize = () => {
      const w = canvas.clientWidth, h = canvas.clientHeight;
      renderer.setSize(w, h, false); camera.aspect = w / h; camera.updateProjectionMatrix();
    };
    new ResizeObserver(resize).observe(canvas); resize();
    (function loop() { requestAnimationFrame(loop); controls.update(); renderer.render(scene, camera); })();
  }
  const { scene, camera, controls } = viewer;
  if (viewer.mesh) { scene.remove(viewer.mesh); viewer.mesh.geometry.dispose(); }
  const geom = new STLLoader().parse(buf);
  geom.computeVertexNormals();
  geom.computeBoundingBox();
  const bb = geom.boundingBox, c = new THREE.Vector3(); bb.getCenter(c);
  // Everything from the second shell onwards is black; without that header, colour by
  // height instead (base plate yellow, raised tops black).
  const topZ = bb.max.z, pos = geom.attributes.position, colors = new Float32Array(pos.count * 3);
  for (let i = 0; i < pos.count; i++) {
    const black = inlayFrom >= 0 ? i >= inlayFrom * 3
                                 : pos.getZ(i) > topZ - 0.05 && isRaisedTri(pos, i, topZ);
    colors.set(black ? [0.08, 0.08, 0.08] : [1.0, 0.82, 0.01], i * 3);
  }
  geom.setAttribute('color', new THREE.BufferAttribute(colors, 3));
  const mesh = new THREE.Mesh(geom, new THREE.MeshStandardMaterial({ vertexColors: true, roughness: 0.6, metalness: 0.05 }));
  mesh.position.sub(c);
  scene.add(mesh); viewer.mesh = mesh;
  const span = Math.max(bb.max.x - bb.min.x, bb.max.y - bb.min.y);
  camera.position.set(0, -span * 0.9, span * 1.1); camera.near = span / 100; camera.far = span * 20; camera.updateProjectionMatrix();
  controls.target.set(0, 0, 0); controls.update();
}

// A vertex belongs to a raised top face if all three vertices of its triangle sit at the top Z.
function isRaisedTri(pos, i) {
  const t = i - (i % 3), z0 = pos.getZ(t), z1 = pos.getZ(t + 1), z2 = pos.getZ(t + 2);
  return Math.abs(z0 - z1) < 0.05 && Math.abs(z1 - z2) < 0.05;
}
