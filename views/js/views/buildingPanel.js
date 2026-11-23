// Floating panel: the clicked cell (if any) and its building's metrics and ranks.
import { colorAt, fmt, metricDef } from '../models/metrics.js';

const el = () => document.getElementById('buildingPanel');
const MONTHS = 'JFMAMJJASOND';
const COMPASS = ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW'];

function row(key, value, pct) {
  const d = metricDef(key);
  const bar = pct === null || pct === undefined ? '' : (() => {
    const [r, g, b] = colorAt(d.ramp, pct);
    return `<div class="pbar"><i style="width:${Math.max(3, pct * 100)}%;background:rgb(${r},${g},${b})"></i></div>
            <div class="pbar-label">better than ${Math.round(pct * 100)}% of buildings in this region</div>`;
  })();
  return `<div class="metric-row"><span>${d.label}</span><span class="val">${fmt(value, d.fmt)}</span>${bar}</div>`;
}

export function cellSummary(c) {
  if (!c) return '';
  const m = c.metrics;
  const where = c.kind === 'roof' ? 'Roof cell' : `Facade cell facing ${COMPASS[Math.round(c.naz / 45) % 8]} (${Math.round(c.naz)}°)`;
  return `<div class="cellbox"><div class="cellbox-title">${where} · ${c.center[2].toFixed(1)} m up</div>
    <div class="kv">
      <span>Sun hours / year</span><span>${fmt(m.sun_h, 'h')}</span>
      <span>Winter sun (Nov–Feb)</span><span>${fmt(m.winter_sun_h, 'h')}</span>
      <span>Sun access vs. unobstructed</span><span>${fmt(m.sun_rel, 'pct')}</span>
      <span>Irradiation</span><span>${fmt(m.irr, 'kwh')}</span>
      <span>View score</span><span>${fmt(m.view_score, 'frac')}</span>
      <span>Openness / green / water</span><span>${fmt(m.view_open, 'frac')} / ${fmt(m.view_green, 'pct')} / ${fmt(m.view_water, 'pct')}</span>
    </div></div>`;
}

export class BuildingPanel {
  constructor({ onOpen3D, onValidate, onClose }) {
    Object.assign(this, { onOpen3D, onValidate, onClose });
  }

  show(p, cell) {
    const title = p.name || p.address || `${p.type === 'yes' ? 'Building' : p.type} ${p.osm_id}`;
    this.title = title;
    const hsrc = { height: 'tagged height', levels: 'from building:levels', default: 'default for type' }[p.height_source];
    el().innerHTML = `
      <button class="ghost close" title="Close">✕</button>
      <h3>${title}</h3>
      <div class="muted">${p.address && p.name ? p.address + ' · ' : ''}${p.type} · ${p.h} m
        <span class="badge ${p.height_source === 'default' ? 'warn' : ''}">${hsrc}</span></div>
      ${cellSummary(cell)}
      <div class="btn-row">
        <button class="cta" data-act="3d">Open 3D view</button>
        <button class="cta alt" data-act="validate" title="Stand at a representative window and see what the view metric sees">Validate view</button>
      </div>
      <div class="section-title">Building (facade average)</div>
      ${row('overall', p.overall, p.overall)}
      ${row('sun_h', p.sun_h, p.pct_sun_h)}
      ${row('winter_sun_h', p.winter_sun_h, p.pct_winter_sun_h)}
      ${row('view_score', p.view_score, p.pct_view_score)}
      ${row('sun_rel', p.sun_rel)}
      ${row('view_open', p.view_open)}
      ${row('view_green', p.view_green)}
      ${row('view_water', p.view_water)}
      ${row('roof_irr', p.roof_irr)}
      <div class="muted small">Facade sun hours by month (weather-adjusted)</div>
      <canvas class="mini-chart"></canvas>
      <div class="muted small">${p.n_facade_cells} facade + ${p.n_roof_cells} roof cells ·
        facade cells range ${p.facade_sun_h_range?.[0] ?? '–'}–${p.facade_sun_h_range?.[1] ?? '–'} sun h/yr</div>`;
    el().classList.remove('hidden');
    el().querySelector('.close').onclick = () => { this.hide(); this.onClose(); };
    el().querySelector('[data-act="3d"]').onclick = () => this.onOpen3D(p, title);
    el().querySelector('[data-act=validate]').onclick = () => this.onValidate(p, title);
    if (p.sun_h_month) this._monthly(el().querySelector('.mini-chart'), p.sun_h_month);
  }

  _monthly(cv, vals) {
    const dpr = window.devicePixelRatio || 1;
    const W = cv.clientWidth, H = 90;
    cv.width = W * dpr; cv.height = H * dpr;
    const g = cv.getContext('2d');
    g.scale(dpr, dpr);
    const max = Math.max(1, ...vals);
    const cw = W / 12;
    const muted = getComputedStyle(document.documentElement).getPropertyValue('--muted');
    g.font = '10px system-ui';
    vals.forEach((v, i) => {
      const h = (H - 26) * (v / max);
      const [r, gg, b] = colorAt('sun', v / max);
      g.fillStyle = `rgb(${r},${gg},${b})`;
      g.fillRect(i * cw + 2, H - 14 - h, cw - 4, h);
      g.fillStyle = muted;
      g.fillText(MONTHS[i], i * cw + cw / 2 - 3, H - 2);
      if (v >= max * 0.999) g.fillText(`${Math.round(v)} h`, i * cw, H - 18 - h);
    });
  }

  hide() {
    el().classList.add('hidden');
  }
}
