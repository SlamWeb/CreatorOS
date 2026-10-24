import { useEffect, useRef, useState, type ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { ArrowLeft, ArrowRight, Maximize2, MessageSquare, MoreHorizontal, RotateCcw, Trash2 } from "lucide-react";
import { ApiError, apiUrl, studioApi } from "../api/client";
import { useRunEvents } from "../api/useRunEvents";
import type { CardView, PartialCardView, RevisionView, RunDetail } from "../api/types";
import { RunControls } from "./RunControls";
import { StatusPill, formatDate } from "./StatusPill";
import { SkillInspector } from "./SkillInspector";
import { SkillReferenceButton } from "./SkillReferenceButton";
import type { ProducerSkillItem } from "../api/types";
import { RunDiscussion } from "./RunDiscussion";
import { PublicationPanel } from "./PublicationPanel";
import { publicationApi } from "../api/publication";
import { useAgentChat } from "./AgentChatHost";
import { ArtifactDownload } from "./ArtifactDownload";
import "./run-inspector.css";

type RunSkillSnapshot = { id: string; name: string; role: string; digest: string | null };
function runSkills(run: RunDetail): RunSkillSnapshot[] {
  const snapshot = run.input_snapshot as {
    skill_name?: unknown; skill_digest?: unknown;
    composition?: { mind?: { id?: unknown; name?: unknown; digest?: unknown }; production?: { id?: unknown; name?: unknown; digest?: unknown } };
  };
  if (snapshot.composition) return (["mind", "production"] as const).flatMap(role => {
    const item = snapshot.composition?.[role];
    return typeof item?.id === "string" ? [{ id: item.id, name: typeof item.name === "string" ? item.name : item.id,
      role, digest: typeof item.digest === "string" ? item.digest : null }] : [];
  });
  return typeof snapshot.skill_name === "string" ? [{ id: snapshot.skill_name, name: snapshot.skill_name,
    role: "production", digest: typeof snapshot.skill_digest === "string" ? snapshot.skill_digest : null }] : [];
}

export function RunInspector({ run }: { run: RunDetail }) {
  const agentChat = useAgentChat();
  const [params] = useSearchParams();
  const requestedReturn = params.get("return");
  const requestedRevision = params.get("revision");
  // Only return to our own content workspace, never an arbitrary external URL.
  const returnTo = requestedReturn && /^\/(?:\?|$|series\/[^/?#]+(?:\?|$))/.test(requestedReturn)
    ? requestedReturn : `/series/${run.series_id}`;
  const [selected, setSelected] = useState<string | null>(() => requestedRevision);
  useEffect(() => { setSelected(requestedRevision); }, [run.id, requestedRevision]);
  const [inspectedSkill, setInspectedSkill] = useState<ProducerSkillItem | null>(null);
  const [panel, setPanel] = useState<"copy" | "discussion" | "publish">(() => params.get("discussion") ? "discussion" : params.get("publish") ? "publish" : "copy");
  const [manualOpen, setManualOpen] = useState(false);
  const publicationRequest = useQuery({ queryKey: ["publication-request", run.id], queryFn: () => publicationApi.forRun(run.id), enabled: false });
  const [historyOpen, setHistoryOpen] = useState(false);
  const [moreOpen, setMoreOpen] = useState(false);
  useEffect(() => { setMoreOpen(false); setHistoryOpen(false); }, [run.id]);
  useEffect(() => { if (params.get("discussion")) setPanel("discussion"); }, [params]);
  const skills = useQuery({ queryKey: ["producer-skills"], queryFn: studioApi.producerSkills, retry: false,
    enabled: runSkills(run).length > 0 });
  const usedSkills = runSkills(run);
  const frozenCreatorId = typeof run.input_snapshot.creator_id === "string" ? run.input_snapshot.creator_id : null;
  const collectionLink = frozenCreatorId ? `/?${new URLSearchParams({ creator: frozenCreatorId, view: "materials", collection_topic: run.topic_id })}` : null;
  const observationNode = btoa(JSON.stringify(["run", run.id])).replaceAll("+", "-").replaceAll("/", "_").replace(/=+$/, "");
  const { events, connection } = useRunEvents(run.id, ["queued", "producing", "validating", "running"].includes(run.status));
  const revision = run.revisions.find((item) => selected ? item.id === selected : item.revision_number === run.active_revision_number);
  const old = revision ? revision.revision_number !== run.active_revision_number : false;
  const missingRevision = Boolean(selected) && !revision;
  const current = !old && !missingRevision;
  const latestRun = !run.latest_run_id || run.latest_run_id === run.id;
  const canShowPublication = current && latestRun && !!revision?.publish_copy;
  const discuss = () => agentChat.open({ creatorId: run.creator_id, accountName: run.creator_name,
    draftSeed: `我想讨论作品「${run.topic_title}」${revision ? `的第 ${revision.revision_number} 版` : ""}。\nRun ID：${run.id}\n${revision ? `Revision ID：${revision.id}\n` : ""}请先查看这版实际图片，再和我讨论；不要自动改 Skill 或生产。\n我的问题：` });
  const discussionButton = <button type="button" className="button button-secondary" onClick={discuss}><MessageSquare size={16} aria-hidden="true" />讨论</button>;
  const partialCards = current && !revision?.cards.length ? [...(run.partial_cards ?? [])].sort((a, b) => a.order - b.order) : [];
  const eventNames: Record<string, string> = { created: "创建内容任务", started: "开始生产", resumed: "恢复生产", produced: "产物已返回", validated: "文件检查通过", approved: "人工批准", revision_requested: "提出返工", interrupted: "执行中断", failed: "执行失败", cancelled: "取消任务" };
  return <article className="run-workbench">
    <header className="run-heading">
      <div className="run-breadcrumb"><Link className="button button-secondary run-back" to={returnTo}><ArrowLeft size={16} />返回栏目</Link><span>{run.creator_name} / {run.series_name}</span>
        {current && latestRun && <button type="button" className="button button-quiet run-more-trigger" aria-expanded={moreOpen} aria-controls="run-more-actions" onClick={() => setMoreOpen(value => !value)}><MoreHorizontal size={18} aria-hidden="true" />更多</button>}
      </div>
      <div className="run-title-row"><h1>{run.topic_title}</h1>{run.publication ? <span className="status-active">已发布</span> : <StatusPill status={run.status} />}</div>
    </header>
    {current && latestRun && <div id="run-more-actions" className="run-more-actions" hidden={!moreOpen}><RunRemoval run={run} returnTo={returnTo} /></div>}
    {old ? <div className="review-warning">正在查看历史版本，仅供对照，不能批准或修改。<button className="text-link" onClick={() => setSelected(null)}>返回当前版本 →</button></div> : null}
    {run.latest_run_id && run.latest_run_id !== run.id ? <div className="review-warning">这篇内容已从头重做。<Link to={`/runs/${run.latest_run_id}?return=${encodeURIComponent(returnTo)}`}>查看最新制作</Link></div> : null}
    {missingRevision ? <div className="review-warning" role="alert">找不到这个内容版本。<button className="text-link" onClick={() => setSelected(null)}>返回当前版本</button></div> : null}
    {run.error_message ? <p className="review-warning" role="alert">{run.error_message}</p> : null}
    <div className="inspector-grid">
      <section className="inspector-visual" aria-label="产物图片">
        <div className="run-visual-toolbar">
          {collectionLink && <Link className="button button-secondary" to={collectionLink}>素材文件夹</Link>}
          <select aria-label="内容版本" value={revision?.id ?? ""} onChange={(e) => setSelected(e.target.value)} disabled={!run.revisions.length}>{missingRevision ? <option value="" disabled>版本不存在</option> : null}{[...run.revisions].reverse().map((item) => <option key={item.id} value={item.id}>第 {item.revision_number} 版{item.revision_number === run.active_revision_number ? " · 当前" : " · 历史"}</option>)}</select>
        {revision?.cards.length && revision.review_digest ? <ArtifactDownload key={`${run.id}-${revision.id}-${revision.review_digest}`} runId={run.id} title={run.topic_title} revision={revision} /> : null}
        {revision?.cards.length && revision.review_digest ? <Link className="button button-secondary" to={`/calibrate?run=${encodeURIComponent(run.id)}&revision=${encodeURIComponent(revision.id)}`}>改进 Skill</Link> : null}
        </div>
        {revision?.cards.length ? <Carousel key={revision.id} cards={revision.cards} /> : partialCards.length ? null : <div className="artifact-empty"><h2>{revision?.artifact_error ? "图片无法读取" : run.status === "producing" ? "正在制作图片" : "还没有图片"}</h2>{revision?.artifact_error ? <p role="alert">{revision.artifact_error}</p> : null}</div>}
        {partialCards.length ? <PartialPreviews pages={partialCards} /> : null}
      </section>
      <aside className="inspector-copy">
        <section className="run-review-console" aria-label="验收与返工">
          <h2>{old ? "历史版本" : "验收与返工"}</h2>
          {!old && revision ? <ReviewActions key={`${run.id}-${revision.id}`} run={run} revision={revision} onRevision={() => setSelected(null)}
            discussion={discussionButton}
            publishAction={run.status === "approved" && canShowPublication ? <button type="button" className="button button-primary" onClick={() => setPanel("publish")}>{run.publication ? "查看发布" : "去发布"}</button> : undefined}
            freshAction={current && run.allowed_actions.includes("fresh") ? <FreshProductionAction key={`${run.id}-${run.version}`} run={run} returnTo={returnTo} /> : undefined} />
            : <div className="run-primary-actions">{discussionButton}</div>}
          {current ? <RunControls run={run} compact /> : null}
        </section>
        {usedSkills.length ? <section className="run-skill-links" aria-label="本次运行使用的 Skill">
          {usedSkills.map(item => {
            const current = skills.data?.items.find(skill => skill.id === item.id);
            return <SkillReferenceButton key={`${item.role}-${item.id}`} id={item.id} known={current} fallback={item.name}
              className="run-skill-link" onInspect={setInspectedSkill} />;
          })}
          {skills.isError ? <button type="button" className="text-link" onClick={() => void skills.refetch()}>Skill 读取失败，重试</button> : null}
        </section> : null}
        <div className="run-panel-tabs" aria-label="内容面板">
          <button type="button" aria-pressed={panel === "copy"} onClick={() => setPanel("copy")}>文案</button>
          {panel === "discussion" && <button type="button" aria-pressed onClick={() => setPanel("discussion")}>旧讨论</button>}
          {canShowPublication ? <button type="button" aria-pressed={panel === "publish"} onClick={() => setPanel("publish")}>发布</button> : null}
        </div>
        <div hidden={panel !== "copy"}>
          {revision?.publish_copy ? <Publication revision={revision} /> : <p className="run-empty-copy">文案尚未生成</p>}
        </div>
        {panel === "discussion" ? <div>{revision ? <RunDiscussion key={revision.id} run={run} revision={revision} focusId={params.get("discussion")} readOnly /> : null}</div> : null}
        {canShowPublication && revision ? <div hidden={panel !== "publish"}>
          <PublicationPanel key={`${run.id}-${revision.id}`} run={run} revision={revision} active={panel === "publish"} />
          {run.status === "approved" && publicationRequest.isSuccess && !publicationRequest.data ? <section className="manual-publication-fallback"><button className="button button-quiet" type="button" aria-expanded={manualOpen} onClick={() => setManualOpen(!manualOpen)}>{manualOpen ? "收起手动发布记录" : "手动发布记录"}</button>{manualOpen ? <ManualPublicationPanel run={run} revision={revision} /> : null}</section> : null}
        </div> : null}
      </aside>
    </div>
    <section className="run-records">
      <button className="button button-quiet" type="button" aria-expanded={historyOpen} aria-controls="run-history" onClick={() => setHistoryOpen(!historyOpen)}>{historyOpen ? "收起技术记录" : "技术记录"}</button>
      <div id="run-history" hidden={!historyOpen}>
      <nav className="run-record-links" aria-label="技术记录入口">
      <Link className="button button-quiet" to={`/observation?node=${encodeURIComponent(observationNode)}`}>Observation</Link>
      <button type="button" className="button button-quiet" onClick={() => setPanel("discussion")}>旧讨论记录</button>
      </nav>
      <span className="stream-status" role="status">{connection}</span>
      {revision?.instruction ? <div className="revision-note"><h3>本版返工要求</h3><p>{revision.instruction}</p></div> : null}
      <div className="inspector-history"><section><h3>状态时间线</h3><ol className="event-timeline">{events.map((event) => <li key={event.id}><time>{formatDate(event.created_at)}</time><span>{eventNames[event.event_type] ?? event.event_type}</span></li>)}</ol></section>
        <section><h3>第 {revision?.revision_number} 版 · 执行尝试</h3>{revision?.attempts.map((attempt) => <div className="attempt-detail" key={attempt.id}><div><b>尝试 {attempt.attempt_number}</b><StatusPill status={attempt.status} /></div><p>{formatDate(attempt.started_at)} · {attempt.duration_ms !== null ? `${Math.round(attempt.duration_ms / 1000)} 秒` : "耗时未记录"}</p><p>{attempt.error_message}</p><dl><dt>Token 用量</dt><dd>{attempt.usage ? JSON.stringify(attempt.usage) : "未记录"}</dd><dt>生产日志</dt><dd>{attempt.trace_available ? "已保存" : "未记录"}</dd></dl></div>)}<dl className="technical-ids"><dt>Run</dt><dd>{run.id}</dd><dt>Thread</dt><dd>{run.producer_thread_id ?? "未记录"}</dd><dt>产物摘要</dt><dd>{revision?.artifact_digest ?? "未记录"}</dd></dl></section></div>
      <dl className="technical-ids">{usedSkills.map(item => <div key={`${item.role}-${item.id}`}><dt>{item.name} · 冻结摘要</dt><dd>{item.digest ?? "未记录"}</dd></div>)}</dl>
      </div>
    </section>
    {inspectedSkill && <SkillInspector key={inspectedSkill.id} skill={inspectedSkill} onClose={() => setInspectedSkill(null)}
      editContext={{ creatorId: run.creator_id, seriesId: run.series_id, runId: run.id, revisionId: revision?.id }} />}
  </article>;
}

function FreshProductionAction({ run, returnTo }: { run: RunDetail; returnTo: string }) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [confirming, setConfirming] = useState(false);
  const [created, setCreated] = useState<string | null>(null);
  const requestId = useRef<string | null>(null);
  const mounted = useRef(false);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  const submit = useMutation({ retry: false,
    mutationFn: async () => {
      const key = `creatoros.fresh-production:${run.id}:${run.version}`;
      if (!requestId.current) {
        try { requestId.current = sessionStorage.getItem(key); } catch { /* Optional storage. */ }
        requestId.current ??= crypto.randomUUID();
        try { sessionStorage.setItem(key, requestId.current); } catch { /* Retain in memory. */ }
      }
      const next = await studioApi.startRun({ topic_id: run.topic_id, mode: "fresh", source_run_id: run.id,
        expected_version: run.version, request_id: requestId.current });
      if (mounted.current) setCreated(next.id);
      // Creation replay may already have finished. Never restart that attempt.
      if (next.status === "queued" && next.active_revision_number === 1 && !next.revisions[0]?.attempts.length) {
        return studioApi.executeRun(next.id, next.version);
      }
      return next;
    },
    onSuccess: next => {
      queryClient.setQueryData(["run", next.id], next);
      void queryClient.invalidateQueries();
      if (mounted.current) navigate(`/runs/${next.id}?return=${encodeURIComponent(returnTo)}`);
    },
  });
  return <div className="fresh-production">
    <button className="button button-secondary" type="button" disabled={submit.isPending} onClick={() => setConfirming(true)}><RotateCcw size={16} aria-hidden="true" />从头重做</button>
    {confirming ? <div className="fresh-production-confirm" role="group" aria-label="确认从头重做">
      <p>同一选题，使用最新 Skill 和全新对话。旧图与记录保留。</p>
      <div className="form-actions"><button type="button" className="button button-primary" disabled={submit.isPending} onClick={() => submit.mutate()}>{submit.isPending ? "正在提交…" : submit.isError ? "核对并继续同一请求" : "确认重做"}</button><button type="button" className="button button-secondary" disabled={submit.isPending} onClick={() => setConfirming(false)}>取消</button></div>
      {submit.isError ? <p className="form-error" role="alert">{submit.error.message} 不会自动重试。{created ? <Link to={`/runs/${created}`}>查看已创建任务</Link> : "可用同一请求核对，不会重复创建。"}</p> : null}
    </div> : null}
  </div>;
}

function PartialPreviews({ pages }: { pages: PartialCardView[] }) {
  return <section className="partial-previews" aria-label="未验收阶段预览">
    <h2>制作中预览 · 尚未验收</h2>
    <div className="partial-preview-grid">{pages.map((page) => <PartialPreview key={page.order} page={page} />)}</div>
  </section>;
}

function PartialPreview({ page }: { page: PartialCardView }) {
  const [failed, setFailed] = useState(false);
  return <figure className="partial-preview-card">
    <figcaption>第 {page.order} 页 · 尚未验收</figcaption>
    {failed ? <p role="alert">图片暂时无法读取，请刷新运行详情重新检查。</p> : <img src={apiUrl(page.image_url)} alt={`第 ${page.order} 页制作中预览，尚未验收`} onError={() => setFailed(true)} />}
    {page.warnings.length ? <ul aria-label={`第 ${page.order} 页提示`}>{page.warnings.map((warning, index) => <li key={`${index}-${warning}`}>{warning}</li>)}</ul> : null}
  </figure>;
}

function ManualPublicationPanel({ run, revision }: { run: RunDetail; revision: RevisionView }) {
  const queryClient = useQueryClient();
  const [postUrl, setPostUrl] = useState("");
  const [metrics, setMetrics] = useState({ views: "", likes: "", favorites: "", comments: "", shares: "" });
  const [metricRequestId, setMetricRequestId] = useState(() => crypto.randomUUID());
  const [receipt, setReceipt] = useState("");
  const publish = useMutation({
    mutationFn: () => studioApi.recordPublication(run.id, {
      expected_version: run.version, revision_id: revision.id,
      artifact_digest: revision.artifact_digest ?? "", post_url: postUrl.trim(),
    }),
    onSuccess: (result) => { queryClient.setQueryData(["run", run.id], result); setReceipt("发布链接已登记；接下来可回填效果数据。"); void queryClient.invalidateQueries(); },
  });
  const feedback = useMutation({
    mutationFn: () => studioApi.addPublicationMetrics(run.id, {
      request_id: metricRequestId,
      ...Object.fromEntries(Object.entries(metrics).filter(([, value]) => value !== "").map(([key, value]) => [key, Number(value)])),
    }),
    onSuccess: (result) => {
      queryClient.setQueryData(["run", run.id], result);
      setMetrics({ views: "", likes: "", favorites: "", comments: "", shares: "" });
      setMetricRequestId(crypto.randomUUID());
      setReceipt("数据已保存");
      void queryClient.invalidateQueries();
    },
  });
  const publication = run.publication;
  return <section className="manual-publication">
    <h2>发布</h2>
    <a className="button button-quiet" href={apiUrl(`/api/runs/${run.id}/download`)}>下载已批准图片包</a>
    {!publication ? <>
      <form onSubmit={(event) => { event.preventDefault(); publish.mutate(); }}>
        <label>发布后粘贴笔记链接<input type="url" required value={postUrl} onChange={(event) => setPostUrl(event.target.value)} placeholder="https://www.xiaohongshu.com/explore/…" /></label>
        <button className="button button-primary" disabled={publish.isPending || !postUrl.trim()}>登记为已发布</button>
      </form>
    </> : <>
      <p>已人工发布 · {formatDate(publication.published_at)} · <a href={publication.post_url} target="_blank" rel="noopener noreferrer">打开笔记 ↗</a></p>
      <form onSubmit={(event) => { event.preventDefault(); feedback.mutate(); }}>
        <h3>回填笔记数据</h3>
        <div className="feedback-fields">{(["views", "likes", "favorites", "comments", "shares"] as const).map((key) => <label key={key}>{({ views: "阅读", likes: "点赞", favorites: "收藏", comments: "评论", shares: "分享" })[key]}<input type="number" min="0" step="1" value={metrics[key]} onChange={(event) => setMetrics((old) => ({ ...old, [key]: event.target.value }))} /></label>)}</div>
        <button className="button button-primary" disabled={feedback.isPending || Object.values(metrics).every((value) => value === "")}>保存这次数据</button>
      </form>
      {publication.metrics.length ? <details><summary>历史回填 · {publication.metrics.length} 次</summary><ul>{publication.metrics.map((item) => <li key={item.id}>{formatDate(item.measured_at)}：阅读 {item.views ?? "—"} · 赞 {item.likes ?? "—"} · 收藏 {item.favorites ?? "—"} · 评论 {item.comments ?? "—"} · 分享 {item.shares ?? "—"}</li>)}</ul></details> : null}
    </>}
    {receipt ? <p role="status" className="copy-receipt">{receipt}</p> : null}
    {publish.isError || feedback.isError ? <p role="alert" className="form-error">{publish.error?.message ?? feedback.error?.message} 请刷新后核对状态；系统不会自动重复提交。</p> : null}
  </section>;
}

function Carousel({ cards }: { cards: CardView[] }) {
  const [index, setIndex] = useState(0);
  const [failed, setFailed] = useState(false);
  const [evidenceOpen, setEvidenceOpen] = useState(false);
  const dialog = useRef<HTMLDialogElement>(null);
  const card = cards[Math.min(index, cards.length - 1)];
  useEffect(() => { setFailed(false); setEvidenceOpen(false); }, [card.url]);
  const move = (delta: number) => setIndex((value) => (value + delta + cards.length) % cards.length);
  return <div className="carousel" onKeyDown={(e) => { if (e.key === "ArrowLeft") { e.preventDefault(); move(-1); } if (e.key === "ArrowRight") { e.preventDefault(); move(1); } }}>
    <div className="carousel-stage">{failed ? <p className="review-warning">图片无法读取或已变化，请刷新详情重新检查。</p> : <button className="image-open" aria-label={`放大第 ${card.order} 张图片`} onClick={() => dialog.current?.showModal()}><img src={apiUrl(card.url)} alt={card.headline} width={card.width} height={card.height} onError={() => setFailed(true)} /></button>}</div>
    <div className="carousel-nav"><div className="carousel-paging">{cards.length > 1 ? <button aria-label="上一张" onClick={() => move(-1)}><ArrowLeft size={16} /></button> : null}<span>{card.order} / {cards.length}</span>{cards.length > 1 ? <button aria-label="下一张" onClick={() => move(1)}><ArrowRight size={16} /></button> : null}</div><button aria-label="放大图片" onClick={() => dialog.current?.showModal()}><Maximize2 size={16} /></button></div>
    {cards.length > 1 ? <div className="carousel-thumbnails" aria-label="选择图片">{cards.map((item, i) => <button key={item.order} aria-label={`查看第 ${item.order} 张`} aria-pressed={i === index} onClick={() => setIndex(i)}><img src={apiUrl(item.url)} alt="" loading="lazy" /><span>{item.order.toString().padStart(2, "0")}</span></button>)}</div> : null}
    {card.page_spec?.trim() || card.image_prompt?.trim() ? <section className="page-evidence" key={card.order}>
      <button className="button button-quiet" type="button" aria-expanded={evidenceOpen} aria-controls={`page-evidence-${card.order}`} onClick={() => setEvidenceOpen(!evidenceOpen)}>本页内容与生图 Prompt</button>
      <div id={`page-evidence-${card.order}`} hidden={!evidenceOpen}>
      {card.page_spec?.trim() ? <><h3>内容稿</h3><pre>{card.page_spec}</pre></> : null}
      {card.image_prompt?.trim() ? <><h3>生图 Prompt</h3><pre>{card.image_prompt}</pre></> : null}
      <p>由生产器报告；不代表图像服务内部改写后的 Prompt。</p>
      </div>
    </section> : null}
    <dialog className="image-dialog" ref={dialog} aria-label="放大图片"><button className="dialog-close" onClick={() => dialog.current?.close()}>关闭 ×</button><img src={apiUrl(card.url)} alt={card.headline} /><div className="dialog-nav"><button onClick={() => move(-1)}>← 上一张</button><span>{card.order} / {cards.length}</span><button onClick={() => move(1)}>下一张 →</button></div></dialog>
  </div>;
}

function Publication({ revision }: { revision: RevisionView }) {
  const [copied, setCopied] = useState("");
  const copy = revision.publish_copy!;
  const copyText = async (value: string, label: string) => {
    try { await navigator.clipboard.writeText(value); setCopied(`${label}已复制`); }
    catch { setCopied("复制失败，请选中文案手动复制。"); }
  };
  return <section className="publication" aria-label="发布文案"><div className="publication-head"><button className="button button-secondary" onClick={() => void copyText(`${copy.title}\n\n${copy.body}\n\n${copy.hashtags.join(" ")}`, "文案")}>复制全部</button></div><h3>{copy.title}</h3><p className="publication-body">{copy.body}</p><p className="hashtags">{copy.hashtags.join(" ")}</p>{copied ? <p className="copy-receipt" role="status">{copied}</p> : null}{revision.sources.length ? <details className="sources"><summary>参考来源 · {revision.sources.length}</summary><ul>{revision.sources.map((source, i) => <li key={i}>{source.url ? <a href={source.url} target="_blank" rel="noopener noreferrer">{source.title} ↗</a> : source.title}</li>)}</ul></details> : null}</section>;
}

function ReviewActions({ run, revision, onRevision, discussion, freshAction, publishAction }: {
  run: RunDetail; revision: RevisionView; onRevision: () => void; discussion: ReactNode; freshAction?: ReactNode; publishAction?: ReactNode;
}) {
  const queryClient = useQueryClient();
  const [instruction, setInstruction] = useState("");
  const [editing, setEditing] = useState(false);
  const [receipt, setReceipt] = useState("");
  const mutation = useMutation({
    mutationFn: async (action: "approve" | "revise") => {
      if (action === "approve") return studioApi.approveRun(run.id, { expected_version: run.version, revision_id: revision.id, artifact_digest: revision.review_digest! });
      return studioApi.reviseRun(run.id, { expected_version: run.version, instruction });
    },
    onSuccess: (result, action) => {
      queryClient.setQueryData(["run", run.id], result);
      setReceipt(action === "revise" ? "返工要求已保存，点击开始生产后才会调用 Codex。" : "已批准 · 尚未发布");
      if (action === "revise") { setEditing(false); setInstruction(""); onRevision(); }
    },
    onSettled: () => queryClient.invalidateQueries(),
  });
  const conflict = mutation.error instanceof ApiError && mutation.error.status === 409;
  return <div className="review-actions">
    <div className="run-primary-actions">
    {publishAction}
    {run.allowed_actions.includes("approve") ? <button className="button button-primary approve-button" disabled={mutation.isPending || conflict || !revision.review_digest || !revision.cards.length} onClick={() => mutation.mutate("approve")}>批准第 {revision.revision_number} 版</button> : null}
    {discussion}
    </div>
    {run.allowed_actions.includes("revise") || freshAction ? <div className="run-rework-actions">
    {run.allowed_actions.includes("revise") ? <button className="button button-secondary" disabled={mutation.isPending || conflict} aria-expanded={editing} onClick={() => setEditing(!editing)}>提出返工</button> : null}
    {freshAction}
    </div> : null}
    {run.status === "approved" ? <p className="approval-receipt">{run.publication ? "已发布" : "已批准 · 尚未发布"}</p> : null}
    {editing ? <form onSubmit={(e) => { e.preventDefault(); mutation.mutate("revise"); }}><label>告诉生产者要改哪里<textarea autoFocus value={instruction} onChange={(e) => setInstruction(e.target.value)} maxLength={10_000} rows={4} placeholder="例如：第二张的例子太抽象，换成点餐场景。" /></label><div className="form-actions"><button className="button button-primary" disabled={!instruction.trim() || mutation.isPending || conflict}>保存返工要求</button><button className="button button-quiet" type="button" onClick={() => setEditing(false)}>收起</button></div><p className="approval-note">保留旧图，创建新版本；现在不会开始生产。</p></form> : null}
    {receipt && run.status !== "approved" ? <p className="copy-receipt" role="status">{receipt}</p> : null}
    {mutation.isError ? <div className="form-error" role="alert">{conflict ? "内容已变化，请重新检查；本次操作没有被自动重试。" : mutation.error.message}<button className="text-link" onClick={async () => { await queryClient.refetchQueries({ queryKey: ["run", run.id] }); mutation.reset(); }}>重新检查</button></div> : null}
  </div>;
}

function RunRemoval({ run, returnTo }: { run: RunDetail; returnTo: string }) {
  const navigate = useNavigate();
  const client = useQueryClient();
  const [confirming, setConfirming] = useState(false);
  const requestId = useRef(crypto.randomUUID().replaceAll("-", ""));
  const mounted = useRef(false);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  const remove = useMutation({ retry: false,
    mutationFn: () => studioApi.removeTopic(run.topic_id, { request_id: requestId.current }),
    onSuccess: async () => { await client.invalidateQueries(); if (mounted.current) navigate(returnTo); },
  });
  return <div className="run-removal">
    <button type="button" className="button button-danger" aria-label="删除该条内容" disabled={remove.isPending} onClick={() => setConfirming(true)}><Trash2 size={16} /><span>删除</span></button>
    {confirming ? <div className="run-remove-confirm" role="group" aria-label="确认删除内容">
      <p>从栏目移除这条内容？图片和生产记录会保留。</p>
      <div className="form-actions"><button type="button" className="button button-danger" disabled={remove.isPending} onClick={() => remove.mutate()}>{remove.isPending ? "正在删除…" : remove.isError ? "重试删除" : "确认删除"}</button><button type="button" className="button button-secondary" disabled={remove.isPending} onClick={() => { setConfirming(false); remove.reset(); }}>取消</button></div>
      {remove.isError ? <p className="form-error" role="alert">{remove.error.message}</p> : null}
    </div> : null}
  </div>;
}
