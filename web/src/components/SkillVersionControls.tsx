import { useEffect, useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ChevronDown, GitBranch } from "lucide-react";
import { ApiError, studioApi } from "../api/client";
import type { ProducerSkillItem, SkillVersionHistory } from "../api/types";

type PendingVersionAction = { kind: "publish"; skill_id: string; expected_digest: string; request_id: string }
  | { kind: "switch"; skill_id: string; expected_digest: string; series_id: string; expected_revision: number; request_id: string };
const actionKey = (id: string) => `creatoros.skill-version-action.v1:${id}`;
function readPending(id: string): PendingVersionAction | null {
  try {
    const item = JSON.parse(sessionStorage.getItem(actionKey(id)) ?? "null");
    return item && item.skill_id === id && typeof item.request_id === "string" && typeof item.expected_digest === "string"
      && (item.kind === "publish" || item.kind === "switch" && typeof item.series_id === "string" && Number.isInteger(item.expected_revision)) ? item : null;
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
  const [selectedSeries, setSelectedSeries] = useState(seriesId ?? "");
  const [columnOpen, setColumnOpen] = useState(false);
  const [pendingAction, setPendingAction] = useState(() => readPending(skill.id));
  const [feedback, setFeedback] = useState("");
  const [actionError, setActionError] = useState("");
  const createRequest = useRef(crypto.randomUUID().replaceAll("-", ""));
  const candidate = skill.version_status === "candidate";
  const bindings = history.bindings;
  const binding = bindings.find(item => item.series_id === selectedSeries)
    ?? (!selectedSeries && bindings.length === 1 ? bindings[0] : undefined);
  const selectedSeriesId = binding?.series_id ?? "";
  const alreadyUsed = binding?.skill_id === skill.id;
  const refresh = async () => {
    await Promise.all([queryClient.invalidateQueries({ queryKey: ["producer-skill-versions"] }),
      queryClient.invalidateQueries({ queryKey: ["producer-skill-files", skill.id] }),
      queryClient.invalidateQueries({ queryKey: ["producer-skill-file", skill.id] }),
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
      try { sessionStorage.removeItem(`creatoros.skill-version-create.${skill.id}.${digest}`); } catch { /* Accepted request remains in history. */ }
      onChoose(result); void refresh();
    },
  });
  const clearPending = () => {
    setPendingAction(null);
    try { sessionStorage.removeItem(actionKey(skill.id)); } catch { /* Server receipt remains authoritative. */ }
  };
  const apply = useMutation({ retry: false,
    mutationFn: async (input: PendingVersionAction) => input.kind === "publish"
      ? { kind: "publish" as const, result: await studioApi.publishSkillVersion(input.skill_id,
        { expected_digest: input.expected_digest, request_id: input.request_id }) }
      : { kind: "switch" as const, result: await studioApi.switchSeriesSkillVersion(input.series_id,
        { skill_id: input.skill_id, expected_revision: input.expected_revision, expected_digest: input.expected_digest, request_id: input.request_id }) },
    onSuccess: result => {
      clearPending(); setActionError("");
      setFeedback(result.kind === "publish" ? "版本已保存，栏目保持不变。" : "栏目版本已切换。旧产物和进行中的任务保持不变。");
      if (result.kind === "publish") onChoose(result.result);
      void refresh();
    },
    onError: (error: Error) => {
      const rejected = error instanceof ApiError && error.status >= 400 && error.status < 500;
      if (rejected) clearPending();
      setActionError(`${error.message}${rejected ? " 请重新读取并核对后操作。" : " 结果待核对，请恢复同一请求。"}`);
    },
  });
  const submit = (kind: PendingVersionAction["kind"]) => {
    if (disabled || pendingAction || apply.isPending || create.isPending || !digest) return;
    if (kind === "switch" && !binding) return;
    if (!window.confirm(kind === "publish" ? "保存这个版本？现有栏目保持不变。"
      : `将「${binding!.series_name}」切换到 ${versionLabel(skill)}？旧产物和正在进行的任务保持不变。`)) return;
    const identity = { skill_id: skill.id, expected_digest: digest, request_id: crypto.randomUUID().replaceAll("-", "") };
    const input: PendingVersionAction = kind === "publish" ? { ...identity, kind }
      : { ...identity, kind, series_id: binding!.series_id, expected_revision: binding!.revision };
    try { sessionStorage.setItem(actionKey(skill.id), JSON.stringify(input)); }
    catch { setActionError("无法暂存操作身份，请检查浏览器存储后重试。"); return; }
    setPendingAction(input); setActionError(""); setFeedback(""); apply.mutate(input);
  };
  const pending = create.isPending || apply.isPending;
  useEffect(() => { onBusy(pending); }, [pending, onBusy]);
  useEffect(() => () => onBusy(false), [onBusy]);
  const locked = disabled || pending || !!pendingAction;
  const problem = create.error?.message ?? actionError;
  return <section className="skill-version-controls" aria-label="Skill 版本管理">
    <div className="skill-version-bar">
      <GitBranch size={16} aria-hidden="true" />
      <label className="skill-version-select"><span className="sr-only">查看版本</span>
        <select aria-label="查看 Skill 版本" value={skill.id} disabled={locked} onChange={event => {
          const next = history.items.find(item => item.id === event.target.value); if (next) onChoose(next);
        }}>{history.items.map(item => <option key={item.id} value={item.id}>{versionLabel(item)}{bindings.some(value => value.skill_id === item.id) ? " · 栏目在用" : ""}</option>)}</select>
      </label>
      <span className={`skill-version-state${candidate ? " is-candidate" : ""}`}>{candidate ? "修改候选" : "已保存版本"}</span>
      {candidate ? <button type="button" className="skill-inspector-add" disabled={locked || !digest}
        onClick={() => submit("publish")}>{apply.isPending && apply.variables?.kind === "publish" ? "正在保存…" : "保存版本"}</button>
        : skill.id.includes("--") && <button type="button" className="skill-version-new" disabled={locked || !digest}
          onClick={() => { setFeedback(""); create.mutate(); }}>{create.isPending ? "正在创建…" : "创建修改候选"}</button>}
      {bindings.length > 0 && <button type="button" className="skill-version-column-toggle" aria-expanded={columnOpen} disabled={locked}
        onClick={() => setColumnOpen(value => !value)}>栏目版本<ChevronDown size={14} aria-hidden="true" /></button>}
    </div>
    {columnOpen && <div className="skill-version-workbench">
      <label className="skill-version-series">栏目<select aria-label="版本应用栏目" value={selectedSeriesId} disabled={locked}
        onChange={event => { setSelectedSeries(event.target.value); setFeedback(""); }}>
        <option value="">选择栏目</option>{bindings.map(item => <option key={item.series_id} value={item.series_id}>{item.series_name}</option>)}</select></label>
      <div className="skill-version-apply">
        <span>{binding ? alreadyUsed ? `「${binding.series_name}」正在使用此版本` : `只切换「${binding.series_name}」，其他栏目不变` : "请选择要切换版本的栏目"}</span>
        <button type="button" className="skill-inspector-add" disabled={locked || !binding || alreadyUsed}
          onClick={() => submit("switch")}>{apply.isPending && apply.variables?.kind === "switch" ? "正在切换…" : "使用这个版本"}</button>
      </div>
    </div>}
    {(problem || feedback) && <p className="skill-version-feedback" role={problem ? "alert" : "status"}>{problem || feedback}
      {problem && <button type="button" disabled={pending} onClick={() => void refresh()}>重新读取状态</button>}</p>}
    {pendingAction && !apply.isPending && <p className="skill-version-feedback" role="status">上次操作待核对 <button type="button" disabled={disabled || pending}
      onClick={() => apply.mutate(pendingAction)}>核对并恢复上次操作</button></p>}
  </section>;
}
