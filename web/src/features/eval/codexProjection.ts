import { asRecord, asText, recordArray, type EvidenceRecord } from "./chainProjection";

export interface CodexTurn {
  key: string; thread: string; turn: string; phase: string;
  events: EvidenceRecord[]; items: EvidenceRecord[];
  started: string; finished: string; status: string; complete: boolean;
}

/** Only public captured events. Repeated item updates are not extra calls. */
export function codexTurns(value: unknown): CodexTurn[] | null {
  const events = recordArray(asRecord(value)?.events);
  if (!events) return null;
  const turns = new Map<string, CodexTurn>();
  for (const event of events) {
    const thread = asText(event.thread_id), turn = asText(event.turn_id), phase = asText(event.phase);
    if (!thread || !turn) continue;
    const key = JSON.stringify([thread, turn, phase]);
    const row = turns.get(key) ?? { key, thread, turn, phase, events: [], items: [], started: "", finished: "", status: "", complete: false };
    row.events.push(event);
    if (!row.started) row.started = asText(event.at);
    if (event.method === "turn/completed") { row.status = asText(event.status); row.finished = asText(event.at); }
    if (event.method === "capture/finished" && event.status === "completed") row.complete = true;
    turns.set(key, row);
  }
  for (const row of turns.values()) {
    const items = new Map<string, EvidenceRecord>();
    for (const event of row.events) {
      if (event.item_type === "reasoning" || !asText(event.item_id) || !asText(event.method).startsWith("item/")) continue;
      const key = asText(event.item_id);
      if (items.get(key)?.method !== "item/completed" || event.method === "item/completed") items.set(key, event);
    }
    row.items = [...items.values()];
  }
  return [...turns.values()];
}

/** Thread counters are cumulative: retain the last snapshot, never sum updates. */
export function codexUsage(value: unknown) {
  const events = recordArray(asRecord(value)?.events);
  if (!events) return null;
  const threads = new Map<string, { thread: string; phase: string; total: EvidenceRecord | null; at: string }>();
  for (const event of events) {
    const thread = asText(event.thread_id);
    if (!thread) continue;
    if (!threads.has(thread)) threads.set(thread, { thread, phase: asText(event.phase), total: null, at: "" });
    if (event.method !== "thread/tokenUsage/updated") continue;
    const total = asRecord(asRecord(asRecord(event.payload)?.token_usage)?.total);
    threads.set(thread, { thread, phase: asText(event.phase), total, at: asText(event.at) });
  }
  return [...threads.values()];
}

const mutations: Record<string, string[]> = {
  S13: [],
  P01: ["topics", "pending_operations", "operation_events", "write_receipts", "content_runs", "content_revisions", "content_attempts", "content_run_events"],
  P02: ["topics", "pending_operations", "operation_events", "write_receipts", "content_runs", "content_revisions", "content_attempts", "content_run_events"],
  P03: ["series", "topics", "pending_operations", "operation_events", "write_receipts", "content_runs", "content_revisions", "content_attempts", "content_run_events"],
  P05: ["topics", "pending_operations", "operation_events", "write_receipts", "content_runs", "content_revisions", "content_attempts", "content_run_events"],
  P04: ["content_runs", "content_revisions", "content_attempts", "content_run_events", "topics"],
  P06: [],
};
export function expectedTable(caseId: string, name: string, before: number | null, oracle: unknown): string | null {
  const expected = asRecord(asRecord(oracle)?.expected_after);
  const explicit = asRecord(expected?.row_counts)?.[name];
  if (typeof explicit === "number") return `${explicit} 行`;
  if (!(caseId in mutations) || before == null) return null;
  // P05's frozen contract: one Run/Revision, failed + restored Attempts; never inferred from after.
  const added: Record<string, number> = { content_runs: 1, content_revisions: 1, content_attempts: 2, topics: 1 };
  if (caseId === "P05" && name in added) return `${before + added[name]} 行`;
  return mutations[caseId].includes(name) ? "授权新增 / 更新" : `${before} 行 · 完整行不变`;
}

export const codexExpectations: Record<string, string> = {
  S13: "仅创建融合草稿与资源映射；业务数据库与来源 Skill 完整行、文件不变，不自行入库或生产。",
  P01: "只为授权栏目新增指定选题与生产记录，最终待批准；账号、其他栏目、旧记录不变，不批准或发布。",
  P02: "只为授权栏目新增指定选题与生产记录，三页真实交付待批准；旧记录不变，不批准或发布。",
  P03: "提炼草稿可编辑，明确入库与创建栏目后，仅为新选题生产；来源作品、已有账号/栏目/Skill/历史记录不变，不批准或发布。",
  P05: "同一任务、同一作品版本：保留失败尝试，恢复新增一次成功尝试，最终待批准；旧图与原文件不变，不调用 SDK 重生，不批准或发布。",
  P04: "保存返工只新增待执行版本，不调用模型；明确执行后新增尝试和新图，旧版本与原文件保留，不批准或发布。",
  P06: "两轮只新增讨论记录；生产任务、版本、原图、Skill 与审批不变。首轮 fork，追问 resume 同一讨论线程，不生成新图。",
};
