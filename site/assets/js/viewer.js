/* CAD mesh viewer.
 *
 * Every part on this site is a GLB carrying positions and indices and nothing
 * else — no normals, no material. That is deliberate: the meshes come out of
 * OpenCASCADE one B-rep face at a time, and keeping those vertex islands
 * separate is what makes `computeVertexNormals()` shade a fillet smoothly while
 * leaving a hard crease at every real edge. Shipping baked normals would have
 * cost bytes to get a worse result.
 *
 * Two things matter for reading an edit:
 *
 *   Shared framing. The three parts of a task (input, ours, ground truth) live
 *   in one coordinate frame — the pipeline has a gate that rejects any edit
 *   which moves the part — so all three viewers are framed on the union of
 *   their bounding boxes. Framing each one to itself would silently re-centre
 *   and re-scale them, and an edit that added 5 mm of material would look
 *   identical to one that did nothing.
 *
 *   Locked cameras. Orbiting one viewer orbits all of them. Comparing two
 *   solids from two different angles is not comparing them.
 */

import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";

const loader = new GLTFLoader();
const cache = new Map();

export const ROLE_COLOR = {
  input: 0x9aa5b5,
  ours:  0x4f9bf5,
  gt:    0xdda23a,
};

const EDGE_COLOR = 0x1b2836;

/** Load one GLB and return its geometry, centred on nothing and shared. */
export function loadGeometry(url) {
  if (!cache.has(url)) {
    cache.set(url, new Promise((resolve, reject) => {
      loader.load(url, (gltf) => {
        let found = null;
        gltf.scene.traverse((o) => { if (!found && o.isMesh) found = o.geometry; });
        if (!found) { reject(new Error("no mesh in " + url)); return; }
        found.computeVertexNormals();
        found.computeBoundingBox();
        resolve(found);
      }, undefined, reject);
    }));
  }
  return cache.get(url);
}

class Viewer {
  constructor(host, { background = null } = {}) {
    this.host = host;
    this.parts = [];
    this.needsRender = true;
    this.spinning = false;

    this.renderer = new THREE.WebGLRenderer({
      antialias: true, alpha: true, powerPreference: "low-power",
    });
    this.renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
    host.appendChild(this.renderer.domElement);

    this.scene = new THREE.Scene();
    if (background !== null) this.scene.background = new THREE.Color(background);

    // A hemisphere fill plus two keys: enough shape reading on a matte solid
    // without a shadow pass, which would cost more than it tells you here.
    this.scene.add(new THREE.HemisphereLight(0xffffff, 0x4a5568, 2.05));
    const key = new THREE.DirectionalLight(0xffffff, 1.6);
    key.position.set(1, 1.35, 1);
    const fill = new THREE.DirectionalLight(0xffffff, 0.55);
    fill.position.set(-1.1, -0.4, -0.9);
    this.scene.add(key, fill);

    this.camera = new THREE.PerspectiveCamera(32, 1, 0.01, 1e5);
    this.pivot = new THREE.Group();      // everything is parented here so the
    this.scene.add(this.pivot);          // whole task can be recentred at once

    this.controls = new OrbitControls(this.camera, this.renderer.domElement);
    this.controls.enableDamping = true;
    this.controls.dampingFactor = 0.09;
    this.controls.rotateSpeed = 0.85;
    this.controls.addEventListener("change", () => {
      this.needsRender = true;
      if (this.onCamera) this.onCamera(this);
    });

    this.observer = new ResizeObserver(() => this.resize());
    this.observer.observe(host);
    this.resize();
  }

  resize() {
    const w = this.host.clientWidth, h = this.host.clientHeight;
    if (!w || !h) return;
    this.renderer.setSize(w, h, false);
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
    this.needsRender = true;
  }

  /** Add one part. `geometry` is shared, so it is never mutated here. */
  addPart(geometry, { color, opacity = 1, edges = true } = {}) {
    const material = new THREE.MeshStandardMaterial({
      color, roughness: 0.52, metalness: 0.12,
      transparent: opacity < 1, opacity,
      depthWrite: opacity === 1,
      side: opacity < 1 ? THREE.DoubleSide : THREE.FrontSide,
    });
    const mesh = new THREE.Mesh(geometry, material);
    this.pivot.add(mesh);

    let line = null;
    if (edges) {
      // 24° keeps the B-rep face boundaries and drops the facet seams inside a
      // tessellated cylinder, which is what makes these read as CAD parts
      // rather than as triangle soup
      line = new THREE.LineSegments(
        new THREE.EdgesGeometry(geometry, 24),
        new THREE.LineBasicMaterial({
          color: EDGE_COLOR, transparent: true,
          opacity: opacity < 1 ? 0.28 : 0.42,
        }));
      this.pivot.add(line);
    }

    this.parts.push({ mesh, line, material });
    this.needsRender = true;
    return mesh;
  }

  setPartVisible(i, on) {
    const p = this.parts[i];
    if (!p) return;
    p.mesh.visible = on;
    if (p.line) p.line.visible = on;
    this.needsRender = true;
  }

  setEdgesVisible(on) {
    for (const p of this.parts) if (p.line) p.line.visible = on;
    this.needsRender = true;
  }

  /** Centre on `box` and pull the camera back far enough to hold it. */
  frame(box) {
    const centre = box.getCenter(new THREE.Vector3());
    const radius = box.getSize(new THREE.Vector3()).length() / 2 || 1;
    this.pivot.position.copy(centre).multiplyScalar(-1);

    const fov = THREE.MathUtils.degToRad(this.camera.fov);
    this.distance = (radius / Math.sin(fov / 2)) * 1.08;
    this.camera.near = this.distance / 120;
    this.camera.far = this.distance * 24;
    this.home();
  }

  /** The three-quarter view the dataset's own renders use. */
  home() {
    const d = this.distance || 3;
    this.camera.position.set(d * 0.62, d * 0.52, d * 0.59);
    this.controls.target.set(0, 0, 0);
    this.controls.update();
    this.camera.updateProjectionMatrix();
    this.needsRender = true;
  }

  setSpinning(on) { this.spinning = on; this.needsRender = true; }

  tick(dt) {
    if (this.spinning) {
      this.controls.autoRotate = true;
      this.controls.autoRotateSpeed = 1.6;
    } else {
      this.controls.autoRotate = false;
    }
    if (this.controls.enableDamping || this.controls.autoRotate) {
      if (this.controls.update(dt)) this.needsRender = true;
    }
    if (this.needsRender) {
      this.renderer.render(this.scene, this.camera);
      this.needsRender = false;
    }
  }

  dispose() {
    this.observer.disconnect();
    this.controls.dispose();
    for (const p of this.parts) {
      p.material.dispose();
      if (p.line) { p.line.geometry.dispose(); p.line.material.dispose(); }
    }
    this.renderer.dispose();
    this.renderer.domElement.remove();
  }
}

/* One animation loop drives every viewer on the page. Each viewer draws only
 * when something changed, so an idle page costs a requestAnimationFrame and
 * three early returns rather than three full renders. */
const live = new Set();
let running = false;
let last = 0;

function loop(now) {
  const dt = Math.min((now - last) / 1000, 0.1);
  last = now;
  for (const v of live) v.tick(dt);
  if (live.size) requestAnimationFrame(loop);
  else running = false;
}

function register(v) {
  live.add(v);
  if (!running) { running = true; last = performance.now(); requestAnimationFrame(loop); }
}

/** Copy one viewer's camera onto its peers, so orbiting one orbits all. */
export function linkCameras(viewers) {
  let echoing = false;
  for (const v of viewers) {
    v.onCamera = (source) => {
      if (echoing) return;
      echoing = true;
      for (const other of viewers) {
        if (other === source) continue;
        other.camera.position.copy(source.camera.position);
        other.camera.quaternion.copy(source.camera.quaternion);
        other.camera.zoom = source.camera.zoom;
        other.camera.updateProjectionMatrix();
        other.controls.target.copy(source.controls.target);
        other.needsRender = true;
      }
      echoing = false;
    };
  }
}

/**
 * Mount a viewer into `host`, load `parts`, frame them, and start drawing.
 *
 * `parts` is [{url, color, opacity}]. Resolves to the Viewer once everything
 * is on screen, or rejects if a GLB will not load — the caller shows that in
 * the viewer's own status layer rather than as a blank box.
 */
export async function mount(host, parts, opts = {}) {
  const viewer = new Viewer(host, opts);
  const geometries = await Promise.all(parts.map((p) => loadGeometry(p.url)));

  const box = new THREE.Box3();
  geometries.forEach((g) => box.union(g.boundingBox));
  geometries.forEach((g, i) => viewer.addPart(g, parts[i]));
  viewer.frame(box);

  register(viewer);
  viewer.triangles = geometries.reduce((n, g) => n + g.index.count / 3, 0);
  return viewer;
}

export { Viewer };
