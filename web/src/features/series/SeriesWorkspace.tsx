import { useEffect, useId, useRef, useState } from "react";
import { Plus, Settings2 } from "lucide-react";
import { Link } from "react-router-dom";
import { useMutation, useMutationState, useQuery, useQueryClient } from "@tanstack/react-query";
import { request, studioApi } from "../../api/client";
import type { ProducerSkillItem, SeriesView, TopicView, SkillLibraryView } from "../../api/types";
import { TopicLibrary } from "../../components/TopicLibrary";
import { SkillReferenceButton } from "../../components/SkillReferenceButton";
import { SeriesSkillPool } from "../../components/SkillLibrary";
import { WorkspaceDialog } from "../../components/WorkspaceDialog";
import { refreshWorkspace } from "../workspace/workspaceData";
import { operationKey, operationView, startSeriesOperation } from "./operationState";

export function SeriesWorkspace({ series, accountName, accounts, skills, onInspectSkill, onDiscuss }: {
  series: SeriesView;
  accountName?: string;
  accounts: { id: string; name: string }[];
  skills: ProducerSkillItem[];
  onInspectSkill: (skill: ProducerSkillItem) => void;
  onDiscuss?: (title: string, topic?: TopicView) => void;
}) {
  const client = useQueryClient();
  const assign = useMutation({
    retry: false,
    mutationKey: operationKey(series.id, "assign"),
    gcTime: Infinity,
    mutationFn: (creatorId: string | null) => studioApi.assignSeries(series.id, {
      creator_id: creatorId, expected_revision: series.revision, request_id: crypto.randomUUID().replaceAll("-", ""),
    }),
    onSuccess: () => refreshWorkspace(client),
    onError: () => refreshWorkspace(client),
  });
  const remove = useMutation({
    retry: false,
    mutationKey: operationKey(series.id, "remove"),
    gcTime: Infinity,
    mutationFn: () => studioApi.deleteSeries(series.id, {
      expected_revision: series.revision, request_id: crypto.randomUUID().replaceAll("-", ""),
    }),
    onSuccess: () => refreshWorkspace(client),
  });
  const assignState = operationView(useMutationState({ filters: { mutationKey: operationKey(series.id, "assign"), exact: true },
    select: mutation => mutation.state }));
  const deleteState = operationView(useMutationState({ filters: { mutationKey: operationKey(series.id, "remove"), exact: true },
    select: mutation => mutation.state }));
  const assignPending = assignState.pending;
  const assignError = assignState.error instanceof Error ? assignState.error.message : null;
  const deletePending = deleteState.pending;
  const deleteError = deleteState.error instanceof Error ? deleteState.error.message : null;
  const onDelete = () => {
    if (series.topic_count > 0 && !window.confirm("从工作区移除，保留选题、图片与记录")) return;
    startSeriesOperation(client, series.id, () => remove.mutate());
  };
  const [topicTitle, setTopicTitle] = useState("");
  const skillPool = useQuery({ queryKey: ["series-skills", series.id], queryFn: () => request<SkillLibraryView>(`/api/series/${encodeURIComponent(series.id)}/skills`), enabled: !!series.creator_id, retry: false });
  const poolItems = skillPool.data?.items ?? [];
  const [addOpen, setAddOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [nameDraft, setNameDraft] = useState<string | null>(null);
  const addId = useId();
  const settingsId = useId();
  const titleInput = useRef<HTMLInputElement>(null);
  const addTrigger = useRef<HTMLButtonElement>(null);
  useEffect(() => { if (addOpen) titleInput.current?.focus(); }, [addOpen]);
  const rename = useMutation({
    retry: false,
    mutationFn: (input: { id: string; name: string; expected_revision: number }) => studioApi.renameSeries(input.id, {
      name: input.name, expected_revision: input.expected_revision, request_id: crypto.randomUUID().replaceAll("-", ""),
    }),
    onSuccess: async () => {
      setNameDraft(null);
      await Promise.all(["series-all", "series", "creators", "overview"].map(key => client.invalidateQueries({ queryKey: [key] })));
    },
    onError: () => client.invalidateQueries({ queryKey: ["series-all"] }),
  });
  useEffect(() => { setNameDraft(null); rename.reset(); }, [series.id]);
  const runMutation = useMutation({
    retry: false,
    mutationFn: async (topic: TopicView) => {
      const run = topic.existing_run_id ? { id: topic.existing_run_id, version: topic.existing_run_version! } : await studioApi.startRun({ topic_id: topic.id });
      return studioApi.executeRun(run.id, run.version, !topic.available_actions.includes("resume"));
    },
    onSettled: async () => {
      await Promise.all(["topics", "series", "overview", "runs", "series-all"].map(key => client.invalidateQueries({ queryKey: key === "topics" || key === "series" ? [key, series.id] : [key] })));
    },
  });
  const addTopic = useMutation({
    retry: false,
    mutationFn: (title: string) => studioApi.queueTopics(series.id, [{ title }]),
    onSuccess: async () => {
      setTopicTitle("");
      setAddOpen(false);
      addTrigger.current?.focus();
      await Promise.all([["topics"], ["series", series.id], ["series-all"]].map(key => client.invalidateQueries({ queryKey: key })));
    },
  });
  const startButton = (topic: TopicView) => <button type="button" className="topic-run-button"
    disabled={!series.is_active || !series.creator_id || runMutation.isPending}
    title={!series.creator_id ? "先分配账号" : undefined}
    onClick={() => runMutation.mutate(topic)}>{runMutation.isPending && runMutation.variables?.id === topic.id ? "提交中…" : topic.available_actions.includes("resume") ? "恢复生产" : "生产"}</button>;
  const heading = <div className="workspace-title-group">
        <p className="column-context">{accountName ?? "未分配"}<span aria-hidden="true"> / </span>栏目</p>
        <h1>{series.name}</h1>
        <div className="workspace-bound-skills" aria-label="栏目绑定的 Skills">
          {!poolItems.length && <button type="button" className="text-link" onClick={() => setSettingsOpen(true)}>设置栏目 Skill</button>}
          {poolItems.length > 0 && <div><span className="muted">栏目 Skill：</span>{poolItems.map((bound, index) => <span key={bound.id}>
            {index > 0 && <span aria-hidden="true"> · </span>}
            <SkillReferenceButton id={bound.id} known={bound} fallback={bound.display_name ?? bound.name} onInspect={onInspectSkill} creatorId={series.creator_id} showVersion />
          </span>)}</div>}
        </div>
      </div>;
  const headerActions = <>
    <button ref={addTrigger} type="button" className="button button-secondary workspace-add-trigger" aria-expanded={addOpen} aria-controls={addId}
      onClick={() => setAddOpen(value => !value)}><Plus size={16} aria-hidden="true" />{addOpen ? "收起添加" : "添加选题"}</button>
    <button type="button" className="button button-quiet workspace-settings-trigger" aria-expanded={settingsOpen} aria-controls={settingsId} aria-haspopup="dialog"
      onClick={() => setSettingsOpen(true)}><Settings2 size={16} aria-hidden="true" /><span>栏目设置</span></button>
  </>;
  const headerPanels = <>
    {settingsOpen && <WorkspaceDialog id={settingsId} title="栏目设置" className="series-settings-drawer"
      busy={rename.isPending || assignPending || deletePending} onClose={() => setSettingsOpen(false)}>
      <div className="series-settings-body">
      <p className="series-settings-name">{series.name}</p>
      <form className="workspace-rename" onSubmit={event => {
        event.preventDefault();
        const name = nameDraft?.trim();
        if (name && name !== series.name) rename.mutate({ id: series.id, name, expected_revision: series.revision });
      }}>
        <label>栏目名称<input aria-label="栏目名称" maxLength={120} value={nameDraft ?? series.name}
          disabled={rename.isPending} onChange={event => setNameDraft(event.target.value)} /></label>
        <button className="button button-secondary" disabled={rename.isPending || !nameDraft?.trim() || nameDraft.trim() === series.name}>
          {rename.isPending ? "保存中…" : "保存名称"}</button>
        {rename.isError && <p className="form-error" role="alert">{rename.error.message}</p>}
        {rename.isSuccess && <small role="status">名称已保存</small>}
      </form>
      {series.description && <p className="workspace-meta">{series.description}</p>}
      <div className="workspace-series-controls"><label>归属账号<select className="workspace-assign" aria-label="归属账号" value={series.creator_id ?? ""} disabled={assignPending || deletePending}
        onChange={event => { const creatorId = event.target.value || null;
          startSeriesOperation(client, series.id, () => assign.mutate(creatorId)); }}>
        <option value="">未分配</option>
        {accounts.map(account => <option key={account.id} value={account.id}>{account.name}</option>)}
      </select></label>
      </div>
      {assignPending && <p role="status" className="series-settings-note">正在调整归属…</p>}
      {assignError && <p className="form-error" role="alert">{assignError}</p>}
      {series.creator_id && <SeriesSkillPool key={`${series.id}:${series.creator_id}`} seriesId={series.id} creatorId={series.creator_id} />}
      {onDelete && <div className="series-settings-danger"><button type="button" className="button button-danger workspace-delete-series" aria-label={`删除栏目 ${series.name}`}
        disabled={deletePending || assignPending} onClick={onDelete}>{deletePending ? "处理中…" : "删除栏目"}</button>
        {deleteError && <p className="form-error" role="alert">{deleteError}</p>}
      </div>}
      </div>
    </WorkspaceDialog>}
    <form id={addId} className="workspace-add" hidden={!addOpen} onSubmit={event => {
      event.preventDefault();
      if (topicTitle.trim()) addTopic.mutate(topicTitle.trim());
    }}>
      <input ref={titleInput} aria-label="新选题标题" placeholder="加一个选题…" maxLength={240} value={topicTitle}
        onChange={event => setTopicTitle(event.target.value)} />
      <button className="button button-primary" disabled={!topicTitle.trim() || addTopic.isPending}>添加</button>
      <button type="button" className="button button-quiet" disabled={addTopic.isPending} onClick={() => { setAddOpen(false); addTrigger.current?.focus(); }}>取消</button>
      {addTopic.isError && <p className="form-error" role="alert">{addTopic.error.message}</p>}
    </form>
    {runMutation.isError && <p className="form-error" role="alert">{runMutation.error.message}</p>}
    {runMutation.isSuccess && <p className="queue-submitted" role="status">{["needs_attention", "failed", "cancelled"].includes(runMutation.data.production_queue?.status ?? "")
      ? "当前任务需要核对。" : runMutation.data.production_queue?.status === "succeeded" ? "已有制作结果。"
      : runMutation.data.submission_accepted === false ? "当前任务未重新启动。" : "已加入生产队列。"}<Link to={`/runs/${runMutation.data.id}`}>查看任务 →</Link></p>}
  </>;
  return <section className="series-content-section">
    <TopicLibrary seriesId={series.id} startButton={startButton} onDiscuss={onDiscuss}
      series={series} skills={skills}
      heading={heading} headerActions={headerActions} headerPanels={headerPanels}
      productionEnabled={series.is_active && !!series.creator_id}
      batchProductionEnabled={!!skillPool.data?.defaults.image.length && skillPool.data.defaults.image.every(item => poolItems.some(skill => skill.id === item.skill_id))} />
  </section>;
}
