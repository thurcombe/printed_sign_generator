// Form submit -> POST /generate -> STL blob -> download link + three.js preview.
const form = document.getElementById('f');
const status = document.getElementById('status');
const dl = document.getElementById('download');
const sizeEl = document.getElementById('size');
const warnEl = document.getElementById('warnings');
const go = document.getElementById('go');
let viewer = null;

form.addEventListener('submit', async (e) => {
  e.preventDefault();
  go.disabled = true;
  status.textContent = 'Rendering, this takes a few seconds...';
  status.className = '';
  dl.style.display = 'none'; sizeEl.textContent = ''; warnEl.textContent = '';
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
    dl.style.display = 'inline-block';
    const size = r.headers.get('X-Plate-Size');
    sizeEl.textContent = size ? `Plate ${size}, ${(blob.size / 1024).toFixed(0)} kB` : '';
    const warns = JSON.parse(r.headers.get('X-Warnings') || '[]');
    warnEl.textContent = warns.join(' | ');
    status.textContent = 'Done.';
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
    status.textContent = 'Done. (3D preview unavailable: could not load three.js from the CDN.)';
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
