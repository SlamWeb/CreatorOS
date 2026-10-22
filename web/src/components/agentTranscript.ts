import type { ChatAttachment } from "./AgentAttachments";
import type { ContextUsage } from "./agentComposerState";
import type { ChatLink } from "./chatLinks";
import type { ResearchSnapshot } from "./ResearchActivity";
import type { DiscussionSnapshot } from "./DiscussionActivity";

export type ChatEntryData = { id?: string; kind: string; text?: string; name?: string; status?: string; run_id?: string;
  iteration_mode?: boolean; input_tokens?: number; output_tokens?: number; turn_id?: string;
  complete?: boolean; terminal?: boolean; model_request_id?: string; tool_call_id?: string;
  research?: ResearchSnapshot; discussion?: DiscussionSnapshot; links?: ChatLink[]; attachments?: ChatAttachment[] };

export type ChatSession = { id: string; title: string; version: number; status: string; error: string | null;
  scope_kind: "overview" | "creator"; creator_id: string | null;
  reasoning_effort?: string; reasoning_options?: string[]; context_usage?: ContextUsage | null;
  reasoning_actual_effort?: string; reasoning_effort_map?: Record<string, string>;
  entries: ChatEntryData[]; updated_at: string; has_older: boolean; stream_revision?: number };

export type TurnUsage = { turnId?: string; input: number | null; output: number | null; requests: number; partial: boolean };
export type TranscriptItem = { key: string; entry: ChatEntryData; usage?: TurnUsage; hiddenLinkUrls?: string };
export type ChatSessionPatch = Partial<Omit<ChatSession, "entries" | "id" | "stream_revision">> & {
  id: string; base_revision: number; stream_revision: number;
  entries_upsert: (Partial<ChatEntryData> & { id: string })[];
  entries_text_append?: { id: string; text: string }[]; entry_order?: string[];
};

export function newestChatSession(old: ChatSession | undefined, next: ChatSession): ChatSession {
  if (old?.id === next.id && old.stream_revision != null && next.stream_revision != null) {
    return old.stream_revision > next.stream_revision ? old : next;
  }
  return old && (old.version > next.version || (old.version === next.version && old.updated_at > next.updated_at)) ? old : next;
}

export function applySessionPatch(current: ChatSession | undefined, patch: ChatSessionPatch):
  { status: "applied" | "ignored"; session: ChatSession } | { status: "resync" } {
  if (!current || patch.id !== current.id || !Number.isSafeInteger(patch.stream_revision)
    || !Number.isSafeInteger(patch.base_revision) || current.stream_revision == null) return { status: "resync" };
  if (patch.stream_revision <= current.stream_revision) return { status: "ignored", session: current };
  if (patch.base_revision !== current.stream_revision || !Array.isArray(patch.entries_upsert)) return { status: "resync" };
  if ((patch.scope_kind != null && patch.scope_kind !== current.scope_kind)
    || (patch.creator_id !== undefined && patch.creator_id !== current.creator_id)) return { status: "resync" };
  const byId = new Map<string, ChatEntryData>();
  for (const entry of current.entries) {
    if (!entry.id || byId.has(entry.id)) return { status: "resync" };
    byId.set(entry.id, entry);
  }
  const added = new Set<string>();
  for (const update of patch.entries_upsert) {
    if (!update || typeof update.id !== "string" || !update.id) return { status: "resync" };
    const before = byId.get(update.id);
    if (!before && typeof update.kind !== "string") return { status: "resync" };
    if (!before) added.add(update.id);
    byId.set(update.id, { ...before, ...update } as ChatEntryData);
  }
  if (patch.entries_text_append !== undefined && !Array.isArray(patch.entries_text_append)) return { status: "resync" };
  for (const append of patch.entries_text_append ?? []) {
    if (!append || typeof append.id !== "string" || typeof append.text !== "string") return { status: "resync" };
    const before = byId.get(append.id);
    if (!before || (before.text != null && typeof before.text !== "string")) return { status: "resync" };
    byId.set(append.id, { ...before, text: `${before.text ?? ""}${append.text}` });
  }
  const order = patch.entry_order ?? current.entries.map(entry => entry.id!);
  if (!Array.isArray(order) || new Set(order).size !== order.length || order.some(id => typeof id !== "string" || !byId.has(id))
    || [...added].some(id => !order.includes(id))) return { status: "resync" };
  const metadata = { ...patch };
  delete (metadata as Partial<ChatSessionPatch>).entries_upsert;
  delete metadata.entries_text_append;
  delete metadata.entry_order;
  delete (metadata as Partial<ChatSessionPatch>).base_revision;
  return { status: "applied", session: { ...current, ...metadata, id: current.id, entries: order.map(id => byId.get(id)!) } };
}

const recordedNumber = (value: number | undefined) => typeof value === "number" && Number.isFinite(value) && value >= 0;

export function entryKey(entry: ChatEntryData, index: number): string {
  // Served entries now have IDs, including projected legacy records. This last
  // fallback only keeps older servers/fixtures readable, not a stream identity.
  return entry.id ?? `legacy:${entry.kind}:${entry.turn_id ?? ""}:${entry.model_request_id ?? ""}:${entry.tool_call_id ?? ""}:${index}`;
}

export function transcriptItems(entries: ChatEntryData[]): TranscriptItem[] {
  const grouped = new Map<string, Map<string, ChatEntryData>>();
  const lastIndex = new Map<string, number>();
  const lastLink = new Map<string, number>();
  entries.forEach((entry, index) => {
    if (entry.turn_id) lastIndex.set(entry.turn_id, index);
    if (entry.turn_id) for (const link of entry.links ?? []) lastLink.set(`${entry.turn_id}:${link.url}`, index);
    if (entry.kind !== "usage" || !entry.turn_id) return;
    const requests = grouped.get(entry.turn_id) ?? new Map<string, ChatEntryData>();
    requests.set(entry.model_request_id ?? entryKey(entry, index), entry);
    grouped.set(entry.turn_id, requests);
  });
  const sumUsage = (values: ChatEntryData[], turnId?: string): TurnUsage => ({ turnId,
    input: values.some(value => recordedNumber(value.input_tokens)) ? values.reduce((sum, value) => sum + (recordedNumber(value.input_tokens) ? value.input_tokens! : 0), 0) : null,
    output: values.some(value => recordedNumber(value.output_tokens)) ? values.reduce((sum, value) => sum + (recordedNumber(value.output_tokens) ? value.output_tokens! : 0), 0) : null,
    requests: values.length, partial: values.some(value => !recordedNumber(value.input_tokens) || !recordedNumber(value.output_tokens)),
  });
  const result: TranscriptItem[] = [];
  entries.forEach((entry, index) => {
    if (entry.kind !== "usage") result.push({ key: entryKey(entry, index), entry,
      hiddenLinkUrls: JSON.stringify((entry.links ?? []).filter(link => entry.turn_id && lastLink.get(`${entry.turn_id}:${link.url}`) !== index).map(link => link.url)) });
    else if (!entry.turn_id) result.push({ key: entryKey(entry, index), entry, usage: sumUsage([entry]) });
    if (entry.turn_id && lastIndex.get(entry.turn_id) === index && grouped.has(entry.turn_id)) {
      result.push({ key: `usage:${entry.turn_id}`, entry: { kind: "usage", turn_id: entry.turn_id },
        usage: sumUsage([...grouped.get(entry.turn_id)!.values()], entry.turn_id) });
    }
  });
  return result;
}
