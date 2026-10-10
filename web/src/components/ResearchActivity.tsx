import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { chatHref, type ChatLink } from "./chatLinks";
import "./research-activity.css";

export type ResearchSnapshot = {
  id: string;
  status: string;
  note?: string;
  error_type?: string;
  error?: string;
  message?: string;
  url?: string;
  progress?: {
    stage?: string;
    last_activity_at?: string | null;
    events?: { id: string | number; kind: string; text: string; status?: string; at?: string }[];
  };
};

const stages: Record<string, string> = {
  queued: "等待开始", starting: "正在启动 Codex", researching: "调研中", searching: "搜索资料",
  tool_call: "正在使用工具", completed: "调研已完成", failed: "调研失败", interrupted: "调研已中断",
  stale: "栏目配置已变化", ready: "候选已就绪", unknown: "调研状态待确认", restarting: "按新配置调研",
};
const eventKinds: Record<string, string> = { message: "Codex", search: "搜索", tool: "工具", status: "状态" };
const runningStates = new Set(["running", "queued", "starting", "researching"]);

export function ResearchActivity({ research, links }: { research: ResearchSnapshot; links: ChatLink[] }) {
  const running = runningStates.has(research.status);
  const [now, setNow] = useState(Date.now);
  useEffect(() => {
    if (!running) return;
    setNow(Date.now());
    const timer = window.setInterval(() => setNow(Date.now()), 10_000);
    return () => window.clearInterval(timer);
  }, [running, research.id]);
  const lastActivity = research.progress?.last_activity_at;
  const lastTime = lastActivity ? Date.parse(lastActivity) : NaN;
  const quiet = running && Number.isFinite(lastTime) && now - lastTime >= 120_000;
  const failed = ["failed", "interrupted", "stale"].includes(research.status);
  const error = research.error || research.message;
  const events = (research.progress?.events ?? []).filter(event => event.kind !== "reasoning").slice(-30);
  const target = chatHref(research.url, links, window.location.origin);
  const href = target?.kind === "internal" ? target.href : null;
  return <section className={`research-activity${failed ? " research-activity-failed" : ""}`} aria-label="选题调研活动">
    <div className="research-activity-heading">
      <strong>{stages[research.status] ?? (running ? "调研中" : research.status)}</strong>
      {research.progress?.stage && running && <span>{stages[research.progress.stage] ?? research.progress.stage}</span>}
    </div>
    {research.note && <p className="research-activity-note">{research.note}</p>}
    {Number.isFinite(lastTime) && <p className="research-activity-time">最近实际活动：<time dateTime={lastActivity!}>{new Date(lastTime).toLocaleTimeString("zh-CN")}</time></p>}
    {quiet && <p className="research-activity-quiet" role="status">暂无新活动，尚未收到完成或失败结果。连接正常不代表仍在搜索。</p>}
    {running && !lastActivity && <p className="research-activity-time">尚未收到 Codex 活动；正在等待同一调研任务。</p>}
    {!!events.length && <ol className="research-activity-events">
      {events.map(event => <li key={`${event.kind}:${event.id}`}>
        <span className="research-activity-kind">{eventKinds[event.kind] ?? "活动"}{event.status === "failed" ? " · 失败" : ""}</span>
        {event.text.length > 240 ? <details>
          <summary><span>{event.text.slice(0, 240)}…</span><span className="research-activity-expand">展开活动全文</span><span className="research-activity-collapse">收起活动全文</span></summary>
          <p>{event.text}</p>
        </details> : <p>{event.text}</p>}
      </li>)}
    </ol>}
    {failed && <p className="research-activity-error" role="alert">{error || research.note || "调研没有完成，请查看任务记录。"}</p>}
    {research.status === "unknown" && <p className="research-activity-quiet" role="status">{error || "观察已中断，任务结果尚不确定；请查询同一批次，不要重新提交。"}</p>}
    {research.status === "ready" && <p className="research-activity-ready">候选已就绪，尚未入队或生产。</p>}
    {href && <Link to={href}>查看同一调研任务 ↗</Link>}
  </section>;
}
