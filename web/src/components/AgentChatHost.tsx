import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { useLocation } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { studioApi } from "../api/client";
import { AccountChatPanel } from "./AccountChatPanel";
import { BrandMark } from "./BrandMark";

type ChatTarget = { creatorId: string | null; accountName?: string; draftSeed?: string; sessionId?: string };
type ChatState = ChatTarget & { isOpen: boolean; seedId?: number; defaultPath?: string };
type ChatControls = { isOpen: boolean; creatorId: string | null; open: (target: ChatTarget) => void;
  close: () => void; setDefault: (target: ChatTarget) => void };
const storageKey = "creatoros.agent.panel.v1";
const ChatContext = createContext<ChatControls | null>(null);
function initialState(): ChatState {
  try {
    const saved = JSON.parse(sessionStorage.getItem(storageKey) ?? "null");
    if (saved && typeof saved.isOpen === "boolean" && (saved.creatorId === null || typeof saved.creatorId === "string")) {
      return { creatorId: saved.creatorId, accountName: typeof saved.accountName === "string" ? saved.accountName : undefined, isOpen: saved.isOpen };
    }
  } catch { /* Browser storage is optional. */ }
  return { creatorId: null, isOpen: false };
}
export function AgentChatProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<ChatState>(initialState);
  const [hasOpened, setHasOpened] = useState(state.isOpen);
  const location = useLocation();
  const routePath = `${location.pathname}${location.search}`;
  const routePathRef = useRef(routePath);
  routePathRef.current = routePath;
  const standalone = location.pathname === "/agent";
  const account = useQuery({ queryKey: ["creator", state.creatorId], queryFn: () => studioApi.creator(state.creatorId!),
    enabled: state.isOpen && !standalone && !!state.creatorId && !state.accountName, retry: false });
  const accountName = state.accountName ?? account.data?.display_name ?? (state.creatorId ? "账号" : "全部账号");
  const opener = useRef<HTMLButtonElement>(null);
  const restoreFocus = useRef(false);
  const open = useCallback((target: ChatTarget) => {
    setHasOpened(true);
    let draftSeed = target.draftSeed;
    if (draftSeed) {
      try {
        const scope = target.creatorId ? `creator:${target.creatorId}` : "overview";
        const existing = sessionStorage.getItem(`creatoros.agent.draft.v1:${encodeURIComponent(scope)}`);
        if (existing?.trim() && existing !== draftSeed && !window.confirm("输入框已有未发送的文字。替换为本次请求？取消会保留原草稿。")) draftSeed = undefined;
      } catch { /* A blocked storage/confirmation must not clear an existing draft. */ draftSeed = undefined; }
    }
    setState(previous => ({ ...target, draftSeed, isOpen: true, seedId: Date.now(), defaultPath: previous.defaultPath }));
  }, []);
  const close = useCallback(() => {
    restoreFocus.current = true;
    setState(previous => ({ ...previous, isOpen: false, draftSeed: undefined }));
  }, []);
  const setDefault = useCallback((target: ChatTarget) => setState(previous => previous.isOpen ? previous
    : { ...previous, ...target, sessionId: undefined, defaultPath: routePathRef.current }), []);
  useEffect(() => {
    try { sessionStorage.setItem(storageKey, JSON.stringify({ creatorId: state.creatorId, accountName: state.accountName, isOpen: state.isOpen })); }
    catch { /* Preserve in-memory state if storage is unavailable. */ }
  }, [state.creatorId, state.accountName, state.isOpen]);
  const visible = state.isOpen && !standalone;
  const launcherReady = state.isOpen || location.pathname !== "/" || state.defaultPath === routePath;
  useEffect(() => {
    // Workspace can need a render to resolve its default after closing. Focus
    // only after the launcher is enabled; focusing a disabled button is ignored.
    if (restoreFocus.current && !state.isOpen && !standalone && launcherReady) {
      opener.current?.focus();
      restoreFocus.current = false;
    }
  }, [state.isOpen, standalone, launcherReady]);
  return <ChatContext.Provider value={{ isOpen: state.isOpen, creatorId: state.creatorId, open, close, setDefault }}>
    <div className={visible ? "global-agent-chat-open" : "global-agent-chat-closed"}>{children}</div>
    {!standalone && <button ref={opener} type="button" className="workspace-chat-launcher" aria-label={visible ? "关闭账号对话" : "打开账号对话"}
      disabled={!launcherReady}
      aria-expanded={visible} title={state.creatorId ? `与 ${accountName} 对话` : "与 Agent 对话"}
      onClick={() => visible ? close() : open({ creatorId: state.creatorId, accountName: state.accountName })}><BrandMark /></button>}
    {hasOpened && <AccountChatPanel creatorId={state.creatorId} accountName={accountName} open={visible}
      onClose={close} openerRef={opener} draftSeed={state.draftSeed} draftSeedId={state.seedId} selectedSessionId={state.sessionId} />}
  </ChatContext.Provider>;
}
export function useAgentChat() {
  const value = useContext(ChatContext);
  if (!value) throw new Error("Agent chat must be inside AgentChatProvider.");
  return value;
}
