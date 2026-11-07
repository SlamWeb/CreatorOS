import { useCallback, useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router-dom";
import { ApiError, request, studioApi } from "../../api/client";
import { History, Music2, Plus, Repeat2, X } from "lucide-react";
import { QA_BGM_CREATOR, uploadBgm } from "../../components/BgmPool";
import { skillEditPrompt, type SkillEditHandoff } from "../../components/skillEditHandoff";
import { ChatImages, ImagePicker, imageBase64, imageFileError, MAX_CHAT_IMAGES, readAttachmentDraft, writeAttachmentDraft, type ChatAttachment } from "../../components/AgentAttachments";
import { useAgentChat } from "./chatHostContext";
import { AgentPlan } from "../../components/AgentPlan";
import { AgentComposerControls } from "../../components/AgentComposerControls";
import { contextFocusDraftKey, initialReasoningEffort, isReasoningEffort, parseContextFocusDraft, parsePendingAgentTurn,
  pendingAgentTurnKey, REASONING_EFFORTS, reasoningPreferenceKey, sameContextTarget,
  type ChatContextFocus, type ChatContextTarget, type PendingAgentTurn, type ReasoningEffort, type ReplayReasoningEffort } from "../../components/agentComposerState";
import { newestChatSession, transcriptItems, type ChatSession as Session } from "../../components/agentTranscript";
import { compactTranscript } from "../../components/compactTranscript";
import { ChatEntryGroup } from "./ChatTranscriptEntries";
import { AGENT_SESSIONS_BASE, useAgentSession } from "./useAgentSession";

const base = AGENT_SESSIONS_BASE;
type AudioDraft = { id: string; name: string };
const audioKey = (sessionId: string) => `creatoros.agent.audio.v1:${sessionId}`;
function readAudioDraft(sessionId: string | null): AudioDraft[] {
  if (!sessionId) return [];
  try {
    const value: unknown = JSON.parse(sessionStorage.getItem(audioKey(sessionId)) ?? "[]");
    return Array.isArray(value) ? value.filter(item => item && typeof item.name === "string" && /^[a-f0-9]{64}$/.test(item.id)) : [];
  } catch { return []; }
}
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
  const [audioDrafts, setAudioDrafts] = useState<Record<string, AudioDraft[]>>({});
  const audioDraftsRef = useRef(audioDrafts);
  audioDraftsRef.current = audioDrafts;
  const audioInput = useRef<HTMLInputElement>(null);
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
  const audioAttachments = id ? (audioDrafts[id] ?? readAudioDraft(id)) : [];
  const currentAudio = (sessionId: string) => audioDraftsRef.current[sessionId] ?? readAudioDraft(sessionId);
  const saveAudio = (sessionId: string, next: AudioDraft[]) => {
    try { if (next.length) sessionStorage.setItem(audioKey(sessionId), JSON.stringify(next)); else sessionStorage.removeItem(audioKey(sessionId)); } catch { /* Keep in memory. */ }
    audioDraftsRef.current = { ...audioDraftsRef.current, [sessionId]: next };
    setAudioDrafts(previous => ({ ...previous, [sessionId]: next }));
  };
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
  const transcript = useRef<HTMLDivElement>(null);
  const follow = useRef(true);
  const lastSeed = useRef("");
  const consumedSkillHandoff = useRef("");
  const { session, connected } = useAgentSession(active, scopeKey, id);
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
  const cannotSend = scopeMismatch || accountError || accountInactive || accountPending || !!doc?.view_persistence_pending;
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
  const addAudio = async (file: File) => {
    if (creatorId !== QA_BGM_CREATOR || uploadLock.current || sendLock.current || pendingTurn || checkingPending || cannotSend || (id && !doc) || doc?.status === "running") return;
    uploadLock.current = true; setUploading(true); updateError(scopeKey, "");
    try {
      const target = await ensureSession(doc, creatorId, scopeKey, viewRef.current);
      const material = await uploadBgm(creatorId, file);
      const current = currentAudio(target.id);
      if (!current.some(item => item.id === material.id)) saveAudio(target.id, [...current, { id: material.id, name: material.name }]);
      void cache.invalidateQueries({ queryKey: ["video-materials", creatorId] });
    } catch (e) { updateError(scopeKey, `${file.name} 上传失败：${e instanceof Error ? e.message : "请重试。"}`); }
    finally { uploadLock.current = false; setUploading(false); }
  };
  const send = useMutation({
    retry: false,
    mutationFn: async ({ text, current, scopeCreatorId, submittedScopeKey, sourceView, imageIds, materialIds, effort, iterationMode, contextTarget, pending }: {
      text: string; current: Session | undefined; scopeCreatorId: string | null; submittedScopeKey: string;
      sourceView: string; imageIds: string[]; materialIds: string[]; effort: ReplayReasoningEffort; iterationMode: boolean;
      contextTarget?: ChatContextTarget; pending?: PendingAgentTurn;
    }) => {
      const target = await ensureSession(current, scopeCreatorId, submittedScopeKey, sourceView);
      if (pending && pending.sessionId !== target.id) throw new Error("待核对请求不属于当前对话，已停止发送。");
      const snapshot: PendingAgentTurn = pending ?? { sessionId: target.id, text, request_id: crypto.randomUUID(),
        expected_version: target.version, attachment_ids: imageIds, material_ids: materialIds, reasoning_effort: effort, iteration_mode: iterationMode,
        ...(contextTarget ? { context_target: { ...contextTarget } } : {}) };
      savePendingTurn(target.id, snapshot);
      writeSessionValue(reasoningPreferenceKey(target.id), snapshot.reasoning_effort);
      setReasoningPreferences(previous => ({ ...previous, [target.id]: snapshot.reasoning_effort }));
      return request<Session>(`${base}/${target.id}/turns`, { method: "POST", body: JSON.stringify({
        text: snapshot.text, request_id: snapshot.request_id, expected_version: snapshot.expected_version,
        attachment_ids: snapshot.attachment_ids, reasoning_effort: snapshot.reasoning_effort,
        ...(snapshot.material_ids === undefined ? {} : { material_ids: snapshot.material_ids }),
        ...(snapshot.iteration_mode === undefined ? {} : { iteration_mode: snapshot.iteration_mode }),
        ...(snapshot.context_target === undefined ? {} : { context_target: snapshot.context_target }),
      }) });
    },
    onSuccess: (next, variables) => {
      cache.setQueryData<Session>(["agent-session", variables.submittedScopeKey, next.id], old => newestSession(old, next));
      if (readSessionValue(draftStorageKey(variables.submittedScopeKey)) === variables.text) updateDraft(variables.submittedScopeKey, "");
      saveAttachments(next.id, readAttachmentDraft(next.id).filter(image => !variables.imageIds.includes(image.id)));
      saveAudio(next.id, currentAudio(next.id).filter(item => !variables.materialIds.includes(item.id)));
      savePendingTurn(next.id, null);
      clearSubmittedFocus(variables.submittedScopeKey, variables.pending?.context_target ?? variables.contextTarget);
      writeSessionValue(iterationDraftKey(variables.submittedScopeKey), "");
      setIterationDrafts(previous => ({ ...previous, [variables.submittedScopeKey]: false }));
      updateError(variables.submittedScopeKey, "");
      if (scopeKeyRef.current === variables.submittedScopeKey) follow.current = true;
      void cache.invalidateQueries({ queryKey: ["agent-sessions", variables.submittedScopeKey] });
    },
    onError: (e: Error, variables) => {
      const rejected = e instanceof ApiError && ((e.status >= 400 && e.status < 500)
        || (e.status === 503 && e.code === "chat_view_not_started"));
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
      const latest = await request<Session>(`${base}/${original.sessionId}`, { cache: "no-store" });
      cache.setQueryData<Session>(["agent-session", submittedScopeKey, original.sessionId], old => newestSession(old, latest));
      if (latest.entries.some(entry => entry.kind === "user" && entry.turn_id === original.request_id)) {
        savePendingTurn(original.sessionId, null);
        clearSubmittedFocus(submittedScopeKey, original.context_target);
        writeSessionValue(iterationDraftKey(submittedScopeKey), "");
        setIterationDrafts(previous => ({ ...previous, [submittedScopeKey]: false }));
        if (readSessionValue(draftStorageKey(submittedScopeKey)) === original.text) updateDraft(submittedScopeKey, "");
        saveAttachments(original.sessionId, readAttachmentDraft(original.sessionId).filter(image => !original.attachment_ids.includes(image.id)));
        saveAudio(original.sessionId, currentAudio(original.sessionId).filter(item => !(original.material_ids ?? []).includes(item.id)));
      } else if (latest.status === "running") {
        updateError(submittedScopeKey, "当前对话正在处理其他指令，请完成后再核对这次提交。");
      } else {
        sendLock.current = true;
        send.mutate({ text: original.text, current: latest, scopeCreatorId: creatorId,
          submittedScopeKey, sourceView: `${submittedScopeKey}:${original.sessionId}`,
          imageIds: original.attachment_ids, materialIds: original.material_ids ?? [], effort: original.reasoning_effort, iterationMode: original.iteration_mode === true, pending: original });
      }
    } catch (e) {
      updateError(submittedScopeKey, `核对失败：${e instanceof Error ? e.message : "请稍后再试。"} 原请求仍保留。`);
    } finally { setCheckingPending(false); }
  };
  const submit = () => {
    if ((!draft.trim() && !attachments.length && !audioAttachments.length) || !validReasoning || pendingTurn || checkingPending || sendLock.current || uploadLock.current || send.isPending || doc?.status === "running" || (id && !doc) || cannotSend) return;
    sendLock.current = true;
    updateError(scopeKey, ""); send.mutate({ text: draft, current: doc, scopeCreatorId: creatorId, submittedScopeKey: scopeKey,
      sourceView: viewRef.current, imageIds: attachments.map(image => image.id), materialIds: audioAttachments.map(item => item.id), effort: reasoningEffort, iterationMode,
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
      {id && <span className="agent-connection">{doc?.view_persistence_pending ? "记录保存失败" : doc?.status === "running" ? (connected ? "实时连接" : "连接中 · 自动刷新") : "记录已保存"}</span>}</div>
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
      {!!audioAttachments.length && <div className="chat-audio-drafts" aria-label="待发送音频">{audioAttachments.map(item => <span key={item.id}><Music2 size={15} />{item.name}<button type="button" aria-label={`移除音频 ${item.name}`} disabled={uploading || send.isPending || !!pendingTurn || doc?.status === "running"} onClick={() => { if (id) saveAudio(id, audioAttachments.filter(value => value.id !== item.id)); }}><X size={14} /></button></span>)}</div>}
      <textarea ref={composerInput} aria-label="给 Agent 的消息" placeholder={creatorId ? "围绕这个账号的栏目与选题继续…" : "说说你想做什么…"} value={draft} maxLength={8000} rows={1}
        onChange={e => updateDraft(scopeKey, e.target.value)} onPaste={e => {
          const files = Array.from(e.clipboardData.items).filter(item => item.kind === "file" && (item.type.startsWith("image/") || item.type.startsWith("audio/"))).map(item => item.getAsFile()).filter((file): file is File => !!file);
          const images = files.filter(file => file.type.startsWith("image/"));
          const audio = files.find(file => file.type.startsWith("audio/"));
          if (images.length) { e.preventDefault(); void addImages(images); }
          else if (audio && creatorId === QA_BGM_CREATOR) { e.preventDefault(); void addAudio(audio); }
        }} onKeyDown={e => {
          if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); submit(); }
        }} />
      <div className="agent-composer-toolbar"><ImagePicker disabled={uploading || send.isPending || !!pendingTurn || checkingPending || cannotSend || (!!id && !doc) || doc?.status === "running" || attachments.length >= MAX_CHAT_IMAGES} add={files => void addImages(files)} />
        {creatorId === QA_BGM_CREATOR && <><input className="sr-only" tabIndex={-1} ref={audioInput} type="file" accept="audio/*,.mp3,.wav,.m4a,.aac,.ogg,.flac" aria-label="选择音频文件" onChange={event => { const file = event.target.files?.[0]; if (file) void addAudio(file); event.target.value = ""; }} /><button className="chat-image-picker" type="button" aria-label="添加音频" title="添加音频到 BGM 池" disabled={uploading || send.isPending || !!pendingTurn || checkingPending || cannotSend || (!!id && !doc) || doc?.status === "running"} onClick={() => audioInput.current?.click()}><Music2 size={19} aria-hidden="true" /></button></>}
        <AgentComposerControls key={`${scopeKey}:${id ?? "new"}`} usage={!scopeMismatch && !session.isError ? doc?.context_usage : null}
          effort={reasoningEffort} options={reasoningOptions} actualEffortMap={doc?.reasoning_effort_map}
          disabled={uploading || send.isPending || !!pendingTurn || checkingPending || doc?.status === "running" || cannotSend || (!!id && !doc)} onChange={chooseReasoning} />
        <button type="button" className="agent-iteration-toggle" aria-label="RSI 迭代模式" aria-pressed={iterationMode}
          title="仅授权本次请求：最多试用 3 轮，交付候选，不自动切换栏目"
          disabled={uploading || send.isPending || !!pendingTurn || checkingPending || doc?.status === "running" || cannotSend || (!!id && !doc)}
          onClick={() => chooseIterationMode(!iterationMode)}><Repeat2 size={15} aria-hidden="true" />RSI</button>
        <span className="agent-compose-status" role="status"><span className={uploading || send.isPending || doc?.status === "running" ? "task-running-text" : undefined}>{uploading ? "正在上传文件…" : send.isPending ? "正在提交…" : doc?.status === "running" ? "正在处理…" : ""}</span></span>
        <button className="button button-primary" disabled={(!draft.trim() && !attachments.length && !audioAttachments.length) || !validReasoning || !!pendingTurn || checkingPending || uploading || send.isPending || doc?.status === "running" || (!!id && !doc) || cannotSend}>发送 ↑</button></div>
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
