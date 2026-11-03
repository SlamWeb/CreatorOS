import type { studioApi } from "../api/client";
import type { RunDetail } from "../api/types";

type Action = "execute" | "cancel";

export type RunControlIntent = {
  action: Action;
  runId: string;
  expectedVersion: number;
  initialOnly: boolean;
  wasProducing: boolean;
};

// Freeze both the target and the user's intended action at click time.
export function runControlIntent(run: RunDetail, action: Action): RunControlIntent {
  return {
    action,
    runId: run.id,
    expectedVersion: run.version,
    initialOnly: action === "execute" && run.status === "queued" && run.production_queue?.status !== "needs_attention",
    wasProducing: run.status === "producing",
  };
}

export function executeRunControlIntent(intent: RunControlIntent, api: Pick<typeof studioApi, "executeRun" | "cancelRun">) {
  return intent.action === "execute"
    ? api.executeRun(intent.runId, intent.expectedVersion, intent.initialOnly)
    : api.cancelRun(intent.runId, { expected_version: intent.expectedVersion });
}
