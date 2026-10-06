import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router-dom";
import { ApiError, apiUrl, studioApi } from "../api/client";
import { useRunEvents } from "../api/useRunEvents";
import type { CardView, PartialCardView, RevisionView, RunDetail } from "../api/types";
import { RunControls } from "./RunControls";
import { StatusPill, formatDate } from "./StatusPill";
import { SkillInspector } from "./SkillInspector";
import type { ProducerSkillItem } from "../api/types";

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
  const [params] = useSearchParams();
  const requestedReturn = params.get("return");
  // Only return to our own content workspace, never an arbitrary external URL.
  const returnTo = requestedReturn && /^\/(?:\?|$|series\/[^/?#]+(?:\?|$))/.test(requestedReturn)
    ? requestedReturn : `/series/${run.series_id}`;
  const [selected, setSelected] = useState<string | null>(null);
  const [inspectedSkill, setInspectedSkill] = useState<ProducerSkillItem | null>(null);
  const skills = useQuery({ queryKey: ["producer-skills"], queryFn: studioApi.producerSkills, retry: false,
    enabled: runSkills(run).length > 0 });
  const usedSkills = runSkills(run);
  const { events, connection } = useRunEvents(run.id, ["queued", "producing", "validating", "running"].includes(run.status));
  const revision = run.revisions.find((item) => selected ? item.id === selected : item.revision_number === run.active_revision_number);
  const old = revision ? revision.revision_number !== run.active_revision_number : false;
  const partialCards = !old && !revision?.cards.length ? [...(run.partial_cards ?? [])].sort((a, b) => a.order - b.order) : [];
  const eventNames: Record<string, string> = { created: "创建内容任务", started: "开始生产", resumed: "恢复生产", produced: "产物已返回", validated: "文件检查通过", approved: "人工批准", revision_requested: "提出返工", interrupted: "执行中断", failed: "执行失败", cancelled: "取消任务" };
  return <>
    <Link className="back-link" to={returnTo}>← 返回栏目</Link>
    <header className="page-heading inspector-heading"><div><p className="run-context">{run.creator_name} / {run.series_name}</p><h1>{run.topic_title}</h1></div>{run.publication ? <span className="status-active">● 已发布</span> : <StatusPill status={run.status} />}</header>
    <div className="inspector-toolbar"><label>内容版本 <select aria-label="内容版本" value={revision?.id ?? ""} onChange={(e) => setSelected(e.target.value)}>{[...run.revisions].reverse().map((item) => <option key={item.id} value={item.id}>第 {item.revision_number} 版{item.revision_number === run.active_revision_number ? " · 当前" : " · 历史"}</option>)}</select></label><span className="stream-status" role="status">{connection}</span></div>
    {old ? <div className="review-warning">正在查看历史版本，仅供对照，不能批准或修改。<button className="text-link" onClick={() => setSelected(null)}>返回当前版本 →</button></div> : null}
    <div className="inspector-grid">
      <section className="inspector-visual" aria-label="产物图片">
        {revision?.cards.length ? <><h2 className="visual-section-title">最终产物</h2><Carousel key={revision.id} cards={revision.cards} /></> : partialCards.length ? null : <div className="artifact-empty"><span className="artifact-empty-icon">▧</span><h2>{revision?.artifact_error ? "产物需要检查" : "图片尚未就绪"}</h2><p>{revision?.artifact_error ?? (run.status === "producing" ? "Codex 正在制作。你可以离开此页，稍后回来验收。" : "开始生产后，真实图片会出现在这里。")}</p></div>}
        {revision?.cards.length ? <p className="file-check">✓ 文件检查通过 · {revision.cards.length} 张图片可读取 <span>内容正确性请逐张验收</span></p> : null}
        {partialCards.length ? <PartialPreviews pages={partialCards} /> : null}
      </section>
      <aside className="inspector-copy">
        {revision?.content_summary ? <p className="content-summary">{revision.content_summary}</p> : null}
        {usedSkills.length ? <section className="run-used-skills" aria-label="本次运行使用的 Skill"><h2>本次运行使用的 Skill</h2>
          <p>下面可打开本地 Skill 库的当前版本查看或编辑。本次 Run 使用创建时冻结的版本，编辑只影响新 Run。</p>
          {usedSkills.map(item => {
            const current = skills.data?.items.find(skill => skill.id === item.id);
            return <div className="run-used-skill" key={`${item.role}-${item.id}`}><div><strong>{current?.name ?? item.name}</strong><span>{item.role === "mind" ? "内容 Skill" : "制作 Skill"}</span>
              <small>Run 冻结摘要：{item.digest ? `${item.digest.slice(0, 12)}…` : "旧记录未保存摘要"}</small></div>
              {skills.isError ? <span role="status">当前 Skill 库暂不可读</span> : current
                ? <button type="button" className="text-link" onClick={() => setInspectedSkill(current)}>打开当前库版本 ↗</button>
                : skills.isPending ? <span role="status">正在读取 Skill 库…</span> : <span>当前库中未登记；Run 冻结版本仍保留</span>}
            </div>;
          })}
        </section> : null}
        {run.error_message ? <p className="review-warning">{run.error_message}</p> : null}
        {revision?.publish_copy ? <Publication revision={revision} /> : <div className="copy-empty"><h2>发布文案</h2><p>产物生成后展示标题、正文与标签。</p></div>}
        {revision?.instruction ? <div className="revision-note"><h3>本版返工要求</h3><p>{revision.instruction}</p></div> : null}
        {!old && revision ? <ReviewActions run={run} revision={revision} onRevision={() => setSelected(null)} /> : null}
        {!old && revision && run.status === "approved" ? <ManualPublicationPanel run={run} revision={revision} /> : null}
      </aside>
    </div>
    <details className="inspector-details"><summary>生产记录与技术详情 <span>{events.length} 条事件</span></summary>
      <div className="inspector-history"><section><h3>状态时间线</h3><ol className="event-timeline">{events.map((event) => <li key={event.id}><time>{formatDate(event.created_at)}</time><span>{eventNames[event.event_type] ?? event.event_type}</span></li>)}</ol></section>
        <section><h3>第 {revision?.revision_number} 版 · 执行尝试</h3>{revision?.attempts.map((attempt) => <div className="attempt-detail" key={attempt.id}><div><b>尝试 {attempt.attempt_number}</b><StatusPill status={attempt.status} /></div><p>{formatDate(attempt.started_at)} · {attempt.duration_ms !== null ? `${Math.round(attempt.duration_ms / 1000)} 秒` : "耗时未记录"}</p><p>{attempt.error_message}</p><dl><dt>Token 用量</dt><dd>{attempt.usage ? JSON.stringify(attempt.usage) : "未记录"}</dd><dt>生产日志</dt><dd>{attempt.trace_available ? "已保存" : "未记录"}</dd></dl></div>)}<dl className="technical-ids"><dt>Run</dt><dd>{run.id}</dd><dt>Thread</dt><dd>{run.producer_thread_id ?? "未记录"}</dd><dt>产物摘要</dt><dd>{revision?.artifact_digest ?? "未记录"}</dd></dl></section></div>
    </details>
    {inspectedSkill && <SkillInspector key={inspectedSkill.id} skill={inspectedSkill} onClose={() => setInspectedSkill(null)} />}
  </>;
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
      setReceipt("这次反馈已保存。以后可以再次回填，形成时间序列。");
      void queryClient.invalidateQueries();
    },
  });
  const publication = run.publication;
  return <section className="manual-publication">
    <h2>人工发布与反馈</h2>
    <a className="button button-quiet" href={apiUrl(`/api/runs/${run.id}/download`)}>下载已批准图片包</a>
    {!publication ? <>
      <p>产物已批准，但还没登记发布。请先下载图片，在自己的小红书账号手动发布。</p>
      <form onSubmit={(event) => { event.preventDefault(); publish.mutate(); }}>
        <label>发布后粘贴笔记链接<input type="url" required value={postUrl} onChange={(event) => setPostUrl(event.target.value)} placeholder="https://www.xiaohongshu.com/explore/…" /></label>
        <button className="button button-primary" disabled={publish.isPending || !postUrl.trim()}>登记为已发布</button>
      </form>
      <p className="approval-note">这里只记录真实笔记链接，不会替你发布。</p>
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
  const dialog = useRef<HTMLDialogElement>(null);
  const card = cards[Math.min(index, cards.length - 1)];
  useEffect(() => { setFailed(false); }, [card.url]);
  const move = (delta: number) => setIndex((value) => (value + delta + cards.length) % cards.length);
  return <div className="carousel" onKeyDown={(e) => { if (e.key === "ArrowLeft") { e.preventDefault(); move(-1); } if (e.key === "ArrowRight") { e.preventDefault(); move(1); } }}>
    <div className="carousel-stage">{failed ? <p className="review-warning">图片无法读取或已变化，请刷新详情重新检查。</p> : <button className="image-open" aria-label={`放大第 ${card.order} 张图片`} onClick={() => dialog.current?.showModal()}><img src={apiUrl(card.url)} alt={card.headline} width={card.width} height={card.height} onError={() => setFailed(true)} /></button>}</div>
    <div className="carousel-nav"><button aria-label="上一张" onClick={() => move(-1)} disabled={cards.length < 2}>←</button><span>{card.order} / {cards.length} <b>{card.headline}</b></span><button aria-label="下一张" onClick={() => move(1)} disabled={cards.length < 2}>→</button></div>
    <div className="carousel-thumbnails" aria-label="选择图片">{cards.map((item, i) => <button key={item.order} aria-label={`查看第 ${item.order} 张`} aria-pressed={i === index} onClick={() => setIndex(i)}><img src={apiUrl(item.url)} alt="" loading="lazy" /><span>{item.order.toString().padStart(2, "0")}</span></button>)}</div>
    {card.page_spec?.trim() || card.image_prompt?.trim() ? <details className="page-evidence" key={card.order}>
      <summary>本页内容与生图 Prompt</summary>
      {card.page_spec?.trim() ? <><h3>内容稿</h3><pre>{card.page_spec}</pre></> : null}
      {card.image_prompt?.trim() ? <><h3>生图 Prompt</h3><pre>{card.image_prompt}</pre></> : null}
      <p>由生产器报告；不代表图像服务内部改写后的 Prompt。</p>
    </details> : null}
    <dialog className="image-dialog" ref={dialog} aria-label="放大图片"><button className="dialog-close" onClick={() => dialog.current?.close()}>关闭 ×</button><img src={apiUrl(card.url)} alt={card.headline} /><div className="dialog-nav"><button onClick={() => move(-1)}>← 上一张</button><span>{card.order} / {cards.length}</span><button onClick={() => move(1)}>下一张 →</button></div></dialog>
  </div>;
}

function Publication({ revision }: { revision: RevisionView }) {
  const [copied, setCopied] = useState("");
  const copy = revision.publish_copy!;
  const copyText = async (value: string, label: string) => {
    try { await navigator.clipboard.writeText(value); setCopied(`${label}已复制，尚未发布。`); }
    catch { setCopied("复制失败，请选中文案手动复制。"); }
  };
  return <section className="publication"><div className="publication-head"><h2>发布文案</h2><button className="text-link" onClick={() => void copyText(`${copy.title}\n\n${copy.body}\n\n${copy.hashtags.join(" ")}`, "文案")}>复制全部</button></div><h3>{copy.title}</h3><p className="publication-body">{copy.body}</p><p className="hashtags">{copy.hashtags.join(" ")}</p>{copied ? <p className="copy-receipt" role="status">{copied}</p> : null}{revision.sources.length ? <details className="sources"><summary>参考来源 · {revision.sources.length}</summary><ul>{revision.sources.map((source, i) => <li key={i}>{source.url ? <a href={source.url} target="_blank" rel="noopener noreferrer">{source.title} ↗</a> : source.title}</li>)}</ul></details> : null}</section>;
}

function ReviewActions({ run, revision, onRevision }: { run: RunDetail; revision: RevisionView; onRevision: () => void }) {
  const queryClient = useQueryClient();
  const [instruction, setInstruction] = useState("");
  const [editing, setEditing] = useState(false);
  const [receipt, setReceipt] = useState("");
  const mutation = useMutation({
    mutationFn: async (action: "approve" | "revise" | "cancel") => {
      if (action === "approve") return studioApi.approveRun(run.id, { expected_version: run.version, revision_id: revision.id, artifact_digest: revision.review_digest! });
      if (action === "revise") return studioApi.reviseRun(run.id, { expected_version: run.version, instruction });
      return studioApi.cancelRun(run.id, { expected_version: run.version });
    },
    onSuccess: (result, action) => {
      queryClient.setQueryData(["run", run.id], result);
      setReceipt(action === "revise" ? "返工要求已保存，点击开始生产后才会调用 Codex。" : action === "approve" ? "已批准 · 尚未发布" : "任务已取消，产物仍保留。");
      if (action === "revise") { setEditing(false); setInstruction(""); onRevision(); }
    },
    onSettled: () => queryClient.invalidateQueries(),
  });
  const conflict = mutation.error instanceof ApiError && mutation.error.status === 409;
  return <div className="review-actions">
    {run.status === "approved" ? <p className="approval-receipt">{run.publication ? "✓ 已发布 · 可继续回填数据" : "✓ 已批准 · 尚未发布"}</p> : null}
    {run.allowed_actions.includes("approve") ? <><button className="button button-primary approve-button" disabled={mutation.isPending || conflict || !revision.review_digest || !revision.cards.length} onClick={() => mutation.mutate("approve")}>批准第 {revision.revision_number} 版</button><p className="approval-note">请先检查全部图片。批准只记录验收，不会发布。</p></> : null}
    {run.allowed_actions.includes("revise") ? <button className="button button-quiet" disabled={mutation.isPending || conflict} onClick={() => setEditing(!editing)}>提出返工</button> : null}
    {editing ? <form onSubmit={(e) => { e.preventDefault(); mutation.mutate("revise"); }}><label>告诉生产者要改哪里<textarea autoFocus value={instruction} onChange={(e) => setInstruction(e.target.value)} maxLength={10_000} rows={4} placeholder="例如：第二张的例子太抽象，换成点餐场景。" /></label><div className="form-actions"><button className="button button-primary" disabled={!instruction.trim() || mutation.isPending || conflict}>保存返工要求</button><button className="button button-quiet" type="button" onClick={() => setEditing(false)}>收起</button></div><p className="approval-note">保留旧图，创建新版本；现在不会开始生产。</p></form> : null}
    {!run.allowed_actions.includes("approve") ? <RunControls run={run} /> : <details className="review-more"><summary>更多操作</summary><button className="button button-quiet" disabled={mutation.isPending || conflict} onClick={() => mutation.mutate("cancel")}>取消任务（保留产物）</button></details>}
    {receipt && run.status !== "approved" ? <p className="copy-receipt" role="status">{receipt}</p> : null}
    {mutation.isError ? <div className="form-error" role="alert">{conflict ? "内容已变化，请重新检查；本次操作没有被自动重试。" : mutation.error.message}<button className="text-link" onClick={async () => { await queryClient.refetchQueries({ queryKey: ["run", run.id] }); mutation.reset(); }}>重新检查</button></div> : null}
  </div>;
}
