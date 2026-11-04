import assert from "node:assert/strict";
import test from "node:test";
import { resolveWorkspaceSelection, selectAccountParams, selectSeriesParams,
  removeDeletedAccountParams, removeDeletedSeriesParams } from "../src/features/workspace/navigation.ts";

const accounts = [{ id: "a", display_name: "甲" }, { id: "b", display_name: "乙" }];
const series = [{ id: "a1", creator_id: "a" }, { id: "b1", creator_id: "b" }, { id: "free", creator_id: null }];

test("deep links resolve the owner and never display a column under another account", () => {
  const inferred = resolveWorkspaceSelection(new URLSearchParams("series=b1"), accounts, series);
  assert.equal(inferred.account?.id, "b");
  assert.equal(inferred.series?.id, "b1");
  const mismatch = resolveWorkspaceSelection(new URLSearchParams("creator=a&series=b1"), accounts, series);
  assert.equal(mismatch.account?.id, "a");
  assert.equal(mismatch.series, null);
  assert.deepEqual(mismatch.accountSeries.map(item => item.id), ["a1"]);
  assert.equal(resolveWorkspaceSelection(new URLSearchParams("series=missing"), accounts, series).account, undefined);
  assert.equal(resolveWorkspaceSelection(new URLSearchParams("series=free"), accounts, series).series?.id, "free");
});

test("account and column transitions clear stale object context but preserve unrelated parameters", () => {
  const original = new URLSearchParams("creator=a&series=a1&topic=t&offset=20&chat=c&view=data&keep=x");
  const next = selectAccountParams(original, "b", true);
  assert.equal(next.toString(), "creator=b&keep=x");
  assert.equal(original.get("series"), "a1");
  const column = selectSeriesParams(new URLSearchParams("creator=a&view=data&topics=queued&topic=t&keep=x"), "b1", "b");
  assert.equal(column.toString(), "creator=b&keep=x&series=b1");
  assert.equal(selectSeriesParams(new URLSearchParams("creator=a&topics=all"), "free", null, true).toString(), "topics=all&series=free");
});
test("switching accounts leaves the previous account's BGM view", () => {
  const original = new URLSearchParams("creator=a&view=bgm&keep=x");
  assert.equal(selectAccountParams(original, "b").toString(), "creator=b&keep=x");
  assert.equal(selectAccountParams(original, "a").toString(), "creator=a&view=bgm&keep=x");
  assert.equal(original.toString(), "creator=a&view=bgm&keep=x");
});

test("late deletion responses do not move a user who already navigated elsewhere", () => {
  const current = new URLSearchParams("creator=b&series=b1&topic=other&keep=x");
  assert.equal(removeDeletedAccountParams(current, "a").toString(), current.toString());
  assert.equal(removeDeletedSeriesParams(current, "a1").toString(), current.toString());
  assert.equal(removeDeletedSeriesParams(current, "b1").toString(), "creator=b&keep=x");
  assert.equal(removeDeletedAccountParams(current, "b").toString(), "keep=x");
});
