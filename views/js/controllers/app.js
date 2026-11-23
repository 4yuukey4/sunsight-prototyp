// App controller: wires models (api, state, tile store) to the views.
import { api } from '../models/api.js';
import { on, set, state } from '../models/state.js';
import { TileStore } from '../models/tileStore.js';
import { MapView } from '../views/mapView.js';
import { SidebarView } from '../views/sidebarView.js';
import { BuildingPanel } from '../views/buildingPanel.js';
import { InspectorView } from '../views/inspectorView.js';

const store = new TileStore();

const sidebar = new SidebarView({
  onRegion: (rid) => loadRegion(rid),
  onMetric: (m) => set({ metric: m }),
  onDetail: (d) => map.setDetail(d),
});

const map = new MapView('map', store, {
  onCellClick: (hit) => selectBuilding(hit.key, hit.bid, hit.slot),
  onBuildingClick: (key, bid) => selectBuilding(key, bid, -1),
  onProgress: (done, total) => sidebar.setProgress(done, total),
});

const inspector = new InspectorView({ api, store });

const panel = new BuildingPanel({
  onOpen3D: (p, title) => inspector.open({ rid: state.regionId, meta: state.meta, key: p.tile, bid: p.bid, title, metric: state.metric }),
  onValidate: (p, title) => inspector.open({ rid: state.regionId, meta: state.meta, key: p.tile, bid: p.bid, title,
                                             metric: 'view_score', validateCell: p.view_cell }),
  onClose: () => map.select(null),
});

on('metric', (m) => { map.applyMetric(m); sidebar.setMetric(m, state.meta); });

async function selectBuilding(key, bid, slot) {
  map.select(key, bid, slot);
  const [p, cell] = await Promise.all([
    api.building(key, bid, state.regionId),
    slot >= 0 ? api.slot(key, slot).catch(() => null) : Promise.resolve(null),
  ]);
  panel.show(p, cell);
}

async function loadRegion(rid) {
  panel.hide();
  const [meta, buildings] = await Promise.all([api.meta(rid), api.buildings(rid)]);
  set({ regionId: rid, meta });
  history.replaceState(null, '', `#${rid}`);
  sidebar.setRegionSummary(meta);
  sidebar.setMetric(state.metric, meta);
  await map.setRegion(meta, buildings);
}

async function boot() {
  const regions = await api.regions();
  if (!regions.length) {
    document.getElementById('loadStatus').textContent = 'No regions yet: run tools/precompute.py <preset>.';
    return;
  }
  const wanted = location.hash.slice(1);
  const rid = regions.some((r) => r.id === wanted) ? wanted : regions[0].id;
  sidebar.setRegions(regions, rid);
  await loadRegion(rid);
}

window.__sunsight = { map, inspector, store };  // debugging / automated UI checks
boot();
