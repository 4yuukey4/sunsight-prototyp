// Minimal observable app state shared by views and controllers.
const listeners = {};

export const state = {
  regionId: null,
  meta: null,
  metric: 'overall',
  selectedBid: null,
  showRoofCells: true,
};

export function set(patch) {
  Object.assign(state, patch);
  for (const k of Object.keys(patch)) (listeners[k] || []).forEach((fn) => fn(state[k], state));
}

export function on(key, fn) {
  (listeners[key] ||= []).push(fn);
}
