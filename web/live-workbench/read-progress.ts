import type { APIRequestContext } from "@playwright/test";

export type ObservationAttempt = {
  path: string;
  attempt: number;
  time: string;
  error: string | null;
  http_status: number | null;
  business_status: string | null;
  recovery: "not_needed" | "retrying" | "recovered" | "exhausted" | "not_retryable";
};

// This observes an existing task, never submits or repeats a business action.
export async function readProgress(
  request: Pick<APIRequestContext, "get">,
  path: string,
  browser: Record<string, unknown>,
): Promise<{ status: string }> {
  if (!/^\/api\/(?:runs|skill-extractions)\/[A-Za-z0-9_-]+$/.test(path)) {
    throw new Error("进度恢复仅允许读取已有 Run 或提炼任务。");
  }
  const history = (browser.observation_retries ??= []) as ObservationAttempt[];
  for (let attempt = 1; attempt <= 4; attempt++) {
    const record: ObservationAttempt = {
      path, attempt, time: new Date().toISOString(), error: null,
      http_status: null, business_status: null, recovery: "not_needed",
    };
    history.push(record);
    try {
      // Disable Playwright's implicit recovery so every actual attempt is recorded.
      const response = await request.get(path, { maxRetries: 0 });
      record.http_status = response.status();
      if (!response.ok()) throw new Error(`进度读取 HTTP ${response.status()}：${path}`);
      const state: unknown = await response.json();
      if (!state || typeof state !== "object" || !("status" in state) || typeof state.status !== "string") {
        throw new Error(`进度响应缺少字符串 status：${path}`);
      }
      record.business_status = state.status;
      record.recovery = attempt > 1 ? "recovered" : "not_needed";
      // failed/unknown/etc. are authoritative business states, not retry reasons.
      return { status: state.status };
    } catch (error) {
      record.error = String(error);
      const transportReset = record.http_status === null
        && /\bECONNRESET\b|socket hang up/i.test(record.error);
      record.recovery = transportReset ? (attempt < 4 ? "retrying" : "exhausted") : "not_retryable";
      if (!transportReset || attempt === 4) throw error;
      await new Promise(resolve => setTimeout(resolve, attempt * 100));
    }
  }
  throw new Error("进度读取未返回结果。");
}
