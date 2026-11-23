// Thin client for the controllers. All network access from the UI goes through here.
async function j(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${r.status}: ${await r.text()}`);
  return r.json();
}

async function bin(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${r.status}: ${url}`);
  return r.arrayBuffer();
}

export const api = {
  regions: () => j('/api/regions'),
  meta: (rid) => j(`/api/regions/${rid}`),
  buildings: (rid) => j(`/api/regions/${rid}/buildings.geojson`),
  sunpath: (rid, date) => j(`/api/regions/${rid}/sunpath?date=${date}&step=5`),
  mesh: (key) => bin(`/api/tiles/${key}/mesh.bin`),
  values: (key, metric) => bin(`/api/tiles/${key}/values/${metric}.bin`),
  building: (key, bid, rid) => j(`/api/tiles/${key}/buildings/${bid}?region=${rid}`),
  slot: (key, slot) => j(`/api/tiles/${key}/slots/${slot}`),
  rays: (key, cell) => j(`/api/tiles/${key}/cells/${cell}/rays`),
  scene: (key, bid) => j(`/api/tiles/${key}/scene/${bid}`),
};
