// 3D inspector around one building, plus the view-validation tools.
//
// Scene content is in the target building's local frame (x east, y north,
// z up, metres from its centroid) inside a root group rotated to Three's y-up.
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { LineSegments2 } from 'three/addons/lines/LineSegments2.js';
import { LineSegmentsGeometry } from 'three/addons/lines/LineSegmentsGeometry.js';
import { LineMaterial } from 'three/addons/lines/LineMaterial.js';
import { METRICS, fmt, legendHTML } from '../models/metrics.js';
import { EMPTY, hitSlot, rampTexture, setValues, slotCorners, surfaceMaterial, valueTexture } from '../lib/surfaces.js';
import { cellSummary } from './buildingPanel.js';

const $ = (id) => document.getElementById(id);
const DEG = Math.PI / 180;
const FRONT_COS = Math.cos(75 * DEG);   // config.VIEW_FRONT_HALF_ANGLE
const FEATURE_MIN = 15;                 // config.VIEW_FEATURE_MIN_M
const MAX_RAY = 500;
const W_OPEN = 0.6, W_GREEN = 0.2, W_WATER = 0.2;

function pad2(n) { return String(n).padStart(2, '0'); }
function hhmm(min) { return `${pad2(Math.floor(min / 60) % 24)}:${pad2(min % 60)}`; }
// local ENU direction -> Three world direction
function enu(x, y, z) { return new THREE.Vector3(x, z, -y); }
function sunDir(az, el) {
  return enu(Math.sin(az * DEG) * Math.cos(el * DEG), Math.cos(az * DEG) * Math.cos(el * DEG), Math.sin(el * DEG)).normalize();
}
function isFront(kind, naz, az) {
  return kind === 'roof' || Math.cos((az - naz) * DEG) > FRONT_COS;
}

export class InspectorView {
  constructor({ api, store }) {
    this.api = api;
    this.store = store;
    this.mode = 'annual';
    this.metric = 'overall';
    this.minutes = 720;
    this.playing = null;
    this.lookout = null;
    $('inspMetric').innerHTML = Object.entries(METRICS).map(([k, d]) => `<option value="${k}">${d.label}</option>`).join('');
    $('inspMetric').addEventListener('change', (e) => { this.metric = e.target.value; this._recolor(); });
    document.querySelectorAll('#inspector .seg button').forEach((b) => b.addEventListener('click', () => this.setMode(b.dataset.mode)));
    $('inspDate').value = new Date().toISOString().slice(0, 10);
    $('inspDate').addEventListener('change', () => this._loadSunpath());
    $('inspTime').addEventListener('input', (e) => { this.minutes = Number(e.target.value); this._updateSun(); });
    $('inspPlay').addEventListener('click', () => this._togglePlay());
    $('inspClose').addEventListener('click', () => this.close());
    $('inspBack').addEventListener('click', () => this._exitLookout());
    $('inspRays').addEventListener('change', (e) => { if (this.rayGroup) this.rayGroup.visible = e.target.checked; });
    addEventListener('keydown', (e) => {
      if (e.key !== 'Escape' || $('inspector').classList.contains('hidden')) return;
      this.lookout ? this._exitLookout() : this.close();
    });
  }

  _initRenderer() {
    if (this.renderer) return;
    const stage = $('inspStage');
    this.renderer = new THREE.WebGLRenderer({ antialias: true });
    this.renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
    this.renderer.shadowMap.enabled = true;
    this.renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    stage.appendChild(this.renderer.domElement);
    this.camera = new THREE.PerspectiveCamera(45, 1, 0.05, 8000);
    this.controls = new OrbitControls(this.camera, this.renderer.domElement);
    this.controls.enableDamping = true;
    this.controls.maxPolarAngle = Math.PI * 0.495;
    new ResizeObserver(() => this._resize()).observe(stage);
    this.raycaster = new THREE.Raycaster();
    const el = this.renderer.domElement;
    let down = null;
    el.addEventListener('pointerdown', (e) => { down = [e.clientX, e.clientY]; });
    el.addEventListener('pointerup', (e) => {
      if (down && Math.hypot(e.clientX - down[0], e.clientY - down[1]) < 5 && !this.lookout) this._pick(e);
    });
  }

  _resize() {
    const s = $('inspStage');
    this.renderer.setSize(s.clientWidth, s.clientHeight);
    this.camera.aspect = s.clientWidth / Math.max(1, s.clientHeight);
    this.camera.updateProjectionMatrix();
    this.rayMats?.forEach((m) => m.resolution.set(s.clientWidth, s.clientHeight));
  }

  // opts: { rid, meta, key, bid, title, metric, validateCell }
  async open(opts) {
    Object.assign(this, { rid: opts.rid, meta: opts.meta, key: opts.key, bid: opts.bid, metric: opts.metric });
    $('inspMetric').value = this.metric;
    $('inspTitle').textContent = opts.title;
    $('inspSub').textContent = 'Loading neighbourhood…';
    $('cellInfo').classList.add('hidden');
    $('inspector').classList.remove('hidden');
    this._exitLookout();
    this._initRenderer();
    this._resize();
    const [scene] = await Promise.all([this.api.scene(opts.key, opts.bid), this._loadSunpath()]);
    this.data = scene;
    await this._build(scene);
    const nCells = Object.values(scene.horizons).reduce((s, h) => s + h.slots.length, 0);
    $('inspSub').textContent = `${scene.buildings.length} buildings within 300 m · ` +
      `${nCells.toLocaleString('en-GB')} cells within 90 m support "sun at a moment" · ${this.meta.name}`;
    this.setMode(this.mode);
    this.renderer.setAnimationLoop(() => { this.controls.update(); this.renderer.render(this.scene, this.camera); });
    if (opts.validateCell !== undefined && opts.validateCell >= 0) {
      const info = await this.api.rays(opts.key, opts.validateCell);
      this._showCell(opts.key, info);
      await this._showRays(opts.key, info);
      this._enterLookout();
    }
  }

  close() {
    this._exitLookout();
    if (this.playing) this._togglePlay();
    this.renderer?.setAnimationLoop(null);
    $('inspector').classList.add('hidden');
    $('cellInfo').classList.add('hidden');
  }

  async _build(d) {
    if (this.scene) {
      this.scene.traverse((o) => {
        if (o.userData.sharedGeometry) { o.material?.dispose(); return; }
        o.geometry?.dispose(); o.material?.dispose?.();
      });
      this.tiles?.forEach((t) => t.tex.dispose());
    }
    const scene = (this.scene = new THREE.Scene());
    scene.fog = new THREE.Fog(0xdfe6ee, 600, 2600);
    scene.add(new THREE.HemisphereLight(0xffffff, 0x7d8a99, 1.1));
    const sun = (this.sun = new THREE.DirectionalLight(0xffffff, 2.2));
    sun.castShadow = true;
    sun.shadow.mapSize.set(4096, 4096);
    Object.assign(sun.shadow.camera, { left: -420, right: 420, top: 420, bottom: -420, near: 1, far: 2400 });
    sun.shadow.bias = -0.0004;
    scene.add(sun, sun.target);
    const root = (this.root = new THREE.Group());
    root.rotation.x = -Math.PI / 2;
    scene.add(root);

    const ground = new THREE.Mesh(new THREE.PlaneGeometry(6000, 6000), new THREE.MeshStandardMaterial({ color: 0xd3d5cc, roughness: 1 }));
    ground.receiveShadow = true;
    root.add(ground);
    const lcMat = {
      green: new THREE.MeshStandardMaterial({ color: 0x8dbb73, roughness: 1 }),
      water: new THREE.MeshStandardMaterial({ color: 0x6aa4d8, roughness: 0.4 }),
    };
    for (const f of d.landcover) {
      const toV2 = (r) => r.map(([x, y]) => new THREE.Vector2(x, y));
      const shape = new THREE.Shape(toV2(f.rings[0]));
      f.rings.slice(1).forEach((r) => shape.holes.push(new THREE.Path(toV2(r))));
      const m = new THREE.Mesh(new THREE.ShapeGeometry(shape), lcMat[f.cls]);
      m.position.z = f.cls === 'water' ? 0.06 : 0.04;
      m.receiveShadow = true;
      root.add(m);
    }

    // Context buildings that no loaded tile analyses: plain extrusions.
    const ctxMat = new THREE.MeshStandardMaterial({ color: 0xe6e3dc, roughness: 0.9 });
    for (const b of d.buildings) {
      if (b.surfaced) continue;
      const toV2 = (r) => r.map(([x, y]) => new THREE.Vector2(x, y));
      const shape = new THREE.Shape(toV2(b.rings[0]));
      b.rings.slice(1).forEach((r) => shape.holes.push(new THREE.Path(toV2(r))));
      const m = new THREE.Mesh(new THREE.ExtrudeGeometry(shape, { depth: b.h, bevelEnabled: false }), ctxMat);
      m.castShadow = m.receiveShadow = true;
      root.add(m);
    }

    // Analysed surfaces: the same pixel-matrix meshes the map uses.
    this.tiles = [];
    for (const t of d.tiles) {
      const mesh = await this.store.mesh(t.key);
      const mat = surfaceMaterial({ useMVP: false });
      const tex = valueTexture(mesh.nSlots);
      mat.uniforms.uValues.value = tex;
      const obj = new THREE.Mesh(mesh.geometry, mat);
      obj.userData = { key: t.key, sharedGeometry: true };
      obj.position.set(t.offset[0], t.offset[1], 0);
      obj.castShadow = true;
      root.add(obj);
      const hz = d.horizons[t.key];
      this.tiles.push({ key: t.key, offset: t.offset, mesh, mat, tex, obj, hz,
                        hzBytes: hz ? Uint8Array.from(atob(hz.horizon_b64), (c) => c.charCodeAt(0)) : null });
    }
    const tgt = this.tiles.find((t) => t.key === d.target.tile);
    if (tgt) tgt.mat.uniforms.uSelBid.value = d.target.bid;

    this.sunMarker = new THREE.Mesh(new THREE.SphereGeometry(10, 24, 16), new THREE.MeshBasicMaterial({ color: 0xffd34d, fog: false }));
    this.pathLine = new THREE.Line(new THREE.BufferGeometry(), new THREE.LineDashedMaterial({ color: 0xe8962e, dashSize: 8, gapSize: 6, fog: false }));
    scene.add(this.sunMarker, this.pathLine);
    this.rayGroup = null;

    const r = Math.max(45, d.target.h * 3);
    this.controls.target.set(0, d.target.h / 2, 0);
    this.camera.position.set(-r * 0.9, r * 0.8, r * 0.9);
    this.camera.fov = 45;
    this.camera.updateProjectionMatrix();
  }

  async _loadSunpath() {
    if (!this.rid) return;
    this.sunpath = await this.api.sunpath(this.rid, $('inspDate').value);
    if (this.scene) { this._drawSunpath(); this._updateSun(); }
  }

  _drawSunpath() {
    const sp = this.sunpath;
    const pts = [];
    sp.az.forEach((az, i) => { if (sp.el[i] > 0) pts.push(sunDir(az, sp.el[i]).multiplyScalar(520)); });
    this.pathLine.geometry.dispose();
    this.pathLine.geometry = new THREE.BufferGeometry().setFromPoints(pts);
    this.pathLine.computeLineDistances();
  }

  setMode(mode) {
    this.mode = mode;
    document.querySelectorAll('#inspector .seg button').forEach((b) => b.classList.toggle('on', b.dataset.mode === mode));
    $('inspMetric').classList.toggle('hidden', mode !== 'annual');
    $('inspector').querySelector('.time-ctl').classList.toggle('disabled', mode !== 'instant');
    if (!this.scene) return;
    this.sunMarker.visible = this.pathLine.visible = mode === 'instant';
    if (mode === 'instant') this._drawSunpath();
    this._updateSun();
    this._recolor();
  }

  _sunNow() {
    const sp = this.sunpath;
    if (!sp) return { az: 180, el: 40 };
    const i = Math.min(sp.az.length - 1, Math.round(this.minutes / sp.step_min));
    return { az: sp.az[i], el: sp.el[i] };
  }

  _updateSun() {
    if (!this.scene) return;
    $('inspTime').value = this.minutes;
    $('inspTimeLabel').textContent = hhmm(this.minutes);
    const sp = this.sunpath;
    const { az, el } = this.mode === 'instant' ? this._sunNow() : { az: 215, el: 38 };
    this.sun.position.copy(sunDir(az, Math.max(el, 0.5)).multiplyScalar(1000));
    this.sun.intensity = this.mode === 'instant' ? (el > 0 ? 2.4 * Math.min(1, el / 8 + 0.2) : 0) : 2.0;
    this.sunMarker.position.copy(sunDir(az, el).multiplyScalar(520));
    this.sunMarker.visible = this.mode === 'instant' && el > -2;
    const sky = this.mode === 'instant'
      ? (el > 0 ? new THREE.Color('#9cc3e6').lerp(new THREE.Color('#f3c79a'), Math.max(0, 1 - el / 12)) : new THREE.Color('#141a2c'))
      : new THREE.Color('#dfe6ee');
    this.scene.background = sky;
    this.scene.fog.color = sky;
    if (this.mode === 'instant' && sp) {
      const day = sp.polar === 'polar_night' ? 'Polar night: the sun never rises'
        : sp.polar === 'midnight_sun' ? 'Midnight sun: the sun never sets'
          : `sunrise ${sp.sunrise ?? '–'} · sunset ${sp.sunset ?? '–'} · ${sp.daylight_h} h daylight`;
      $('inspSun').textContent = `sun az ${az.toFixed(0)}° el ${el.toFixed(1)}° · ${day} (${sp.timezone})`;
      this._recolor();
    } else {
      $('inspSun').textContent = '';
    }
  }

  async _recolor() {
    if (!this.tiles) return;
    if (this.mode === 'annual') {
      const d = this.meta.domains, metric = this.metric;
      await Promise.all(this.tiles.map(async (t) => {
        const vals = await this.store.values(t.key, metric, this.meta);
        if (this.mode !== 'annual' || this.metric !== metric) return;
        setValues(t.tex, vals);
        const u = t.mat.uniforms;
        u.uMode.value = 0;
        u.uRamp.value = rampTexture(METRICS[metric].ramp);
        u.uDomRoof.value.fromArray(d.roof[metric] || [0, 1]);
        u.uDomWall.value.fromArray(d.facade[metric] || [0, 1]);
      }));
      $('inspLegend').innerHTML = legendHTML('Facade cells', metric, d.facade[metric]) + legendHTML('Roof cells', metric, d.roof[metric]);
      return;
    }
    // Sun at a moment: shade every cell within 90 m from its stored horizon profile.
    const { az, el } = this._sunNow();
    const A = this.data.az_bins, a = Math.floor(az / (360 / A)) % A;
    let lit = 0, facing = 0;
    for (const t of this.tiles) {
      const v = t.tex.image.data;
      v.fill(-1e30);
      if (t.hz) {
        const { slots, kind, naz } = t.hz;
        for (let i = 0; i < slots.length; i++) {
          const roof = kind[i] === 0;
          const cosInc = roof ? Math.sin(el * DEG) : Math.cos(el * DEG) * Math.cos((az - naz[i]) * DEG);
          let val;
          if (el <= 0) val = 0;
          else if (cosInc <= 0) val = 1;
          else if (el * 2 > t.hzBytes[i * A + a]) { val = 3 + Math.min(1, cosInc * 1.3); lit++; }
          else val = 2;
          if (el > 0 && cosInc > 0) facing++;
          v[slots[i]] = val;
        }
      }
      t.tex.needsUpdate = true;
      t.mat.uniforms.uMode.value = 1;
    }
    $('inspLegend').innerHTML = `<div class="legend-title">Sun at ${hhmm(this.minutes)}</div>
      <div><b style="color:#f3c35a">■</b> in direct sun (brighter = more head-on)</div>
      <div><b style="color:#33405c">■</b> shaded by a building</div>
      <div><b style="color:#29304a">■</b> facing away from the sun</div>
      <div><b style="color:#9a9a9e">■</b> beyond 90 m (not evaluated)</div>
      <div class="muted" style="margin-top:4px">${facing ? Math.round((100 * lit) / facing) : 0}% of sun-facing cells are lit</div>`;
  }

  _togglePlay() {
    if (this.playing) { clearInterval(this.playing); this.playing = null; $('inspPlay').textContent = '▶'; return; }
    if (this.mode !== 'instant') this.setMode('instant');
    $('inspPlay').textContent = '⏸';
    this.playing = setInterval(() => { this.minutes = (this.minutes + 5) % 1440; this._updateSun(); }, 60);
  }

  async _pick(e) {
    const rect = this.renderer.domElement.getBoundingClientRect();
    const ndc = new THREE.Vector2(((e.clientX - rect.left) / rect.width) * 2 - 1, -((e.clientY - rect.top) / rect.height) * 2 + 1);
    this.raycaster.setFromCamera(ndc, this.camera);
    const hit = this.raycaster.intersectObjects(this.tiles.map((t) => t.obj), false)[0];
    if (!hit) return;
    const { slot } = hitSlot(hit);
    const key = hit.object.userData.key;
    try {
      const info = await this.api.slot(key, slot);
      this._showCell(key, info);
    } catch {
      $('cellInfo').innerHTML = '<p class="muted">No cell here (shared wall with a neighbour).</p>';
      $('cellInfo').classList.remove('hidden');
    }
  }

  _showCell(key, info) {
    this.cell = { key, info };
    for (const t of this.tiles) t.mat.uniforms.uSelSlot.value = t.key === key ? info.slot : -1;
    $('cellInfo').innerHTML = `${cellSummary(info)}
      <div class="btn-row">
        <button class="cta" id="lookBtn">${info.kind === 'roof' ? 'Stand on the roof here' : 'Look out of this window'}</button>
        <button class="cta alt" id="raysBtn">Validate view</button>
      </div><div id="rayReport"></div>`;
    $('cellInfo').classList.remove('hidden');
    $('lookBtn').onclick = () => this._enterLookout();
    $('raysBtn').onclick = async () => this._showRays(key, await this.api.rays(key, info.cell));
  }

  // ---- view validation ----------------------------------------------------
  async _showRays(key, info) {
    if (this.rayGroup) { this.root.remove(this.rayGroup); this.rayGroup.traverse((o) => { o.geometry?.dispose(); o.material?.dispose(); }); }
    const t = this.tiles.find((x) => x.key === key);
    const [ox, oy, oz] = [info.origin[0] + t.offset[0], info.origin[1] + t.offset[1], info.origin[2]];
    this.cell = { key, info, origin: [ox, oy, oz] };
    const segs = { sight: [], ground: [], green: [], water: [], hit: [] };
    let nFront = 0, open = 0, nGreen = 0, nWater = 0;
    for (const r of info.rays) {
      const front = isFront(info.kind, info.naz, r.az);
      if (!front) continue;
      nFront++;
      const sx = Math.sin(r.az * DEG), sy = Math.cos(r.az * DEG);
      const d = r.first_hit ?? MAX_RAY;
      open += Math.log1p(d) / Math.log1p(MAX_RAY);
      const gl = r.runs.filter((q) => q[2] === 'green').reduce((s, q) => s + q[1] - q[0], 0);
      const wl = r.runs.filter((q) => q[2] === 'water').reduce((s, q) => s + q[1] - q[0], 0);
      if (gl >= FEATURE_MIN) nGreen++;
      if (wl >= FEATURE_MIN) nWater++;
      segs.sight.push(ox, oy, oz, ox + sx * d, oy + sy * d, oz);
      segs.ground.push(ox, oy, 0.25, ox + sx * d, oy + sy * d, 0.25);
      for (const [d0, d1, cls] of r.runs) segs[cls].push(ox + sx * d0, oy + sy * d0, 0.35, ox + sx * d1, oy + sy * d1, 0.35);
      if (r.first_hit !== null) segs.hit.push(ox + sx * d, oy + sy * d, 0, ox + sx * d, oy + sy * d, r.hit_h);
    }
    const g = (this.rayGroup = new THREE.Group());
    this.rayMats = [];
    const add = (arr, color, width, opacity = 1, name = '') => {
      if (!arr.length) return;
      const geo = new LineSegmentsGeometry().setPositions(arr);
      const mat = new LineMaterial({ color, linewidth: width, transparent: opacity < 1, opacity, depthTest: true });
      mat.resolution.set($('inspStage').clientWidth, $('inspStage').clientHeight);
      this.rayMats.push(mat);
      const line = new LineSegments2(geo, mat);
      line.name = name;
      g.add(line);
    };
    add(segs.ground, 0x6b7280, 1.5, 0.7);
    add(segs.green, 0x1f9d3a, 5);
    add(segs.water, 0x1769d1, 5);
    add(segs.sight, 0xf0a030, 1.2, 0.55, 'sight');
    add(segs.hit, 0xe5484d, 3);
    g.visible = $('inspRays').checked;
    this.root.add(g);

    const o = open / Math.max(1, nFront), gr = nGreen / Math.max(1, nFront), wa = nWater / Math.max(1, nFront);
    const score = W_OPEN * o + W_GREEN * gr + W_WATER * wa;
    const m = info.metrics;
    $('rayReport').innerHTML = `
      <div class="section-title">What the view metric sees</div>
      <canvas id="polar" width="220" height="220"></canvas>
      <div class="kv small">
        <span>Directions (±75° of ${info.kind === 'roof' ? 'roof: all' : 'normal'})</span><span>${nFront} of ${info.rays.length}</span>
        <span>Openness (mean log distance)</span><span>${o.toFixed(3)}</span>
        <span>Green (≥${FEATURE_MIN} m seen)</span><span>${nGreen}/${nFront} = ${fmt(gr, 'pct')}</span>
        <span>Water (≥${FEATURE_MIN} m seen)</span><span>${nWater}/${nFront} = ${fmt(wa, 'pct')}</span>
        <span>0.6·${o.toFixed(2)} + 0.2·${gr.toFixed(2)} + 0.2·${wa.toFixed(2)}</span><span><b>${score.toFixed(3)}</b></span>
        <span>Stored metric (check)</span><span>${fmt(m.view_score, 'frac')}</span>
      </div>
      <div class="muted small">On the map: <span style="color:#1f9d3a">■</span> green and <span style="color:#1769d1">■</span> water
        under each sight-line, <span style="color:#e5484d">|</span> first building that blocks it, orange = line of sight at eye height.</div>`;
    this._drawPolar(info);
  }

  _drawPolar(info) {
    const cv = $('polar'), g = cv.getContext('2d'), R = 100, C = 110;
    const css = getComputedStyle(document.documentElement);
    g.clearRect(0, 0, 220, 220);
    g.strokeStyle = css.getPropertyValue('--line');
    for (const f of [0.25, 0.5, 0.75, 1]) { g.beginPath(); g.arc(C, C, R * f, 0, 2 * Math.PI); g.stroke(); }
    const step = 360 / info.rays.length;
    for (const r of info.rays) {
      const front = isFront(info.kind, info.naz, r.az);
      const d = r.first_hit ?? MAX_RAY;
      const rad = (R * Math.log1p(d)) / Math.log1p(MAX_RAY);
      const gl = r.runs.filter((q) => q[2] === 'green').reduce((s, q) => s + q[1] - q[0], 0);
      const wl = r.runs.filter((q) => q[2] === 'water').reduce((s, q) => s + q[1] - q[0], 0);
      g.fillStyle = !front ? 'rgba(128,128,128,0.15)' : wl >= FEATURE_MIN ? '#1769d1' : gl >= FEATURE_MIN ? '#1f9d3a' : '#9aa0aa';
      const a0 = (r.az - step / 2 - 90) * DEG, a1 = (r.az + step / 2 - 90) * DEG;
      g.beginPath(); g.moveTo(C, C); g.arc(C, C, rad, a0, a1); g.closePath(); g.fill();
    }
    g.fillStyle = css.getPropertyValue('--muted'); g.font = '11px system-ui';
    g.fillText('N', C - 4, 9); g.fillText('S', C - 4, 218); g.fillText('E', 211, C + 4); g.fillText('W', 1, C + 4);
    if (info.kind !== 'roof') {
      g.strokeStyle = '#ff3fa4'; g.lineWidth = 2; g.beginPath(); g.moveTo(C, C);
      g.lineTo(C + 30 * Math.sin(info.naz * DEG), C - 30 * Math.cos(info.naz * DEG)); g.stroke(); g.lineWidth = 1;
    }
  }

  // ---- first-person window view -------------------------------------------
  _enterLookout() {
    if (!this.cell) return;
    const { key, info } = this.cell;
    const t = this.tiles.find((x) => x.key === key);
    const [x, y, z] = [info.center[0] + t.offset[0], info.center[1] + t.offset[1], info.center[2]];
    const roof = info.kind === 'roof';
    const naz = roof ? 180 : info.naz;
    const nx = Math.sin(naz * DEG), ny = Math.cos(naz * DEG);
    const eyeL = roof ? [x, y, z + 1.7] : [x + nx * 0.5, y + ny * 0.5, z + 0.2];
    this.root.updateMatrixWorld(true);
    const eye = new THREE.Vector3(...eyeL).applyMatrix4(this.root.matrixWorld);
    if (!this.lookout) this.lookout = { pos: this.camera.position.clone(), target: this.controls.target.clone() };
    this.camera.position.copy(eye);
    this.controls.target.copy(eye.clone().add(enu(nx, ny, 0).multiplyScalar(0.05)));
    Object.assign(this.controls, { enableZoom: false, enablePan: false, rotateSpeed: -0.35, maxPolarAngle: Math.PI * 0.9 });
    this.camera.fov = 75;
    this.camera.updateProjectionMatrix();
    const sight = this.rayGroup?.getObjectByName('sight');
    if (sight) sight.visible = false;
    $('inspBack').classList.remove('hidden');
    $('inspHint').textContent = 'Window view: drag to look around · Esc to go back';
  }

  _exitLookout() {
    if (!this.lookout) return;
    this.camera.position.copy(this.lookout.pos);
    this.controls.target.copy(this.lookout.target);
    Object.assign(this.controls, { enableZoom: true, enablePan: true, rotateSpeed: 1, maxPolarAngle: Math.PI * 0.495 });
    this.camera.fov = 45;
    this.camera.updateProjectionMatrix();
    this.lookout = null;
    const sight = this.rayGroup?.getObjectByName('sight');
    if (sight) sight.visible = true;
    $('inspBack').classList.add('hidden');
    $('inspHint').textContent = 'Drag to orbit · scroll to zoom · click a cell for details';
  }
}
