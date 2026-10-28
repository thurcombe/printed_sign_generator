// Form submit -> POST /generate -> STL blob -> download link + three.js preview.
const form = document.getElementById('f');
const status = document.getElementById('status');
const dl = document.getElementById('download');
const sizeEl = document.getElementById('size');
const warnEl = document.getElementById('warnings');
const fitEl2 = document.getElementById('fitnote');
const go = document.getElementById('go');
const dlBtn = dl.querySelector('button');
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
  dlBtn.disabled = !enabled;
  dl.classList.toggle('disabled', !enabled);
  dl.setAttribute('aria-disabled', String(!enabled));
}

// Any change after a render makes the preview and download stale until re-rendered.
function markStale() {
  if (!rendered) return;
  rendered = false;
  setDownload(false);
  status.textContent = 'Settings changed since the last render. Press Render to update the preview and download.';
  status.className = 'stale';
  document.getElementById('viewer').classList.add('stale');
  plate = null; checkFit();
}
form.addEventListener('input', markStale);
form.addEventListener('change', markStale);

form.addEventListener('submit', async (e) => {
  e.preventDefault();
  go.disabled = true;
  rendered = false;
  setDownload(false);
  status.textContent = 'Rendering, this takes a few seconds...';
  status.className = '';
  sizeEl.textContent = ''; warnEl.textContent = ''; fitEl2.textContent = '';
  const fd = new FormData(form);
  try {
    const r = await fetch('/generate', { method: 'POST', body: fd });
    if (!r.ok) {
      let msg = r.statusText;
      try { const d = (await r.json()).detail; msg = typeof d === 'string' ? d : JSON.stringify(d); } catch {}
      throw new Error(msg);
    }
    const blob = await r.blob();
    if (dl.href) URL.revokeObjectURL(dl.href);
    dl.href = URL.createObjectURL(blob);
    rendered = true;
    setDownload(true);
    document.getElementById('viewer').classList.remove('stale');
    const size = r.headers.get('X-Plate-Size');
    sizeEl.textContent = size ? `Sign ${size}, ${(blob.size / 1024).toFixed(0)} kB` : '';
    const m = /([\d.]+) x ([\d.]+)/.exec(size || '');
    plate = m ? [parseFloat(m[1]), parseFloat(m[2])] : null;
    checkFit();
    const fitNote = r.headers.get('X-Fit');
    fitEl2.textContent = fitNote ? `Note: ${fitNote}.` : '';
    const warns = JSON.parse(r.headers.get('X-Warnings') || '[]');
    warnEl.textContent = warns.join(' | ');
    status.textContent = 'Rendered. Download is up to date with the settings.';
    await preview(await blob.arrayBuffer());
  } catch (err) {
    status.textContent = err.message;
    status.className = 'err';
  } finally {
    go.disabled = false;
  }
});

async function preview(buf) {
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
  // colour by height: base plate yellow, raised features black
  const topZ = bb.max.z, pos = geom.attributes.position, colors = new Float32Array(pos.count * 3);
  for (let i = 0; i < pos.count; i++) {
    const raised = pos.getZ(i) > topZ - 0.05 && isRaisedTri(pos, i, topZ);
    colors.set(raised ? [0.08, 0.08, 0.08] : [1.0, 0.82, 0.01], i * 3);
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
