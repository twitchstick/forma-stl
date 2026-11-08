// Forma 3D viewer. Built with `npm run build` into static/vendor/forma-viewer.js.
// The server converts STL, OBJ, 3MF and model ZIPs into a simplified binary STL
// (/api/files/<id>/mesh), so the browser only needs one loader.
import {
  Color,
  DirectionalLight,
  GridHelper,
  HemisphereLight,
  Mesh,
  MeshStandardMaterial,
  PerspectiveCamera,
  Scene,
  Vector3,
  WebGLRenderer,
} from 'three';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';
import { STLLoader } from 'three/examples/jsm/loaders/STLLoader.js';

function cssColor(element, name, fallback) {
  const value = getComputedStyle(element).getPropertyValue(name).trim();
  try {
    return new Color(value || fallback);
  } catch {
    return new Color(fallback);
  }
}

async function download(url, signal, onProgress) {
  const response = await fetch(url, { signal, credentials: 'same-origin' });
  if (!response.ok) {
    let message = 'This file could not be shown in 3D.';
    try {
      message = (await response.json()).error || message;
    } catch {}
    throw new Error(message);
  }
  const total = Number(response.headers.get('Content-Length')) || 0;
  if (!response.body || !total) return response.arrayBuffer();
  const reader = response.body.getReader();
  const body = new Uint8Array(total);
  let received = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    body.set(value, received);
    received += value.length;
    onProgress(received / total);
  }
  return body.buffer;
}

function mount(container, { url, onStatus = () => {} }) {
  const abort = new AbortController();
  let renderer, controls, geometry, frame, resizeObserver;
  let disposed = false;
  let needsRender = true;

  const status = (state, extra = {}) => {
    if (!disposed) onStatus({ state, ...extra });
  };

  try {
    renderer = new WebGLRenderer({ antialias: true, alpha: true });
  } catch {
    status('error', { message: 'Your browser could not start 3D graphics.' });
    return { dispose() {}, resetView() {} };
  }
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  renderer.domElement.className = 'viewer-canvas';
  container.appendChild(renderer.domElement);

  const scene = new Scene();
  const camera = new PerspectiveCamera(35, 1, 0.1, 10000);
  scene.add(new HemisphereLight(0xffffff, 0x8a8174, 1.6));
  const key = new DirectionalLight(0xffffff, 1.9);
  key.position.set(1, 1.6, 1.2);
  scene.add(key);
  const rim = new DirectionalLight(0xffffff, 0.6);
  rim.position.set(-1.2, 0.6, -1);
  scene.add(rim);

  controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true;
  controls.dampingFactor = 0.12;
  controls.addEventListener('change', () => (needsRender = true));

  const resize = () => {
    const { width, height } = container.getBoundingClientRect();
    if (!width || !height) return;
    renderer.setSize(width, height, false);
    camera.aspect = width / height;
    camera.updateProjectionMatrix();
    needsRender = true;
  };
  resizeObserver = new ResizeObserver(resize);
  resizeObserver.observe(container);
  resize();

  let home = null;
  const resetView = () => {
    if (!home) return;
    camera.position.copy(home.position);
    controls.target.copy(home.target);
    controls.update();
    needsRender = true;
  };

  const loop = () => {
    frame = requestAnimationFrame(loop);
    if (controls.update() || needsRender) {
      renderer.render(scene, camera);
      needsRender = false;
    }
  };
  loop();

  status('loading', { progress: 0 });
  download(url, abort.signal, (progress) => status('loading', { progress }))
    .then((buffer) => {
      if (disposed) return;
      geometry = new STLLoader().parse(buffer);
      // Printers are Z-up; three.js is Y-up.
      geometry.rotateX(-Math.PI / 2);
      geometry.computeBoundingBox();
      const box = geometry.boundingBox;
      const center = box.getCenter(new Vector3());
      geometry.translate(-center.x, -box.min.y, -center.z);
      geometry.computeBoundingBox();
      const size = geometry.boundingBox.getSize(new Vector3());

      const material = new MeshStandardMaterial({
        color: cssColor(container, '--model', '#c68b6c'),
        roughness: 0.72,
        metalness: 0.02,
      });
      scene.add(new Mesh(geometry, material));

      const extent = Math.max(size.x, size.z) * 1.6 || 10;
      const grid = new GridHelper(
        extent,
        10,
        cssColor(container, '--line-strong', '#cfc9bd'),
        cssColor(container, '--line', '#e3dfd6')
      );
      scene.add(grid);

      geometry.computeBoundingSphere();
      const radius = geometry.boundingSphere.radius || 10;
      const target = new Vector3(0, size.y / 2, 0);
      const distance = (radius / Math.sin(((camera.fov / 2) * Math.PI) / 180)) * 1.1;
      const direction = new Vector3(1, 0.75, 1.25).normalize();
      camera.near = distance / 100;
      camera.far = distance * 20;
      camera.updateProjectionMatrix();
      home = { position: target.clone().add(direction.multiplyScalar(distance)), target };
      controls.minDistance = radius * 0.4;
      controls.maxDistance = distance * 4;
      resetView();
      // Report size in printer axes: width (X), depth (Y), height (Z), in millimetres.
      status('ready', { size: [size.x, size.z, size.y] });
    })
    .catch((error) => {
      if (error.name !== 'AbortError') status('error', { message: error.message });
    });

  return {
    resetView,
    dispose() {
      disposed = true;
      abort.abort();
      cancelAnimationFrame(frame);
      resizeObserver.disconnect();
      controls.dispose();
      geometry?.dispose();
      scene.traverse((object) => object.material?.dispose?.());
      renderer.dispose();
      renderer.domElement.remove();
    },
  };
}

window.FormaViewer = { mount };
