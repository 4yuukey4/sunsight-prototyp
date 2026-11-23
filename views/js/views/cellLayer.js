// MapLibre custom layer drawing every roof and facade cell of a region with Three.js.
//
// Each tile has its own local metric frame; per frame we compose
// (map projection) x (tile model matrix) in double precision on the CPU and
// hand the result to the shader, so there is no float32 jitter at street zoom.
import * as THREE from 'three';
import { METRICS } from '../models/metrics.js';
import { hitSlot, rampTexture, setValues, surfaceMaterial, valueTexture } from '../lib/surfaces.js';

export class CellLayer {
  constructor(store, { onProgress } = {}) {
    this.id = 'cells';
    this.type = 'custom';
    this.renderingMode = '3d';
    this.store = store;
    this.onProgress = onProgress || (() => {});
    this.tiles = new Map();
    this.visible = true;
    this.metric = 'overall';
    this.loadToken = 0;
    this.raycaster = new THREE.Raycaster();
  }

  onAdd(map, gl) {
    this.map = map;
    this.renderer = new THREE.WebGLRenderer({ canvas: map.getCanvas(), context: gl, antialias: true });
    this.renderer.autoClear = false;
    this.scene = new THREE.Scene();
    this.camera = new THREE.Camera();
  }

  render(gl, matrix) {
    if (!this.visible || !this.tiles.size) return;
    const P = new THREE.Matrix4().fromArray(matrix);
    for (const t of this.tiles.values()) {
      t.mvp.multiplyMatrices(P, t.model);
      t.mat.uniforms.uMVP.value.copy(t.mvp);
    }
    this.renderer.resetState();
    this.renderer.render(this.scene, this.camera);
  }

  setVisible(v) {
    this.visible = v;
    this.map?.triggerRepaint();
  }

  clear() {
    for (const t of this.tiles.values()) {
      this.scene.remove(t.obj);
      t.mat.dispose();
      t.tex.dispose();
    }
    this.tiles.clear();
  }

  // Load every tile of the region (nearest to the view first).
  async loadRegion(meta, metric) {
    const token = ++this.loadToken;
    this.clear();
    this.meta = meta;
    this.metric = metric;
    const c = this.map.getCenter();
    const keys = [...meta.tiles].sort((a, b) => tileDist(a, c) - tileDist(b, c));
    let done = 0;
    const worker = async () => {
      while (keys.length) {
        const key = keys.shift();
        const mesh = await this.store.mesh(key);
        if (token !== this.loadToken) return;
        const mat = surfaceMaterial({ useMVP: true });
        const tex = valueTexture(mesh.nSlots);
        mat.uniforms.uValues.value = tex;
        const obj = new THREE.Mesh(mesh.geometry, mat);
        obj.frustumCulled = false;
        const mc = maplibregl.MercatorCoordinate.fromLngLat([mesh.lon0, mesh.lat0], 0);
        const s = mc.meterInMercatorCoordinateUnits();
        const model = new THREE.Matrix4().makeTranslation(mc.x, mc.y, mc.z).scale(new THREE.Vector3(s, -s, s));
        const t = { key, mesh, mat, tex, obj, model, mvp: new THREE.Matrix4() };
        this.tiles.set(key, t);
        this.scene.add(obj);
        await this._applyMetric(t, this.metric);
        this.onProgress(++done, meta.tiles.length);
      }
    };
    await Promise.all(Array.from({ length: 6 }, worker));
  }

  async setMetric(metric) {
    this.metric = metric;
    await Promise.all([...this.tiles.values()].map((t) => this._applyMetric(t, metric)));
  }

  async _applyMetric(t, metric) {
    const vals = await this.store.values(t.key, metric, this.meta);
    if (metric !== this.metric) return;
    setValues(t.tex, vals);
    const u = t.mat.uniforms, d = this.meta.domains;
    u.uRamp.value = rampTexture(METRICS[metric].ramp);
    u.uDomRoof.value.fromArray(d.roof[metric] || [0, 1]);
    u.uDomWall.value.fromArray(d.facade[metric] || [0, 1]);
    this.map.triggerRepaint();
  }

  select(key, bid, slot = -1) {
    for (const t of this.tiles.values()) {
      t.mat.uniforms.uSelBid.value = t.key === key ? bid : -1;
      t.mat.uniforms.uSelSlot.value = t.key === key ? slot : -1;
    }
    this.map.triggerRepaint();
  }

  // Ray-cast a screen point into every tile's local frame; nearest hit wins.
  pick(point) {
    const cv = this.map.getCanvas();
    const ndc = new THREE.Vector2((point.x / cv.clientWidth) * 2 - 1, -(point.y / cv.clientHeight) * 2 + 1);
    let best = null;
    for (const t of this.tiles.values()) {
      const inv = t.mvp.clone().invert();
      const near = new THREE.Vector3(ndc.x, ndc.y, -1).applyMatrix4(inv);
      const far = new THREE.Vector3(ndc.x, ndc.y, 1).applyMatrix4(inv);
      this.raycaster.ray.set(near, far.sub(near).normalize());
      const hit = this.raycaster.intersectObject(t.obj, false)[0];
      if (hit && (!best || hit.distance < best.distance)) best = { ...hitSlot(hit), key: t.key, distance: hit.distance };
    }
    return best;
  }
}

function tileDist(key, c) {
  const [z, x, y] = key.split('-').map(Number);
  const n = 2 ** z;
  const lon = ((x + 0.5) / n) * 360 - 180;
  const lat = (Math.atan(Math.sinh(Math.PI * (1 - (2 * (y + 0.5)) / n))) * 180) / Math.PI;
  return Math.hypot(lon - c.lng, (lat - c.lat) * 1.8);
}
