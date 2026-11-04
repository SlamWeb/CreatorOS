import { memo, useEffect, useId, useRef, useState } from "react";
import { Link } from "react-router-dom";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Check, CircleAlert, Copy, LoaderCircle, Minus, Music2, Plus } from "lucide-react";
import { apiUrl } from "../../api/client";
import { ChatTrace } from "../../components/ChatTrace";
import { ChatToolDetails } from "../../components/ChatToolDetails";
import { ResearchActivity } from "../../components/ResearchActivity";
import { DiscussionActivity } from "../../components/DiscussionActivity";
import { ChatImages } from "../../components/AgentAttachments";
import { chatHref, chatLinkKind, chatLinkLabel, uniqueChatLinks, type ChatLink } from "../../components/chatLinks";
import type { ChatEntryData as Entry } from "../../components/agentTranscript";
import { compactReplyText, toolBatchStatus, type CompactTranscriptItem } from "../../components/compactTranscript";
import "../../components/chat-transcript.css";
import "../../components/compact-chat.css";

const tools: Record<string, string> = { list_creators: "查看账号", list_creator_series: "查看栏目",
  discuss_content_run: "讨论作品", get_content_discussion: "查看作品讨论", request_content_revision: "提出返工", get_creator_tasks: "查看账号任务",
  list_series_topics: "查看选题", start_content_run: "提交生产", get_content_run: "查询任务",
  research_series_topics: "调研选题", get_topic_research: "查看调研候选", prepare_topic_selection: "准备选题预览",
  queue_topics: "选题入队", compose_series: "创建栏目", update_series_composition: "编辑栏目", assign_series: "分配账号",
  list_producer_skills: "查看生产 Skill", install_producer_skill: "安装 Skill", get_skill_install: "查询安装",
  get_producer_skill: "读取 Skill 文件", update_producer_skill_file: "保存 Skill 文件",
  manage_skill_iteration: "校准 Skill",
  manage_plan: "管理计划",
  manage_topics: "管理选题",
  manage_media_collection: "管理素材集",
  manage_material: "管理素材",
  manage_skill_library: "管理 Skill 库",
  read_image: "看图片", extract_skill_from_images: "提炼 Skill", create_skill_draft: "保存 Skill 草稿", import_content_images: "导入成品",
  read_file: "读取历史资料", read_tool_result: "回读工具结果" };
const ChatEntry = memo(function ChatEntry({ entry, sessionId, sessionStatus, hiddenLinkUrls }: {
  entry: Entry; sessionId: string; sessionStatus: string; hiddenLinkUrls?: string;
}) {
  const hidden = new Set<string>(hiddenLinkUrls ? JSON.parse(hiddenLinkUrls) : []);
  const links = uniqueChatLinks(entry.links ?? []).filter(link => !hidden.has(link.url));
  if (entry.kind === "user") return <div className="chat-user">{!!entry.attachments?.length && <ChatImages attachments={entry.attachments} />}{!!entry.materials?.length && <div className="chat-audio-drafts" aria-label="消息音频">{entry.materials.map(item => <span key={item.id}><Music2 size={15} /><a href={apiUrl(item.url)} target="_blank" rel="noopener noreferrer">{item.name}</a></span>)}</div>}{entry.text}
    {entry.iteration_mode && <small className="chat-user-iteration">RSI · 最多 3 轮</small>}</div>;
  if (entry.kind === "assistant") return entry.text ? <div className="chat-reply"><div className="chat-answer"><Markdown remarkPlugins={[remarkGfm]} skipHtml components={{
    table: ({ children }) => <div className="chat-table-scroll" tabIndex={0} role="region" aria-label="表格，可横向滚动"><table>{children}</table></div>,
    img: ({ alt }) => <span>{alt ?? "图片请在内容页查看"}</span>,
    a: ({ href, children }) => {
      const target = chatHref(href, entry.links ?? [], window.location.origin);
      // Confirmed task links have one named destination below the reply; keep
      // repeated model-written links from multiplying indistinguishable CTAs.
      return target?.kind === "internal" ? <span>{children}</span>
        : target?.kind === "external" ? <a href={target.href} target="_blank" rel="noopener noreferrer">{children}</a>
          : <span title="此地址未由工具确认；请使用回复下方的任务入口">{children}</span>;
    },
  }}>{compactReplyText(entry.text, url => chatHref(url, links, window.location.origin)?.kind === "internal")}</Markdown></div>
    {!!links.length && <ReplyLinks links={links} />}
    {(entry.complete || entry.terminal || (entry.complete === undefined && entry.terminal === undefined && sessionStatus !== "running")) && <div className="chat-reply-actions">
      <CopyReply text={entry.text} />
      <ChatTrace sessionId={sessionId} turnId={entry.turn_id} requestId={entry.model_request_id}
        incomplete={entry.terminal === true && !entry.complete} />
    </div>}
  </div> : null;
  if (entry.kind === "tool") return <ToolEntry entry={entry} sessionId={sessionId} links={links} />;
  return <p className="agent-note">{entry.kind === "guard_stop" ? "已达到本次调用上限，请检查当前结果。" : entry.kind === "context_compacted" ? "较早消息已压缩，完整记录仍在本地。" : "上下文接近预算。"}</p>;
});

export function ChatEntryGroup({ group, sessionId, sessionStatus }: {
  group: CompactTranscriptItem; sessionId: string; sessionStatus: string;
}) {
  const [open, setOpen] = useState(false);
  const detailsId = useId();
  const entries = group.items.map(item => <ChatEntry key={item.key} entry={item.entry} sessionId={sessionId}
    sessionStatus={sessionStatus} hiddenLinkUrls={item.hiddenLinkUrls} />);
  if (group.items.length === 1) return entries[0];
  const status = toolBatchStatus(group.items);
  return <section className="chat-tool-batch" data-attention={status.attention > 0} aria-label="执行记录">
    <button type="button" className="chat-tool-toggle" aria-expanded={open} aria-controls={detailsId} onClick={() => setOpen(value => !value)}>
      {status.running ? <LoaderCircle size={14} className="chat-tool-spinner" aria-hidden="true" />
        : status.attention ? <CircleAlert size={14} aria-hidden="true" /> : <Check size={14} aria-hidden="true" />}
      <span>执行记录</span><small>{status.label}</small>
      {open ? <Minus size={13} aria-hidden="true" /> : <Plus size={13} aria-hidden="true" />}
    </button>
    {open && <div id={detailsId} className="chat-tool-batch-body">{entries}</div>}
  </section>;
}

function ReplyLinks({ links }: { links: ChatLink[] }) {
  const [open, setOpen] = useState(false);
  const detailsId = useId();
  const many = links.length > 2;
  return <div className="chat-result-list">
    {many && <button type="button" className="chat-tool-toggle" aria-expanded={open} aria-controls={detailsId} onClick={() => setOpen(value => !value)}>
      <span>{open ? "收起结果" : "查看结果"}（{links.length}）</span>
      {open ? <Minus size={13} aria-hidden="true" /> : <Plus size={13} aria-hidden="true" />}
    </button>}
    {(!many || open) && <nav id={detailsId} className="chat-delivery-links" aria-label="本轮任务入口">
      {links.map(link => <NamedChatLink key={link.url} link={link} />)}
    </nav>}
  </div>;
}

function NamedChatLink({ link }: { link: ChatLink }) {
  const target = chatHref(link.url, [link], window.location.origin);
  if (target?.kind !== "internal") return null;
  const label = chatLinkLabel(link);
  if (link.kind === "download" && /^\/api\/creators\/[A-Za-z0-9_-]+\/collections\/[A-Za-z0-9_-]+\/exports\/[a-f0-9]{64}\/download$/.test(target.href)) {
    return <a href={target.href} download title={label} data-link-kind="download"><span>{label}</span></a>;
  }
  const at = link.created_at ? Date.parse(link.created_at) : NaN;
  return <Link to={target.href} title={label} data-link-kind={chatLinkKind(link) ?? undefined}>
    <span>{label}</span>{Number.isFinite(at) && <small>{new Date(at).toLocaleString("zh-CN", {
      month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false,
    })}</small>}
  </Link>;
}

function ToolEntry({ entry, sessionId, links }: { entry: Entry; sessionId: string; links: ChatLink[] }) {
  const [open, setOpen] = useState(false);
  const detailsId = useId();
  const running = entry.status === "running";
  const successful = entry.status === "done";
  const backgroundStatus = entry.background?.status;
  const status = entry.status === "waiting" ? (backgroundStatus === "unknown" ? "后台状态待确认" : "后台进行中")
    : running ? (entry.background ? (backgroundStatus === "queued" ? "等待执行" : "正在提炼")
      : entry.research ? "等待调研结果" : entry.discussion ? "等待讨论回复" : "调用中")
    : successful ? "已返回" : entry.status === "unknown" ? "结果未确认"
      : entry.status === "not_dispatched" ? "未执行" : "结果需检查";
  const navigation = links.filter(link => !["research", "discussion"].includes(chatLinkKind(link) ?? ""));
  return <div className="chat-tool chat-tool-expandable" data-status={entry.status} data-entry-id={entry.id}>
    <button type="button" className="chat-tool-toggle" aria-expanded={open} aria-controls={detailsId}
      onClick={() => setOpen(value => !value)}>
      {running || entry.status === "waiting" ? <LoaderCircle size={14} className={running ? "chat-tool-spinner" : undefined} aria-hidden="true" />
        : successful ? <Check size={14} aria-hidden="true" /> : <CircleAlert size={14} aria-hidden="true" />}
      <span className="chat-tool-name">{tools[entry.name ?? ""] ?? entry.name ?? "工具"}</span>
      <small>{status}</small>
      {open ? <Minus size={13} className="chat-tool-expand-icon" aria-hidden="true" /> : <Plus size={13} className="chat-tool-expand-icon" aria-hidden="true" />}
    </button>
    {running && entry.background?.note && <small role="status">{entry.background.note}</small>}
    {open && !!navigation.length && <nav className="chat-tool-links" aria-label="工具任务入口">
      {navigation.map(link => <NamedChatLink key={link.url} link={link} />)}
    </nav>}
    {open && <div id={detailsId} className="chat-tool-details" role="region" aria-label={`${tools[entry.name ?? ""] ?? entry.name ?? "工具"}详情`}>
      <ChatToolDetails sessionId={sessionId} turnId={entry.turn_id} requestId={entry.model_request_id}
        callId={entry.tool_call_id} running={running} />
    </div>}
    {entry.research && <ResearchActivity research={entry.research} links={links} />}
    {entry.discussion && <DiscussionActivity discussion={entry.discussion} links={links} />}
  </div>;
}

function CopyReply({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState("");
  const reset = useRef<number | undefined>(undefined);
  useEffect(() => () => window.clearTimeout(reset.current), []);
  const copy = async () => {
    try {
      if (!navigator.clipboard?.writeText) throw new Error("浏览器不支持剪贴板写入。");
      await navigator.clipboard.writeText(text);
      setCopied(true); setError("");
      window.clearTimeout(reset.current);
      reset.current = window.setTimeout(() => setCopied(false), 1600);
    } catch (e) {
      setCopied(false);
      setError(e instanceof Error ? `复制失败：${e.message}` : "复制失败，请检查浏览器剪贴板权限。");
    }
  };
  return <>
    <button type="button" className="chat-reply-action" aria-label="复制回复原文" title="复制回复原文" onClick={() => void copy()}>
      {copied ? <Check size={15} aria-hidden="true" /> : <Copy size={15} aria-hidden="true" />}
    </button>
    {error && <span className="chat-copy-error" role="alert">{error}</span>}
    {copied && <span className="sr-only" role="status">已复制回复原文</span>}
  </>;
}
