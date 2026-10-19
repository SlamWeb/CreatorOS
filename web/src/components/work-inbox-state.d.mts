export type InboxVersion = { key?: string; kind: string; id: string; token: string; created_at: string };
export type InboxSeenState = { since: string; baseline: Record<string, string>; seen: Record<string, string> };
export function initialInboxState(data: { items: InboxVersion[]; objects: InboxVersion[]; as_of: string }): InboxSeenState;
export function isInboxUnseen(state: InboxSeenState | null, object: InboxVersion): boolean;
export function markInboxSeen(state: InboxSeenState | null, objects: InboxVersion[]): InboxSeenState | null;
