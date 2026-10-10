import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Link } from "react-router-dom";
import { chatHref, type ChatLink } from "./chatLinks";
import "./discussion-activity.css";

export type DiscussionSnapshot = {
  id?: string;
  run_id?: string;
  revision_id?: string;
  status: string;
  reply?: string | null;
  error?: string | null;
  updated_at?: string;
  events?: { id: number | string; kind: string; text: string; at?: string }[];
};

const statusLabels: Record<string, string> = {
  queued: "等待讨论", running: "Codex 正在看图", completed: "讨论已完成",
  failed: "讨论失败", interrupted: "讨论中断", unknown: "讨论状态待确认",
};
const eventLabels: Record<string, string> = { status: "状态", message: "Codex", tool: "工具", error: "错误" };

export function DiscussionActivity({ discussion, links }: { discussion: DiscussionSnapshot; links: ChatLink[] }) {
  const events = (discussion.events ?? []).slice(-10);
  const href = links.find(link => {
    const url = new URL(link.url, window.location.origin);
    return link.label === "查看讨论" && url.pathname === `/runs/${discussion.run_id}`
      && url.searchParams.get("discussion") === discussion.id && url.searchParams.get("revision") === discussion.revision_id;
  })?.url;
  const failure = ["failed", "interrupted", "unknown"].includes(discussion.status);
  return <section className={`discussion-activity${failure ? " discussion-activity-failed" : ""}`} aria-label="作品讨论进度">
    <header><strong>{statusLabels[discussion.status] ?? discussion.status}</strong>{discussion.updated_at && <time>{new Date(discussion.updated_at).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" })}</time>}</header>
    {discussion.reply && <div className="discussion-activity-reply"><Markdown remarkPlugins={[remarkGfm]} skipHtml disallowedElements={["img"]} components={{ a: ({ href, children }) => {
      const target = chatHref(href, links, window.location.origin);
      return target?.kind === "internal" ? <Link to={target.href}>{children}</Link>
        : target?.kind === "external" ? <a href={target.href} target="_blank" rel="noopener noreferrer">{children}</a> : <span>{children}</span>;
    } }}>{discussion.reply}</Markdown></div>}
    {discussion.error && <p className="discussion-activity-error" role="alert">{discussion.error}</p>}
    {events.length > 0 && <ol>{events.map(event => <li key={`${event.kind}:${event.id}`}><span>{eventLabels[event.kind] ?? "活动"}</span><p>{event.text}</p></li>)}</ol>}
    {href && <Link to={href}>查看同一讨论 ↗</Link>}
  </section>;
}
