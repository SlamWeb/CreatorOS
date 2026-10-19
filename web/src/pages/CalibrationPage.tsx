import { useEffect, useMemo, useRef, useState } from "react";
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { ArrowLeft, Plus, Trash2 } from "lucide-react";
import { ApiError, apiUrl, studioApi } from "../api/client";
import type { CardView, SkillCalibrationArtifact, SkillCalibrationInput, SkillCalibrationRating } from "../api/types";
import { ExtractionImagePicker } from "../components/ExtractionImagePicker";
import "../components/artifact-skill-extraction.css";
import "./calibration.css";

type MarkedArtifact = SkillCalibrationArtifact & { title: string; cards: CardView[]; revision_number: number };
type Reference = { id: string; name: string; url: string; rating: SkillCalibrationRating; comment: string };
type Draft = { seriesId: string; skillId: string; runId: string; revisionId: string; artifacts: MarkedArtifact[]; references: Reference[]; instruction: string; pending: SkillCalibrationInput | null };
const emptyDraft: Draft = { seriesId: "", skillId: "", runId: "", revisionId: "", artifacts: [], references: [], instruction: "", pending: null };
function readDraft(key: string): Draft {
  try { const value = JSON.parse(sessionStorage.getItem(key) || "null"); return value && Array.isArray(value.artifacts) && Array.isArray(value.references) ? { ...emptyDraft, ...value } : emptyDraft; }
  catch { return emptyDraft; }
}
async function imageBase64(file: File) {
  const value = await new Promise<string>((resolve, reject) => { const reader = new FileReader(); reader.onload = () => resolve(String(reader.result)); reader.onerror = () => reject(new Error(`无法读取 ${file.name}`)); reader.readAsDataURL(file); });
  return value.slice(value.indexOf(",") + 1);
}

export function CalibrationPage() {
  const [params] = useSearchParams();
  return <CalibrationForm key={params.get("run") || "new"} />;
}

function CalibrationForm() {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const cache = useQueryClient();
  const seedRun = params.get("run") || "";
  const storageKey = `creatoros:skill-calibration:form:${seedRun || "new"}`;
  const [draft, setDraft] = useState<Draft>(() => ({ ...readDraft(storageKey), ...(readDraft(storageKey).runId ? {} : { runId: seedRun, revisionId: params.get("revision") || "" }) }));
  const [files, setFiles] = useState<File[]>([]);
  const [fileMarks, setFileMarks] = useState<{ file: File; rating: SkillCalibrationRating; comment: string }[]>([]);
  const [order, setOrder] = useState<string>("all");
  const [rating, setRating] = useState<SkillCalibrationRating>("bad");
  const [comment, setComment] = useState("");
  const [error, setError] = useState("");
  const [progress, setProgress] = useState("");
  const [storageError, setStorageError] = useState("");
  const submitLock = useRef(false);
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  const uploaded = useRef(new Map<File, Reference>());
  const series = useQuery({ queryKey: ["series-all"], queryFn: studioApi.seriesAll });
  const skills = useQuery({ queryKey: ["producer-skills"], queryFn: studioApi.producerSkills });
  const selectedRun = useQuery({ queryKey: ["run", draft.runId], queryFn: () => studioApi.run(draft.runId), enabled: Boolean(draft.runId), retry: false });
  const selectedSeries = series.data?.find(item => item.id === draft.seriesId);
  const runs = useInfiniteQuery({ queryKey: ["calibration-runs", selectedSeries?.creator_id], queryFn: ({ pageParam }) => studioApi.runsPage(pageParam, selectedSeries?.creator_id ?? undefined), initialPageParam: 0,
    getNextPageParam: last => last.page.offset + last.page.limit < last.page.total ? last.page.offset + last.page.limit : undefined, enabled: Boolean(draft.seriesId), retry: false });
  const boundIds = selectedSeries ? [selectedSeries.mind_skill_id, selectedSeries.production_skill_id, selectedSeries.skill_name].filter(Boolean) : [];
  const targets = skills.data?.items.filter(item => boundIds.includes(item.id) && item.editable !== false) ?? [];
  const targetFiles = useQuery({ queryKey: ["producer-skill-files", draft.skillId], queryFn: () => studioApi.producerSkillFiles(draft.skillId), enabled: Boolean(draft.skillId), retry: false });
  const revision = selectedRun.data?.revisions.find(item => draft.revisionId ? item.id === draft.revisionId : item.revision_number === selectedRun.data?.active_revision_number);
  const locked = Boolean(draft.pending);
  const existing = useQuery({ queryKey: ["skill-calibrations", draft.seriesId], queryFn: () => studioApi.skillCalibrations(draft.seriesId), enabled: locked && Boolean(draft.seriesId), retry: false });
  const goTo = (id: string, jobId: string) => { if (mounted.current) navigate(`/skills?extraction=${encodeURIComponent(jobId)}&calibration=${encodeURIComponent(id)}`); };
  useEffect(() => {
    const found = existing.data?.items.find(item => item.request_id === draft.pending?.request_id);
    if (found) { sessionStorage.removeItem(storageKey); goTo(found.id, found.job_id); }
  }, [existing.data, draft.pending?.request_id]);
  useEffect(() => {
    try { sessionStorage.setItem(storageKey, JSON.stringify(draft)); setStorageError(""); }
    catch { setStorageError("无法暂存表单，请勿刷新；已选图片尚未上传时也不会保留。"); }
  }, [draft, storageKey]);
  useEffect(() => {
    const warn = (event: BeforeUnloadEvent) => { if (files.length) { event.preventDefault(); event.returnValue = ""; } };
    window.addEventListener("beforeunload", warn); return () => window.removeEventListener("beforeunload", warn);
  }, [files.length]);
  useEffect(() => {
    if (selectedRun.data && !draft.seriesId) setDraft(current => ({ ...current, seriesId: selectedRun.data!.series_id }));
  }, [selectedRun.data, draft.seriesId]);
  useEffect(() => {
    if (!draft.skillId && targets.length === 1) setDraft(current => ({ ...current, skillId: targets[0].id }));
  }, [draft.skillId, targets.map(item => item.id).join(",")]);
  useEffect(() => { setOrder("all"); }, [draft.runId, draft.revisionId]);
  const previewUrls = useMemo(() => files.map(file => URL.createObjectURL(file)), [files]);
  useEffect(() => () => previewUrls.forEach(url => URL.revokeObjectURL(url)), [previewUrls]);
  const patch = (value: Partial<Draft>) => { if (!locked && !submitLock.current) setDraft(current => ({ ...current, ...value })); };
  const addArtifact = () => {
    if (!selectedRun.data || !revision?.review_digest || !comment.trim()) return;
    const selectedOrder = order === "all" ? null : Number(order);
    const marked: MarkedArtifact = { run_id: selectedRun.data.id, revision_id: revision.id, artifact_digest: revision.review_digest, order: selectedOrder, rating, comment: comment.trim(), title: selectedRun.data.topic_title, cards: revision.cards.filter(card => selectedOrder === null || card.order === selectedOrder), revision_number: revision.revision_number };
    const others = draft.artifacts.filter(item => !(item.run_id === marked.run_id && item.revision_id === marked.revision_id && item.order === marked.order && item.rating === marked.rating));
    if (others.length >= 40) { setError("最多添加 40 条产物评价。"); return; }
    patch({ artifacts: [...others, marked] }); setComment("");
  };
  const submit = useMutation({
    retry: false,
    mutationFn: async () => {
      if (draft.pending) return studioApi.createSkillCalibration(draft.pending);
      if (!draft.seriesId || !draft.skillId || !targetFiles.data?.digest) throw new Error("请选择栏目和要改进的 Skill。");
      if (targetFiles.data.editable === false) throw new Error("这份 Skill 是只读的，不能覆盖；请选择本地可编辑的 Skill。");
      if (!draft.artifacts.length && !draft.references.length && !files.length) throw new Error("至少选择一个已有产物或上传参考作品。");
      if (draft.references.length + files.filter(file => !uploaded.current.has(file)).length > 20) throw new Error("最多添加 20 张参考作品，请先移除多余图片。");
      if (files.some(file => !fileMarks.find(mark => mark.file === file)?.comment.trim())) throw new Error("请为每张参考图写下具体评价。");
      const references = [...draft.references];
      for (let index = 0; index < files.length; index += 1) {
        const file = files[index]; const mark = fileMarks.find(item => item.file === file)!;
        setProgress(`上传参考图 ${index + 1} / ${files.length}`);
        let reference = uploaded.current.get(file);
        if (!reference) { const upload = await studioApi.uploadSkillExtractionImage({ name: file.name, data_base64: await imageBase64(file) }); reference = { ...upload, rating: mark.rating, comment: mark.comment.trim() }; uploaded.current.set(file, reference); }
        const updated = { ...reference, rating: mark.rating, comment: mark.comment.trim() };
        const match = references.findIndex(item => item.id === updated.id);
        if (match < 0) references.push(updated); else references[match] = updated;
        setDraft(current => ({ ...current, references: [...references] }));
      }
      const payload: SkillCalibrationInput = { request_id: crypto.randomUUID().replaceAll("-", ""), series_id: draft.seriesId, skill_id: draft.skillId, expected_skill_digest: targetFiles.data.digest, artifacts: draft.artifacts.map(({ run_id, revision_id, artifact_digest, order: imageOrder, rating: imageRating, comment: text }) => ({ run_id, revision_id, artifact_digest, order: imageOrder, rating: imageRating, comment: text })), references: references.map(item => ({ upload_id: item.id, rating: item.rating, comment: item.comment })), instruction: draft.instruction.trim() };
      setDraft(current => ({ ...current, references, pending: payload }));
      sessionStorage.setItem(storageKey, JSON.stringify({ ...draft, references, pending: payload }));
      setProgress("提交校准草稿");
      return studioApi.createSkillCalibration(payload);
    },
    onSuccess: result => { sessionStorage.removeItem(storageKey); cache.setQueryData(["skill-calibration", result.id], result); cache.setQueryData(["skill-extraction", result.job_id], result.job); void cache.invalidateQueries({ queryKey: ["skill-extractions"] }); goTo(result.id, result.job_id); },
    onError: failure => {
      setError(failure.message);
      if (failure instanceof ApiError && failure.status > 0 && failure.status < 500) setDraft(current => ({ ...current, pending: null }));
      else void existing.refetch();
    },
    onSettled: () => { submitLock.current = false; setProgress(""); },
  });
  const busy = submit.isPending;
  const availableRuns = runs.data?.pages.flatMap(page => page.items).filter(item => item.series_id === draft.seriesId) ?? [];
  const runOptions = selectedRun.data && !availableRuns.some(item => item.id === selectedRun.data!.id) ? [selectedRun.data, ...availableRuns] : availableRuns;
  const errors = [series.error, skills.error, runs.error, selectedRun.error, targetFiles.error].filter(Boolean);
  return <section className="calibration-page">
    <header className="calibration-heading"><Link to={seedRun ? `/runs/${encodeURIComponent(seedRun)}${params.get("revision") ? `?revision=${encodeURIComponent(params.get("revision")!)}` : ""}` : "/skills"} className="button button-secondary"><ArrowLeft size={16} />返回</Link><h1>改进 Skill</h1></header>
    <form onSubmit={event => { event.preventDefault(); if (submitLock.current) return; submitLock.current = true; setError(""); submit.mutate(); }}>
      <fieldset disabled={busy || locked} className="calibration-fields">
        <div className="calibration-targets"><label>栏目<select aria-label="校准栏目" value={draft.seriesId} onChange={event => patch({ seriesId: event.target.value, skillId: "", runId: "", revisionId: "", artifacts: [] })}><option value="">选择栏目</option>{series.data?.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
          <label>要改进的 Skill<select aria-label="要改进的 Skill" value={draft.skillId} onChange={event => patch({ skillId: event.target.value })}><option value="">选择一份 Skill</option>{targets.map(item => <option key={item.id} value={item.id}>{item.display_name || item.name}{item.role === "mind" ? " · Mind" : item.role === "production" ? " · Visualize" : ""}</option>)}</select></label></div>
        {selectedSeries && targets.length === 0 && <p className="extraction-help">这个栏目还没有可编辑的本地 Skill。</p>}
        {targetFiles.data?.editable === false && <p role="alert" className="extraction-error">这份 Skill 是只读的，请选择本地可编辑的 Skill。</p>}
        <section className="calibration-section" aria-labelledby="calibration-artifacts"><h2 id="calibration-artifacts">已有产物</h2>
          <div className="calibration-targets"><label>作品<select aria-label="选择已有产物" value={draft.runId} onChange={event => patch({ runId: event.target.value, revisionId: "" })}><option value="">选择作品</option>{runOptions.map(item => <option key={item.id} value={item.id}>{item.topic_title}</option>)}</select></label><label>版本<select aria-label="对照产物版本" value={revision?.id || ""} onChange={event => patch({ revisionId: event.target.value })}><option value="">选择版本</option>{selectedRun.data?.revisions.map(item => <option key={item.id} value={item.id}>第 {item.revision_number} 版{item.cards.length ? ` · ${item.cards.length} 张` : " · 暂无成品"}</option>)}</select></label></div>
          {runs.hasNextPage && <button type="button" className="button button-secondary calibration-load" disabled={runs.isFetchingNextPage} onClick={() => void runs.fetchNextPage()}>{runs.isFetchingNextPage ? "读取中…" : "加载更早的作品"}</button>}
          {revision && <>
            {!!revision.cards.length && <div className="calibration-filmstrip">{revision.cards.map(card => <a key={card.order} href={apiUrl(card.url)} target="_blank" rel="noreferrer"><img src={apiUrl(card.url)} alt={`产物第 ${card.order} 张`} /><span>第 {card.order} 张</span></a>)}</div>}
            {!revision.review_digest || !revision.cards.length ? <p className="extraction-help">这版还没有可核验的完整产物，请选择已完成的版本。</p> : <div className="calibration-mark">
              <label>评价范围<select aria-label="评价范围" value={order} onChange={event => setOrder(event.target.value)}><option value="all">整组</option>{revision.cards.map(card => <option key={card.order} value={card.order}>第 {card.order} 张</option>)}</select></label>
              <Rating value={rating} onChange={setRating} label="产物评价" />
              <label className="calibration-comment">具体哪里好、哪里不好<textarea aria-label="产物具体评价" value={comment} rows={2} maxLength={2000} onChange={event => setComment(event.target.value)} placeholder="例如：结尾只讲道理，没有用开头的蛋糕完成行动回扣。" /></label>
              <button type="button" className="button button-secondary" disabled={!comment.trim()} onClick={addArtifact}><Plus size={16} />加入对照</button>
            </div>}
          </>}
          {!!draft.artifacts.length && <div className="calibration-annotations" aria-label="已加入的产物评价">{draft.artifacts.map((item, index) => <article key={`${item.run_id}-${item.revision_id}-${item.order}-${item.rating}`}><div className="calibration-annotation-title"><strong>{item.title} · 第 {item.revision_number} 版 · {item.order === null ? "整组" : `第 ${item.order} 张`}</strong><span className={`calibration-rating ${item.rating}`}>{item.rating === "good" ? "好在哪里" : "需要改进"}</span><button type="button" className="button button-quiet danger-link" aria-label={`移除评价 ${index + 1}`} onClick={() => patch({ artifacts: draft.artifacts.filter((_, position) => position !== index) })}><Trash2 size={16} /></button></div><p>{item.comment}</p></article>)}</div>}
        </section>
        <section className="calibration-section" aria-labelledby="calibration-references"><h2 id="calibration-references">参考作品</h2><ExtractionImagePicker files={files} disabled={busy || locked} onError={setError} onChange={next => { setFiles(next); setFileMarks(current => next.map(file => current.find(item => item.file === file) || { file, rating: "good", comment: "" })); }} />
          {!!files.length && <p className="extraction-help">图片尚未提交；刷新会丢失未上传的图片。下面写下每张的评价。</p>}
          {files.map((file, index) => { const mark = fileMarks.find(item => item.file === file)!; return <div className="calibration-reference-mark" key={`${file.name}-${file.lastModified}-${index}`}><img src={previewUrls[index]} alt={file.name} /><div><strong>{file.name}</strong><Rating value={mark?.rating || "good"} label={`参考图 ${index + 1} 评价`} onChange={value => setFileMarks(current => current.map(item => item.file === file ? { ...item, rating: value } : item))} /><textarea aria-label={`参考图 ${index + 1} 具体评价`} placeholder="值得借鉴或需要避免的具体特点" rows={2} maxLength={2000} value={mark?.comment || ""} onChange={event => setFileMarks(current => current.map(item => item.file === file ? { ...item, comment: event.target.value } : item))} /></div></div>; })}
          {draft.references.filter(item => !files.some(file => uploaded.current.get(file)?.id === item.id)).map(item => <article className="calibration-reference-mark" key={item.id}><img src={apiUrl(item.url)} alt={item.name} /><div><strong>{item.name}</strong><Rating value={item.rating} label={`${item.name} 评价`} onChange={value => patch({ references: draft.references.map(reference => reference.id === item.id ? { ...reference, rating: value } : reference) })} /><textarea aria-label={`${item.name} 具体评价`} rows={2} value={item.comment} onChange={event => patch({ references: draft.references.map(reference => reference.id === item.id ? { ...reference, comment: event.target.value } : reference) })} /><button type="button" className="button button-quiet danger-link" onClick={() => patch({ references: draft.references.filter(reference => reference.id !== item.id) })}>移除参考</button></div></article>)}
        </section>
        <label>其他要求（可选）<textarea aria-label="校准其他要求" rows={2} maxLength={4000} value={draft.instruction} onChange={event => patch({ instruction: event.target.value })} placeholder="例如：只改故事方法，不改变角色和画风。" /></label>
      </fieldset>
      {errors.map((failure, index) => <p role="alert" className="extraction-error" key={index}>{failure?.message}</p>)}
      {(error || storageError) && <p role="alert" className="extraction-error">{error || storageError}</p>}
      {locked && !busy && <p role="status" className="extraction-help">提交结果待核对；表单已保留，继续使用同一次请求恢复，不会另开任务。</p>}
      <footer className="calibration-submit"><span>先生成独立草稿，不修改共享 Skill。</span><button className="button button-primary" disabled={busy || (!locked && (!draft.skillId || !targetFiles.data?.digest || targetFiles.data.editable === false || (!draft.artifacts.length && !files.length && !draft.references.length)))}>{busy ? progress || "正在提交…" : locked ? "核对并恢复这次提交" : "生成修改草稿"}</button></footer>
    </form>
  </section>;
}

function Rating({ value, onChange, label }: { value: SkillCalibrationRating; onChange: (value: SkillCalibrationRating) => void; label: string }) {
  return <div className="calibration-rating-control" role="group" aria-label={label}><button type="button" aria-pressed={value === "good"} onClick={() => onChange("good")}>好在哪里</button><button type="button" aria-pressed={value === "bad"} onClick={() => onChange("bad")}>需要改进</button></div>;
}
