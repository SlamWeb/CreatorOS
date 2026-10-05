import { useEffect } from "react";
import type { RefObject } from "react";
import { X } from "lucide-react";
import { AgentConversation } from "../pages/AgentPage";

export type AccountChatPanelProps = {
  creatorId: string | null;
  accountName: string;
  open: boolean;
  onClose: () => void;
  draftSeed?: string | null;
  draftSeedId?: string | number;
  openerRef?: RefObject<HTMLElement | null>;
};

export function AccountChatPanel({ creatorId, accountName, open, onClose, draftSeed, draftSeedId, openerRef }: AccountChatPanelProps) {
  useEffect(() => {
    if (!open) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape" || event.defaultPrevented) return;
      if (event.target instanceof Element && event.target.closest("dialog[open]")) return;
      event.preventDefault();
      onClose();
      window.requestAnimationFrame(() => openerRef?.current?.focus());
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [open, onClose, openerRef]);

  const close = () => {
    onClose();
    window.requestAnimationFrame(() => openerRef?.current?.focus());
  };

  return <aside className="account-chat-panel" aria-label={creatorId ? `${accountName}账号对话` : "全部账号对话"} hidden={!open}>
    <header className="account-chat-panel-header">
      <div><h2>{creatorId ? accountName : "全部账号"}</h2><span>Agent 对话</span></div>
      <button type="button" className="account-chat-panel-close" aria-label="关闭账号对话" title="关闭账号对话" onClick={close}>
        <X size={18} aria-hidden="true" />
      </button>
    </header>
    <AgentConversation mode="embedded" creatorId={creatorId} accountName={accountName} active={open}
      draftSeed={draftSeed} draftSeedId={draftSeedId} />
  </aside>;
}
