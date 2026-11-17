import { useEffect, useId, useState } from "react";
import { Archive, Music2, Search, Upload } from "lucide-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { bgmApi, bgmAnalysisKey, bgmListKey, bgmUsageKey, refreshBgm, type BgmImport, type BgmTrack } from "../api/bgm";
import { imageBase64 } from "./AgentAttachments";
import { BgmPreview, bgmDuration } from "./BgmPreview";
import { WorkspaceDialog } from "./WorkspaceDialog";
import "./bgm-pool.css";

const importKey = ["bgm-global-imports"];
const analysisLabels: Record<BgmTrack["analysis_status"], string> = {
  not_started: "未分析", queued: "等待分析", running: "分析中", completed: "已分析", failed: "分析失败", interrupted: "分析中断",
};
const sourceLabels: Record<BgmTrack["source_type"], string> = { upload: "文件上传", share: "分享链接导入", legacy: "原账号素材" };

export function BgmPool() {
  const client = useQueryClient();
  const [filter, setFilter] = useState("");
  const [q, setQ] = useState("");
  const [archived, setArchived] = useState(false);
  const [link, setLink] = useState("");
  const [notice, setNotice] = useState("");
  const [archiveTarget, setArchiveTarget] = useState<BgmTrack | null>(null);
  const shareId = useId();
  useEffect(() => {
    const timer = window.setTimeout(() => setQ(filter.trim()), 220);
    return () => window.clearTimeout(timer);
  }, [filter]);
  const tracks = useQuery({ queryKey: bgmListKey(q, archived), queryFn: ({ signal }) => bgmApi.list(q, archived, signal), retry: false,
    refetchInterval: query => query.state.data?.items.some(item => item.analysis_status === "running" || item.analysis_status === "queued") ? 2500 : false });
  const imports = useQuery({ queryKey: importKey, queryFn: ({ signal }) => bgmApi.imports(signal), retry: false,
    refetchInterval: query => query.state.data?.items.some(item => item.status === "running") ? 2500 : false });
  const completedImports = imports.data?.items.filter(job => job.status === "completed").map(job => job.id).join(",");
  useEffect(() => { if (completedImports) void refreshBgm(client); }, [client, completedImports]);
  const saveJob = (job: BgmImport) => {
    client.setQueryData<{ items: BgmImport[] }>(importKey, previous => ({ items: [job, ...(previous?.items ?? []).filter(item => item.id !== job.id)] }));
    void client.invalidateQueries({ queryKey: importKey });
  };
  const upload = useMutation({ retry: false, mutationFn: async (file: File) => {
    if (!file.size || file.size > 64 * 1024 * 1024) throw new Error("请选择不超过 64 MiB 的音频文件。");
    return bgmApi.upload(file.name, await imageBase64(file));
  }, onSuccess: item => {
    setNotice(item.archived ? `「${item.name}」已在归档中，可以前往恢复。` : `「${item.name}」已加入 BGM 池。`);
    void refreshBgm(client);
  }, onError: () => { void refreshBgm(client); } });
  const addLink = useMutation({ retry: false, mutationFn: bgmApi.import, onSuccess: saveJob,
    onError: () => { void client.invalidateQueries({ queryKey: importKey }); } });
  const retry = useMutation({ retry: false, mutationFn: bgmApi.retryImport, onSuccess: saveJob,
    onError: () => { void client.invalidateQueries({ queryKey: importKey }); } });
  const restore = useMutation({ retry: false, mutationFn: bgmApi.restore, onSuccess: item => {
    setNotice(`已恢复「${item.name}」。`);
    void refreshBgm(client);
  }, onError: () => { void refreshBgm(client); } });
  const jobs = imports.data?.items ?? [];
  const incomplete = jobs.filter(job => job.status !== "completed");
  const completed = jobs.filter(job => job.status === "completed");
  const linkRunning = incomplete.some(job => job.status === "running" && job.source_url === link.trim());

  function importRow(job: BgmImport) {
    const failed = job.status === "failed" || job.status === "interrupted";
    return <li key={job.id}>
      <p className="bgm-import-source">{job.source_url}</p>
      <p role={failed ? "alert" : "status"}>{job.status === "completed" ? "导入完成" : job.status === "running" ? "正在获取音频…" :
        job.message || (job.status === "interrupted" ? "导入中断" : "无法获取音频")}</p>
      {failed && <button type="button" className="button button-secondary" disabled={retry.isPending} onClick={() => retry.mutate(job.id)}>
        {retry.isPending && retry.variables === job.id ? "正在重试…" : "重试导入"}</button>}
    </li>;
  }

  return <section className="bgm-pool" aria-label="BGM 池">
    <header className="bgm-head"><div><h1>BGM 池</h1><p>上传音频、带音轨的视频或导入分享链接，供各栏目选用。</p></div>
      <label className="button button-primary bgm-upload"><Upload size={16} aria-hidden="true" />{upload.isPending ? "正在上传…" : "上传音频"}
        <input type="file" aria-label="上传音频" accept="audio/*,video/*,.mp3,.wav,.m4a" disabled={upload.isPending} onChange={event => {
          const file = event.target.files?.[0];
          if (file && !upload.isPending) { setNotice(""); upload.mutate(file); }
          event.target.value = "";
        }} />
      </label>
    </header>
    <form className="bgm-link" onSubmit={event => {
      event.preventDefault();
      if (link.trim() && !addLink.isPending && !linkRunning) addLink.mutate(link.trim());
    }}>
      <label htmlFor={shareId}>添加分享链接</label>
      <div><textarea id={shareId} value={link} onChange={event => setLink(event.target.value)} rows={2} placeholder="粘贴视频链接或完整分享文字" maxLength={10000} />
        <button className="button button-secondary" disabled={!link.trim() || addLink.isPending || linkRunning}>{addLink.isPending ? "正在提交…" : "导入"}</button></div>
    </form>
    {notice && <p role="status" className="bgm-notice">{notice}</p>}
    {(upload.isError || addLink.isError || retry.isError || restore.isError) &&
      <p className="form-error" role="alert">{upload.error?.message ?? addLink.error?.message ?? retry.error?.message ?? restore.error?.message}</p>}
    {imports.isError && <p className="form-error" role="alert">{imports.error.message} <button className="text-link" type="button" onClick={() => void imports.refetch()}>重读导入记录</button></p>}
    {!!incomplete.length && <section className="bgm-imports" aria-label="待处理导入"><h2>待处理导入</h2><ul>{incomplete.map(importRow)}</ul></section>}
    {!!completed.length && <details className="bgm-imports"><summary>已完成的导入</summary><ul>{completed.map(importRow)}</ul></details>}
    <div className="bgm-toolbar">
      <nav className="bgm-tabs" aria-label="音乐状态">
        <button type="button" aria-pressed={!archived} onClick={() => setArchived(false)}><Music2 size={15} aria-hidden="true" />在库</button>
        <button type="button" aria-pressed={archived} onClick={() => setArchived(true)}><Archive size={15} aria-hidden="true" />已归档</button>
      </nav>
      <label className="bgm-search"><Search size={16} aria-hidden="true" /><input type="search" aria-label="搜索音乐" placeholder="搜索名称或来源" value={filter} maxLength={200} onChange={event => setFilter(event.target.value)} /></label>
    </div>
    {tracks.isPending ? <p role="status">正在读取音乐…</p> : tracks.isError ? <p role="alert" className="form-error">{tracks.error.message} <button className="text-link" type="button" onClick={() => void tracks.refetch()}>重新读取</button></p> :
      tracks.data.items.length ? <div className="bgm-list">{tracks.data.items.map(item => <BgmRow key={item.id} item={item}
        restoreBusy={restore.isPending} onRestore={() => restore.mutate(item.id)} onArchive={() => setArchiveTarget(item)} />)}</div> :
        <div className="bgm-empty"><h2>{q ? "没有匹配的音乐" : archived ? "还没有归档的音乐" : "还没有音乐"}</h2>
          {!q && !archived && <p>添加第一首音乐后，就能在栏目里选用。</p>}</div>}
    {archiveTarget && <ArchiveTrackDialog item={archiveTarget} onClose={() => setArchiveTarget(null)} onArchived={name => {
      setArchiveTarget(null);
      setNotice(`已归档「${name}」。`);
    }} />}
  </section>;
}

function BgmRow({ item, restoreBusy, onRestore, onArchive }: { item: BgmTrack; restoreBusy: boolean; onRestore: () => void; onArchive: () => void }) {
  const client = useQueryClient();
  const [analysisOpen, setAnalysisOpen] = useState(false);
  const status = item.analysis_status;
  const detail = useQuery({ queryKey: [...bgmAnalysisKey(item.id), status], queryFn: ({ signal }) => bgmApi.analysis(item.id, signal),
    enabled: analysisOpen, retry: false });
  const analysis = useMutation({ retry: false, mutationFn: () => bgmApi.analyze(item.id, item.analysis_available),
    onSettled: async () => {
      await Promise.all([refreshBgm(client), client.invalidateQueries({ queryKey: bgmAnalysisKey(item.id) })]);
    } });
  const running = status === "queued" || status === "running";
  const result = detail.data;
  return <article className="bgm-row">
    <div className="bgm-row-title"><h2>{item.name}</h2><span>{bgmDuration(item.duration_seconds)}</span><span>{sourceLabels[item.source_type]}</span>
      <span className={`bgm-status ${status}`} aria-live="polite">{analysisLabels[status]}</span>
    </div>
    <BgmPreview item={item} />
    <div className="bgm-row-actions">
      {item.analysis_available && <button className="text-link" type="button" aria-expanded={analysisOpen} onClick={() => setAnalysisOpen(value => !value)}>{analysisOpen ? "收起分析" : "查看分析"}</button>}
      {!item.archived && <button type="button" className="button button-secondary" disabled={analysis.isPending || running} onClick={() => analysis.mutate()}>
        {analysis.isPending ? "正在提交…" : running ? analysisLabels[status] : status === "not_started" ? "分析整首" : "重新分析"}
      </button>}
      {item.archived ? <button type="button" className="button button-secondary" disabled={restoreBusy} onClick={onRestore}>恢复</button> :
        <button type="button" className="button button-quiet" onClick={onArchive}>归档</button>}
    </div>
    {item.source_url && <details className="bgm-source bgm-row-detail"><summary>查看来源</summary><p>{item.source_url}</p></details>}
    {(item.analysis_message || analysis.isError) && <p className="form-error bgm-row-detail" role="alert">{analysis.error?.message ?? item.analysis_message}</p>}
    {analysisOpen && <div className="bgm-analysis bgm-row-detail">
      {detail.isPending ? <p role="status">正在读取整首分析…</p> : detail.isError ? <p className="form-error" role="alert">{detail.error.message} <button type="button" className="text-link" onClick={() => void detail.refetch()}>重新读取</button></p> :
        <><h3>整首分析</h3><p>{analysisLabels[result!.status]}</p>
          {typeof result?.overview?.bpm === "number" && <p>速度 {Math.round(result.overview.bpm)} BPM</p>}
          {typeof result?.beat_count === "number" && typeof result.onset_count === "number" && <p>节拍 {result.beat_count} 个 · 起音 {result.onset_count} 个</p>}
          {!!result?.sections?.length && <ol aria-label="音乐段落">{result.sections.map((section, index) => <li key={index}>
            {bgmDuration(section.start_seconds)}–{bgmDuration(section.end_seconds)} {section.label || `段落 ${index + 1}`}
          </li>)}</ol>}</>}
    </div>}
  </article>;
}

function ArchiveTrackDialog({ item, onClose, onArchived }: { item: BgmTrack; onClose: () => void; onArchived: (name: string) => void }) {
  const client = useQueryClient();
  const usage = useQuery({ queryKey: bgmUsageKey(item.id), queryFn: ({ signal }) => bgmApi.usage(item.id, signal), retry: false, staleTime: 0 });
  const archive = useMutation({ retry: false, mutationFn: () => bgmApi.archive(item.id),
    onSuccess: result => { onArchived(result.name); void refreshBgm(client); },
    onError: () => { void refreshBgm(client); } });
  return <WorkspaceDialog title="归档音乐" onClose={onClose} busy={archive.isPending} className="bgm-archive-dialog">
    <p><strong>{item.name}</strong></p>
    <p>归档会将音乐从新制作的可选列表隐藏。现有歌单引用和制作记录会保留，可随时恢复。</p>
    {usage.isFetching ? <p role="status">正在核对引用…</p> : usage.isError ? <p className="form-error" role="alert">{usage.error.message} <button className="text-link" type="button" onClick={() => void usage.refetch()}>重新核对</button></p> :
      <><p>{usage.data?.reference_count ? `已有 ${usage.data.reference_count} 处引用` : "目前没有引用"}</p>
        {!!usage.data?.references.length && <ul className="bgm-usage">{usage.data.references.map(ref => <li key={ref.task_id ?? ref.series_id}>
          {ref.kind === "production_task" ? <strong>{ref.task_title ?? "制作任务"}</strong> :
            <Link to={`/workspace?${new URLSearchParams({ series: ref.series_id, ...(ref.creator_id ? { creator: ref.creator_id } : {}), view: "playlist" })}`}>{ref.series_name}歌单</Link>}
          <span>{[ref.creator_name, ref.kind === "production_task" && ref.series_name].filter(Boolean).join(" · ")}</span>
        </li>)}</ul>}</>}
    {archive.isError && <p className="form-error" role="alert">{archive.error.message}</p>}
    <footer className="workspace-dialog-actions"><button className="button button-secondary" type="button" data-initial-focus disabled={archive.isPending} onClick={onClose}>取消</button>
      <button className="button button-danger" type="button" disabled={!usage.isSuccess || usage.isFetching || archive.isPending} onClick={() => archive.mutate()}>{archive.isPending ? "正在归档…" : "确认归档"}</button></footer>
  </WorkspaceDialog>;
}
