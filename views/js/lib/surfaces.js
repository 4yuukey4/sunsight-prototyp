// Surface meshes: one quad per wall, one polygon per roof, with a per-cell
// "pixel matrix" drawn in the fragment shader from a float value texture.
// Geometry stays tiny (no per-cell triangles), so a whole city, and later
// whole countries, can be on screen at once. Used by the map and the inspector.
import * as THREE from 'three';
import { RAMPS } from '../models/metrics.js';

export const TEX_W = 2048;
export const EMPTY = -1e29;           // values below this mean "no cell here"
const DEG = Math.PI / 180;

export function parseMesh(buf) {
  const dv = new DataView(buf);
  const lat0 = dv.getFloat64(0, true), lon0 = dv.getFloat64(8, true);
  const h = [0, 1, 2, 3, 4, 5].map((i) => dv.getInt32(16 + 4 * i, true));
  const [, nW, nR, nRF, nS, cellMm] = h;
  let off = 40;
  const walls = new Float32Array(buf, off, nW * 11); off += nW * 44;
  const roofs = new Float32Array(buf, off, nR * 11); off += nR * 44;
  const roofCoords = new Float32Array(buf, off, nRF); off += nRF * 4;
  const slotKind = new Uint8Array(buf, off, nS);
  // index of surfaces by slot range, for slot -> surface lookups
  const surf = [];
  for (let i = 0; i < nW; i++) {
    const w = walls.subarray(i * 11, i * 11 + 11);
    surf.push({ type: 'wall', i, base: w[9], cols: w[7], rows: w[8], bid: w[0], naz: w[10] });
  }
  let p = 0;
  const roofRings = [];
  for (let i = 0; i < nR; i++) {
    const r = roofs.subarray(i * 11, i * 11 + 11);
    const rings = [];
    for (let k = 0; k < r[10]; k++) {
      const n = roofCoords[p++];
      rings.push(roofCoords.subarray(p, p + 2 * n)); p += 2 * n;
    }
    roofRings.push(rings);
    surf.push({ type: 'roof', i, base: r[9], cols: r[7], rows: r[8], bid: r[0] });
  }
  surf.sort((a, b) => a.base - b.base);
  return { lat0, lon0, walls, roofs, roofRings, slotKind, nSlots: nS, cell: cellMm / 1000, surf };
}

export function surfaceOfSlot(mesh, slot) {
  const s = mesh.surf;
  let lo = 0, hi = s.length - 1;
  while (lo < hi) {
    const mid = (lo + hi + 1) >> 1;
    if (s[mid].base <= slot) lo = mid; else hi = mid - 1;
  }
  return s[lo];
}

// Corners (local x, y, z) of one cell, for highlighting.
export function slotCorners(mesh, slot) {
  const s = surfaceOfSlot(mesh, slot);
  const k = slot - s.base, col = k % s.cols, row = Math.floor(k / s.cols);
  if (s.type === 'wall') {
    const w = mesh.walls.subarray(s.i * 11, s.i * 11 + 11);
    const [x0, y0, x1, y1, , h] = [w[1], w[2], w[3], w[4], w[5], w[6]];
    const a = col / s.cols, b = (col + 1) / s.cols, z0 = (row / s.rows) * h, z1 = ((row + 1) / s.rows) * h;
    const P = (t, z) => [x0 + (x1 - x0) * t, y0 + (y1 - y0) * t, z];
    return { kind: 'facade', naz: w[10], pts: [P(a, z0), P(b, z0), P(b, z1), P(a, z1)] };
  }
  const r = mesh.roofs.subarray(s.i * 11, s.i * 11 + 11);
  const [, h, ang, cx, cy, ox, oy] = r;
  const c = Math.cos(ang), sn = Math.sin(ang), cell = mesh.cell;
  const back = (u, v) => {           // grid (rotated) -> local
    const dx = ox + u * cell - cx, dy = oy + v * cell - cy;
    return [cx + c * dx - sn * dy, cy + sn * dx + c * dy, h];
  };
  return { kind: 'roof', naz: 0, pts: [back(col, row), back(col + 1, row), back(col + 1, row + 1), back(col, row + 1)] };
}

export function buildGeometry(mesh) {
  const pos = [], uv = [], base = [], cols = [], rows = [], kind = [], shade = [], bid = [], index = [];
  const push = (x, y, z, u, v, b, c, r, k, s, id) => {
    pos.push(x, y, z); uv.push(u, v); base.push(b); cols.push(c); rows.push(r); kind.push(k); shade.push(s); bid.push(id);
    return pos.length / 3 - 1;
  };
  const W = mesh.walls;
  for (let i = 0; i < W.length / 11; i++) {
    const o = i * 11;
    const [id, x0, y0, x1, y1, z0, h, nc, nr, b, naz] = W.subarray(o, o + 11);
    const s = 0.72 + 0.28 * (0.5 + 0.5 * Math.cos((naz - 200) * DEG));   // soft light from SSW
    const a = push(x0, y0, z0, 0, 0, b, nc, nr, 1, s, id);
    push(x1, y1, z0, nc, 0, b, nc, nr, 1, s, id);
    push(x1, y1, h, nc, nr, b, nc, nr, 1, s, id);
    push(x0, y0, h, 0, nr, b, nc, nr, 1, s, id);
    index.push(a, a + 1, a + 2, a, a + 2, a + 3);
  }
  const R = mesh.roofs;
  for (let i = 0; i < R.length / 11; i++) {
    const o = i * 11;
    const [id, h, ang, cx, cy, ox, oy, nx, ny, b] = R.subarray(o, o + 10);
    const rings = mesh.roofRings[i].map((f) => {
      const pts = [];
      for (let k = 0; k < f.length; k += 2) pts.push(new THREE.Vector2(f[k], f[k + 1]));
      return pts;
    });
    const contour = rings[0], holes = rings.slice(1);
    const tris = THREE.ShapeUtils.triangulateShape(contour, holes);
    const all = contour.concat(...holes);
    const c = Math.cos(ang), sn = Math.sin(ang), cell = mesh.cell;
    const first = pos.length / 3;
    for (const p of all) {
      const dx = p.x - cx, dy = p.y - cy;
      const u = (cx + c * dx + sn * dy - ox) / cell, v = (cy - sn * dx + c * dy - oy) / cell;
      push(p.x, p.y, h, u, v, b, nx, ny, 0, 1.0, id);
    }
    for (const t of tris) index.push(first + t[0], first + t[1], first + t[2]);
  }
  const g = new THREE.BufferGeometry();
  g.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3));
  g.setAttribute('uv', new THREE.Float32BufferAttribute(uv, 2));
  g.setAttribute('aBase', new THREE.Float32BufferAttribute(base, 1));
  g.setAttribute('aCols', new THREE.Float32BufferAttribute(cols, 1));
  g.setAttribute('aRows', new THREE.Float32BufferAttribute(rows, 1));
  g.setAttribute('aKind', new THREE.Float32BufferAttribute(kind, 1));
  g.setAttribute('aShade', new THREE.Float32BufferAttribute(shade, 1));
  g.setAttribute('aBid', new THREE.Float32BufferAttribute(bid, 1));
  g.setIndex(new THREE.BufferAttribute(new Uint32Array(index), 1));
  g.computeBoundingSphere();
  return g;
}

export function valueTexture(nSlots) {
  const rows = Math.max(1, Math.ceil(nSlots / TEX_W));
  const data = new Float32Array(TEX_W * rows).fill(-1e30);
  const tex = new THREE.DataTexture(data, TEX_W, rows, THREE.RedFormat, THREE.FloatType);
  tex.minFilter = tex.magFilter = THREE.NearestFilter;
  tex.needsUpdate = true;
  return tex;
}

export function setValues(tex, values) {
  tex.image.data.set(values.subarray(0, Math.min(values.length, tex.image.data.length)));
  tex.needsUpdate = true;
}

const rampCache = {};
export function rampTexture(name) {
  if (rampCache[name]) return rampCache[name];
  const stops = RAMPS[name].map((h) => [parseInt(h.slice(1, 3), 16), parseInt(h.slice(3, 5), 16), parseInt(h.slice(5, 7), 16)]);
  const data = new Uint8Array(256 * 4);
  for (let i = 0; i < 256; i++) {
    const t = (i / 255) * (stops.length - 1), k = Math.min(stops.length - 2, Math.floor(t)), f = t - k;
    for (let c = 0; c < 3; c++) data[i * 4 + c] = Math.round(stops[k][c] + (stops[k + 1][c] - stops[k][c]) * f);
    data[i * 4 + 3] = 255;
  }
  const tex = new THREE.DataTexture(data, 256, 1, THREE.RGBAFormat, THREE.UnsignedByteType);
  tex.minFilter = tex.magFilter = THREE.LinearFilter;
  tex.needsUpdate = true;
  return (rampCache[name] = tex);
}

const VERT = /* glsl */`
uniform mat4 uMVP;
uniform float uUseMVP;
attribute float aBase; attribute float aCols; attribute float aRows;
attribute float aKind; attribute float aShade; attribute float aBid;
varying vec2 vUv;
flat varying float vBase; flat varying float vCols; flat varying float vRows;
flat varying float vKind; flat varying float vBid;
varying float vShade;
void main() {
  vUv = uv; vBase = aBase; vCols = aCols; vRows = aRows; vKind = aKind; vShade = aShade; vBid = aBid;
  gl_Position = uUseMVP > 0.5 ? uMVP * vec4(position, 1.0)
                              : projectionMatrix * modelViewMatrix * vec4(position, 1.0);
}`;

const FRAG = /* glsl */`
uniform sampler2D uValues;
uniform sampler2D uRamp;
uniform vec2 uDomRoof;
uniform vec2 uDomWall;
uniform float uSelBid;
uniform float uSelSlot;
uniform float uGrid;
uniform float uMode;          // 0 = continuous ramp, 1 = sun-at-a-moment categories
uniform vec3 uNoData;
varying vec2 vUv;
flat varying float vBase; flat varying float vCols; flat varying float vRows;
flat varying float vKind; flat varying float vBid;
varying float vShade;
void main() {
  float col = clamp(floor(vUv.x), 0.0, vCols - 1.0);
  float row = clamp(floor(vUv.y), 0.0, vRows - 1.0);
  float slot = vBase + row * vCols + col;
  int idx = int(slot + 0.5);
  float v = texelFetch(uValues, ivec2(idx % ${TEX_W}, idx / ${TEX_W}), 0).r;
  vec3 c;
  if (v < -1e29) {
    c = uNoData;
  } else if (uMode > 0.5) {
    if (v < 0.5) c = vec3(0.11, 0.13, 0.21);          // night
    else if (v < 1.5) c = vec3(0.16, 0.19, 0.29);     // facing away
    else if (v < 2.5) c = vec3(0.20, 0.25, 0.36);     // shaded by a building
    else c = mix(vec3(0.91, 0.64, 0.23), vec3(1.0, 0.95, 0.70), clamp(v - 3.0, 0.0, 1.0));
  } else {
    vec2 d = vKind < 0.5 ? uDomRoof : uDomWall;
    float t = clamp((v - d.x) / max(d.y - d.x, 1e-6), 0.0, 1.0);
    c = texture2D(uRamp, vec2(0.002 + t * 0.996, 0.5)).rgb;
  }
  c *= vShade;
  vec2 f = fract(vUv), w = fwidth(vUv);
  vec2 g = smoothstep(vec2(0.0), w * 1.2, f) * smoothstep(vec2(0.0), w * 1.2, 1.0 - f);
  float line = 1.0 - min(g.x, g.y);
  float fade = 1.0 - smoothstep(0.10, 0.30, max(w.x, w.y));   // hide the grid when cells are tiny on screen
  c = mix(c, c * 0.5, line * fade * uGrid);
  if (abs(vBid - uSelBid) < 0.5) c = mix(c, vec3(1.0, 0.25, 0.64), 0.28);
  if (abs(slot - uSelSlot) < 0.5) c = mix(c, vec3(1.0, 0.25, 0.64), 0.75);
  gl_FragColor = vec4(c, 1.0);
}`;

export function surfaceMaterial({ useMVP = true } = {}) {
  return new THREE.ShaderMaterial({
    vertexShader: VERT, fragmentShader: FRAG,
    uniforms: {
      uMVP: { value: new THREE.Matrix4() }, uUseMVP: { value: useMVP ? 1 : 0 },
      uValues: { value: null }, uRamp: { value: rampTexture('quality') },
      uDomRoof: { value: new THREE.Vector2(0, 1) }, uDomWall: { value: new THREE.Vector2(0, 1) },
      uSelBid: { value: -1 }, uSelSlot: { value: -1 }, uGrid: { value: 1 }, uMode: { value: 0 },
      uNoData: { value: new THREE.Color(0.6, 0.6, 0.62) },
    },
    side: THREE.DoubleSide,
  });
}

// Which cell slot a ray hit landed in.
export function hitSlot(hit) {
  const g = hit.object.geometry, a = hit.face.a;
  const cols = g.attributes.aCols.getX(a), rows = g.attributes.aRows.getX(a);
  const col = Math.min(cols - 1, Math.max(0, Math.floor(hit.uv.x)));
  const row = Math.min(rows - 1, Math.max(0, Math.floor(hit.uv.y)));
  return { slot: g.attributes.aBase.getX(a) + row * cols + col, bid: g.attributes.aBid.getX(a) };
}
