// Sidebar: region + metric + detail pickers, legend, region summary, climate chart.
import { METRICS, fmt, legendHTML } from '../models/metrics.js';

const $ = (id) => document.getElementById(id);
const MONTHS = 'JFMAMJJASOND';

function cssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

export class SidebarView {
  constructor({ onRegion, onMetric, onDetail }) {
    $('regionSelect').addEventListener('change', (e) => onRegion(e.target.value));
    $('metricSelect').innerHTML = Object.entries(METRICS)
      .map(([k, d]) => `<option value="${k}">${d.label}</option>`).join('');
    $('metricSelect').addEventListener('change', (e) => onMetric(e.target.value));
    document.querySelectorAll('#detailSeg button').forEach((b) => b.addEventListener('click', () => {
      document.querySelectorAll('#detailSeg button').forEach((x) => x.classList.toggle('on', x === b));
      onDetail(b.dataset.detail);
      this.detail = b.dataset.detail;
      this.setMetric(this.metric, this.meta);
    }));
    this.detail = 'cells';
  }

  setRegions(regions, selected) {
    $('regionSelect').innerHTML = regions.map((r) =>
      `<option value="${r.id}" ${r.id === selected ? 'selected' : ''}>${r.name}</option>`).join('');
  }

  setMetric(metric, meta) {
    this.metric = metric;
    this.meta = meta;
    $('metricSelect').value = metric;
    $('metricHelp').textContent = METRICS[metric].help;
    if (!meta) return;
    const d = meta.domains;
    $('legend').innerHTML = this.detail === 'cells'
      ? legendHTML('Facade cells', metric, d.facade[metric]) + legendHTML('Roof cells', metric, d.roof[metric])
      : legendHTML('Buildings (facade average)', metric, d.building[metric]);
  }

  setProgress(done, total) {
    $('loadStatus').innerHTML = done >= total ? ''
      : `Loading tiles ${done}/${total}<div class="bar"><i style="width:${(100 * done) / total}%"></i></div>`;
  }

  setRegionSummary(meta) {
    const c = meta.counts;
    const hs = meta.height_sources;
    const pctDefault = Math.round((100 * hs.default) / Math.max(1, c.buildings));
    $('regionSummary').innerHTML = `
      <span>Buildings analysed</span><span>${c.buildings.toLocaleString('en-GB')}</span>
      <span>Surface cells</span><span>${c.cells.toLocaleString('en-GB')}</span>
      <span>Tiles (z${meta.tile_z})</span><span>${c.tiles}</span>
      <span>Median facade sun</span><span>${fmt(meta.summary.sun_h, 'h')}</span>
      <span>Median view score</span><span>${fmt(meta.summary.view_score, 'frac')}</span>
      <span>Height from default</span><span title="OSM had no height/levels tag">${pctDefault}%</span>`;
    this.drawClimate(meta.climate);
  }

  drawClimate(clim) {
    $('climateYears').textContent = `${clim.years[0]}–${clim.years[1]}`;
    const cv = $('climateChart');
    const dpr = window.devicePixelRatio || 1;
    const W = cv.clientWidth, H = 150;
    cv.width = W * dpr; cv.height = H * dpr;
    const g = cv.getContext('2d');
    g.scale(dpr, dpr);
    const m = clim.monthly;
    const sun = m.sunshine_fraction_eff || m.sunshine_fraction;
    const pad = { l: 30, r: 6, t: 8, b: 18 };
    const cw = (W - pad.l - pad.r) / 12, ch = H - pad.t - pad.b;
    const y = (v) => pad.t + ch * (1 - v);
    g.font = '10px system-ui'; g.fillStyle = cssVar('--muted'); g.strokeStyle = cssVar('--line');
    for (const v of [0, 0.5, 1]) {
      g.beginPath(); g.moveTo(pad.l, y(v)); g.lineTo(W - pad.r, y(v)); g.stroke();
      g.fillText(`${v * 100}%`, 0, y(v) + 3);
    }
    g.fillStyle = cssVar('--sun');
    sun.forEach((v, i) => g.fillRect(pad.l + i * cw + 3, y(v), cw - 6, ch * v));
    const line = (arr, color) => {
      g.strokeStyle = color; g.lineWidth = 2; g.beginPath();
      arr.forEach((v, i) => { const X = pad.l + (i + 0.5) * cw; i ? g.lineTo(X, y(v / 100)) : g.moveTo(X, y(v / 100)); });
      g.stroke(); g.lineWidth = 1;
    };
    line(m.rain_day_pct, cssVar('--rain'));
    line(m.snow_day_pct, cssVar('--snow'));
    g.fillStyle = cssVar('--muted');
    [...MONTHS].forEach((c, i) => g.fillText(c, pad.l + (i + 0.5) * cw - 3, H - 4));
    const a = clim.annual;
    $('climateStats').innerHTML = `
      <span><b style="color:var(--sun)">■</b> Sunshine (share of daylight)</span><span>${Math.round(a.sunshine_hours_eff ?? a.sunshine_hours)} h/yr</span>
      <span><b style="color:var(--rain)">—</b> Rain days (≥1 mm)</span><span>${Math.round(a.rain_day_pct)}%</span>
      <span><b style="color:var(--snow)">—</b> Snow days (≥1 cm)</span><span>${Math.round(a.snow_day_pct)}%</span>
      <span>Global irradiation</span><span>${Math.round(a.ghi_kwh_m2)} kWh/m²</span>
      <span>Time zone</span><span>${clim.timezone}</span>`;
  }
}
