import assert from "node:assert/strict";
import test from "node:test";
import { MutationObserver, QueryClient, onlineManager } from "@tanstack/query-core";
import { executeRunControlIntent, runControlIntent } from "../src/components/runControlIntent.ts";

test("an offline start retains the clicked run, version and initial-only intent after props change", async (t) => {
  const client = new QueryClient();
  client.mount();
  t.after(() => { onlineManager.setOnline(true); client.unmount(); });
  onlineManager.setOnline(false);
  const calls = [];
  const api = {
    executeRun: async (...args) => { calls.push(["execute", ...args]); },
    cancelRun: async (...args) => { calls.push(["cancel", ...args]); },
  };
  const before = { id: "run-a", version: 1, status: "queued", production_queue: null };
  const after = { id: "run-b", version: 2, status: "queued", production_queue: { status: "needs_attention" } };
  const options = (run) => ({
    // This is the mutation function used by RunControls. React may replace the
    // pending observer's options when the view receives new run props.
    mutationFn: (intent) => executeRunControlIntent(intent, api),
    onSuccess: () => run.id,
  });
  const observer = new MutationObserver(client, options(before));
  const pending = observer.mutate(runControlIntent(before, "execute"));
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(observer.getCurrentResult().isPaused, true);
  assert.deepEqual(calls, []);
  observer.setOptions(options(after));
  onlineManager.setOnline(true);
  await pending;
  assert.deepEqual(calls, [["execute", "run-a", 1, true]]);

  // An explicitly clicked resume remains a resume even if the view changes back.
  onlineManager.setOnline(false);
  const resume = observer.mutate(runControlIntent(after, "execute"));
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(observer.getCurrentResult().isPaused, true);
  observer.setOptions(options(before));
  onlineManager.setOnline(true);
  await resume;
  assert.deepEqual(calls[1], ["execute", "run-b", 2, false]);
});

test("cancel also retains its original target and version", async () => {
  const calls = [];
  const api = {
    executeRun: async (...args) => calls.push(["execute", ...args]),
    cancelRun: async (...args) => calls.push(["cancel", ...args]),
  };
  const intent = runControlIntent({ id: "run-a", version: 4, status: "producing" }, "cancel");
  await executeRunControlIntent(intent, api);
  assert.equal(intent.wasProducing, true);
  assert.deepEqual(calls, [["cancel", "run-a", { expected_version: 4 }]]);
});
