import { memo, useCallback, useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router-dom";
import { ApiError, apiUrl, request, studioApi } from "../api/client";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Check, CircleAlert, Copy, History, LoaderCircle, Minus, Plus, Repeat2, X } from "lucide-react";
import { ChatTrace } from "../components/ChatTrace";
import { ChatToolDetails } from "../components/ChatToolDetails";
import { ResearchActivity } from "../components/ResearchActivity";
import { DiscussionActivity } from "../components/DiscussionActivity";
import { skillEditPrompt, type SkillEditHandoff } from "../components/skillEditHandoff";
import { chatHref, chatLinkKind, chatLinkLabel, uniqueChatLinks, type ChatLink } from "../components/chatLinks";
import { ChatImages, ImagePicker, imageBase64, imageFileError, MAX_CHAT_IMAGES, readAttachmentDraft, writeAttachmentDraft, type ChatAttachment } from "../components/AgentAttachments";
import { useAgentChat } from "../components/AgentChatHost";
import { AgentPlan } from "../components/AgentPlan";
import { AgentComposerControls } from "../components/AgentComposerControls";
import { contextFocusDraftKey, initialReasoningEffort, isReasoningEffort, parseContextFocusDraft, parsePendingAgentTurn,
  pendingAgentTurnKey, REASONING_EFFORTS, reasoningPreferenceKey, sameContextTarget,
  type ChatContextFocus, type ChatContextTarget, type PendingAgentTurn, type ReasoningEffort, type ReplayReasoningEffort } from "../components/agentComposerState";
import { applySessionPatch, newestChatSession, transcriptItems, type ChatEntryData as Entry,
  type ChatSession as Session, type ChatSessionPatch } from "../components/agentTranscript";
import "../components/chat-transcript.css";
import { compactReplyText, compactTranscript, toolBatchStatus, type CompactTranscriptItem } from "../components/compactTranscript";
import "../components/compact-chat.css";

const tools: Record<string, string> = { list_creators: "查看账号", list_creator_series: "查看栏目",
  discuss_content_run: "讨论作品", get_content_discussion: "查看作品讨论", request_content_revision: "提出返工", get_creator_tasks: "查看账号任务",
  list_series_topics: "查看选题", start_content_run: "提交生产", get_content_run: "查询任务",
  research_series_topics: "调研选题", get_topic_research: "查看调研候选", prepare_topic_selection: "准备选题预览",
  queue_topics: "选题入队", compose_series: "创建栏目", update_series_composition: "编辑栏目", assign_series: "分配账号",
  list_producer_skills: "查看生产 Skill", install_producer_skill: "安装 Skill", get_skill_install: "查询安装",
  get_producer_skill: "读取 Skill 文件", update_producer_skill_file: "保存 Skill 文件",
  manage_skill_iteration: "校准 Skill",
  manage_plan: "管理计划",
  manage_topics: "管理选题",
  manage_media_collection: "管理素材集",
  manage_material: "管理素材",
  manage_skill_library: "管理 Skill 库",
  read_image: "看图片", extract_skill_from_images: "提炼 Skill", create_skill_draft: "保存 Skill 草稿", import_content_images: "导入成品",
  read_file: "读取历史资料", read_tool_result: "回读工具结果" };
const base = "/api/agent/sessions";
const newestSession = newestChatSession;
const draftStorageKey = (scope: string) => `creatoros.agent.draft.v1:${encodeURIComponent(scope)}`;
const iterationDraftKey = (scope: string) => `creatoros.agent.iteration-draft.v1:${encodeURIComponent(scope)}`;
const selectedChatStorageKey = (scope: string) => `creatoros.agent.selected-chat.v1:${encodeURIComponent(scope)}`;
const readSessionValue = (key: string) => {
  try { return window.sessionStorage.getItem(key) ?? ""; } catch { return ""; }
};
const writeSessionValue = (key: string, value: string) => {
  try {
    if (value) window.sessionStorage.setItem(key, value);
    else window.sessionStorage.removeItem(key);
  } catch { /* In-memory state still works when storage is unavailable. */ }
};

export function AgentPage() {
  return <AgentConversation mode="standalone" />;
}

export type AgentConversationProps = {
  mode: "standalone" | "embedded";
  creatorId?: string | null;
  accountName?: string;
  active?: boolean;
  draftSeed?: string | null;
  draftSeedId?: string | number;
  selectedSessionId?: string;
  contextTarget?: ChatContextTarget;
  contextTitle?: string;
  onClose?: () => void;
};

export function AgentConversation({ mode, creatorId: embeddedCreatorId = null, accountName,
  active = true, draftSeed, draftSeedId, selectedSessionId, contextTarget, contextTitle, onClose }: AgentConversationProps) {
  const [params, setParams] = useSearchParams();
  const chatPanel = useAgentChat();
  const cache = useQueryClient();
  const standalone = mode === "standalone";
  const requestedCreatorId = standalone ? params.get("creator") : embeddedCreatorId;
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [contextFocusDrafts, setContextFocusDrafts] = useState<Record<string, ChatContextFocus | null>>({});
  const [selectedChats, setSelectedChats] = useState<Record<string, string>>({});
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [reasoningPreferences, setReasoningPreferences] = useState<Record<string, string>>({});
  const [iterationDrafts, setIterationDrafts] = useState<Record<string, boolean>>({});
  const [pendingTurns, setPendingTurns] = useState<Record<string, PendingAgentTurn | null>>({});
  const [checkingPending, setCheckingPending] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const historyId = useId();
  const [attachmentDrafts, setAttachmentDrafts] = useState<Record<string, ChatAttachment[]>>({});
  const [uploading, setUploading] = useState(false);
  const uploadLock = useRef(false);
  const sendLock = useRef(false);
  const viewRef = useRef("");
  const scopeKeyRef = useRef("");
  const creatorScopeId = requestedCreatorId;
  const scopeKey = creatorScopeId ? `creator:${creatorScopeId}` : "overview";
  scopeKeyRef.current = scopeKey;
  const draft = drafts[scopeKey] ?? readSessionValue(draftStorageKey(scopeKey));
  const contextFocus = contextFocusDrafts[scopeKey] === undefined
    ? parseContextFocusDraft(readSessionValue(contextFocusDraftKey(scopeKey))) : contextFocusDrafts[scopeKey];
  const composerInput = useRef<HTMLTextAreaElement>(null);
  const fitComposer = useCallback(() => {
    const input = composerInput.current;
    if (!input || !input.getClientRects().length) return;
    const limit = Math.min(220, window.innerHeight * .3);
    input.style.height = "0px";
    const height = input.scrollHeight;
    input.style.height = `${Math.min(height, limit)}px`;
    input.style.overflowY = height > limit ? "auto" : "hidden";
  }, []);
  useLayoutEffect(fitComposer, [draft, active, fitComposer]);
  useEffect(() => {
    const parent = composerInput.current?.parentElement;
    if (!parent) return;
    let width = -1;
    const observer = new ResizeObserver(entries => {
      const nextWidth = entries[0].contentRect.width;
      if (nextWidth !== width) { width = nextWidth; fitComposer(); }
    });
    observer.observe(parent);
    window.addEventListener("resize", fitComposer);
    return () => { observer.disconnect(); window.removeEventListener("resize", fitComposer); };
  }, [fitComposer]);
  const id = standalone ? params.get("chat") : ((selectedChats[scopeKey] ?? readSessionValue(selectedChatStorageKey(scopeKey))) || null);
  viewRef.current = `${scopeKey}:${id ?? "new"}`;
  const attachments = id ? (attachmentDrafts[id] ?? readAttachmentDraft(id)) : [];
  const saveAttachments = (sessionId: string, next: ChatAttachment[]) => {
    writeAttachmentDraft(sessionId, next);
    setAttachmentDrafts(previous => ({ ...previous, [sessionId]: next }));
  };
  const error = errors[scopeKey] ?? "";
  const updateDraft = useCallback((scope: string, value: string) => {
    setDrafts(previous => ({ ...previous, [scope]: value }));
    writeSessionValue(draftStorageKey(scope), value);
  }, []);
  const updateError = useCallback((scope: string, value: string) => {
    setErrors(previous => ({ ...previous, [scope]: value }));
  }, []);
  const updateContextFocus = useCallback((scope: string, value: ChatContextFocus | null) => {
    writeSessionValue(contextFocusDraftKey(scope), value ? JSON.stringify(value) : "");
    setContextFocusDrafts(previous => ({ ...previous, [scope]: value }));
  }, []);
  const clearSubmittedFocus = (scope: string, target?: ChatContextTarget) => {
    if (!target) return;
    setContextFocusDrafts(previous => {
      const current = previous[scope] === undefined
        ? parseContextFocusDraft(readSessionValue(contextFocusDraftKey(scope))) : previous[scope];
      if (!sameContextTarget(current?.target, target)) return previous;
      writeSessionValue(contextFocusDraftKey(scope), "");
      return { ...previous, [scope]: null };
    });
  };
  const [connected, setConnected] = useState(false);
  const [streamReconnect, setStreamReconnect] = useState(0);
  const transcript = useRef<HTMLDivElement>(null);
  const follow = useRef(true);
  const lastSeed = useRef("");
  const consumedSkillHandoff = useRef("");
  const session = useQuery({ queryKey: ["agent-session", scopeKey, id], queryFn: async () => {
    const next = await request<Session>(`${base}/${id}`);
    return newestSession(cache.getQueryData<Session>(["agent-session", scopeKey, id]), next);
  },
    enabled: active && !!id, refetchInterval: q => active && !connected && q.state.data?.status === "running" ? 2000 : false,
    refetchOnWindowFocus: !connected });
  const doc = session.data;
  const renderedEntries = useMemo(() => compactTranscript(transcriptItems(doc?.entries ?? [])), [doc?.entries]);
  const preferenceKey = id ?? `new:${scopeKey}`;
  const storedReasoning = readSessionValue(reasoningPreferenceKey(preferenceKey));
  const pendingTurn = id ? (pendingTurns[id] === undefined
    ? parsePendingAgentTurn(readSessionValue(pendingAgentTurnKey(id)), id) : pendingTurns[id]) : null;
  const shownContextFocus = pendingTurn ? (pendingTurn.context_target ? {
    target: pendingTurn.context_target,
    title: sameContextTarget(contextFocus?.target, pendingTurn.context_target) ? contextFocus!.title : "已绑定选题",
  } : null) : contextFocus;
  const iterationMode = pendingTurn ? pendingTurn.iteration_mode === true
    : iterationDrafts[scopeKey] ?? readSessionValue(iterationDraftKey(scopeKey)) === "true";
  const chooseIterationMode = (value: boolean) => {
    writeSessionValue(iterationDraftKey(scopeKey), value ? "true" : "");
    setIterationDrafts(previous => ({ ...previous, [scopeKey]: value }));
  };
  const reasoningEffort = pendingTurn?.reasoning_effort ?? initialReasoningEffort(
    reasoningPreferences[preferenceKey] ?? (storedReasoning || doc?.reasoning_effort));
  const reasoningOptions = doc?.reasoning_options ?? [...REASONING_EFFORTS];
  const validReasoning = isReasoningEffort(reasoningEffort) && reasoningOptions.includes(reasoningEffort);
  const savePendingTurn = (sessionId: string, value: PendingAgentTurn | null) => {
    writeSessionValue(pendingAgentTurnKey(sessionId), value ? JSON.stringify(value) : "");
    setPendingTurns(previous => ({ ...previous, [sessionId]: value }));
  };
  const chooseReasoning = (value: ReasoningEffort) => {
    writeSessionValue(reasoningPreferenceKey(preferenceKey), value);
    setReasoningPreferences(previous => ({ ...previous, [preferenceKey]: value }));
  };
  const creatorId = standalone ? (requestedCreatorId ?? (doc?.scope_kind === "creator" ? doc.creator_id : null)) : embeddedCreatorId;
  const dataScopeKey = creatorId ? `creator:${creatorId}` : "overview";
  const scopeReady = active && (!id || !!doc || session.isError);
  const scopeQuery = creatorId
    ? `scope_kind=creator&creator_id=${encodeURIComponent(creatorId)}`
    : "scope_kind=overview";
  const sessions = useQuery({ queryKey: ["agent-sessions", dataScopeKey],
    queryFn: () => request<{ items: Session[] }>(`${base}?${scopeQuery}`),
    enabled: scopeReady, refetchInterval: q => active && historyOpen && q.state.data?.items.some(item => item.status === "running") ? 5000 : false });
  const creator = useQuery({ queryKey: ["creator", creatorId], queryFn: () => studioApi.creator(creatorId!), retry: false,
    enabled: active && !!creatorId });
  const docCreatorId = doc?.scope_kind === "creator" ? doc.creator_id : null;
  const scopeMismatch = !!doc && (doc.scope_kind !== (creatorId ? "creator" : "overview") || docCreatorId !== creatorId);
  const accountError = creatorId ? creator.isError : false;
  const accountInactive = !!creator.data && !creator.data.is_active;
  const accountPending = !!creatorId && creator.isPending;
  const cannotSend = scopeMismatch || accountError || accountInactive || accountPending;
  const observedSession = useRef<{ key: string; status?: string }>({ key: "" });

  useEffect(() => {
    if (!active || !doc || scopeMismatch) return;
    const key = `${scopeKey}:${doc.id}`;
    const previous = observedSession.current;
    observedSession.current = { key, status: doc.status };
    if (previous.key === key && previous.status === "running" && doc.status !== "running") {
      // A finished tool turn may have changed business state. Refresh once;
      // do not keep polling the workspace while the conversation is idle.
      for (const queryKey of ["creators", "series-all", "series", "topics", "runs", "overview"]) {
        void cache.invalidateQueries({ queryKey: [queryKey] });
      }
    }
  }, [active, doc?.id, doc?.status, scopeKey, scopeMismatch, cache]);

  useEffect(() => {
    if (!standalone || !id || requestedCreatorId || !doc || doc.scope_kind !== "creator" || !doc.creator_id) return;
    setParams(previous => {
      const next = new URLSearchParams(previous);
      next.set("creator", doc.creator_id!);
      return next;
    }, { replace: true });
  }, [standalone, id, requestedCreatorId, doc, setParams]);
  useEffect(() => { updateError(scopeKey, ""); setHistoryOpen(false); }, [id, scopeKey, updateError]);
  useEffect(() => {
    if (!active) return;
    try {
      // The standalone view and the hidden shared panel can alternate. Reload
      // this scope's focus so an accepted turn in the other view cannot revive it.
      const value = parseContextFocusDraft(window.sessionStorage.getItem(contextFocusDraftKey(scopeKey)) ?? "");
      setContextFocusDrafts(previous => ({ ...previous, [scopeKey]: value }));
    } catch { /* A blocked store keeps this view's in-memory draft focus. */ }
  }, [active, scopeKey]);
  useEffect(() => {
    if (!active || standalone || !selectedSessionId) return;
    writeSessionValue(selectedChatStorageKey(scopeKey), selectedSessionId);
    setSelectedChats(previous => ({ ...previous, [scopeKey]: selectedSessionId }));
  }, [active, standalone, scopeKey, selectedSessionId]);
  useEffect(() => {
    if (!draftSeed) { lastSeed.current = ""; return; }
    if (!active) return;
    const seedKey = `${scopeKey}\u001f${draftSeedId ?? draftSeed}`;
    if (lastSeed.current === seedKey) return;
    lastSeed.current = seedKey;
    updateDraft(scopeKey, contextTarget ? "" : draftSeed);
    updateContextFocus(scopeKey, contextTarget ? { target: { ...contextTarget }, title: contextTitle || "已绑定选题" } : null);
  }, [active, draftSeed, draftSeedId, scopeKey, contextTarget, contextTitle, updateDraft, updateContextFocus]);
  useEffect(() => {
    // Remove only the exact old auto-prefill, never a user's edited draft or
    // the immutable payload of a submission whose result is still unknown.
    if (active && !pendingTurn && contextFocus && draft === `我们讨论一下选题「${contextFocus.title}」。`) {
      updateDraft(scopeKey, "");
    }
  }, [active, pendingTurn, contextFocus, draft, scopeKey, updateDraft]);
  useEffect(() => {
    const nonce = params.get("skill_edit");
    if (!active || !nonce || consumedSkillHandoff.current === nonce) return;
    consumedSkillHandoff.current = nonce;
    const key = `creatoros.skill-edit-handoff:${nonce}`;
    try {
      const raw = window.sessionStorage.getItem(key);
      if (!raw) return;
      const value = JSON.parse(raw) as SkillEditHandoff;
      if (!value.skillId || !value.skillName || !value.path || !value.request) return;
      if ((value.creatorId ?? null) !== (requestedCreatorId ?? null)) {
        updateError(scopeKey, "Skill 修改草稿的账号与当前对话不一致，请返回原账号重新打开。");
        return;
      }
      updateDraft(scopeKey, skillEditPrompt(value));
      updateContextFocus(scopeKey, null);
      window.sessionStorage.removeItem(key);
      setParams(previous => { const next = new URLSearchParams(previous); next.delete("skill_edit"); return next; }, { replace: true });
    } catch { updateError(scopeKey, "无法读取 Skill 修改草稿，请返回 Skill 卡片重新提交。"); }
  }, [active, params, requestedCreatorId, scopeKey, setParams, updateDraft, updateError, updateContextFocus]);
  useEffect(() => {
    setConnected(false);
    if (!active || !id || doc?.status !== "running") return;
    let live = true;
    let recovering = false;
    let reconnectTimer: number | undefined;
    const source = new EventSource(apiUrl(`${base}/${id}/events`));
    const resync = () => {
      if (!live || recovering) return;
      recovering = true;
      source.close(); setConnected(false);
      // A missing base cannot be patched by guessing. Read one authoritative
      // snapshot, then reconnect; polling remains read-only if this fails.
      void request<Session>(`${base}/${id}`).then(next => {
        if (live && next.id === id) cache.setQueryData<Session>(["agent-session", scopeKey, id], old => newestSession(old, next));
      }).catch(() => { /* The disconnected polling fallback retains the view. */ })
        .finally(() => { if (live) reconnectTimer = window.setTimeout(() => setStreamReconnect(value => value + 1), 1000); });
    };
    source.onopen = () => { if (live && !recovering) setConnected(true); };
    source.onerror = () => { if (live) setConnected(false); };
    source.addEventListener("snapshot", event => {
      if (!live || recovering) return;
      try {
        const next = JSON.parse((event as MessageEvent).data) as Session;
        if (next.id !== id || !Array.isArray(next.entries)) { resync(); return; }
        cache.setQueryData<Session>(["agent-session", scopeKey, id], old => newestSession(old, next));
        setConnected(true);
      } catch { resync(); }
    });
    source.addEventListener("patch", event => {
      if (!live || recovering) return;
      try {
        const patch = JSON.parse((event as MessageEvent).data) as ChatSessionPatch;
        const merged = applySessionPatch(cache.getQueryData<Session>(["agent-session", scopeKey, id]), patch);
        if (merged.status === "resync") { resync(); return; }
        if (merged.status === "applied") cache.setQueryData(["agent-session", scopeKey, id], merged.session);
        setConnected(true);
      } catch { resync(); }
    });
    return () => { live = false; source.close(); window.clearTimeout(reconnectTimer); };
  }, [active, id, scopeKey, doc?.status, streamReconnect, cache]);
  useEffect(() => { follow.current = true; }, [id, scopeKey]);
  useLayoutEffect(() => {
    if (follow.current && transcript.current) transcript.current.scrollTop = transcript.current.scrollHeight;
  }, [doc?.entries]);
  const ensureSession = async (current: Session | undefined, scopeCreatorId: string | null, submittedScopeKey: string, sourceView: string) => {
    const target = current ?? await request<Session>(base, { method: "POST", body: JSON.stringify({ creator_id: scopeCreatorId }) });
    const targetCreatorId = target.scope_kind === "creator" ? target.creator_id : null;
    if (targetCreatorId !== scopeCreatorId || target.scope_kind !== (scopeCreatorId ? "creator" : "overview")) {
      throw new Error("会话账号范围与当前选择不一致，已停止发送。请返回对应账号后打开正确的对话。");
    }
    if (!current) {
      cache.setQueryData(["agent-session", submittedScopeKey, target.id], target);
      // Late uploads may finish after the user switches account or conversation.
      // Keep their original session, but never navigate the new view backwards.
      if (viewRef.current === sourceView) {
        writeSessionValue(selectedChatStorageKey(submittedScopeKey), target.id);
        setSelectedChats(previous => ({ ...previous, [submittedScopeKey]: target.id }));
        if (standalone) setParams(previous => {
          const next = new URLSearchParams(previous);
          next.set("chat", target.id);
          if (scopeCreatorId) next.set("creator", scopeCreatorId); else next.delete("creator");
          return next;
        });
      }
      void cache.invalidateQueries({ queryKey: ["agent-sessions", submittedScopeKey] });
    }
    return target;
  };
  const addImages = async (files: File[]) => {
    if (!files.length || uploadLock.current || sendLock.current || pendingTurn || checkingPending || cannotSend || (id && !doc) || doc?.status === "running") return;
    if (attachments.length + files.length > MAX_CHAT_IMAGES) { updateError(scopeKey, "每条消息最多 6 张图片，请移除部分图片后再添加。"); return; }
    const invalid = files.map(imageFileError).find(Boolean);
    if (invalid) { updateError(scopeKey, invalid); return; }
    uploadLock.current = true; setUploading(true); updateError(scopeKey, "");
    const sourceView = viewRef.current;
    let fileName = "图片";
    try {
      const target = await ensureSession(doc, creatorId, scopeKey, sourceView);
      const next = [...attachments];
      for (const file of files) {
        fileName = file.name;
        const image = await request<ChatAttachment>(`${base}/${target.id}/attachments`, { method: "POST", body: JSON.stringify({ name: file.name, data_base64: await imageBase64(file) }) });
        if (!next.some(item => item.id === image.id)) next.push(image);
        saveAttachments(target.id, [...next]);
      }
    } catch (e) {
      updateError(scopeKey, `${fileName} 上传失败：${e instanceof Error ? e.message : "请重新选择图片。"} 已上传的图片仍保留，请重新选择失败的图片。`);
    } finally { uploadLock.current = false; setUploading(false); }
  };
  const send = useMutation({
    retry: false,
    mutationFn: async ({ text, current, scopeCreatorId, submittedScopeKey, sourceView, imageIds, effort, iterationMode, contextTarget, pending }: {
      text: string; current: Session | undefined; scopeCreatorId: string | null; submittedScopeKey: string;
      sourceView: string; imageIds: string[]; effort: ReplayReasoningEffort; iterationMode: boolean;
      contextTarget?: ChatContextTarget; pending?: PendingAgentTurn;
    }) => {
      const target = await ensureSession(current, scopeCreatorId, submittedScopeKey, sourceView);
      if (pending && pending.sessionId !== target.id) throw new Error("待核对请求不属于当前对话，已停止发送。");
      const snapshot: PendingAgentTurn = pending ?? { sessionId: target.id, text, request_id: crypto.randomUUID(),
        expected_version: target.version, attachment_ids: imageIds, reasoning_effort: effort, iteration_mode: iterationMode,
        ...(contextTarget ? { context_target: { ...contextTarget } } : {}) };
      savePendingTurn(target.id, snapshot);
      writeSessionValue(reasoningPreferenceKey(target.id), snapshot.reasoning_effort);
      setReasoningPreferences(previous => ({ ...previous, [target.id]: snapshot.reasoning_effort }));
      return request<Session>(`${base}/${target.id}/turns`, { method: "POST", body: JSON.stringify({
        text: snapshot.text, request_id: snapshot.request_id, expected_version: snapshot.expected_version,
        attachment_ids: snapshot.attachment_ids, reasoning_effort: snapshot.reasoning_effort,
        ...(snapshot.iteration_mode === undefined ? {} : { iteration_mode: snapshot.iteration_mode }),
        ...(snapshot.context_target === undefined ? {} : { context_target: snapshot.context_target }),
      }) });
    },
    onSuccess: (next, variables) => {
      cache.setQueryData<Session>(["agent-session", variables.submittedScopeKey, next.id], old => newestSession(old, next));
      if (readSessionValue(draftStorageKey(variables.submittedScopeKey)).trim() === variables.text) updateDraft(variables.submittedScopeKey, "");
      saveAttachments(next.id, readAttachmentDraft(next.id).filter(image => !variables.imageIds.includes(image.id)));
      savePendingTurn(next.id, null);
      clearSubmittedFocus(variables.submittedScopeKey, variables.pending?.context_target ?? variables.contextTarget);
      writeSessionValue(iterationDraftKey(variables.submittedScopeKey), "");
      setIterationDrafts(previous => ({ ...previous, [variables.submittedScopeKey]: false }));
      updateError(variables.submittedScopeKey, "");
      if (scopeKeyRef.current === variables.submittedScopeKey) follow.current = true;
      void cache.invalidateQueries({ queryKey: ["agent-sessions", variables.submittedScopeKey] });
    },
    onError: (e: Error, variables) => {
      const rejected = e instanceof ApiError && e.status >= 400 && e.status < 500;
      const targetId = variables.pending?.sessionId ?? variables.current?.id ??
        readSessionValue(selectedChatStorageKey(variables.submittedScopeKey));
      if (rejected && targetId) savePendingTurn(targetId, null);
      updateError(variables.submittedScopeKey, `${e.message} ${rejected ? "本次未提交，草稿已保留。" : "提交结果待核对；请核对上次提交，避免重复操作。"}`);
      void cache.invalidateQueries({ queryKey: ["agent-session", variables.submittedScopeKey] });
    },
    onSettled: () => { sendLock.current = false; },
  });
  const recoverPending = async () => {
    if (!pendingTurn || !id || checkingPending || sendLock.current || cannotSend) return;
    setCheckingPending(true); updateError(scopeKey, "");
    const original = pendingTurn;
    const submittedScopeKey = scopeKey;
    try {
      const latest = await request<Session>(`${base}/${original.sessionId}`);
      cache.setQueryData<Session>(["agent-session", submittedScopeKey, original.sessionId], old => newestSession(old, latest));
      if (latest.entries.some(entry => entry.kind === "user" && entry.turn_id === original.request_id)) {
        savePendingTurn(original.sessionId, null);
        clearSubmittedFocus(submittedScopeKey, original.context_target);
        writeSessionValue(iterationDraftKey(submittedScopeKey), "");
        setIterationDrafts(previous => ({ ...previous, [submittedScopeKey]: false }));
        if (readSessionValue(draftStorageKey(submittedScopeKey)).trim() === original.text) updateDraft(submittedScopeKey, "");
        saveAttachments(original.sessionId, readAttachmentDraft(original.sessionId).filter(image => !original.attachment_ids.includes(image.id)));
      } else if (latest.status === "running") {
        updateError(submittedScopeKey, "当前对话正在处理其他指令，请完成后再核对这次提交。");
      } else {
        sendLock.current = true;
        send.mutate({ text: original.text, current: latest, scopeCreatorId: creatorId,
          submittedScopeKey, sourceView: `${submittedScopeKey}:${original.sessionId}`,
          imageIds: original.attachment_ids, effort: original.reasoning_effort, iterationMode: original.iteration_mode === true, pending: original });
      }
    } catch (e) {
      updateError(submittedScopeKey, `核对失败：${e instanceof Error ? e.message : "请稍后再试。"} 原请求仍保留。`);
    } finally { setCheckingPending(false); }
  };
  const submit = () => {
    if ((!draft.trim() && !attachments.length) || !validReasoning || pendingTurn || checkingPending || sendLock.current || uploadLock.current || send.isPending || doc?.status === "running" || (id && !doc) || cannotSend) return;
    sendLock.current = true;
    updateError(scopeKey, ""); send.mutate({ text: draft.trim(), current: doc, scopeCreatorId: creatorId, submittedScopeKey: scopeKey,
      sourceView: viewRef.current, imageIds: attachments.map(image => image.id), effort: reasoningEffort, iterationMode,
      ...(contextFocus ? { contextTarget: { ...contextFocus.target } } : {}) });
  };
  const chooseSession = (next: string | null) => {
    updateError(scopeKey, ""); setHistoryOpen(false);
    updateContextFocus(scopeKey, null);
    if (standalone) {
      setParams(previous => {
        const nextParams = new URLSearchParams(previous);
        nextParams.delete("command");
        if (next) nextParams.set("chat", next); else nextParams.delete("chat");
        if (creatorId) nextParams.set("creator", creatorId);
        else nextParams.delete("creator");
        return nextParams;
      });
    } else {
      writeSessionValue(selectedChatStorageKey(scopeKey), next ?? "");
      setSelectedChats(previous => ({ ...previous, [scopeKey]: next ?? "" }));
    }
  };
  const history = sessions.data?.items ?? [];
  return <section className={`agent-workspace${standalone ? "" : " account-chat-workspace"}`} onClickCapture={event => {
    // A full-page Agent can follow a task while continuing in the shared panel.
    // Only navigation is handed off; never send a turn or infer a new scope.
    if (!standalone || scopeMismatch || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    const anchor = event.target instanceof Element ? event.target.closest("a") : null;
    if (!anchor || anchor.target === "_blank" || !anchor.getAttribute("href")) return;
    const destination = new URL(anchor.href, window.location.href);
    if (destination.origin !== window.location.origin || destination.pathname === "/agent") return;
    if (id) writeSessionValue(selectedChatStorageKey(dataScopeKey), id);
    chatPanel.open({ creatorId, accountName: accountName ?? creator.data?.display_name, sessionId: id ?? undefined });
  }}>
    {!standalone && <header className="account-chat-panel-header">
      <h2 title={creatorId ? accountName ?? creator.data?.display_name ?? "当前账号" : "全部账号"}>{creatorId ? accountName ?? creator.data?.display_name ?? "当前账号" : "全部账号"}</h2>
      <div className="account-chat-panel-actions">
        <button type="button" aria-label="历史对话" title="历史对话" aria-expanded={historyOpen} aria-controls={historyId}
          onClick={() => setHistoryOpen(value => !value)}><History size={16} aria-hidden="true" /><span>历史</span></button>
        <button type="button" disabled={send.isPending} onClick={() => { chooseSession(null); window.requestAnimationFrame(() => composerInput.current?.focus()); }}>
          <Plus size={16} aria-hidden="true" /><span>新对话</span></button>
        <button type="button" className="account-chat-panel-close" aria-label="关闭账号对话" title="关闭" onClick={onClose ?? chatPanel.close}><X size={18} aria-hidden="true" /></button>
      </div>
    </header>}
    {standalone && <button type="button" className="agent-history-toggle" aria-expanded={historyOpen} aria-controls={historyId}
      onClick={() => setHistoryOpen(!historyOpen)}><History size={16} aria-hidden="true" /><span>历史对话</span></button>}
    <aside id={historyId} className={`agent-history ${historyOpen ? "is-open" : ""}`} aria-label="历史对话">
      {standalone && <button type="button" className="agent-new" disabled={send.isPending} onClick={() => chooseSession(null)}><Plus size={16} aria-hidden="true" />新对话</button>}
      <p className="agent-history-label">最近对话</p>
      <nav aria-label="选择对话" className="agent-history-list">
        {sessions.isPending && <p className="agent-note">正在读取…</p>}
        {!sessions.isPending && !sessions.isError && !history.length && <p className="agent-note">开始对话后，会保存在这里。</p>}
        {history.map(s => <button key={s.id} type="button" disabled={send.isPending} title={s.title}
          aria-current={s.id === id ? "page" : undefined} onClick={() => chooseSession(s.id)}>
          <span className="agent-history-title">{s.title}</span><small>{s.status === "running" ? "处理中" : new Date(s.updated_at).toLocaleDateString("zh-CN", { month: "short", day: "numeric" })}</small>
        </button>)}
        {id && !history.some(s => s.id === id) && <button type="button" aria-current="page" disabled>{doc?.title ?? "读取当前对话…"}</button>}
      </nav>
    </aside>
    <div className="agent-conversation">
    {standalone && <div className="agent-heading"><div><h1>{doc?.title ?? (creatorId ? "账号对话" : "把想法交给 Agent")}</h1>
      <span className="agent-connection">{creatorId ? `账号：${accountName ?? creator.data?.display_name ?? (creator.isPending ? "读取中…" : creatorId)}` : "全部账号"}{standalone && creatorId && <> · <Link to={`/?creator=${encodeURIComponent(creatorId)}`}>返回账号工作台</Link></>}</span>
      {id && <span className="agent-connection">{doc?.status === "running" ? (connected ? "实时连接" : "连接中 · 自动刷新") : "记录已保存"}</span>}</div>
      {standalone && <Link className="text-link" to={(() => { const next = new URLSearchParams(params); next.set("command", "new"); return `?${next.toString()}`; })()}>添加 / 调整选题 ↗</Link>}</div>}
    {(session.isError || sessions.isError || accountError || accountInactive || scopeMismatch) && <p role="alert" className="review-warning">
      {scopeMismatch ? "这个对话属于其他账号范围，不能在当前账号上下文中继续发送。" : accountInactive ? "该账号已停用，不能发送新的对话指令。" : accountError ? `无法读取账号，已阻止发送：${creator.error?.message ?? "账号不存在或暂不可用。"}` : session.error?.message ?? sessions.error?.message}
    </p>}
    {!scopeMismatch && id && <AgentPlan key={id} sessionId={id} visible={active} conversationRunning={doc?.status === "running"} />}
    {!scopeMismatch && <div className="agent-transcript" ref={transcript} onScroll={e => {
      const el = e.currentTarget; follow.current = el.scrollHeight - el.scrollTop - el.clientHeight < 70;
    }} aria-label="对话记录">
      {id && session.isPending && <p className="agent-note">正在读取对话…</p>}
      {standalone && (!id || (doc && !doc.entries.length)) && <div className="agent-welcome"><span>✦</span><h2>{creatorId ? `从「${accountName ?? creator.data?.display_name ?? "当前账号"}」开始` : "从你已有的账号开始"}</h2>
        <button type="button" disabled={cannotSend} onClick={() => updateDraft(scopeKey, creatorId ? `看看「${accountName ?? creator.data?.display_name ?? "这个账号"}」的栏目和选题。` : "看看我有哪些账号和栏目。")}>{creatorId ? "查看这个账号的栏目与选题 ↗" : "看看我的账号和栏目 ↗"}</button></div>}
      {doc?.has_older && <p className="agent-note">显示最近记录；完整消息仍保存在本地会话中。</p>}
      {doc && renderedEntries.map(item => <ChatEntryGroup key={`${doc.id}:${item.key}`} group={item} sessionId={doc.id}
        sessionStatus={doc.status} />)}
      {doc?.error && <p className="review-warning" role="alert">{doc.error}</p>}
    </div>}
    <form className="agent-composer" onSubmit={e => { e.preventDefault(); submit(); }}>
      {shownContextFocus && <div className="agent-context-focus" role="group" aria-label="本次选题">
        <span>本次选题：{shownContextFocus.title}</span>
        <button type="button" aria-label="移除选题焦点" title="移除选题焦点"
          disabled={!!pendingTurn || checkingPending || send.isPending || doc?.status === "running"}
          onClick={() => updateContextFocus(scopeKey, null)}><X size={14} aria-hidden="true" /></button>
      </div>}
      {!!attachments.length && <ChatImages attachments={attachments} disabled={uploading || send.isPending || !!pendingTurn || doc?.status === "running"}
        remove={imageId => { if (id) saveAttachments(id, attachments.filter(image => image.id !== imageId)); }} />}
      <textarea ref={composerInput} aria-label="给 Agent 的消息" placeholder={creatorId ? "围绕这个账号的栏目与选题继续…" : "说说你想做什么…"} value={draft} maxLength={8000} rows={1}
        onChange={e => updateDraft(scopeKey, e.target.value)} onPaste={e => {
          const files = Array.from(e.clipboardData.items).filter(item => item.kind === "file" && item.type.startsWith("image/")).map(item => item.getAsFile()).filter((file): file is File => !!file);
          if (files.length) { e.preventDefault(); void addImages(files); }
        }} onKeyDown={e => {
          if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); submit(); }
        }} />
      <div className="agent-composer-toolbar"><ImagePicker disabled={uploading || send.isPending || !!pendingTurn || checkingPending || cannotSend || (!!id && !doc) || doc?.status === "running" || attachments.length >= MAX_CHAT_IMAGES} add={files => void addImages(files)} />
        <AgentComposerControls key={`${scopeKey}:${id ?? "new"}`} usage={!scopeMismatch && !session.isError ? doc?.context_usage : null}
          effort={reasoningEffort} options={reasoningOptions} actualEffortMap={doc?.reasoning_effort_map}
          disabled={uploading || send.isPending || !!pendingTurn || checkingPending || doc?.status === "running" || cannotSend || (!!id && !doc)} onChange={chooseReasoning} />
        <button type="button" className="agent-iteration-toggle" aria-label="RSI 迭代模式" aria-pressed={iterationMode}
          title="仅授权本次请求：最多试用 3 轮，交付候选，不自动切换栏目"
          disabled={uploading || send.isPending || !!pendingTurn || checkingPending || doc?.status === "running" || cannotSend || (!!id && !doc)}
          onClick={() => chooseIterationMode(!iterationMode)}><Repeat2 size={15} aria-hidden="true" />RSI</button>
        <span className="agent-compose-status" role="status"><span className={uploading || send.isPending || doc?.status === "running" ? "task-running-text" : undefined}>{uploading ? "正在上传图片…" : send.isPending ? "正在提交…" : doc?.status === "running" ? "正在处理…" : ""}</span></span>
        <button className="button button-primary" disabled={(!draft.trim() && !attachments.length) || !validReasoning || !!pendingTurn || checkingPending || uploading || send.isPending || doc?.status === "running" || (!!id && !doc) || cannotSend}>发送 ↑</button></div>
      {pendingTurn && !send.isPending && <div className="agent-submit-recovery">
        <span>上次提交待核对</span><button type="button" disabled={checkingPending || cannotSend || (!!id && !doc)} onClick={() => void recoverPending()}>
          {checkingPending ? "正在核对…" : "核对并恢复上次提交"}</button>
      </div>}
      {!validReasoning && !pendingTurn && <p className="form-error" role="alert">当前推理设置不可用，请重新选择。</p>}
      {iterationMode && <p className="agent-iteration-hint">本次最多 3 轮 · 只交付候选，不自动切换栏目</p>}
    </form>
    {error && <p role="alert" className="review-warning">{error}</p>}
    </div>
  </section>;
}

const ChatEntry = memo(function ChatEntry({ entry, sessionId, sessionStatus, hiddenLinkUrls }: {
  entry: Entry; sessionId: string; sessionStatus: string; hiddenLinkUrls?: string;
}) {
  const hidden = new Set<string>(hiddenLinkUrls ? JSON.parse(hiddenLinkUrls) : []);
  const links = uniqueChatLinks(entry.links ?? []).filter(link => !hidden.has(link.url));
  if (entry.kind === "user") return <div className="chat-user">{!!entry.attachments?.length && <ChatImages attachments={entry.attachments} />}{entry.text}
    {entry.iteration_mode && <small className="chat-user-iteration">RSI · 最多 3 轮</small>}</div>;
  if (entry.kind === "assistant") return entry.text ? <div className="chat-reply"><div className="chat-answer"><Markdown remarkPlugins={[remarkGfm]} skipHtml components={{
    table: ({ children }) => <div className="chat-table-scroll" tabIndex={0} role="region" aria-label="表格，可横向滚动"><table>{children}</table></div>,
    img: ({ alt }) => <span>{alt ?? "图片请在内容页查看"}</span>,
    a: ({ href, children }) => {
      const target = chatHref(href, entry.links ?? [], window.location.origin);
      // Confirmed task links have one named destination below the reply; keep
      // repeated model-written links from multiplying indistinguishable CTAs.
      return target?.kind === "internal" ? <span>{children}</span>
        : target?.kind === "external" ? <a href={target.href} target="_blank" rel="noopener noreferrer">{children}</a>
          : <span title="此地址未由工具确认；请使用回复下方的任务入口">{children}</span>;
    },
  }}>{compactReplyText(entry.text, url => chatHref(url, links, window.location.origin)?.kind === "internal")}</Markdown></div>
    {!!links.length && <ReplyLinks links={links} />}
    {(entry.complete || entry.terminal || (entry.complete === undefined && entry.terminal === undefined && sessionStatus !== "running")) && <div className="chat-reply-actions">
      <CopyReply text={entry.text} />
      <ChatTrace sessionId={sessionId} turnId={entry.turn_id} requestId={entry.model_request_id}
        incomplete={entry.terminal === true && !entry.complete} />
    </div>}
  </div> : null;
  if (entry.kind === "tool") return <ToolEntry entry={entry} sessionId={sessionId} links={links} />;
  return <p className="agent-note">{entry.kind === "guard_stop" ? "已达到本次调用上限，请检查当前结果。" : entry.kind === "context_compacted" ? "较早消息已压缩，完整记录仍在本地。" : "上下文接近预算。"}</p>;
});

function ChatEntryGroup({ group, sessionId, sessionStatus }: {
  group: CompactTranscriptItem; sessionId: string; sessionStatus: string;
}) {
  const [open, setOpen] = useState(false);
  const detailsId = useId();
  const entries = group.items.map(item => <ChatEntry key={item.key} entry={item.entry} sessionId={sessionId}
    sessionStatus={sessionStatus} hiddenLinkUrls={item.hiddenLinkUrls} />);
  if (group.items.length === 1) return entries[0];
  const status = toolBatchStatus(group.items);
  return <section className="chat-tool-batch" data-attention={status.attention > 0} aria-label="执行记录">
    <button type="button" className="chat-tool-toggle" aria-expanded={open} aria-controls={detailsId} onClick={() => setOpen(value => !value)}>
      {status.running ? <LoaderCircle size={14} className="chat-tool-spinner" aria-hidden="true" />
        : status.attention ? <CircleAlert size={14} aria-hidden="true" /> : <Check size={14} aria-hidden="true" />}
      <span>执行记录</span><small>{status.label}</small>
      {open ? <Minus size={13} aria-hidden="true" /> : <Plus size={13} aria-hidden="true" />}
    </button>
    {open && <div id={detailsId} className="chat-tool-batch-body">{entries}</div>}
  </section>;
}

function ReplyLinks({ links }: { links: ChatLink[] }) {
  const [open, setOpen] = useState(false);
  const detailsId = useId();
  const many = links.length > 2;
  return <div className="chat-result-list">
    {many && <button type="button" className="chat-tool-toggle" aria-expanded={open} aria-controls={detailsId} onClick={() => setOpen(value => !value)}>
      <span>{open ? "收起结果" : "查看结果"}（{links.length}）</span>
      {open ? <Minus size={13} aria-hidden="true" /> : <Plus size={13} aria-hidden="true" />}
    </button>}
    {(!many || open) && <nav id={detailsId} className="chat-delivery-links" aria-label="本轮任务入口">
      {links.map(link => <NamedChatLink key={link.url} link={link} />)}
    </nav>}
  </div>;
}

function NamedChatLink({ link }: { link: ChatLink }) {
  const target = chatHref(link.url, [link], window.location.origin);
  if (target?.kind !== "internal") return null;
  const label = chatLinkLabel(link);
  if (link.kind === "download" && /^\/api\/creators\/[A-Za-z0-9_-]+\/collections\/[A-Za-z0-9_-]+\/exports\/[a-f0-9]{64}\/download$/.test(target.href)) {
    return <a href={target.href} download title={label} data-link-kind="download"><span>{label}</span></a>;
  }
  const at = link.created_at ? Date.parse(link.created_at) : NaN;
  return <Link to={target.href} title={label} data-link-kind={chatLinkKind(link) ?? undefined}>
    <span>{label}</span>{Number.isFinite(at) && <small>{new Date(at).toLocaleString("zh-CN", {
      month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false,
    })}</small>}
  </Link>;
}

function ToolEntry({ entry, sessionId, links }: { entry: Entry; sessionId: string; links: ChatLink[] }) {
  const [open, setOpen] = useState(false);
  const detailsId = useId();
  const running = entry.status === "running";
  const successful = entry.status === "done";
  const backgroundStatus = entry.background?.status;
  const status = entry.status === "waiting" ? (backgroundStatus === "unknown" ? "后台状态待确认" : "后台进行中")
    : running ? (entry.background ? (backgroundStatus === "queued" ? "等待执行" : "正在提炼")
      : entry.research ? "等待调研结果" : entry.discussion ? "等待讨论回复" : "调用中")
    : successful ? "已返回" : "结果需检查";
  const navigation = links.filter(link => !["research", "discussion"].includes(chatLinkKind(link) ?? ""));
  return <div className="chat-tool chat-tool-expandable" data-status={entry.status} data-entry-id={entry.id}>
    <button type="button" className="chat-tool-toggle" aria-expanded={open} aria-controls={detailsId}
      onClick={() => setOpen(value => !value)}>
      {running || entry.status === "waiting" ? <LoaderCircle size={14} className={running ? "chat-tool-spinner" : undefined} aria-hidden="true" />
        : successful ? <Check size={14} aria-hidden="true" /> : <CircleAlert size={14} aria-hidden="true" />}
      <span className="chat-tool-name">{tools[entry.name ?? ""] ?? entry.name ?? "工具"}</span>
      <small>{status}</small>
      {open ? <Minus size={13} className="chat-tool-expand-icon" aria-hidden="true" /> : <Plus size={13} className="chat-tool-expand-icon" aria-hidden="true" />}
    </button>
    {running && entry.background?.note && <small role="status">{entry.background.note}</small>}
    {open && !!navigation.length && <nav className="chat-tool-links" aria-label="工具任务入口">
      {navigation.map(link => <NamedChatLink key={link.url} link={link} />)}
    </nav>}
    {open && <div id={detailsId} className="chat-tool-details" role="region" aria-label={`${tools[entry.name ?? ""] ?? entry.name ?? "工具"}详情`}>
      <ChatToolDetails sessionId={sessionId} turnId={entry.turn_id} requestId={entry.model_request_id}
        callId={entry.tool_call_id} running={running} />
    </div>}
    {entry.research && <ResearchActivity research={entry.research} links={links} />}
    {entry.discussion && <DiscussionActivity discussion={entry.discussion} links={links} />}
  </div>;
}

function CopyReply({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState("");
  const reset = useRef<number | undefined>(undefined);
  useEffect(() => () => window.clearTimeout(reset.current), []);
  const copy = async () => {
    try {
      if (!navigator.clipboard?.writeText) throw new Error("浏览器不支持剪贴板写入。");
      await navigator.clipboard.writeText(text);
      setCopied(true); setError("");
      window.clearTimeout(reset.current);
      reset.current = window.setTimeout(() => setCopied(false), 1600);
    } catch (e) {
      setCopied(false);
      setError(e instanceof Error ? `复制失败：${e.message}` : "复制失败，请检查浏览器剪贴板权限。");
    }
  };
  return <>
    <button type="button" className="chat-reply-action" aria-label="复制回复原文" title="复制回复原文" onClick={() => void copy()}>
      {copied ? <Check size={15} aria-hidden="true" /> : <Copy size={15} aria-hidden="true" />}
    </button>
    {error && <span className="chat-copy-error" role="alert">{error}</span>}
    {copied && <span className="sr-only" role="status">已复制回复原文</span>}
  </>;
}
