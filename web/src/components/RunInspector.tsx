import { useEffect, useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { ApiError, apiUrl, studioApi } from "../api/client";
import { useRunEvents } from "../api/useRunEvents";
import type { CardView, RevisionView, RunDetail } from "../api/types";
import { RunControls } from "./RunControls";
import { StatusPill, formatDate } from "./StatusPill";

export function RunInspector({ run }: { run: RunDetail }) {
  const [selected, setSelected] = useState<string | null>(null);
  const { events, connection } = useRunEvents(run.id);
  const revision = run.revisions.find((item) => selected ? item.id === selected : item.revision_number === run.active_revision_number);
  const old = revision?.revision_number !== run.active_revision_number;
  const eventNames: Record<string, string> = { created: "创建内容任务", started: "开始生产", resumed: "恢复生产", produced: "产物已返回", validated: "文件检查通过", approved: "人工批准", revision_requested: "提出返工", interrupted: "执行中断", failed: "执行失败", cancelled: "取消任务" };
  return <>
    <Link className="back-link" to={`/series/${run.series_id}`}>← 返回栏目</Link>
    <header className="page-heading inspector-heading"><div><p className="section-kicker">{run.creator_name} / {run.series_name}</p><h1>{run.topic_title}</h1></div><StatusPill status={run.status} /></header>
    <div className="inspector-toolbar"><label>内容版本 <select aria-label="内容版本" value={revision?.id ?? ""} onChange={(e) => setSelected(e.target.value)}>{[...run.revisions].reverse().map((item) => <option key={item.id} value={item.id}>第 {item.revision_number} 版{item.revision_number === run.active_revision_number ? " · 当前" : " · 历史"}</option>)}</select></label><span className="stream-status" role="status">{connection}</span></div>
    {old ? <div className="review-warning">正在查看历史版本，仅供对照，不能批准或修改。<button className="text-link" onClick={() => setSelected(null)}>返回当前版本 →</button></div> : null}
    <div className="inspector-grid">
      <section className="inspector-visual" aria-label="产物图片">
        {revision?.cards.length ? <Carousel key={revision.id} cards={revision.cards} /> : <div className="artifact-empty"><span className="artifact-empty-icon">▧</span><h2>{revision?.artifact_error ? "产物需要检查" : "图片尚未就绪"}</h2><p>{revision?.artifact_error ?? (run.status === "producing" ? "Codex 正在制作。你可以离开此页，稍后回来验收。" : "开始生产后，真实图片会出现在这里。")}</p></div>}
        {revision?.cards.length ? <p className="file-check">✓ 文件检查通过 · {revision.cards.length} 张图片可读取 <span>内容正确性请逐张验收</span></p> : null}
      </section>
      <aside className="inspector-copy">
        {revision?.content_summary ? <p className="content-summary">{revision.content_summary}</p> : null}
        {run.error_message ? <p className="review-warning">{run.error_message}</p> : null}
        {revision?.publish_copy ? <Publication revision={revision} /> : <div className="copy-empty"><h2>发布文案</h2><p>产物生成后展示标题、正文与标签。</p></div>}
        {revision?.instruction ? <div className="revision-note"><h3>本版返工要求</h3><p>{revision.instruction}</p></div> : null}
        {!old && revision ? <ReviewActions run={run} revision={revision} onRevision={() => setSelected(null)} /> : null}
      </aside>
    </div>
    <details className="inspector-details"><summary>生产记录与技术详情 <span>{events.length} 条事件</span></summary>
      <div className="inspector-history"><section><h3>状态时间线</h3><ol className="event-timeline">{events.map((event) => <li key={event.id}><time>{formatDate(event.created_at)}</time><span>{eventNames[event.event_type] ?? event.event_type}</span></li>)}</ol></section>
        <section><h3>第 {revision?.revision_number} 版 · 执行尝试</h3>{revision?.attempts.map((attempt) => <div className="attempt-detail" key={attempt.id}><div><b>尝试 {attempt.attempt_number}</b><StatusPill status={attempt.status} /></div><p>{formatDate(attempt.started_at)} · {attempt.duration_ms !== null ? `${Math.round(attempt.duration_ms / 1000)} 秒` : "耗时未记录"}</p><p>{attempt.error_message}</p><dl><dt>Token 用量</dt><dd>{attempt.usage ? JSON.stringify(attempt.usage) : "未记录"}</dd><dt>生产日志</dt><dd>{attempt.trace_available ? "已保存" : "未记录"}</dd></dl></div>)}<dl className="technical-ids"><dt>Run</dt><dd>{run.id}</dd><dt>Thread</dt><dd>{run.producer_thread_id ?? "未记录"}</dd><dt>产物摘要</dt><dd>{revision?.artifact_digest ?? "未记录"}</dd></dl></section></div>
    </details>
  </>;
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
    {run.status === "approved" ? <p className="approval-receipt">✓ 已批准 · 尚未发布</p> : null}
    {run.allowed_actions.includes("approve") ? <><button className="button button-primary approve-button" disabled={mutation.isPending || conflict || !revision.review_digest || !revision.cards.length} onClick={() => mutation.mutate("approve")}>批准第 {revision.revision_number} 版</button><p className="approval-note">请先检查全部图片。批准只记录验收，不会发布。</p></> : null}
    {run.allowed_actions.includes("revise") ? <button className="button button-quiet" disabled={mutation.isPending || conflict} onClick={() => setEditing(!editing)}>提出返工</button> : null}
    {editing ? <form onSubmit={(e) => { e.preventDefault(); mutation.mutate("revise"); }}><label>告诉生产者要改哪里<textarea autoFocus value={instruction} onChange={(e) => setInstruction(e.target.value)} maxLength={10_000} rows={4} placeholder="例如：第二张的例子太抽象，换成点餐场景。" /></label><div className="form-actions"><button className="button button-primary" disabled={!instruction.trim() || mutation.isPending || conflict}>保存返工要求</button><button className="button button-quiet" type="button" onClick={() => setEditing(false)}>收起</button></div><p className="approval-note">保留旧图，创建新版本；现在不会开始生产。</p></form> : null}
    {!run.allowed_actions.includes("approve") ? <RunControls run={run} /> : <details className="review-more"><summary>更多操作</summary><button className="button button-quiet" disabled={mutation.isPending || conflict} onClick={() => mutation.mutate("cancel")}>取消任务（保留产物）</button></details>}
    {receipt && run.status !== "approved" ? <p className="copy-receipt" role="status">{receipt}</p> : null}
    {mutation.isError ? <div className="form-error" role="alert">{conflict ? "内容已变化，请重新检查；本次操作没有被自动重试。" : mutation.error.message}<button className="text-link" onClick={async () => { await queryClient.refetchQueries({ queryKey: ["run", run.id] }); mutation.reset(); }}>重新检查</button></div> : null}
  </div>;
}
