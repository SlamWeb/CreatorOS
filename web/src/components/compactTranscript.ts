import type { TranscriptItem } from "./agentTranscript";

export type CompactTranscriptItem = { key: string; items: TranscriptItem[] };

export function compactTranscript(items: TranscriptItem[]): CompactTranscriptItem[] {
  const result: CompactTranscriptItem[] = [];
  for (const item of items) {
    const entry = item.entry;
    const previous = result.at(-1);
    const last = previous?.items.at(-1)?.entry;
    const plainTool = entry.kind === "tool" && !entry.research && !entry.discussion && !entry.background;
    if (plainTool && entry.turn_id && last?.kind === "tool" && last.turn_id === entry.turn_id
      && !last.research && !last.discussion && !last.background) previous!.items.push(item);
    else result.push({ key: item.key, items: [item] });
  }
  return result;
}

export function toolBatchStatus(items: TranscriptItem[]) {
  const running = items.filter(({ entry }) => entry.status === "running").length;
  const waiting = items.filter(({ entry }) => entry.status === "waiting").length;
  const attention = items.filter(({ entry }) => !["done", "running", "waiting"].includes(entry.status ?? "")).length;
  const parts = [`${items.length} 次调用`];
  if (running) parts.push(`${running} 次调用中`);
  if (waiting) parts.push(`${waiting} 项后台进行中`);
  if (attention) parts.push(`${attention} 次需检查`);
  if (!running && !waiting && !attention) parts.push("已返回");
  return { label: parts.join(" · "), running, waiting, attention };
}

// Remove only link-only paragraphs whose destinations are already represented
// by confirmed result links. Prose, external citations and unconfirmed URLs stay.
export function compactReplyText(text: string, isConfirmed: (url: string) => boolean): string {
  return text.split(/\n\s*\n/).filter(paragraph => {
    const matches = [...paragraph.matchAll(/\[[^\]]*\]\(([^\s)]+)(?:\s+"[^"]*")?\)/g)];
    if (!matches.length || paragraph.replace(/\[[^\]]*\]\(([^\s)]+)(?:\s+"[^"]*")?\)/g, "").trim()) return true;
    return !matches.every(match => isConfirmed(match[1]));
  }).join("\n\n");
}
