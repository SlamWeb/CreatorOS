import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ExternalLink, RefreshCw, Send, X } from "lucide-react";
import { ApiError, apiUrl } from "../api/client";
import { publicationApi, publicationDraft, type PublicationDraft, type PublicationRequest } from "../api/publication";
import type { RevisionView, RunDetail } from "../api/types";
import { formatDate } from "./StatusPill";
import "./publication-panel.css";

const statusNames = { draft: "待确认", sending: "正在发送", verifying: "正在核对", unknown: "需要核对", published: "已发布" };
const initialDraft = (revision: RevisionView): PublicationDraft => ({ title: revision.publish_copy?.title ?? "", body: revision.publish_copy?.body ?? "", tags: revision.publish_copy?.hashtags.map(tag => tag.replace(/^#/, "")) ?? [], visibility: "公开可见" });

export function PublicationPanel({ run, revision, active }: { run: RunDetail; revision: RevisionView; active: boolean }) {
  const client = useQueryClient();
  const [visible, setVisible] = useState(!document.hidden);
  const [draft, setDraft] = useState(() => initialDraft(revision));
  const [tagsText, setTagsText] = useState(draft.tags.join(" "));
  const [dirty, setDirty] = useState(false);
  const [postUrl, setPostUrl] = useState("");
  const [confirmation, setConfirmation] = useState<PublicationRequest | null>(null);
  const [submitUncertain, setSubmitUncertain] = useState(false);
  const [imageStates, setImageStates] = useState<Record<string, "loaded" | "failed">>({});
  const dialog = useRef<HTMLDialogElement>(null);
  const clickGuard = useRef(false);
  const key = ["publication-request", run.id];
  useEffect(() => { const update = () => setVisible(!document.hidden); document.addEventListener("visibilitychange", update); return () => document.removeEventListener("visibilitychange", update); }, []);
  const query = useQuery({ queryKey: key, queryFn: () => publicationApi.forRun(run.id), enabled: active && visible,
    retry: false, refetchOnWindowFocus: true, refetchIntervalInBackground: false,
    refetchInterval: q => active && visible && ["sending", "verifying"].includes(q.state.data?.status ?? "") ? 4000 : false });
  const record = query.data;
  useEffect(() => { void client.invalidateQueries({ queryKey: key }); }, [run.version]); // Approval changes the server's confirmation guard.
  useEffect(() => {
    if (record && !dirty) { setDraft(publicationDraft(record.payload)); setTagsText(record.payload.tags.join(" ")); }
  }, [record?.digest, dirty]);
  useEffect(() => {
    if (confirmation) dialog.current?.showModal(); else dialog.current?.close();
  }, [confirmation]);
  useEffect(() => { if (confirmation && record?.digest !== confirmation.digest) setConfirmation(null); }, [record?.digest]);
  const accept = (value: PublicationRequest) => {
    client.setQueryData(key, value);
    void client.invalidateQueries({ queryKey: ["run", run.id] });
  };
  const prepare = useMutation({ mutationFn: () => publicationApi.prepare(run.id, { ...draft, tags: tagsText.split(/[\s,，]+/).map(tag => tag.replace(/^#/, "")).filter(Boolean) }),
    onSuccess: value => { setDirty(false); setDraft(publicationDraft(value.payload)); setTagsText(value.payload.tags.join(" ")); accept(value); setSubmitUncertain(false); } });
  const confirm = useMutation({ mutationFn: (value: PublicationRequest) => publicationApi.confirm(value.id, value.digest),
    onSuccess: value => { accept(value); setConfirmation(null); setSubmitUncertain(false); },
    onError: async error => {
      setSubmitUncertain(true); setConfirmation(null);
      const fresh = await query.refetch();
      // Rejection before dispatch + a fresh authoritative draft is editable,
      // not an ambiguous remote publication. Never automatically resubmit.
      if (error instanceof ApiError && [400, 403, 404, 409, 422].includes(error.status)
        && fresh.isSuccess && fresh.data?.status === "draft") setSubmitUncertain(false);
    },
    onSettled: () => { clickGuard.current = false; } });
  const checkSubmission = useMutation({ mutationFn: () => publicationApi.forRun(run.id), onSuccess: value => {
    if (value) { accept(value); setSubmitUncertain(false); confirm.reset(); }
  } });
  const reconcile = useMutation({ mutationFn: () => publicationApi.reconcile(record!.id, postUrl.trim() || undefined), onSuccess: value => { accept(value); setSubmitUncertain(false); confirm.reset(); } });
  const metrics = useMutation({ mutationFn: () => publicationApi.metrics(record!.id), onSuccess: accept });
  const busy = prepare.isPending || confirm.isPending || checkSubmission.isPending || reconcile.isPending || metrics.isPending;
  const editable = !record || record.status === "draft";
  const update = (value: Partial<PublicationDraft>) => { setDraft(old => ({ ...old, ...value })); setDirty(true); setConfirmation(null); };
  const error = prepare.error ?? confirm.error ?? checkSubmission.error ?? reconcile.error ?? metrics.error;
  const imagesReady = Boolean(confirmation?.payload.images.length) && confirmation!.payload.images.every(image => imageStates[image.url] === "loaded");
  return <section className="publication-panel" aria-label="小红书发布">
    <header className="publication-panel-heading"><h2>小红书</h2>{record ? <span role="status">{statusNames[record.status]}</span> : null}</header>
    {query.isLoading ? <p role="status">读取发布单…</p> : query.isError ? <p role="alert">{query.error.message} <button className="text-link" onClick={() => void query.refetch()}>重新读取</button></p> : <>
      {record ? <p className="publication-account">发布账号 <strong>{record.account.name}</strong></p> : null}
      {editable ? <form onSubmit={event => { event.preventDefault(); prepare.mutate(); }}>
        <fieldset disabled={busy || submitUncertain}>
          <label>标题<input value={draft.title} onChange={event => update({ title: event.target.value })} required /></label>
          <label>正文<textarea value={draft.body} rows={7} onChange={event => update({ body: event.target.value })} required /></label>
          <label>话题<input value={tagsText} onChange={event => { setTagsText(event.target.value); setDirty(true); }} placeholder="空格分隔" /></label>
          <label>可见范围<select value={draft.visibility} onChange={event => update({ visibility: event.target.value })}><option>公开可见</option><option>仅自己可见</option><option>仅互关好友可见</option></select></label>
          <div className="publication-actions"><button type="submit" className="button button-secondary" disabled={Boolean(record) && !dirty}>{prepare.isPending ? "保存中…" : record ? "保存修改" : "准备发布单"}</button>
            {record && <button className="button button-primary" type="button" disabled={dirty || !record.can_confirm || submitUncertain} onClick={() => { confirm.reset(); setImageStates({}); setConfirmation(record); }}><Send size={15} />确认发布…</button>}</div>
        </fieldset>
        {dirty ? <p className="publication-note">修改未保存，离开或刷新将丢失。</p> : null}
        {record?.blockers?.length ? <ul className="publication-blockers">{record.blockers.map(item => <li key={item}>{item}</li>)}</ul> : null}
      </form> : <div className="publication-sent-copy"><h3>{record.payload.title}</h3><p>{record.payload.body}</p><span>{record.payload.visibility} · {record.payload.images.length} 张图片</span></div>}
      {record?.status === "published" ? <>
        {record.post_url ? <a className="button button-secondary" href={record.post_url} target="_blank" rel="noopener noreferrer">打开笔记<ExternalLink size={15} /></a> : null}
        <div className="publication-feedback"><h3>效果数据</h3><button className="button button-quiet" type="button" disabled={busy} onClick={() => metrics.mutate()}><RefreshCw size={14} />{metrics.isPending ? "刷新中…" : "刷新数据"}</button></div>
        {record.metrics?.length ? <><dl className="publication-metrics">{(["views", "likes", "favorites", "comments", "shares"] as const).map(name => <div key={name}><dt>{{ views: "阅读", likes: "点赞", favorites: "收藏", comments: "评论", shares: "分享" }[name]}</dt><dd>{record.metrics[record.metrics.length - 1][name] ?? "—"}</dd></div>)}</dl><p className="publication-note">{record.metrics.at(-1)?.measured_at ? formatDate(record.metrics.at(-1)!.measured_at!) : ""} · 未提供的数据以 — 显示</p></> : <p className="publication-note">尚未读取效果数据</p>}
      </> : record?.status === "draft" && submitUncertain ? <div className="publication-reconcile"><p>提交结果尚未确认，请先读取最新状态。</p><button className="button button-secondary" disabled={busy} onClick={() => checkSubmission.mutate()}>{checkSubmission.isPending ? "读取中…" : "核对提交状态"}</button></div> : record && ["unknown", "verifying", "sending"].includes(record.status) ? <div className="publication-reconcile">
        <p>{record.status === "sending" ? "正在提交，请勿重复发布。" : "先核对笔记是否已发布，不会自动重发。"}</p>
        <label>笔记链接（可选）<input type="url" value={postUrl} placeholder="https://www.xiaohongshu.com/explore/…" onChange={event => setPostUrl(event.target.value)} disabled={busy} /></label>
        <button className="button button-secondary" disabled={busy || record.status === "sending"} onClick={() => reconcile.mutate()}>{reconcile.isPending ? "核对中…" : "核对发布结果"}</button>
      </div> : null}
      {record?.last_error ? <p role="alert" className="form-error">{record.last_error}</p> : null}
      {error ? <p role="alert" className="form-error">{error.message}{submitUncertain ? " 请核对发布结果，不要重新发送。" : ""}</p> : null}
    </>}
    <dialog className="publication-confirm" ref={dialog} aria-labelledby="publication-confirm-title" onCancel={event => { if (confirm.isPending) event.preventDefault(); else setConfirmation(null); }}>
      {confirmation ? <><header><h2 id="publication-confirm-title">确认发布到小红书</h2><button className="button button-quiet" aria-label="关闭发布确认" disabled={confirm.isPending} onClick={() => setConfirmation(null)}><X size={18} /></button></header>
        <p><strong>{confirmation.account.name}</strong> · {confirmation.payload.visibility}</p>
        <div className="publication-confirm-images">{confirmation.payload.images.map(image => <img key={`${confirmation.digest}-${image.order}`} src={apiUrl(image.url)} alt={`将发布的第 ${image.order} 张图片`}
          onLoad={event => { const loaded = event.currentTarget.naturalWidth > 0 && event.currentTarget.naturalHeight > 0; setImageStates(old => ({ ...old, [image.url]: loaded ? "loaded" : "failed" })); }}
          onError={() => setImageStates(old => ({ ...old, [image.url]: "failed" }))} />)}</div>
        {Object.values(imageStates).includes("failed") ? <p role="alert" className="form-error">图片读取失败，请返回后重新打开预览。</p> : !imagesReady ? <p role="status" className="publication-note">正在读取全部图片…</p> : null}
        <h3>{confirmation.payload.title}</h3><p className="publication-confirm-body">{confirmation.payload.body}</p><p className="publication-confirm-tags">{confirmation.payload.tags.map(tag => `#${tag}`).join(" ")}</p>
        <p className="publication-note">共 {confirmation.payload.images.length} 张图片。确认后会实际发布，不是保存草稿。</p>
        <footer><button className="button button-secondary" disabled={confirm.isPending} onClick={() => setConfirmation(null)}>返回修改</button><button className="button button-primary" disabled={confirm.isPending || confirmation.digest !== record?.digest || !record.can_confirm || dirty || !imagesReady} onClick={() => { if (clickGuard.current || !imagesReady) return; clickGuard.current = true; confirm.mutate(confirmation); }}>{confirm.isPending ? "正在发送…" : "确认并发布"}</button></footer>
      </> : null}
    </dialog>
  </section>;
}
