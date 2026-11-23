// Client-side cache of tile meshes and per-slot value arrays, shared by the
// map layer and the 3D inspector (each tile is downloaded once).
import { api } from './api.js';
import { EMPTY, buildGeometry, parseMesh } from '../lib/surfaces.js';

function cdfRank(q, v) {
  // q: 101 quantiles -> fraction of cells below v (linear between quantiles)
  if (v <= q[0]) return 0;
  if (v >= q[q.length - 1]) return 1;
  let lo = 0, hi = q.length - 1;
  while (hi - lo > 1) { const m = (lo + hi) >> 1; if (q[m] <= v) lo = m; else hi = m; }
  const f = q[hi] > q[lo] ? (v - q[lo]) / (q[hi] - q[lo]) : 0;
  return (lo + f) / (q.length - 1);
}

export class TileStore {
  constructor() {
    this.meshes = new Map();
    this.vals = new Map();
  }

  mesh(key) {
    if (!this.meshes.has(key)) {
      this.meshes.set(key, api.mesh(key).then((buf) => {
        const m = parseMesh(buf);
        m.key = key;
        m.geometry = buildGeometry(m);
        return m;
      }));
    }
    return this.meshes.get(key);
  }

  // Float32 value per slot. "overall" is derived from region-wide percentiles,
  // so the same tile can belong to several regions.
  values(key, metric, meta) {
    const ck = metric === 'overall' ? `${key}|overall|${meta.id}` : `${key}|${metric}`;
    if (!this.vals.has(ck)) {
      const p = metric === 'overall'
        ? Promise.all([this.values(key, 'sun_h'), this.values(key, 'view_score'), this.mesh(key)])
          .then(([s, v, m]) => {
            const out = new Float32Array(s.length);
            const cdf = [meta.cdf.roof, meta.cdf.facade];
            for (let i = 0; i < s.length; i++) {
              if (s[i] < EMPTY) { out[i] = s[i]; continue; }
              const c = cdf[m.slotKind[i]];
              out[i] = 0.5 * cdfRank(c.sun_h, s[i]) + 0.5 * cdfRank(c.view_score, v[i]);
            }
            return out;
          })
        : api.values(key, metric).then((b) => new Float32Array(b));
      this.vals.set(ck, p);
    }
    return this.vals.get(ck);
  }
}
