import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FlaskConical, GitBranch } from "lucide-react";
import { apiUrl, studioApi } from "../api/client";
import type { ProducerSkillItem, SkillVersionHistory } from "../api/types";

function trialKey(id: string) { return `creatoros.skill-version-trial.${id}`; }
function savedTrial(id: string) { try { return sessionStorage.getItem(trialKey(id)) ?? ""; } catch { return ""; } }
type PendingTrial = { signature: string; id: string; topic: string; series_id: string };
function pendingTrial(id: string): PendingTrial | null {
  try {
    const value = JSON.parse(sessionStorage.getItem(`creatoros.skill-version-pending.${id}`) ?? "null");
    return value && [value.signature, value.id, value.topic, value.series_id].every(item => typeof item === "string") ? value : null;
  } catch { return null; }
}
export function versionLabel(skill: ProducerSkillItem) {
  return `v${skill.version_number ?? 1}${skill.version_status === "candidate" ? " · 候选" : ""}`;
}

export function SkillVersionControls({ skill, digest, history, seriesId, disabled, onChoose, onBusy }: {
  skill: ProducerSkillItem; digest: string; history: SkillVersionHistory; seriesId?: string;
  disabled: boolean; onChoose: (skill: ProducerSkillItem) => void; onBusy: (busy: boolean) => void;
}) {
  const queryClient = useQueryClient();
  const [restoredTrial] = useState(() => pendingTrial(skill.id));
  const [selectedSeries, setSelectedSeries] = useState(restoredTrial?.series_id ?? seriesId ?? "");
  const [trialOpen, setTrialOpen] = useState(!!restoredTrial);
  const [topic, setTopic] = useState(restoredTrial?.topic ?? "");
  const [jobId, setJobId] = useState(skill.version_trial_job_id ?? skill.latest_trial_job_id ?? savedTrial(skill.id));
  const [feedback, setFeedback] = useState("");
  const createRequest = useRef(crypto.randomUUID().replaceAll("-", ""));
  const trialRequest = useRef<PendingTrial | null>(restoredTrial);
  const switchRequest = useRef<{ signature: string; id: string } | null>(null);
  const publishRequest = useRef<{ signature: string; id: string } | null>(null);
  const candidate = skill.version_status === "candidate";
  const bindings = history.bindings;
  const binding = bindings.find(item => item.series_id === selectedSeries)
    ?? (!selectedSeries && bindings.length === 1 ? bindings[0] : undefined);
  const selectedSeriesId = binding?.series_id ?? "";
  const job = useQuery({ queryKey: ["skill-version-trial", jobId],
    queryFn: () => studioApi.skillExtraction(jobId), enabled: !!jobId,
    retry: false, refetchInterval: query => {
      const data = query.state.data;
      return data && (data.operation || data.queue_state === "queued" || data.queue_state === "running") ? 2000 : false;
    } });
  const currentJob = job.data?.version_candidate_id === skill.id ? job.data : undefined;
  const matchingJob = currentJob?.version_candidate_digest === digest;
  const latestTrial = currentJob?.trials?.at(-1);
  const completedTrial = matchingJob && latestTrial?.status === "completed" && latestTrial.digest === currentJob?.digest
    && latestTrial.cards.length > 0 ? latestTrial : undefined;
  const busyTrial = !!currentJob && (!!currentJob.operation || currentJob.queue_state === "queued" || currentJob.queue_state === "running");
  const alreadyUsed = binding?.skill_id === skill.id;
  const refresh = async () => {
    await Promise.all([queryClient.invalidateQueries({ queryKey: ["producer-skill-versions"] }),
      queryClient.invalidateQueries({ queryKey: ["producer-skills"] }),
      queryClient.invalidateQueries({ queryKey: ["series"] }),
      queryClient.invalidateQueries({ queryKey: ["series-all"] }),
      queryClient.invalidateQueries({ queryKey: ["overview"] })]);
  };
  const create = useMutation({ retry: false,
    mutationFn: () => {
      const key = `creatoros.skill-version-create.${skill.id}.${digest}`;
      try { createRequest.current = sessionStorage.getItem(key) ?? createRequest.current; sessionStorage.setItem(key, createRequest.current); }
      catch { throw new Error("无法暂存创建请求。请检查浏览器存储后重试，避免重复创建版本。"); }
      return studioApi.createSkillVersion(skill.id, { expected_digest: digest, request_id: createRequest.current });
    },
    onSuccess: result => {
      try { sessionStorage.removeItem(`creatoros.skill-version-create.${skill.id}.${digest}`); } catch { /* Accepted request is recoverable in history. */ }
      onChoose(result);
      void refresh();
    },
  });
  const trial = useMutation({ retry: false,
    mutationFn: () => {
      const signature = JSON.stringify([digest, selectedSeriesId, topic.trim()]);
      if (trialRequest.current?.signature !== signature) trialRequest.current = { signature, id: crypto.randomUUID().replaceAll("-", ""), topic: topic.trim(), series_id: selectedSeriesId };
      try { sessionStorage.setItem(`creatoros.skill-version-pending.${skill.id}`, JSON.stringify(trialRequest.current)); }
      catch { throw new Error("无法暂存试用请求。请检查浏览器存储后重试，避免重复生图。"); }
      return studioApi.createSkillVersionTrial(skill.id, { expected_digest: digest, request_id: trialRequest.current.id,
        ...(selectedSeriesId ? { series_id: selectedSeriesId } : {}), topic: topic.trim() });
    }, onSuccess: async result => {
      trialRequest.current = null;
      try { sessionStorage.removeItem(`creatoros.skill-version-pending.${skill.id}`); } catch { /* Read current server task before retrying. */ }
      setJobId(result.id); queryClient.setQueryData(["skill-version-trial", result.id], result);
      try { sessionStorage.setItem(trialKey(skill.id), result.id); } catch { /* Server history also retains the trial. */ }
      await refresh();
    },
  });
  const activate = useMutation({ retry: false,
    mutationFn: () => {
      if (!binding) throw new Error("请先选择栏目。");
      const signature = JSON.stringify([skill.id, binding.series_id, binding.revision, currentJob?.id, completedTrial?.id]);
      if (switchRequest.current?.signature !== signature) switchRequest.current = { signature, id: crypto.randomUUID().replaceAll("-", "") };
      return studioApi.switchSeriesSkillVersion(binding.series_id, { skill_id: skill.id, expected_revision: binding.revision,
        request_id: switchRequest.current.id, ...(completedTrial && currentJob?.version_series_id === binding.series_id
          && currentJob.version_series_revision === binding.revision
          ? { trial_job_id: currentJob.id, trial_id: completedTrial.id } : {}) });
    }, onSuccess: async () => { setFeedback(`「${binding?.series_name}」已切换到 v${skill.version_number ?? 1}。`); await refresh(); },
  });
  const publish = useMutation({ retry: false,
    mutationFn: () => {
      if (!currentJob || !completedTrial) throw new Error("请先完成本候选的试用。");
      const signature = JSON.stringify([digest, currentJob.id, completedTrial.id]);
      if (publishRequest.current?.signature !== signature) publishRequest.current = { signature, id: crypto.randomUUID().replaceAll("-", "") };
      return studioApi.publishSkillVersion(skill.id, { expected_digest: digest, trial_job_id: currentJob.id,
        trial_id: completedTrial.id, request_id: publishRequest.current.id });
    }, onSuccess: async result => { setFeedback("版本已保存，可以用于新栏目。"); await refresh(); onChoose(result); },
  });
  const pending = create.isPending || trial.isPending || activate.isPending || publish.isPending;
  useEffect(() => {
    const storedJobId = skill.version_trial_job_id ?? skill.latest_trial_job_id;
    if (storedJobId && storedJobId !== jobId && !trial.isPending) setJobId(storedJobId);
  }, [skill.version_trial_job_id, skill.latest_trial_job_id, trial.isPending]);
  useEffect(() => {
    if (currentJob && trialRequest.current?.id === currentJob.request_id) {
      trialRequest.current = null;
      try { sessionStorage.removeItem(`creatoros.skill-version-pending.${skill.id}`); } catch { /* Server task remains authoritative. */ }
    }
  }, [currentJob?.id, currentJob?.request_id, skill.id]);
  useEffect(() => { onBusy(pending); }, [pending, onBusy]);
  useEffect(() => () => onBusy(false), [onBusy]);
  const problem = create.error || trial.error || activate.error || publish.error;
  const requiresCompanion = skill.role === "mind" || skill.role === "production" && bindings.length > 0;
  const canTrial = candidate && !!topic.trim() && !busyTrial && !(jobId && (job.isPending || job.isError))
    && (!requiresCompanion || !!binding);
  return <section className="skill-version-controls" aria-label="Skill 版本管理">
    <div className="skill-version-bar">
      <GitBranch size={16} aria-hidden="true" />
      <label className="skill-version-select"><span className="sr-only">查看版本</span>
        <select aria-label="查看 Skill 版本" value={skill.id} disabled={disabled || pending} onChange={event => {
          const next = history.items.find(item => item.id === event.target.value); if (next) onChoose(next);
        }}>{history.items.map(item => <option key={item.id} value={item.id}>{versionLabel(item)}{bindings.some(value => value.skill_id === item.id) ? " · 栏目在用" : ""}</option>)}</select>
      </label>
      <span className={`skill-version-state${candidate ? " is-candidate" : ""}`}>{candidate ? "修改候选" : "已保存版本"}</span>
      {!candidate && skill.id.includes("--") && <button type="button" className="skill-version-new" disabled={disabled || pending || !digest}
        onClick={() => { setFeedback(""); create.mutate(); }}>{create.isPending ? "正在创建…" : "创建修改候选"}</button>}
      <button type="button" className="skill-version-trial-toggle" aria-expanded={trialOpen} disabled={disabled || pending}
        onClick={() => setTrialOpen(value => !value)}><FlaskConical size={15} aria-hidden="true" />{candidate ? "试用与启用" : "栏目版本"}</button>
    </div>
    {trialOpen && <div className="skill-version-workbench">
      {bindings.length > 0 && <label className="skill-version-series">栏目<select aria-label="版本应用栏目" value={selectedSeriesId} disabled={disabled || pending || busyTrial}
        onChange={event => { setSelectedSeries(event.target.value); setFeedback(""); }}>
        <option value="">选择栏目</option>{bindings.map(item => <option key={item.series_id} value={item.series_id}>{item.series_name}</option>)}</select></label>}
      {candidate && <form className="skill-version-trial-form" onSubmit={event => { event.preventDefault(); if (canTrial && !disabled && !pending) trial.mutate(); }}>
        <label>试用选题<input aria-label="版本试用选题" value={topic} maxLength={1000} placeholder="用一个熟悉的选题检查这次修改" disabled={disabled || pending || busyTrial}
          onChange={event => setTopic(event.target.value)} /></label>
        <button type="submit" className="skill-inspector-add" disabled={disabled || pending || !canTrial}>{busyTrial ? "试用中…" : trial.isPending ? "正在提交…" : "干净线程试用"}</button>
      </form>}
      {candidate && requiresCompanion && !binding && <p className="skill-version-help">请选择栏目，试用会带上该栏目的另一份 Skill。</p>}
      {job.isPending && jobId && <p role="status">正在读取试用…</p>}
      {job.isError && <p role="alert">{job.error.message} <button type="button" onClick={() => void job.refetch()}>重新读取</button></p>}
      {currentJob && <div className="skill-version-trial-result">
        <p role="status">{busyTrial ? currentJob.queue_state === "queued" ? "试用已排队，可以关闭窗口稍后查看。" : "Codex 正在试用，可以关闭窗口稍后查看。"
          : currentJob.trials?.at(-1)?.status === "failed" || currentJob.status === "failed" ? "试用失败，可查看任务记录后重试。"
          : completedTrial ? "试用完成，检查图片后再确认启用。" : !matchingJob ? "候选已修改，需要重新试用。" : "试用记录已保存。"}</p>
        <a href={`/skills?extraction=${encodeURIComponent(currentJob.id)}`} target="_blank" rel="noopener noreferrer">查看试用记录</a>
        {currentJob.trials?.at(-1)?.error && <p role="alert">{currentJob.trials.at(-1)!.error}</p>}
        {(currentJob.trials?.at(-1)?.cards?.length ?? 0) > 0 && <div className="skill-version-trial-images">{currentJob.trials!.at(-1)!.cards.map(card =>
          <a href={apiUrl(card.url)} key={card.order} target="_blank" rel="noopener noreferrer"><img src={apiUrl(card.url)} alt={`试用第 ${card.order} 张`} /></a>)}</div>}
      </div>}
      <div className="skill-version-apply">
        {binding ? <><span>{alreadyUsed ? `「${binding.series_name}」正在使用此版本` : `只切换「${binding.series_name}」，其他栏目不变`}</span>
          <button type="button" className="skill-inspector-add" disabled={disabled || pending || busyTrial || alreadyUsed || candidate && (!completedTrial || currentJob?.version_series_id !== binding.series_id)}
            onClick={() => { if (window.confirm(`将「${binding.series_name}」切换到 ${versionLabel(skill)}？旧产物和正在进行的任务保持不变。`)) activate.mutate(); }}>
            {activate.isPending ? "正在切换…" : candidate ? "满意，切换栏目" : "使用这个版本"}</button></>
          : candidate && bindings.length === 0 ? <><span>保存后可用于新栏目</span><button type="button" className="skill-inspector-add" disabled={disabled || pending || busyTrial || !completedTrial}
            onClick={() => { if (window.confirm("确认保存这个已试用的版本？现有栏目不会自动切换。")) publish.mutate(); }}>{publish.isPending ? "正在保存…" : "满意，保存版本"}</button></>
          : !candidate && <span>{bindings.length ? "请选择要切换版本的栏目" : "此版本尚未用于栏目，可在 Skill 库中组建栏目。"}</span>}
      </div>
    </div>}
    {(problem || feedback) && <p className="skill-version-feedback" role={problem ? "alert" : "status"}>{problem?.message ?? feedback}
      {problem && <button type="button" disabled={pending} onClick={() => void refresh()}>重新读取状态</button>}</p>}
  </section>;
}
