import assert from "node:assert/strict";
import test from "node:test";
import { MutationObserver, QueryClient } from "@tanstack/query-core";
import { operationKey, operationView, startSeriesOperation, subscribeSeriesOperations } from "../src/features/series/operationState.ts";

function states(client, id, kind) {
  return client.getMutationCache().findAll({ mutationKey: operationKey(id, kind), exact: true }).map(mutation => mutation.state);
}

test("a pending operation survives unmount, blocks duplicates, and exposes a late failure", async () => {
  const client = new QueryClient();
  let reject;
  const observer = new MutationObserver(client, { mutationKey: operationKey("a", "assign"), gcTime: Infinity,
    mutationFn: () => new Promise((_resolve, rejectRequest) => { reject = rejectRequest; }) });
  const unsubscribe = observer.subscribe(() => {});
  let request;
  assert.equal(startSeriesOperation(client, "a", () => { request = observer.mutate().catch(() => {}); }), true);
  await Promise.resolve();
  unsubscribe(); // The user left the column; the request is still in flight.
  assert.equal(operationView(states(client, "a", "assign")).pending, true);
  assert.equal(startSeriesOperation(client, "a", () => assert.fail("duplicate started")), false);
  assert.equal(operationView(states(client, "b", "assign")).pending, false);
  reject(new Error("controlled 409"));
  await request;
  const returned = operationView(states(client, "a", "assign"));
  assert.equal(returned.pending, false);
  assert.equal(returned.error?.message, "controlled 409");
  assert.equal(operationView(states(client, "b", "assign")).error, null);
});

test("assignment and removal serialize per column; a new request clears an old error", async () => {
  const client = new QueryClient();
  const failed = new MutationObserver(client, { mutationKey: operationKey("a", "assign"), gcTime: Infinity,
    mutationFn: async () => { throw new Error("first failed"); } });
  let request;
  assert.equal(startSeriesOperation(client, "a", () => { request = failed.mutate().catch(() => {}); }), true);
  await request;
  assert.equal(operationView(states(client, "a", "assign")).error?.message, "first failed");
  let resolve;
  const removal = new MutationObserver(client, { mutationKey: operationKey("a", "remove"), gcTime: Infinity,
    mutationFn: () => new Promise(done => { resolve = done; }) });
  assert.equal(startSeriesOperation(client, "a", () => { request = removal.mutate(); }), true);
  await Promise.resolve();
  assert.equal(operationView(states(client, "a", "assign")).error, null);
  assert.equal(operationView(states(client, "a", "remove")).pending, true);
  assert.equal(startSeriesOperation(client, "a", () => assert.fail("assignment raced removal")), false);
  resolve({ id: "a" });
  await request;
  assert.equal(operationView(states(client, "a", "remove")).pending, false);
});

test("the current workspace receives a completion after the original view unmounts", async () => {
  const client = new QueryClient();
  let resolve;
  const original = new MutationObserver(client, { mutationKey: operationKey("a", "assign"), gcTime: Infinity,
    mutationFn: () => new Promise(done => { resolve = done; }) });
  const oldEvents = [], currentEvents = [];
  const unsubscribeOld = subscribeSeriesOperations(client, outcome => oldEvents.push(outcome));
  let request;
  startSeriesOperation(client, "a", () => { request = original.mutate(); });
  await Promise.resolve();
  unsubscribeOld();
  const unsubscribeCurrent = subscribeSeriesOperations(client, outcome => currentEvents.push(outcome));
  resolve({ series: { id: "a", creator_id: "owner" } });
  await request;
  assert.deepEqual(oldEvents, []);
  assert.deepEqual(currentEvents, [{ kind: "assign", seriesId: "a", owner: "owner" }]);
  unsubscribeCurrent();
});
