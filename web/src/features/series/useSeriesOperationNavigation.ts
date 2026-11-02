import { useEffect, useRef } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { subscribeSeriesOperations } from "./operationState";

// Handle completions in the current workspace, even when navigation remounted
// the column while its request was pending. Callers still guard the URL scope.
export function useSeriesOperationNavigation(onSelected: (id: string, owner: string | null) => void,
                                             onDeleted: (id: string) => void) {
  const client = useQueryClient();
  const handlers = useRef({ onSelected, onDeleted });
  handlers.current = { onSelected, onDeleted };
  useEffect(() => subscribeSeriesOperations(client, outcome => {
    if (outcome.kind === "assign") handlers.current.onSelected(outcome.seriesId, outcome.owner);
    else handlers.current.onDeleted(outcome.seriesId);
  }), [client]);
}
