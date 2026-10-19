import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { Link, useLocation } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { Bell, RefreshCw, X } from "lucide-react";
import { request } from "../api/client";
import { initialInboxState, isInboxUnseen, markInboxSeen, type InboxSeenState, type InboxVersion } from "./work-inbox-state.mjs";
import "./task-inbox.css";

export type WorkInboxItem = InboxVersion & { key: string; title: string; status: string; bucket: "active" | "attention" | "recent";
  updated_at: string; url: string; series_id: string | null; skill_ids: string[]; label?: string };
type WorkInboxData = { items: WorkInboxItem[]; objects: InboxVersion[]; as_of: string;
  counts: Record<string, number>; has_more: boolean; unreadable_records: number };
type Controls = { items: WorkInboxItem[]; isUnseen: (item: InboxVersion) => boolean;
  hasUnseen: (kind: "skill" | "series", id: string) => boolean;
  markSeen: (item: InboxVersion) => void; markObjectSeen: (kind: "skill" | "series", id: string) => void;
  refresh: () => void; open: boolean; setOpen: (value: boolean) => void; unread: number; skillUnread: boolean;
  seriesUnread: boolean; data?: WorkInboxData; error: string; loading: boolean; storageFailed: boolean };
const InboxContext = createContext<Controls | null>(null);
const seenKey = "creatoros:work-inbox:seen:v1";
const labels: Record<string, string> = { queued: "排队中", researching: "调研中", running: "进行中", producing: "制作中",
  validating: "检查中", ready: "待查看", awaiting_approval: "待验收", failed: "失败", interrupted: "已中断",
  stale: "需重新检查", saved: "已保存", applied: "已保存", approved: "已验收", cancelled: "已取消", completed: "已完成", created: "新栏目" };
const kindLabels: Record<string, string> = { production: "生产", research: "调研", extraction: "Skill 提炼", calibration: "Skill 校准", series: "栏目" };

function readSeen(): InboxSeenState | null {
  try {
    const value = JSON.parse(localStorage.getItem(seenKey) ?? "null");
    if (value && typeof value.since === "string" && value.baseline && typeof value.baseline === "object"
        && value.seen && typeof value.seen === "object") return value;
  } catch { /* The task index remains usable without browser storage. */ }
  return null;
}

export function WorkInboxProvider({ children }: { children: ReactNode }) {
  const [seen, setSeen] = useState(readSeen);
  const [open, setOpen] = useState(false);
  const [visible, setVisible] = useState(() => document.visibilityState !== "hidden");
  const [storageFailed, setStorageFailed] = useState(false);
  const location = useLocation();
  const query = useQuery({ queryKey: ["work-inbox"], queryFn: ({ signal }) => request<WorkInboxData>("/api/work-inbox?limit=50", { signal }),
    enabled: visible, retry: false, staleTime: 10_000, refetchInterval: open && visible ? 10_000 : false,
    refetchIntervalInBackground: false, refetchOnWindowFocus: true });
  useEffect(() => {
    const update = () => setVisible(document.visibilityState !== "hidden");
    document.addEventListener("visibilitychange", update);
    return () => document.removeEventListener("visibilitychange", update);
  }, []);
  useEffect(() => { if (query.data) setSeen(previous => previous ?? initialInboxState(query.data)); }, [query.data]);
  useEffect(() => {
    if (!seen) return;
    try { localStorage.setItem(seenKey, JSON.stringify(seen)); setStorageFailed(false); }
    catch { setStorageFailed(true); }
  }, [seen]);
  useEffect(() => {
    const update = (event: StorageEvent) => { if (event.key === seenKey) setSeen(readSeen()); };
    window.addEventListener("storage", update);
    return () => window.removeEventListener("storage", update);
  }, []);
  const refresh = useCallback(() => { void query.refetch(); }, [query.refetch]);
  useEffect(() => { if (visible) refresh(); setOpen(false); }, [location.pathname, location.search]);
  const items = query.data?.items ?? [];
  const objects = query.data?.objects ?? [];
  const isUnseen = useCallback((item: InboxVersion) => isInboxUnseen(seen, item), [seen]);
  const related = useCallback((kind: "skill" | "series", id: string): InboxVersion[] => [
    ...objects.filter(object => object.kind === kind && object.id === id),
    ...items.filter(item => kind === "series" ? item.series_id === id : item.skill_ids.includes(id)),
  ], [items, objects]);
  const markSeen = useCallback((item: InboxVersion) => setSeen(previous => markInboxSeen(previous, [item])), []);
  const markObjectSeen = useCallback((kind: "skill" | "series", id: string) =>
    setSeen(previous => markInboxSeen(previous, related(kind, id))), [related]);
  const hasUnseen = useCallback((kind: "skill" | "series", id: string) => related(kind, id).some(isUnseen), [related, isUnseen]);
  const unread = items.filter(isUnseen).length + objects.filter(isUnseen).filter(object =>
    !items.some(item => isUnseen(item) && (object.kind === "series" ? item.series_id === object.id : item.skill_ids.includes(object.id)))).length;
  const value = useMemo(() => ({ items, data: query.data, isUnseen, hasUnseen, markSeen, markObjectSeen, refresh, open, setOpen,
    unread, skillUnread: items.some(item => ["extraction", "calibration"].includes(item.kind) && isUnseen(item))
      || objects.some(object => object.kind === "skill" && isUnseen(object)),
    seriesUnread: items.some(item => !!item.series_id && isUnseen(item)) || objects.some(object => object.kind === "series" && isUnseen(object)),
    loading: query.isFetching, error: query.error instanceof Error ? query.error.message : "", storageFailed }),
  [items, query.data, isUnseen, hasUnseen, markSeen, markObjectSeen, refresh, open, unread, query.isFetching, query.error, storageFailed, objects]);
  return <InboxContext.Provider value={value}>{children}</InboxContext.Provider>;
}

export function useWorkInbox() {
  const context = useContext(InboxContext);
  if (!context) throw new Error("WorkInboxProvider is required");
  return context;
}

export function UnseenDot({ label = "有未查看内容" }: { label?: string }) {
  return <span className="work-unseen-dot" role="img" aria-label={label} title={label} />;
}

export function TaskInbox() {
  const inbox = useWorkInbox();
  const trigger = useRef<HTMLButtonElement>(null);
  const panel = useRef<HTMLDivElement>(null);
  const closeButton = useRef<HTMLButtonElement>(null);
  const wasOpen = useRef(false);
  useEffect(() => {
    if (inbox.open) { inbox.refresh(); closeButton.current?.focus(); }
    else if (wasOpen.current) trigger.current?.focus();
    wasOpen.current = inbox.open;
  }, [inbox.open]);
  useEffect(() => {
    if (!inbox.open) return;
    const click = (event: PointerEvent) => {
      if (!panel.current?.contains(event.target as Node) && !trigger.current?.contains(event.target as Node)) inbox.setOpen(false);
    };
    const key = (event: KeyboardEvent) => { if (event.key === "Escape") inbox.setOpen(false); };
    document.addEventListener("pointerdown", click);
    document.addEventListener("keydown", key);
    return () => { document.removeEventListener("pointerdown", click); document.removeEventListener("keydown", key); };
  }, [inbox.open, inbox.setOpen]);
  const top = trigger.current?.getBoundingClientRect().bottom ?? 56;
  return <>
    <button ref={trigger} type="button" className="command-trigger work-inbox-trigger" aria-label="任务" title="任务"
      aria-haspopup="dialog" aria-expanded={inbox.open} onClick={() => inbox.setOpen(!inbox.open)}>
      <Bell size={17} strokeWidth={1.8} />{inbox.unread > 0 && <UnseenDot label="有未查看的任务" />}
    </button>
    {inbox.open && createPortal(<div ref={panel} role="dialog" aria-label="任务" className="work-inbox-panel" style={{ top: top + 12 }}>
      <header className="work-inbox-heading"><h2>任务</h2><div>
        <button type="button" className="work-inbox-icon" aria-label="刷新任务" onClick={inbox.refresh} disabled={inbox.loading}><RefreshCw size={16} /></button>
        <button ref={closeButton} type="button" className="work-inbox-icon" aria-label="关闭任务" onClick={() => inbox.setOpen(false)}><X size={18} /></button>
      </div></header>
      {inbox.error && <p className="work-inbox-error" role="alert">刷新失败：{inbox.error}</p>}
      <div className="work-inbox-scroll">
        {!inbox.data && inbox.loading ? <p className="work-inbox-empty">正在读取任务…</p> : !inbox.items.length && !inbox.error
          ? <p className="work-inbox-empty">还没有任务。生产、调研和 Skill 草稿会显示在这里。</p> : null}
        {([ ["active", "进行中"], ["attention", "待处理"], ["recent", "最近"] ] as const).map(([bucket, title]) => {
          const group = inbox.items.filter(item => item.bucket === bucket);
          return group.length ? <section key={bucket} className="work-inbox-group"><h3>{title}</h3>
            {group.map(item => <Link key={item.key} to={item.url} className="work-inbox-row" onClick={() => { inbox.markSeen(item); inbox.setOpen(false); }}>
              <span className="work-inbox-row-main"><span className="work-inbox-row-kind">{item.label ?? kindLabels[item.kind] ?? "任务"}</span>
                <span className="work-inbox-row-title">{item.title}</span></span>
              <span className={`work-inbox-state ${["failed", "interrupted", "stale"].includes(item.status) ? "is-error" : ""}`}>
                {inbox.isUnseen(item) && <UnseenDot />}{labels[item.status] ?? "状态待核对"}</span>
            </Link>)}
          </section> : null;
        })}
      </div>
      {(inbox.data?.has_more || inbox.data?.unreadable_records || inbox.storageFailed) && <footer className="work-inbox-footer">
        {inbox.data?.has_more ? <span>显示最近 50 条，优先进行中和待处理。</span> : null}
        {inbox.data?.unreadable_records ? <span>部分记录暂时无法读取，原文件未改动。</span> : null}
        {inbox.storageFailed && <span>浏览器无法保存已查看标记，刷新后可能再次提示。</span>}
      </footer>}
    </div>, document.body)}
  </>;
}
