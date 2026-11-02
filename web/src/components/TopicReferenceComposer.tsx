import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { referenceApi } from "../api/reference-inputs";
import { request } from "../api/client";
import type { SkillLibraryView } from "../api/types";
import { imageBase64 } from "./AgentAttachments";
import { ExtractionImagePicker } from "./ExtractionImagePicker";
import "./artifact-skill-extraction.css";
import "./topic-reference-composer.css";

export function TopicReferenceComposer({ seriesId, onResearch, onBusy }: {
  seriesId: string; onResearch: (id: string) => void; onBusy: (busy: boolean) => void;
}) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [count, setCount] = useState(10);
  const [instructions, setInstructions] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [presentation, setPresentation] = useState(false);
  const [selectedSkills, setSelectedSkills] = useState<string[]>([]);
  const skillPool = useQuery({ queryKey: ["series-skills", seriesId], queryFn: () => request<SkillLibraryView>(`/api/series/${encodeURIComponent(seriesId)}/skills`), retry: false });
  const skillChoices = skillPool.data?.available_versions ?? skillPool.data?.items ?? [];
  const [error, setError] = useState("");
  const [activity, setActivity] = useState("");
  const busy = useRef(false);
  const uploads = useRef(new WeakMap<File, string>());
  const references = useRef(new Map<string, string>());
  const requests = useRef(new Map<string, string>());
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);

  async function submit(action: "research" | "produce") {
    if (busy.current) return;
    if (action === "produce" && (!files.length || !instructions.trim())) {
      setError("请添加参考图片，并写下这次想制作的内容。"); return;
    }
    if (action === "produce" && !selectedSkills.length && !skillPool.data?.defaults.image?.length) {
      setError("请从栏目 Skill 池勾选本次使用的版本。"); return;
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
        const run = await referenceApi.produce(seriesId, referenceId!, Array.from(instructions.trim()).slice(0, 240).join(""), requestId, selectedSkills.length ? selectedSkills : undefined);
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
    <fieldset disabled={!!activity}>
      <ExtractionImagePicker files={files} disabled={!!activity} onChange={setFiles} onError={setError} />
      <div className="topic-reference-fields">
        <label>候选数量<input type="number" min={1} max={30} required value={count} onChange={event => setCount(event.target.valueAsNumber)} /></label>
        <label>本次偏好<textarea maxLength={3000} rows={3} placeholder={files.length ? "想借鉴哪个选题、切入点？直接制作时，请写下本次具体要求。" : "例如：面向初学者，暂不讨论模型训练"} value={instructions} onChange={event => setInstructions(event.target.value)} /></label>
      </div>
      {!!files.length && <label className="topic-reference-presentation"><input type="checkbox" checked={presentation} onChange={event => setPresentation(event.target.checked)} />同时参考呈现<span>默认只借鉴内容</span></label>}
      {!!files.length && <fieldset className="topic-reference-skills">
        <legend>本次制作 Skill</legend>
        <small>可勾选一份或多份精确版本，仅用于本次制作，不更改栏目设置。{!!skillPool.data?.defaults.image?.length && " 不勾选时沿用图片阶段默认。"}</small>
        {skillPool.isPending ? <p role="status">加载栏目 Skill…</p> : skillPool.isError ? <p role="alert">栏目 Skill 加载失败，请重新打开后再制作。</p> : !skillChoices.length ? <p>栏目 Skill 池为空，请先在栏目设置中添加 Skill。</p> : <div className="topic-reference-skill-options">{skillChoices.map(skill => <label key={skill.id}>
          <input type="checkbox" checked={selectedSkills.includes(skill.id)} onChange={event => { setSelectedSkills(previous => event.target.checked ? [...previous, skill.id] : previous.filter(id => id !== skill.id)); setError(previous => previous === "请从栏目 Skill 池勾选本次使用的版本。" ? "" : previous); }} />
          <span>{skill.id === "knowledge-to-carousel" ? "知识点轮播（内置）" : skill.display_name || skill.name}{skill.version_number ? ` · v${skill.version_number}` : ""}{skill.version_status === "candidate" ? " · 候选" : ""}</span>
        </label>)}</div>}
      </fieldset>}
      <div className="topic-reference-actions">
        <button className="button button-primary" type="submit">{files.length ? "扩展选题" : "开始联网调研"}</button>
        {!!files.length && <button className="button button-secondary" type="button" disabled={skillPool.isPending || skillPool.isError} onClick={() => void submit("produce")}>直接制作</button>}
        <small>{files.length ? "扩展为待选；直接制作进入生产队列。" : "使用 Codex 额度，结果保存为待选。"}</small>
      </div>
    </fieldset>
    {activity && <p className="topic-reference-status" role="status"><span className="task-running-text">{activity}</span></p>}
    {error && <p className="form-error" role="alert">{error}</p>}
  </form>;
}
