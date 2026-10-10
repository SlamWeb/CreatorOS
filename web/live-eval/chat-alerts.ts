import type { Locator } from "@playwright/test";

// A failed business tool is still a delivered result. Keep its alert visible
// and in evidence; only conversation/transport/clipboard errors stop the GUI.
export const toolResultAlerts = (panel: Locator) =>
  panel.locator('.chat-tool .research-activity [role="alert"]');

export const conversationErrorAlerts = (panel: Locator) =>
  panel.locator('[role="alert"]:not(.chat-tool .research-activity [role="alert"])');
