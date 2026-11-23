// Metric catalogue + colour ramps, shared by the map, panel and 3D inspector.

export const RAMPS = {
  // dark violet -> magenta -> orange -> pale yellow: "how much sun"
  sun: ['#1b1d4b', '#4a2a7a', '#8f3c8a', '#d1576b', '#f28c3c', '#fcd25a', '#fff3b0'],
  // deep blue -> teal -> green -> lime: "how good the view"
  view: ['#1f1e46', '#25427a', '#1f7192', '#20a08a', '#5cc56c', '#c4e65a'],
  // overall quality: diverging red -> neutral -> green
  quality: ['#b2182b', '#e0705a', '#f5c79f', '#ededdf', '#a9d6a2', '#4fa65c', '#1b7837'],
};

export const METRICS = {
  overall:      { label: 'Overall quality (sun + view)', ramp: 'quality', fmt: 'pct',
                  help: 'Average of the sun-hours and view-score percentiles within this region.' },
  sun_h:        { label: 'Sun hours per year', ramp: 'sun', fmt: 'h',
                  help: 'Hours of direct sun on the facade, weather-adjusted with the local 10-year climate.' },
  winter_sun_h: { label: 'Winter sun hours (Nov–Feb)', ramp: 'sun', fmt: 'h',
                  help: 'Weather-adjusted direct sun in the dark months, when it matters most in Sweden.' },
  sun_h_clear:  { label: 'Clear-sky sun hours', ramp: 'sun', fmt: 'h',
                  help: 'Geometric sun hours if every day were cloudless. Only shading and orientation.' },
  sun_rel:      { label: 'Sun access vs. unobstructed', ramp: 'sun', fmt: 'pct',
                  help: 'Share of the sun this surface would get with no neighbours. 100% = never shaded.' },
  irr:          { label: 'Solar irradiation (kWh/m²·yr)', ramp: 'sun', fmt: 'kwh',
                  help: 'Direct + diffuse energy on the surface, calibrated to measured irradiation.' },
  svf:          { label: 'Sky view factor', ramp: 'view', fmt: 'frac',
                  help: 'How much sky the surface sees (wall max 0.5, roof max 1).' },
  view_score:   { label: 'View score', ramp: 'view', fmt: 'frac',
                  help: '60% openness + 20% green + 20% water visible from the facade.' },
  view_open:    { label: 'View openness', ramp: 'view', fmt: 'frac',
                  help: 'How far you can see before the next building (log scale, up to 500 m).' },
  view_green:   { label: 'Green view', ramp: 'view', fmt: 'pct',
                  help: 'Share of viewing directions that overlook parks, lawns or forest.' },
  view_water:   { label: 'Water view', ramp: 'view', fmt: 'pct',
                  help: 'Share of viewing directions that overlook lakes, rivers or the sea.' },
};

export const BUILDING_ONLY = { roof_sun_h: { label: 'Roof sun hours', ramp: 'sun', fmt: 'h' },
                               roof_irr: { label: 'Roof irradiation (solar potential)', ramp: 'sun', fmt: 'kwh' } };

export function fmt(v, kind) {
  if (v === null || v === undefined || Number.isNaN(v)) return '–';
  switch (kind) {
    case 'h': return `${Math.round(v).toLocaleString('sv-SE')} h`;
    case 'kwh': return `${Math.round(v).toLocaleString('sv-SE')} kWh/m²`;
    case 'pct': return `${Math.round(v * 100)}%`;
    default: return v.toFixed(2);
  }
}

function hexToRgb(h) {
  const n = parseInt(h.slice(1), 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

const rgbCache = {};
export function colorAt(rampName, t) {
  const ramp = (rgbCache[rampName] ||= RAMPS[rampName].map(hexToRgb));
  if (t === null || Number.isNaN(t)) return [150, 150, 150];
  t = Math.min(1, Math.max(0, t)) * (ramp.length - 1);
  const i = Math.min(ramp.length - 2, Math.floor(t));
  const f = t - i;
  return ramp[i].map((c, k) => Math.round(c + (ramp[i + 1][k] - c) * f));
}

export function cssGradient(rampName) {
  return `linear-gradient(90deg, ${RAMPS[rampName].join(', ')})`;
}

export function metricDef(key) {
  return METRICS[key] || BUILDING_ONLY[key];
}

// MapLibre colour expression for a property on a domain [lo, hi].
export function mapColorExpr(key, domain, nullColor = '#c8c8c8') {
  const ramp = RAMPS[metricDef(key).ramp];
  let [lo, hi] = domain || [0, 1];
  if (!(hi > lo)) hi = lo + 1e-6;
  const stops = ramp.flatMap((c, i) => [lo + (hi - lo) * (i / (ramp.length - 1)), c]);
  return ['case', ['==', ['typeof', ['get', key]], 'number'],
    ['interpolate', ['linear'], ['get', key], ...stops], nullColor];
}

export function legendHTML(title, key, domain) {
  const d = metricDef(key);
  const [lo, hi] = domain || [0, 1];
  return `<div class="legend-title">${title}</div>
    <div class="ramp" style="background:${cssGradient(d.ramp)}"></div>
    <div class="ramp-labels"><span>${fmt(lo, d.fmt)}</span><span>${fmt(hi, d.fmt)}</span></div>`;
}
