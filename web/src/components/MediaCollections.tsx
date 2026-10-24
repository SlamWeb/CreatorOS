import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router-dom";
import { ArrowLeft, ArrowRight, Download, Folder, Pause, Play, Plus, RefreshCw } from "lucide-react";
import { ApiError, apiUrl, request } from "../api/client";
import { StatusPill } from "./StatusPill";
import "./media-collections.css";

type CollectionSummary = { id: string; name: string; version: number; topic_id: string | null };
type MediaSource = {
  run_id: string; run_title: string; topic_id?: string | null; topic_title?: string | null; status: string;
  revision_id: string | null; revision_number: number | null; image_count: number; error?: string | null;
};
type MediaItem = {
  id: string; run_id: string; run_title: string; revision_id: string; revision_number: number;
  order: number; checksum: string; width: number; height: number; url: string; included: boolean;
};
type CollectionDetail = CollectionSummary & {
  run_ids: string[]; items: MediaItem[]; sources: MediaSource[]; snapshot_digest: string;
};
type SourceList = { items: MediaSource[] };
const basePath = (creatorId: string) => `/api/creators/${encodeURIComponent(creatorId)}/collections`;
const collectionPath = (creatorId: string, id: string) => `${basePath(creatorId)}/${encodeURIComponent(id)}`;
const uncertain = (error: Error | null) => error instanceof ApiError && (error.status === 0 || error.status >= 500);
const errorMessage = (error: Error | null) => error ? `${error.message}${uncertain(error) ? " 请求结果待核对，已重新读取文件夹；不会自动重发。" : ""}` : "";

export function MediaCollections({ creatorId }: { creatorId: string }) {
  const [params, setParams] = useSearchParams();
  const client = useQueryClient();
  const selectedId = params.get("collection");
  const topicHint = params.get("collection_topic");
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");
  const [topicId, setTopicId] = useState("");
  const [runIds, setRunIds] = useState<string[]>([]);
  const requestId = useRef(crypto.randomUUID());
  const handledHint = useRef<string | null>(null);
  const list = useQuery({ queryKey: ["media-collections", creatorId],
    queryFn: () => request<{ items: CollectionSummary[] }>(basePath(creatorId)), retry: false });
  const sources = useQuery({ queryKey: ["media-sources", creatorId],
    queryFn: () => request<SourceList>(`${basePath(creatorId)}/sources`), retry: false });
  const refresh = () => Promise.all([
    client.invalidateQueries({ queryKey: ["media-collections", creatorId] }),
    client.invalidateQueries({ queryKey: ["media-sources", creatorId] }),
  ]);
  const select = (id: string | null) => setParams(previous => {
    const next = new URLSearchParams(previous);
    if (id) next.set("collection", id); else next.delete("collection");
    next.delete("collection_topic");
    return next;
  });
  useEffect(() => {
    if (!topicHint || selectedId || !list.data || list.isFetching || handledHint.current === topicHint) return;
    handledHint.current = topicHint;
    const existing = list.data.items.find(item => item.topic_id === topicHint);
    if (existing) select(existing.id);
    else { setTopicId(topicHint); setCreating(true); }
  }, [topicHint, selectedId, list.data, list.isFetching]);
  const topics = useMemo(() => Array.from(new Map((sources.data?.items ?? [])
    .filter(source => source.topic_id).map(source => [source.topic_id!, { id: source.topic_id!, title: source.topic_title ?? source.run_title }])).values()), [sources.data]);
  const create = useMutation({ retry: false,
    mutationFn: () => request<CollectionDetail>(basePath(creatorId), { method: "POST", body: JSON.stringify({
      name: name.trim(), topic_id: topicId || null, run_ids: runIds, request_id: requestId.current,
    }) }),
    onSuccess: async result => {
      await refresh();
      setCreating(false); setName(""); setRunIds([]); setTopicId(""); requestId.current = crypto.randomUUID();
      setParams(previous => {
        if (previous.get("creator") && previous.get("creator") !== creatorId) return previous;
        const next = new URLSearchParams(previous); next.set("collection", result.id); next.delete("collection_topic"); return next;
      });
    },
    onError: refresh,
  });
  const locked = create.isPending || uncertain(create.error);
  function editDraft() { create.reset(); requestId.current = crypto.randomUUID(); }
  const topicLabel = topics.find(topic => topic.id === topicId)?.title;
  return <section className="media-collections" aria-label="素材文件夹">
    <header className="media-section-head"><div><h2>素材文件夹</h2><p>把同一选题和不同制作任务的图片放在一起选片。</p></div>
      <button className="button button-primary" type="button" disabled={create.isPending} onClick={() => setCreating(value => !value)} aria-expanded={creating}><Plus size={16} aria-hidden="true" />新建文件夹</button>
    </header>
    {creating && <form className="media-create" onSubmit={event => { event.preventDefault(); if (name.trim()) create.mutate(); }}>
      <h3>新建文件夹</h3>
      <label className="media-field">文件夹名称<input autoFocus maxLength={120} required value={name} disabled={locked} placeholder={topicLabel ?? "例如：坚持系列选片"}
        onChange={event => { editDraft(); setName(event.target.value); }} /></label>
      <label className="media-field">自动收图的选题（可选）<select value={topicId} disabled={locked || sources.isPending || sources.isError}
        onChange={event => { editDraft(); setTopicId(event.target.value); }}><option value="">不关联选题</option>
        {topicId && !topics.some(topic => topic.id === topicId) && <option value={topicId}>从制作任务带入的选题</option>}
        {topics.map(topic => <option key={topic.id} value={topic.id}>{topic.title}</option>)}
      </select></label>
      {topicId && <p className="media-hint">这个选题已有和之后产出的图片会自动收进来。</p>}
      <RunPicker sources={sources.data?.items ?? []} selected={runIds} topicId={topicId} disabled={locked}
        onChange={ids => { editDraft(); setRunIds(ids); }} />
      {sources.isPending && <p role="status">正在读取可选来源…</p>}
      {sources.isError && <p className="form-error" role="alert">来源读取失败：{sources.error.message} <button type="button" className="text-link" onClick={() => void sources.refetch()}>重新读取来源</button></p>}
      <div className="media-actions"><button className="button button-primary" disabled={!name.trim() || create.isPending}>{create.isPending ? "正在创建…" : uncertain(create.error) ? "核对同一创建请求" : "创建文件夹"}</button>
        <button className="button button-secondary" type="button" disabled={create.isPending} onClick={() => setCreating(false)}>收起</button></div>
      {create.isError && <p className="form-error" role="alert">{errorMessage(create.error)}</p>}
    </form>}
    {list.isPending ? <div className="media-loading" role="status">正在读取文件夹…</div> : list.isError ? <p className="form-error" role="alert">{list.error.message} <button className="text-link" type="button" onClick={() => void list.refetch()}>重新读取文件夹</button></p> : <>
      <nav className="media-folder-list" aria-label="选择素材文件夹">{list.data.items.map(item => <button type="button" key={item.id} aria-pressed={selectedId === item.id}
        onClick={() => select(item.id)}><Folder size={17} aria-hidden="true" /><span>{item.name}</span></button>)}</nav>
      {!list.data.items.length && !creating && <div className="media-empty"><h3>先建一个文件夹</h3><p>关联选题后会持续收图，也可以加入这个账号的其他制作任务。</p><button type="button" className="button button-secondary" onClick={() => setCreating(true)}>新建文件夹</button></div>}
      {selectedId ? <CollectionContent key={`${creatorId}:${selectedId}`} creatorId={creatorId} collectionId={selectedId} pickerSources={sources.data?.items ?? []}
        onRefreshSources={() => void sources.refetch()} pickerError={sources.error?.message} onDeleted={() => setParams(previous => {
          if (previous.get("collection") !== selectedId || (previous.get("creator") && previous.get("creator") !== creatorId)) return previous;
          const next = new URLSearchParams(previous); next.delete("collection"); return next;
        })} /> : !!list.data.items.length && !creating && <p className="media-hint">选择一个文件夹，查看图片与来源。</p>}
    </>}
  </section>;
}

function RunPicker({ sources, selected, topicId, disabled, onChange }: {
  sources: MediaSource[]; selected: string[]; topicId: string | null; disabled: boolean; onChange: (ids: string[]) => void;
}) {
  const runs = Array.from(new Map(sources.filter(source => !topicId || source.topic_id !== topicId).map(source => [source.run_id, source])).values());
  return <fieldset className="media-run-picker" disabled={disabled}><legend>额外加入制作任务（可选）</legend>
    {!runs.length ? <p className="media-hint">暂无其他可加入的任务；等待中的任务也可以先加入。</p> : <div className="media-run-options">{runs.map(source => <label key={source.run_id}>
      <input type="checkbox" checked={selected.includes(source.run_id)} onChange={event => onChange(event.target.checked ? [...selected, source.run_id] : selected.filter(id => id !== source.run_id))} />
      <span><span className="media-source-title">{source.run_title}</span><span className="media-source-meta">{source.status === "unavailable" ? <span className="status-pill">来源不可用</span> : <StatusPill status={source.status} />}{source.revision_number ? `第 ${source.revision_number} 版` : "等待图片"}</span></span>
      <Link to={`/runs/${encodeURIComponent(source.run_id)}`} aria-label={`查看制作任务 ${source.run_title}`}>查看</Link>
    </label>)}</div>}
  </fieldset>;
}

function CollectionContent({ creatorId, collectionId, pickerSources, pickerError, onRefreshSources, onDeleted }: {
  creatorId: string; collectionId: string; pickerSources: MediaSource[]; pickerError?: string;
  onRefreshSources: () => void; onDeleted: () => void;
}) {
  const client = useQueryClient();
  const path = collectionPath(creatorId, collectionId);
  const key = ["media-collection", creatorId, collectionId];
  const detail = useQuery({ queryKey: key, queryFn: () => request<CollectionDetail>(path), retry: false,
    refetchInterval: query => query.state.data?.sources.some(source => ["queued", "producing", "validating", "running"].includes(source.status)) ? 5000 : false });
  const [rename, setRename] = useState<{ name: string; version: number } | null>(null);
  const [sourceDraft, setSourceDraft] = useState<{ ids: string[]; version: number } | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<{ name: string; version: number } | null>(null);
  const [previewOpen, setPreviewOpen] = useState(false);
  const [notice, setNotice] = useState("");
  const downloadAbort = useRef<AbortController | null>(null);
  useEffect(() => () => downloadAbort.current?.abort(), []);
  const refresh = () => Promise.all([
    client.invalidateQueries({ queryKey: key }),
    client.invalidateQueries({ queryKey: ["media-collections", creatorId] }),
  ]);
  const write = useMutation({ retry: false,
    mutationFn: ({ method = "PATCH", suffix = "", body }: { method?: string; suffix?: string; body: Record<string, unknown> }) =>
      request<CollectionDetail>(`${path}${suffix}`, { method, body: JSON.stringify(body) }),
    onSuccess: async () => { setRename(null); setSourceDraft(null); setNotice("已保存"); await refresh(); },
    onError: async () => { setNotice(""); await refresh(); },
  });
  const remove = useMutation({ retry: false,
    mutationFn: (version: number) => request<unknown>(`${path}?expected_version=${version}`, {
      method: "DELETE", headers: { "Content-Type": "application/json" },
    }),
    onSuccess: async () => { await client.invalidateQueries({ queryKey: ["media-collections", creatorId] }); onDeleted(); },
    onError: refresh,
  });
  const download = useMutation({ retry: false,
    mutationFn: async (data: CollectionDetail) => {
      const abort = new AbortController(); downloadAbort.current = abort;
      const query = new URLSearchParams({ version: String(data.version), snapshot: data.snapshot_digest });
      let response: Response;
      try { response = await fetch(apiUrl(`${path}/download?${query}`), { signal: abort.signal }); }
      catch (error) { if (abort.signal.aborted) throw error; throw new Error("下载连接中断，请重新读取后再下载。"); }
      if (!response.ok) { const body = await response.json().catch(() => null); throw new Error(body?.error?.message ?? `下载失败（${response.status}），请重新读取后再下载。`); }
      if (!response.headers.get("content-type")?.includes("application/zip")) throw new Error("下载格式异常，请重新读取后再下载。");
      const blob = await response.blob();
      if (abort.signal.aborted) return;
      const url = URL.createObjectURL(blob); const anchor = document.createElement("a");
      anchor.href = url; anchor.download = `${data.name.replace(/[\\/:*?"<>|]/g, "_")}.zip`; anchor.click();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    }, onError: refresh,
  });
  const included = useMemo(() => detail.data?.items.filter(item => item.included) ?? [], [detail.data?.items]);
  if (detail.isPending) return <div className="media-loading" role="status">正在读取图片与来源…</div>;
  if (detail.isError) return <p className="form-error" role="alert">{detail.error.message} <button className="text-link" type="button" onClick={() => void detail.refetch()}>重新读取详情</button></p>;
  const data = detail.data;
  const busy = write.isPending || remove.isPending || download.isPending;
  const patch = (body: Record<string, unknown>) => { setNotice(""); write.mutate({ body: { expected_version: data.version, ...body } }); };
  function move(index: number, delta: number) {
    const ids = data.items.map(item => item.id); [ids[index], ids[index + delta]] = [ids[index + delta], ids[index]];
    patch({ order_ids: ids });
  }
  const anchorTitle = pickerSources.find(source => source.topic_id === data.topic_id)?.topic_title;
  return <section className="media-detail" aria-label={`文件夹 ${data.name}`}>
    <header className="media-detail-head"><div><h3>{data.name}</h3><p>{included.length} 张保留 · {data.items.length} 张图片{data.topic_id ? ` · 自动收集：${anchorTitle ?? "关联选题"}` : ""}</p></div>
      <div className="media-actions"><button type="button" className="button button-quiet" disabled={busy || detail.isFetching} onClick={() => void refresh()}><RefreshCw size={15} aria-hidden="true" />{detail.isFetching ? "读取中…" : "刷新"}</button>
        <button type="button" className="button button-secondary" disabled={busy} onClick={() => { setRename({ name: data.name, version: data.version }); write.reset(); }}>改名</button>
        <button type="button" className="button button-quiet action-danger" disabled={busy} onClick={() => setConfirmDelete(value => value ? null : { name: data.name, version: data.version })}>删除文件夹</button></div>
    </header>
    {rename && <form className="media-inline-edit" onSubmit={event => { event.preventDefault(); if (rename.name.trim()) write.mutate({ body: { name: rename.name.trim(), expected_version: rename.version } }); }}>
      <label className="media-field">文件夹名称<input autoFocus maxLength={120} required value={rename.name} disabled={busy || uncertain(write.error)} onChange={event => setRename({ ...rename, name: event.target.value })} /></label>
      <div className="media-actions"><button className="button button-primary" disabled={busy || !rename.name.trim()}>保存名称</button><button className="button button-secondary" type="button" disabled={busy} onClick={() => setRename(null)}>取消</button></div>
    </form>}
    {confirmDelete && <div className="media-delete-confirm"><p>只删除「{confirmDelete.name}」文件夹，原图和制作记录会保留。</p><div className="media-actions"><button type="button" className="button button-danger" disabled={busy} onClick={() => remove.mutate(confirmDelete.version)}>{remove.isPending ? "删除中…" : "确认删除文件夹"}</button><button type="button" className="button button-secondary" disabled={busy} onClick={() => setConfirmDelete(null)}>取消</button></div></div>}
    <div className="media-sources"><div className="media-sources-head"><h4>图片来源</h4><button type="button" className="button button-secondary" disabled={busy} aria-expanded={!!sourceDraft} onClick={() => { setSourceDraft(sourceDraft ? null : { ids: [...data.run_ids], version: data.version }); write.reset(); }}>编辑来源</button></div>
      {data.sources.length ? <ul>{data.sources.map((source, index) => <li key={`${source.run_id}:${source.revision_id ?? index}`}><Link to={`/runs/${encodeURIComponent(source.run_id)}`}>{source.run_title}</Link><span>{source.revision_number ? `第 ${source.revision_number} 版` : "等待图片"} · {source.image_count} 张</span>{source.status === "unavailable" ? <span className="status-pill">来源不可用</span> : <StatusPill status={source.status} />}{source.error && <p className="form-error" role="alert">{source.error}</p>}</li>)}</ul> : <p className="media-hint">还没有来源。可以加入已有或等待中的制作任务。</p>}
      {sourceDraft && <div className="media-source-editor"><RunPicker sources={[...data.sources, ...pickerSources]} selected={sourceDraft.ids} topicId={data.topic_id} disabled={busy || uncertain(write.error)} onChange={ids => setSourceDraft({ ...sourceDraft, ids })} />
        {pickerError && <p className="form-error" role="alert">{pickerError} <button className="text-link" type="button" onClick={onRefreshSources}>重新读取来源</button></p>}
        <div className="media-actions"><button type="button" className="button button-primary" disabled={busy || !!pickerError} onClick={() => write.mutate({ body: { expected_version: sourceDraft.version, run_ids: sourceDraft.ids } })}>保存来源</button><button type="button" className="button button-secondary" disabled={busy} onClick={() => setSourceDraft(null)}>取消</button></div></div>}
    </div>
    <div className="media-selection-toolbar"><div className="media-actions"><button type="button" className="button button-secondary" disabled={busy || included.length === data.items.length} onClick={() => patch({ excluded_ids: [] })}>全部保留</button>
      <button type="button" className="button button-secondary" disabled={busy || !included.length} onClick={() => patch({ excluded_ids: data.items.map(item => item.id) })}>全部排除</button>
      <button type="button" className="button button-secondary" disabled={busy || data.items.length < 2} onClick={() => { setNotice(""); write.mutate({ suffix: "/sort", method: "POST", body: { expected_version: data.version, snapshot_digest: data.snapshot_digest } }); }}>相近排序（试验性）</button></div>
      <div className="media-actions"><button type="button" className="button button-secondary" disabled={!included.length || busy} aria-expanded={previewOpen} onClick={() => setPreviewOpen(value => !value)}>{previewOpen ? "收起预览" : "连续预览"}</button>
      <button type="button" className="button button-primary" disabled={!included.length || busy} onClick={() => download.mutate(data)}><Download size={16} aria-hidden="true" />{download.isPending ? "打包中…" : `下载所选 ZIP（${included.length}）`}</button></div>
    </div>
    <p className="media-hint">相近排序只调整图片顺序；勾选保留的图片用于预览和下载。</p>
    {write.isError && <p className="form-error" role="alert">{errorMessage(write.error)} <button type="button" className="text-link" disabled={busy} onClick={() => { void refresh(); setRename(null); setSourceDraft(null); write.reset(); }}>重新读取并检查</button></p>}
    {remove.isError && <p className="form-error" role="alert">{errorMessage(remove.error)}</p>}
    {download.isError && <p className="form-error" role="alert">{download.error.message}</p>}
    <p className="media-save-status" role="status">{write.isPending ? "正在保存…" : notice}</p>
    {previewOpen && !!included.length && <ContinuousPreview key={`${collectionId}:${data.snapshot_digest}`} items={included} />}
    {data.items.length ? <div className="media-image-grid">{data.items.map((item, index) => <article className={`media-image ${item.included ? "" : "is-excluded"}`} key={item.id}>
      <label className="media-image-selection"><input type="checkbox" aria-label={`保留第 ${index + 1} 张图片`} checked={item.included} disabled={busy} onChange={event => patch({ excluded_ids: data.items.filter(other => other.id === item.id ? !event.target.checked : !other.included).map(other => other.id) })} /><span>{item.included ? "保留" : "已排除"}</span><b>{index + 1}</b></label>
      <MaterialImage item={item} number={index + 1} />
      <div className="media-image-caption"><Link to={`/runs/${encodeURIComponent(item.run_id)}?revision=${encodeURIComponent(item.revision_id)}`}>{item.run_title}</Link><span>第 {item.revision_number} 版 · 第 {item.order} 页</span>
        <div className="media-image-move"><button type="button" className="button button-quiet" aria-label={`将第 ${index + 1} 张图片向前移动`} disabled={busy || index === 0} onClick={() => move(index, -1)}><ArrowLeft size={15} aria-hidden="true" />向前</button><button type="button" className="button button-quiet" aria-label={`将第 ${index + 1} 张图片向后移动`} disabled={busy || index === data.items.length - 1} onClick={() => move(index, 1)}>向后<ArrowRight size={15} aria-hidden="true" /></button></div>
      </div>
    </article>)}</div> : <div className="media-empty"><h4>等待来源图片</h4><p>已有图片会显示在这里。等待中的任务完成后，刷新即可看到。</p></div>}
  </section>;
}

function MaterialImage({ item, number }: { item: MediaItem; number: number }) {
  const [failed, setFailed] = useState(false);
  return <div className="media-image-frame">{failed ? <p role="alert">图片无法读取。<button className="text-link" type="button" onClick={() => setFailed(false)}>重新读取图片</button></p> : <img src={apiUrl(item.url)} width={item.width} height={item.height} alt={`${item.run_title}，第 ${item.revision_number} 版第 ${item.order} 页，文件夹第 ${number} 张`} loading="lazy" onError={() => setFailed(true)} />}</div>;
}

function ContinuousPreview({ items }: { items: MediaItem[] }) {
  const [current, setCurrent] = useState(0);
  const [pending, setPending] = useState<number | null>(null);
  const [playing, setPlaying] = useState(false);
  const [seconds, setSeconds] = useState(1);
  const [loaded, setLoaded] = useState<Set<string>>(() => new Set());
  const [failed, setFailed] = useState<string | null>(null);
  const next = pending ?? (current + 1) % items.length;
  useEffect(() => {
    const stopWhenHidden = () => { if (document.hidden) { setPlaying(false); setPending(null); } };
    document.addEventListener("visibilitychange", stopWhenHidden);
    return () => document.removeEventListener("visibilitychange", stopWhenHidden);
  }, []);
  useEffect(() => {
    if (!playing || pending !== null || !loaded.has(items[current].id) || items.length < 2) return;
    const timer = window.setTimeout(() => setPending((current + 1) % items.length), seconds * 1000);
    return () => window.clearTimeout(timer);
  }, [playing, seconds, current, pending, loaded, items]);
  useEffect(() => {
    if (pending !== null && loaded.has(items[pending].id)) { setCurrent(pending); setPending(null); }
  }, [pending, loaded, items]);
  return <section className="media-preview" aria-label="连续预览">
    <div className="media-preview-stage">{Array.from(new Set([current, next])).map(index => <img key={items[index].id} src={apiUrl(items[index].url)} alt={index === current ? `预览第 ${current + 1} 张：${items[current].run_title}` : ""}
      className={index === current ? "is-current" : "is-preloading"} aria-hidden={index !== current} onLoad={() => setLoaded(previous => new Set(previous).add(items[index].id))} onError={() => { setPlaying(false); setPending(null); setFailed("预览图片无法读取，请刷新文件夹。"); }} />)}</div>
    <div className="media-preview-controls"><div className="media-actions"><button type="button" className="button button-secondary" aria-label="预览上一张" disabled={!!failed || pending !== null || items.length < 2} onClick={() => { setPlaying(false); setPending((current + items.length - 1) % items.length); }}><ArrowLeft size={16} /></button>
      <button type="button" className="button button-primary" disabled={!!failed || items.length < 2} onClick={() => { setPlaying(value => !value); setPending(null); }}>{playing ? <Pause size={16} aria-hidden="true" /> : <Play size={16} aria-hidden="true" />}{playing ? "停止" : "播放"}</button>
      <button type="button" className="button button-secondary" aria-label="预览下一张" disabled={!!failed || pending !== null || items.length < 2} onClick={() => { setPlaying(false); setPending((current + 1) % items.length); }}><ArrowRight size={16} /></button></div>
      <span>{current + 1} / {items.length}</span><label>每张时长<select aria-label="每张图片预览时长" value={seconds} onChange={event => setSeconds(Number(event.target.value))}>{[0.3, 0.5, 1, 2, 3, 5].map(value => <option key={value} value={value}>{value} 秒</option>)}</select></label>
      <span role="status">{pending !== null ? "正在加载下一张…" : playing ? "播放中" : "已停止"}</span></div>
    {failed && <p className="form-error" role="alert">{failed}</p>}
  </section>;
}
