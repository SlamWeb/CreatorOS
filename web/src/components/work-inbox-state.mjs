export function initialInboxState(data) {
  const baseline = {};
  for (const item of data.items) baseline[item.key] = item.token;
  for (const object of data.objects) baseline[`${object.kind}:${object.id}`] = object.token;
  return { since: data.as_of, baseline, seen: {} };
}

export function isInboxUnseen(state, object) {
  if (!state) return false;
  const key = object.key ?? `${object.kind}:${object.id}`;
  if (state.seen[key] === object.token) return false;
  const original = state.baseline[key];
  if (original !== undefined) return original !== object.token;
  const created = Date.parse(object.created_at);
  return Number.isFinite(created) && created > Date.parse(state.since);
}

export function markInboxSeen(state, objects) {
  if (!state) return state;
  let next = state;
  for (const object of objects) {
    const key = object.key ?? `${object.kind}:${object.id}`;
    if (state.seen[key] === object.token) continue;
    if (next === state) next = { ...state, seen: { ...state.seen } };
    next.seen[key] = object.token;
  }
  return next;
}
