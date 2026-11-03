import { createContext, useContext } from "react";
import type { ChatContextTarget } from "../../components/agentComposerState";

export type ChatTarget = { creatorId: string | null; accountName?: string; draftSeed?: string; sessionId?: string;
  contextTarget?: ChatContextTarget; contextTitle?: string };
export type ChatControls = { isOpen: boolean; creatorId: string | null; open: (target: ChatTarget) => void;
  close: () => void; setDefault: (target: ChatTarget, routePath: string) => void; clearDefault: (routePath: string) => void };

export const AgentChatContext = createContext<ChatControls | null>(null);

export function useAgentChat() {
  const value = useContext(AgentChatContext);
  if (!value) throw new Error("Agent chat must be inside AgentChatProvider.");
  return value;
}
