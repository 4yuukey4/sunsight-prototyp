// MapLibre view. Two levels of detail over the same region:
//   "cells"     every roof/facade cell as a pixel matrix (custom Three.js layer)
//   "buildings" one colour per building (fill-extrusion), facade averages
import { mapColorExpr } from '../models/metrics.js';
import { CellLayer } from './cellLayer.js';

const DARK = matchMedia('(prefers-color-scheme: dark)').matches;
const STYLE = DARK
  ? 'https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json'
  : 'https://basemaps.cartocdn.com/gl/positron-gl-style/style.json';
const SEL = '#ff3fa4';
const EMPTY_FC = { type: 'FeatureCollection', features: [] };

export class MapView {
  constructor(container, store, { onCellClick, onBuildingClick, onProgress }) {
    this.detail = 'cells';
    this.metric = 'overall';
    this.selectedId = '';
    this.map = new maplibregl.Map({
      container, style: STYLE, center: [14.808, 56.878], zoom: 13.5, pitch: 50, bearing: -20,
      antialias: true, maxPitch: 78,
    });
    this.map.addControl(new maplibregl.NavigationControl({ visualizePitch: true }), 'top-right');
    this.map.addControl(new maplibregl.ScaleControl(), 'bottom-right');
    this.cells = new CellLayer(store, { onProgress });
    this.ready = new Promise((res) => this.map.on('load', () => {
      this.map.addSource('bld', { type: 'geojson', data: EMPTY_FC });
      this.map.addLayer({
        id: 'bld', type: 'fill-extrusion', source: 'bld', layout: { visibility: 'none' },
        paint: { 'fill-extrusion-color': '#999', 'fill-extrusion-height': ['get', 'height'], 'fill-extrusion-opacity': 0.92 },
      });
      this.map.addLayer(this.cells);
      res();
    }));
    this.map.on('click', (e) => {
      if (this.detail === 'cells') {
        const hit = this.cells.pick(e.point);
        if (hit) onCellClick(hit);
      } else {
        const f = this.map.queryRenderedFeatures(e.point, { layers: ['bld'] })[0];
        if (f) onBuildingClick(f.properties.tile, f.properties.bid);
      }
    });
    this.map.on('mousemove', 'bld', () => { this.map.getCanvas().style.cursor = 'pointer'; });
    this.map.on('mouseleave', 'bld', () => { this.map.getCanvas().style.cursor = ''; });
  }

  async setRegion(meta, buildings) {
    await this.ready;
    this.meta = meta;
    const [w, s, e, n] = meta.bbox;
    this.map.fitBounds([[w, s], [e, n]], { padding: 30, pitch: 50, bearing: -20, duration: 0 });
    this.map.getSource('bld').setData(buildings);
    this._paintBuildings();
    await this.cells.loadRegion(meta, this.metric);
  }

  setDetail(detail) {
    this.detail = detail;
    this.ready.then(() => {
      this.map.setLayoutProperty('bld', 'visibility', detail === 'buildings' ? 'visible' : 'none');
      this.cells.setVisible(detail === 'cells');
    });
  }

  async applyMetric(metric) {
    this.metric = metric;
    await this.ready;
    this._paintBuildings();
    if (this.meta) await this.cells.setMetric(metric);
  }

  _paintBuildings() {
    if (!this.meta) return;
    const expr = mapColorExpr(this.metric, this.meta.domains.building[this.metric]);
    this.map.setPaintProperty('bld', 'fill-extrusion-color', ['case', ['==', ['get', 'id'], this.selectedId], SEL, expr]);
  }

  select(key, bid, slot) {
    this.selectedId = key === null ? '' : `${key}:${bid}`;
    this._paintBuildings();
    this.cells.select(key, bid ?? -1, slot ?? -1);
  }
}
