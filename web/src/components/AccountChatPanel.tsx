import { useEffect } from "react";
import type { RefObject } from "react";
import { AgentConversation } from "../pages/AgentPage";

export type AccountChatPanelProps = {
  creatorId: string | null;
  accountName: string;
  open: boolean;
  onClose: () => void;
  draftSeed?: string | null;
  draftSeedId?: string | number;
  selectedSessionId?: string;
  openerRef?: RefObject<HTMLElement | null>;
};

export function AccountChatPanel({ creatorId, accountName, open, onClose, draftSeed, draftSeedId, selectedSessionId, openerRef }: AccountChatPanelProps) {
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
    <AgentConversation mode="embedded" creatorId={creatorId} accountName={accountName} active={open}
      draftSeed={draftSeed} draftSeedId={draftSeedId} selectedSessionId={selectedSessionId} onClose={close} />
  </aside>;
}
