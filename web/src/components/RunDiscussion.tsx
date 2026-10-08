import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import ReactMarkdown from "react-markdown";
import { ApiError, studioApi } from "../api/client";
import type { DiscussionContext, DiscussionEntry, RevisionView, RunDetail } from "../api/types";
import { formatDate } from "./StatusPill";
import "./run-discussion.css";

const activeRunStatuses = new Set(["producing", "validating", "running"]);
const activeDiscussionStatuses = new Set(["queued", "running"]);
const labels: Record<string, string> = {
  queued: "等待执行", running: "讨论中", completed: "已完成", failed: "失败", interrupted: "中断",
};
const eventLabels: Record<string, string> = { message: "消息", tool: "工具活动", status: "状态", error: "错误" };
const historyLabels: Record<DiscussionContext["history_mode"], string> = {
  forked_production: "从生产对话分出独立讨论", explicit_snapshot: "使用本版冻结快照启动", continued_discussion: "继续本讨论对话",
};

type Draft = { requestId: string; message: string; submitted: boolean; revisionId: string; artifactDigest: string };
function storageKey(runId: string, revisionId: string) { return `creatoros-discussion:${runId}:${revisionId}`; }
function loadDraft(runId: string, revisionId: string, reviewDigest: string): Draft {
  try {
    const value = JSON.parse(localStorage.getItem(storageKey(runId, revisionId)) ?? "null") as Partial<Draft> | null;
    if (typeof value?.requestId === "string" && typeof value.message === "string"
      && typeof value.revisionId === "string" && typeof value.artifactDigest === "string") {
      return { requestId: value.requestId, message: value.message, submitted: value.submitted === true,
        revisionId: value.revisionId, artifactDigest: value.artifactDigest };
    }
  } catch { /* Storage may be unavailable; the in-memory draft still works. */ }
  return { requestId: crypto.randomUUID(), message: "", submitted: false, revisionId, artifactDigest: reviewDigest };
}
function saveDraft(runId: string, revisionId: string, draft: Draft) {
  try { localStorage.setItem(storageKey(runId, revisionId), JSON.stringify(draft)); } catch { /* Keep the draft in memory. */ }
}

export function RunDiscussion({ run, revision, focusId }: { run: RunDetail; revision: RevisionView; focusId?: string | null }) {
  const client = useQueryClient();
  const [draft, setDraft] = useState(() => loadDraft(run.id, revision.id, revision.review_digest ?? ""));
  const [checkedAfterFailure, setCheckedAfterFailure] = useState(false);
  const focusedEntry = useRef<string | null>(null);
  const discussions = useQuery({
    queryKey: ["run-discussions", run.id], queryFn: () => studioApi.runDiscussions(run.id),
    retry: false, staleTime: 1_000,
    refetchInterval: query => query.state.data?.items.some(item => activeDiscussionStatuses.has(item.status)) ? 1_500 : false,
  });
  const mutation = useMutation({
    retry: false,
    mutationFn: (input: Draft) => studioApi.createRunDiscussion(run.id, {
      request_id: input.requestId, revision_id: input.revisionId,
      artifact_digest: input.artifactDigest, message: input.message.trim(),
    }),
    onSuccess: entry => {
      client.setQueryData<{ items: DiscussionEntry[] }>(["run-discussions", run.id], old => ({
        items: [...(old?.items ?? []).filter(item => item.id !== entry.id), entry].sort((a, b) => a.created_at.localeCompare(b.created_at)),
      }));
      void client.invalidateQueries({ queryKey: ["run-discussions", run.id] });
    },
  });
  useEffect(() => { saveDraft(run.id, revision.id, draft); }, [run.id, revision.id, draft]);
  useEffect(() => {
    if (!draft.submitted && !draft.message.trim() && draft.revisionId === revision.id
      && !draft.artifactDigest && revision.review_digest) {
      setDraft(old => ({ ...old, artifactDigest: revision.review_digest! }));
    }
  }, [draft, revision.id, revision.review_digest]);

  const allEntries = discussions.data?.items ?? [];
  const entries = allEntries.filter(item => item.revision_id === revision.id);
  const matchingRequest = allEntries.find(item => item.request_id === draft.requestId);
  const anyDiscussionActive = allEntries.some(item => activeDiscussionStatuses.has(item.status));
  const productionActive = activeRunStatuses.has(run.status);
  const canDiscuss = revision.artifact_available && Boolean(revision.review_digest);
  const draftMatchesCurrent = draft.revisionId === revision.id && draft.artifactDigest === revision.review_digest;
  const blockedReason = productionActive ? "生产仍在进行，完成后才能开始讨论。" : run.status === "cancelled" ? "已取消的任务不能开启讨论。" : !canDiscuss
    ? "本版还没有可核验的冻结产物，暂时不能讨论。" : anyDiscussionActive ? "已有一条讨论正在执行，请等它完成。" : null;
  const mayRetry = draft.submitted && !matchingRequest && checkedAfterFailure && !blockedReason && draftMatchesCurrent;
  const visibleEntries = [...entries].sort((a, b) => a.created_at.localeCompare(b.created_at));

  useEffect(() => {
    if (!focusId || focusedEntry.current === focusId || !visibleEntries.some(entry => entry.id === focusId)) return;
    focusedEntry.current = focusId;
    requestAnimationFrame(() => document.getElementById(`discussion-entry-${focusId}`)?.scrollIntoView({ block: "center", behavior: "smooth" }));
  }, [focusId, visibleEntries]);

  const updateDraft = (next: Draft) => {
    setDraft(next);
    saveDraft(run.id, revision.id, next);
  };
  const submit = () => {
    if (blockedReason || !draftMatchesCurrent || !draft.message.trim() || mutation.isPending) return;
    const submitted = { ...draft, submitted: true };
    updateDraft(submitted);
    setCheckedAfterFailure(false);
    mutation.mutate(submitted);
  };
  const checkOutcome = async () => {
    const result = await discussions.refetch();
    setCheckedAfterFailure(!result.isError);
  };
  const continueDiscussion = () => {
    updateDraft({ requestId: crypto.randomUUID(), message: "", submitted: false,
      revisionId: revision.id, artifactDigest: revision.review_digest ?? "" });
    mutation.reset();
    setCheckedAfterFailure(false);
  };
  const startNewRequest = () => {
    updateDraft({ ...draft, requestId: crypto.randomUUID(), submitted: false,
      revisionId: revision.id, artifactDigest: revision.review_digest ?? "" });
    mutation.reset();
    setCheckedAfterFailure(false);
  };
  const error = mutation.error;
  // A successful POST whose body could not be read/parsed may still be recorded.
  const unknownOutcome = !(error instanceof ApiError) || error.status === 0 || error.status >= 500;

  return <section className="run-discussion" aria-label={`第 ${revision.revision_number} 版的 Codex 讨论`}>
    <header className="discussion-heading"><div><p>只讨论，不会修改图片、文案或审批状态</p><h2>与 Codex 讨论</h2></div><span>第 {revision.revision_number} 版</span></header>
    {blockedReason && <p className="discussion-note" role="status">{blockedReason}</p>}
    {!draftMatchesCurrent && <div className="discussion-note" role="status"><p>页面所显示的版本摘要已变化。旧请求不会自动改用新版本。</p><button type="button" className="text-link" onClick={startNewRequest}>确认当前版本并新建请求</button></div>}
    {discussions.isError && <p className="discussion-error" role="alert">讨论记录读取失败：{discussions.error.message} <button type="button" className="text-link" onClick={() => void discussions.refetch()}>重新读取</button></p>}
    {visibleEntries.length ? <ol className="discussion-list">
      {visibleEntries.map(entry => <li className="discussion-entry" id={`discussion-entry-${entry.id}`} key={entry.id}>
        <div className="discussion-entry-head"><strong>你</strong><StatusText status={entry.status} /><time>{formatDate(entry.created_at)}</time></div>
        <p className="discussion-message">{entry.message}</p>
        {entry.reply && <div className="discussion-reply"><strong>Codex</strong><ReactMarkdown skipHtml disallowedElements={["img"]}>{entry.reply}</ReactMarkdown></div>}
        {entry.error && <p className="discussion-error" role="alert">{entry.error}</p>}
        {entry.events.length > 0 && <details className="discussion-events"><summary>执行活动 · {entry.events.length}</summary><ol>
          {entry.events.map(event => <li key={event.id}><time>{formatDate(event.at)}</time><span>{eventLabels[event.kind] ?? "活动"}</span><p>{event.text}</p></li>)}
        </ol></details>}
        <details className="discussion-context"><summary>本次传入什么</summary><ContextSummary context={entry.context} /></details>
      </li>)}
    </ol> : !discussions.isPending && !discussions.isError ? <p className="discussion-empty">本版还没有讨论记录。</p> : null}

    {draft.submitted ? <div className="discussion-pending-check">
      {matchingRequest ? <><p role="status">这条请求已记录，当前状态：{labels[matchingRequest.status] ?? matchingRequest.status}。</p><button type="button" className="button button-quiet" onClick={continueDiscussion}>继续讨论</button></>
        : mutation.isError && !unknownOutcome ? <><p className="discussion-error" role="alert">{error?.message ?? "请求未被接受。"} 可以调整正文或稍后使用新请求编号重试。</p><button type="button" className="button button-secondary" onClick={startNewRequest}>编辑消息并新建请求</button></>
        : <><p role={mutation.isError ? "alert" : "status"}>{mutation.isError && unknownOutcome ? `${error?.message ?? "响应无法读取。"} 结果可能尚未返回。` : "上次提交状态尚未确认。"}先查询记录，再决定是否用同一请求编号重试。</p>
          <div className="discussion-actions"><button type="button" className="button button-secondary" disabled={discussions.isFetching || mutation.isPending} onClick={() => void checkOutcome()}>{mutation.isPending ? "提交中…" : discussions.isFetching ? "正在核对…" : "核对提交状态"}</button>
            {mayRetry && <button type="button" className="button button-primary" disabled={mutation.isPending} onClick={submit}>使用同一请求编号重试</button>}
          </div></>}
    </div> : <form className="discussion-form" onSubmit={event => { event.preventDefault(); submit(); }}>
      <label htmlFor={`discussion-message-${revision.id}`}>想和 Codex 核对什么？</label>
      <textarea id={`discussion-message-${revision.id}`} value={draft.message} maxLength={10_000} rows={3}
        onChange={event => updateDraft({ ...draft, message: event.target.value })}
        placeholder="例如：这页的解释是否准确？请指出依据；不要修改产物。" disabled={Boolean(blockedReason) || !draftMatchesCurrent || mutation.isPending} />
      <div className="discussion-form-footer"><span>提交后会另开讨论任务；需要修改产物，请使用现有返工入口。</span>
        <button type="submit" className="button button-primary" disabled={!draft.message.trim() || Boolean(blockedReason) || !draftMatchesCurrent || mutation.isPending}>{mutation.isPending ? "提交中…" : "发送讨论"}</button>
      </div>
    </form>}
    {mutation.isError && !matchingRequest && !draft.submitted && <p className="discussion-error" role="alert">{error?.message ?? "提交失败。"}{unknownOutcome ? " 结果可能尚未返回，请核对记录后再决定是否重试。" : " 请先核对服务端记录。"}</p>}
  </section>;
}

function StatusText({ status }: { status: string }) {
  return <span className={`discussion-status discussion-status-${status}`}>{labels[status] ?? status}</span>;
}

function ContextSummary({ context }: { context: DiscussionContext }) {
  return <div className="discussion-context-body">
    <p>{historyLabels[context.history_mode] ?? "使用本版快照"}</p>
    <p>第 {context.revision_number} 版 · {context.image_count} 张已冻结图片</p>
    <div><strong>图片摘要</strong>{context.images.length ? <ul>{context.images.map(image => <li key={image.order}>第 {image.order} 张 · {image.sha256.slice(0, 12)}…</li>)}</ul> : <p>没有图片摘要。</p>}</div>
    <div><strong>冻结 Skill</strong>{context.skills.length ? <ul>{context.skills.map((skill, index) => <li key={`${skill.role}-${index}`}>{skill.role} · {skill.name} · {skill.digest.slice(0, 12)}…</li>)}</ul> : <p>未记录 Skill。</p>}</div>
    <div><strong>包含</strong>{context.includes.length ? <ul>{context.includes.map((item, index) => <li key={`${index}-${item}`}>{item}</li>)}</ul> : <p>未记录。</p>}</div>
    <div><strong>不包含</strong>{context.excludes.length ? <ul>{context.excludes.map((item, index) => <li key={`${index}-${item}`}>{item}</li>)}</ul> : <p>未记录。</p>}</div>
  </div>;
}
