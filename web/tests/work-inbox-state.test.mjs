import test from 'node:test';
import assert from 'node:assert/strict';
import { initialInboxState, isInboxUnseen, markInboxSeen } from '../src/components/work-inbox-state.mjs';

const old = { key: 'production:old', kind: 'production', id: 'old', token: 'running:r1', created_at: '2026-10-19T00:00:00Z' };
const data = { items: [old], objects: [], as_of: '2026-10-20T00:00:00Z' };
test('first opening does not light up historical work', () => {
  assert.equal(isInboxUnseen(initialInboxState(data), old), false);
});
test('old running task completing creates a new stable notification', () => {
  assert.equal(isInboxUnseen(initialInboxState(data), { ...old, token: 'ready:r1' }), true);
});
test('new work is unseen; older work loaded later is not', () => {
  const state = initialInboxState(data);
  const item = { ...old, key: 'extraction:new', id: 'new', created_at: '2026-10-20T01:00:00Z' };
  assert.equal(isInboxUnseen(state, item), true);
  assert.equal(isInboxUnseen(state, { ...item, created_at: old.created_at }), false);
});
test('seen versions survive localStorage round trip but changed revision becomes unseen', () => {
  const completed = { ...old, token: 'ready:r1' };
  const state = JSON.parse(JSON.stringify(markInboxSeen(initialInboxState(data), [completed])));
  assert.equal(isInboxUnseen(state, completed), false);
  assert.equal(isInboxUnseen(state, { ...completed, token: 'ready:r2' }), true);
});
test('same-token mark is referentially stable and heartbeat does not relight', () => {
  const state = markInboxSeen(initialInboxState(data), [old]);
  assert.equal(markInboxSeen(state, [old]), state);
  assert.equal(isInboxUnseen(state, { ...old, updated_at: '2026-10-20T03:00:00Z' }), false);
});
test('object versions share viewed semantics without task approval mutation', () => {
  const object = { kind: 'skill', id: 'story', token: 'saved:digest', created_at: '2026-10-20T01:00:00Z' };
  const state = initialInboxState(data);
  assert.equal(isInboxUnseen(state, object), true);
  assert.equal(isInboxUnseen(markInboxSeen(state, [object]), object), false);
  assert.deepEqual(object, { kind: 'skill', id: 'story', token: 'saved:digest', created_at: '2026-10-20T01:00:00Z' });
});
