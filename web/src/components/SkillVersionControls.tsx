import { useEffect, useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ChevronDown, GitBranch } from "lucide-react";
import { ApiError, isUnknownSkillWrite, studioApi } from "../api/client";
import type { ProducerSkillItem, SkillVersionHistory } from "../api/types";

type PendingVersionAction = { kind: "publish"; skill_id: string; expected_digest: string; request_id: string }
  | { kind: "switch"; skill_id: string; expected_digest: string; series_id: string; expected_revision: number; request_id: string };
const actionKey = (scope: string, id: string) => `creatoros.skill-version-action.v2:${encodeURIComponent(scope)}:${encodeURIComponent(id)}`;
const createUnknownKey = (scope: string, id: string) => `creatoros.skill-version-create-unknown.v2:${encodeURIComponent(scope)}:${encodeURIComponent(id)}`;
const creationRequestKey = (scope: string, id: string, digest: string) => `creatoros.skill-version-create.v2:${encodeURIComponent(scope)}:${encodeURIComponent(id)}:${digest}`;
function readUnknownCreation(scope: string, id: string): { skillId?: string } | null {
  try {
    const item = JSON.parse(sessionStorage.getItem(createUnknownKey(scope, id)) ?? "null") as { skillId?: string } | null;
    return item && typeof item === "object" ? item : null;
  } catch { return null; }
}
function readPending(scope: string, id: string): PendingVersionAction | null {
  try {
    const item = JSON.parse(sessionStorage.getItem(actionKey(scope, id)) ?? "null");
    return item && item.skill_id === id && typeof item.request_id === "string" && typeof item.expected_digest === "string"
      && (item.kind === "publish" || item.kind === "switch" && typeof item.series_id === "string" && Number.isInteger(item.expected_revision)) ? item : null;
  } catch { return null; }
}
export function versionLabel(skill: ProducerSkillItem) {
  return `v${skill.version_number ?? 1}${skill.version_status === "candidate" ? " · 候选" : ""}`;
}

export function SkillVersionControls({ skill, digest, history, seriesId, disabled, onChoose, onBusy, scope, creatorId }: {
  skill: ProducerSkillItem; digest: string; history: SkillVersionHistory; seriesId?: string;
  disabled: boolean; onChoose: (skill: ProducerSkillItem) => void; onBusy: (busy: boolean) => void; scope: string; creatorId?: string;
}) {
  const queryClient = useQueryClient();
  const family = skill.family_id ?? skill.id;
  const [selectedSeries, setSelectedSeries] = useState(seriesId ?? "");
  const [columnOpen, setColumnOpen] = useState(false);
  const [pendingAction, setPendingAction] = useState(() => readPending(scope, skill.id));
  const [feedback, setFeedback] = useState("");
  const [actionError, setActionError] = useState("");
  const [createUnknown, setCreateUnknown] = useState(() => readUnknownCreation(scope, family));
  const [creationChoices, setCreationChoices] = useState<ProducerSkillItem[]>([]);
  const [creationPreview, setCreationPreview] = useState<{ item: ProducerSkillItem; content: string } | null>(null);
  const [checking, setChecking] = useState(false);
  const [checkedPending, setCheckedPending] = useState(false);
  const [checkedCreation, setCheckedCreation] = useState(false);
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
      const key = creationRequestKey(scope, skill.id, digest);
      try {
        createRequest.current = sessionStorage.getItem(key) ?? createRequest.current;
        sessionStorage.setItem(key, createRequest.current);
        sessionStorage.setItem(createUnknownKey(scope, family), JSON.stringify({}));
      }
      catch { throw new Error("无法暂存创建请求。请检查浏览器存储后重试，避免重复创建版本。"); }
      return studioApi.createSkillVersion(skill.id, { expected_digest: digest, request_id: createRequest.current }, creatorId);
    },
    onSuccess: result => {
      try { sessionStorage.removeItem(creationRequestKey(scope, skill.id, digest)); sessionStorage.removeItem(createUnknownKey(scope, family)); } catch { /* Accepted request remains in history. */ }
      onChoose(result); void refresh();
    },
    onError: error => {
      if (!isUnknownSkillWrite(error)) {
        try { sessionStorage.removeItem(createUnknownKey(scope, family)); } catch { /* No write was accepted. */ }
        return;
      }
      const uncertain = { skillId: error.skillId };
      setCreateUnknown(uncertain);
      try { sessionStorage.setItem(createUnknownKey(scope, family), JSON.stringify(uncertain)); } catch { /* Keep the open panel locked. */ }
    },
  });
  const clearPending = () => {
    setPendingAction(null);
    try { sessionStorage.removeItem(actionKey(scope, skill.id)); } catch { /* Server receipt remains authoritative. */ }
  };
  const apply = useMutation({ retry: false,
    mutationFn: async (input: PendingVersionAction) => input.kind === "publish"
      ? { kind: "publish" as const, result: await studioApi.publishSkillVersion(input.skill_id,
        { expected_digest: input.expected_digest, request_id: input.request_id }, creatorId) }
      : { kind: "switch" as const, result: await studioApi.switchSeriesSkillVersion(input.series_id,
        { skill_id: input.skill_id, expected_revision: input.expected_revision, expected_digest: input.expected_digest, request_id: input.request_id }, creatorId) },
    onSuccess: result => {
      clearPending(); setActionError("");
      setFeedback(result.kind === "publish" ? "版本已保存，栏目保持不变。" : "栏目版本已切换。旧产物和进行中的任务保持不变。");
      if (result.kind === "publish") onChoose(result.result);
      void refresh();
    },
    onError: (error: Error) => {
      const rejected = error instanceof ApiError && error.status >= 400 && error.status < 500 && !isUnknownSkillWrite(error);
      if (rejected) clearPending();
      setActionError(`${error.message}${rejected ? " 请重新读取并核对后操作。" : " 结果待核对，请先查看当前版本和栏目状态，不要重复提交。"}`);
    },
  });
  const submit = (kind: PendingVersionAction["kind"]) => {
    if (disabled || pendingAction || createUnknown || apply.isPending || create.isPending || !digest) return;
    if (kind === "switch" && !binding) return;
    if (!window.confirm(kind === "publish" ? "保存这个版本？现有栏目保持不变。"
      : `将「${binding!.series_name}」切换到 ${versionLabel(skill)}？旧产物和正在进行的任务保持不变。`)) return;
    const identity = { skill_id: skill.id, expected_digest: digest, request_id: crypto.randomUUID().replaceAll("-", "") };
    const input: PendingVersionAction = kind === "publish" ? { ...identity, kind }
      : { ...identity, kind, series_id: binding!.series_id, expected_revision: binding!.revision };
    try { sessionStorage.setItem(actionKey(scope, skill.id), JSON.stringify(input)); }
    catch { setActionError("无法暂存操作身份，请检查浏览器存储后重试。"); return; }
    setPendingAction(input); setActionError(""); setFeedback(""); apply.mutate(input);
  };
  const verifyCreation = async (chosenId?: string) => {
    if (!createUnknown || checking) return;
    setChecking(true); setActionError("");
    try {
      const latest = await studioApi.producerSkillVersions(skill.id, undefined, creatorId);
      setCheckedCreation(true);
      const identity = chosenId ?? createUnknown.skillId;
      if (!identity) {
        setCreationChoices(latest.items);
        setActionError("未收到具体版本，请选择要核对的版本；此操作只读取，不会再次创建。");
        return;
      }
      const found = latest.items.find(item => item.id === identity);
      if (!found) {
        setCreationChoices(latest.items);
        setActionError("暂未找到回执中的版本，请核对版本列表，必要时稍后再读取。");
        return;
      }
      if (!createUnknown.skillId || identity !== createUnknown.skillId) {
        const body = await studioApi.producerSkillText(found.id, "SKILL.md", undefined, creatorId);
        setCreationPreview({ item: found, content: body.content });
        setActionError("已读取所选版本，但无法确认它就是本次创建的候选；请核对后手动解除锁定。");
        return;
      }
      try { sessionStorage.removeItem(createUnknownKey(scope, family)); } catch { /* Read state remains visible. */ }
      setCreateUnknown(null); setCreationChoices([]); onChoose(found); void refresh();
    } catch (error) { setActionError(error instanceof Error ? error.message : "读取版本失败，请稍后再核对。"); }
    finally { setChecking(false); }
  };
  const verifyPending = async () => {
    if (!pendingAction || checking) return;
    setChecking(true); setActionError("");
    try {
      const latest = await studioApi.producerSkillVersions(pendingAction.skill_id, undefined, creatorId);
      const item = latest.items.find(value => value.id === pendingAction.skill_id);
      const applied = pendingAction.kind === "publish"
        ? item?.version_status === "released" && item.digest === pendingAction.expected_digest
        : latest.bindings.some(value => value.series_id === pendingAction.series_id
          && value.skill_id === pendingAction.skill_id && value.revision > pendingAction.expected_revision);
      if (applied) {
        clearPending(); setCheckedPending(false);
        setFeedback(pendingAction.kind === "publish" ? "已核对：版本已保存。" : "已核对：栏目已切换到该版本。");
        if (item) onChoose(item);
      } else {
        setCheckedPending(true);
        setActionError("当前状态尚不能确认这次操作，未重新提交。请继续核对版本与栏目。");
      }
      void refresh();
    } catch (error) { setActionError(error instanceof Error ? error.message : "读取状态失败，请稍后再核对。"); }
    finally { setChecking(false); }
  };
  const pending = create.isPending || apply.isPending || checking;
  useEffect(() => { onBusy(pending); }, [pending, onBusy]);
  useEffect(() => () => onBusy(false), [onBusy]);
  const locked = disabled || pending || !!pendingAction || !!createUnknown;
  const problem = actionError || create.error?.message;
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
    {createUnknown && <div className="skill-version-feedback" role="alert">创建结果待核对，不要重复创建。
      <button type="button" disabled={pending} onClick={() => void verifyCreation()}>核对创建结果</button>
      {creationChoices.map(item => <button key={item.id} type="button" disabled={pending} onClick={() => void verifyCreation(item.id)}>{versionLabel(item)}</button>)}
      {creationPreview && <details><summary>查看所选版本正文</summary><pre>{creationPreview.content}</pre></details>}
      {checkedCreation && <button type="button" disabled={pending} onClick={() => {
        if (window.confirm("确认已核对当前账号的版本列表？解除锁定不会重新创建。")) {
          try { sessionStorage.removeItem(createUnknownKey(scope, family)); } catch { /* A stale marker only requires a read. */ }
          setCreateUnknown(null); setCreationChoices([]); setCheckedCreation(false);
          if (creationPreview) onChoose(creationPreview.item);
        }
      }}>已核对，解除锁定</button>}
    </div>}
    {pendingAction && !apply.isPending && <p className="skill-version-feedback" role="status">上次操作待核对 <button type="button" disabled={pending}
      onClick={() => void verifyPending()}>核对当前状态</button>{checkedPending && <button type="button" disabled={pending}
        onClick={() => { if (window.confirm("确认已核对版本与栏目，解除待核对状态？此操作不会重新提交。")) { clearPending(); setCheckedPending(false); } }}>已核对，解除锁定</button>}</p>}
  </section>;
}
