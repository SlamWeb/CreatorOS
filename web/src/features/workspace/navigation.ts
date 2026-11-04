// URL is the durable selection for a workspace. These transitions deliberately
// leave unrelated parameters intact and never mutate the router's input.
type Account = { id: string };
type Series = { id: string; creator_id: string | null };

export function resolveWorkspaceSelection<A extends Account, S extends Series>(
  params: URLSearchParams, accounts: A[], allSeries: S[],
) {
  const requestedSeries = allSeries.find(item => item.id === params.get("series")) ?? null;
  const scopedCreatorId = params.get("creator") ?? requestedSeries?.creator_id ?? (params.has("series") ? null : accounts[0]?.id ?? null);
  const account = accounts.find(item => item.id === scopedCreatorId);
  const series = requestedSeries && (!params.get("creator") || requestedSeries.creator_id === scopedCreatorId) ? requestedSeries : null;
  return { scopedCreatorId, account, series, accountSeries: allSeries.filter(item => item.creator_id === scopedCreatorId) };
}

export function selectAccountParams(previous: URLSearchParams, id: string, resetView = false) {
  const next = new URLSearchParams(previous);
  next.set("creator", id);
  if (resetView || (previous.get("creator") !== id && next.get("view") === "bgm")) next.delete("view");
  for (const key of ["series", "topics", "offset", "select", "research", "operation", "topic", "chat", "collection", "collection_topic"]) next.delete(key);
  return next;
}

export function selectSeriesParams(previous: URLSearchParams, id: string, creatorId: string | null | undefined, keepFilter = false) {
  const next = new URLSearchParams(previous);
  next.set("series", id);
  if (creatorId) next.set("creator", creatorId); else next.delete("creator");
  for (const key of ["offset", "select", "research", "operation", "topic", "view", "collection", "collection_topic"]) next.delete(key);
  if (!keepFilter) next.delete("topics");
  return next;
}

export function removeDeletedAccountParams(previous: URLSearchParams, id: string) {
  if (previous.get("creator") !== id) return previous;
  const next = new URLSearchParams(previous);
  for (const key of ["creator", "series", "view", "topic", "collection", "collection_topic"]) next.delete(key);
  return next;
}

export function removeDeletedSeriesParams(previous: URLSearchParams, id: string) {
  if (previous.get("series") !== id) return previous;
  const next = new URLSearchParams(previous);
  next.delete("series");
  for (const key of ["topics", "offset", "select", "research", "operation", "topic"]) next.delete(key);
  return next;
}
