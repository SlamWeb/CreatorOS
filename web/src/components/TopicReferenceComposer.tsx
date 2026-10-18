import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { referenceApi } from "../api/reference-inputs";
import { imageBase64 } from "./AgentAttachments";
import { ExtractionImagePicker } from "./ExtractionImagePicker";
import "./artifact-skill-extraction.css";
import "./topic-reference-composer.css";

export function TopicReferenceComposer({ seriesId, researching, onResearch, onBusy }: {
  seriesId: string; researching: boolean; onResearch: (id: string) => void; onBusy: (busy: boolean) => void;
}) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [count, setCount] = useState(10);
  const [instructions, setInstructions] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [presentation, setPresentation] = useState(false);
  const [error, setError] = useState("");
  const [activity, setActivity] = useState("");
  const busy = useRef(false);
  const uploads = useRef(new WeakMap<File, string>());
  const references = useRef(new Map<string, string>());
  const requests = useRef(new Map<string, string>());
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);

  async function submit(action: "research" | "produce") {
    if (busy.current || researching) return;
    if (action === "produce" && (!files.length || !instructions.trim())) {
      setError("请添加参考图片，并写下这次想制作的内容。"); return;
    }
    busy.current = true;
    onBusy(true);
    setError("");
    setActivity(files.length ? "上传参考图片…" : "提交调研…");
    try {
      let referenceId: string | undefined;
      if (files.length) {
        const ids: string[] = [];
        for (let index = 0; index < files.length; index++) {
          const file = files[index];
          let id = uploads.current.get(file);
          if (!id) {
            setActivity(`上传参考图片 ${index + 1} / ${files.length}…`);
            id = (await referenceApi.upload(seriesId, file.name, await imageBase64(file))).id;
            uploads.current.set(file, id);
          }
          if (!ids.includes(id)) ids.push(id);
        }
        const signature = JSON.stringify([ids, instructions, presentation]);
        referenceId = references.current.get(signature);
        if (!referenceId) {
          referenceId = (await referenceApi.create(seriesId, ids, instructions, presentation)).id;
          references.current.set(signature, referenceId);
        }
      }
      if (action === "research") {
        setActivity("提交选题调研…");
        const batch = await referenceApi.research(seriesId, count, instructions, referenceId);
        if (mounted.current) onResearch(batch.id);
      } else {
        setActivity("提交制作任务…");
        const signature = referenceId!;
        let requestId = requests.current.get(signature);
        if (!requestId) {
          const storageKey = `creatoros.reference-production.v1:${seriesId}:${signature}`;
          try { requestId = sessionStorage.getItem(storageKey) ?? undefined; } catch { /* Memory retry still works. */ }
          requestId ??= crypto.randomUUID().replaceAll("-", "");
          try { sessionStorage.setItem(storageKey, requestId); } catch { /* Storage may be unavailable. */ }
          requests.current.set(signature, requestId);
        }
        const run = await referenceApi.produce(seriesId, referenceId!, Array.from(instructions.trim()).slice(0, 240).join(""), requestId);
        await Promise.all([["topics"], ["runs"], ["overview"]].map(queryKey => queryClient.invalidateQueries({ queryKey })));
        if (mounted.current) navigate(`/runs/${encodeURIComponent(run.id)}`);
      }
    } catch (failure) {
      setError(`${failure instanceof Error ? failure.message : "提交失败，请稍后重试。"} 图片和要求已保留；结果未知时请先查看记录，重试同一制作不会重复创建。`);
    } finally {
      busy.current = false;
      setActivity("");
      onBusy(false);
    }
  }

  return <form className="research-form topic-reference-composer" aria-busy={!!activity} onSubmit={event => { event.preventDefault(); void submit("research"); }}>
    <fieldset disabled={!!activity || researching}>
      <ExtractionImagePicker files={files} disabled={!!activity || researching} onChange={setFiles} onError={setError} />
      <div className="topic-reference-fields">
        <label>候选数量<input type="number" min={1} max={30} required value={count} onChange={event => setCount(event.target.valueAsNumber)} /></label>
        <label>本次偏好<textarea maxLength={3000} rows={3} placeholder={files.length ? "想借鉴哪个选题、切入点？直接制作时，请写下本次具体要求。" : "例如：面向初学者，暂不讨论模型训练"} value={instructions} onChange={event => setInstructions(event.target.value)} /></label>
      </div>
      {!!files.length && <label className="topic-reference-presentation"><input type="checkbox" checked={presentation} onChange={event => setPresentation(event.target.checked)} />同时参考呈现<span>默认只借鉴内容</span></label>}
      <div className="topic-reference-actions">
        <button className="button button-primary" type="submit">{files.length ? "扩展选题" : "开始联网调研"}</button>
        {!!files.length && <button className="button button-secondary" type="button" onClick={() => void submit("produce")}>直接制作</button>}
        <small>{files.length ? "扩展为待选；直接制作进入生产队列。" : "使用 Codex 额度，结果保存为待选。"}</small>
      </div>
    </fieldset>
    {activity && <p className="topic-reference-status" role="status"><span className="task-running-text">{activity}</span></p>}
    {error && <p className="form-error" role="alert">{error}</p>}
  </form>;
}
