import type { QueryClient } from "@tanstack/react-query";

type OperationKind = "assign" | "remove";
type OperationState = { status: string; error: unknown };
export type SeriesOperationOutcome = { kind: "assign"; seriesId: string; owner: string | null }
  | { kind: "remove"; seriesId: string };

export function operationKey(seriesId: string, kind?: OperationKind) {
  return kind ? ["series-operation", seriesId, kind] : ["series-operation", seriesId];
}

// Mutation cache outlives the column component, so navigating away cannot
// silently discard its in-flight state or the eventual failure.
export function operationView(states: readonly OperationState[]) {
  const latest = states.at(-1);
  return { pending: states.some(state => state.status === "pending"),
    error: latest?.status === "error" ? latest.error : null };
}

export function startSeriesOperation(client: QueryClient, seriesId: string, start: () => void) {
  const cache = client.getMutationCache();
  const key = operationKey(seriesId);
  if (client.isMutating({ mutationKey: key })) return false;
  // Keep only the current operation for each column during this page session.
  for (const mutation of cache.findAll({ mutationKey: key })) cache.remove(mutation);
  start();
  return true;
}

// Notify the workspace currently on screen, not the component that started a
// request and may already have unmounted. Workspace navigation checks scope.
export function subscribeSeriesOperations(client: QueryClient, onSuccess: (outcome: SeriesOperationOutcome) => void) {
  return client.getMutationCache().subscribe(event => {
    if (event.type !== "updated" || event.action.type !== "success") return;
    const key = event.mutation.options.mutationKey;
    if (!key || key.length !== 3 || key[0] !== "series-operation" || typeof key[1] !== "string") return;
    const data: unknown = event.mutation.state.data;
    if (!data || typeof data !== "object") return;
    if (key[2] === "assign" && "series" in data && data.series && typeof data.series === "object"
        && "id" in data.series && data.series.id === key[1] && "creator_id" in data.series
        && (typeof data.series.creator_id === "string" || data.series.creator_id === null)) {
      onSuccess({ kind: "assign", seriesId: key[1], owner: data.series.creator_id });
    } else if (key[2] === "remove" && "id" in data && data.id === key[1]) {
      onSuccess({ kind: "remove", seriesId: key[1] });
    }
  });
}
