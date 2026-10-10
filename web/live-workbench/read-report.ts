import { expect, type APIRequestContext, type Page } from "@playwright/test";
import { expectEvalEvidence, readEvalViewEvidence } from "../live-eval/ui-assertions";

// Read-only API establishes expected bytes. Acceptance still requires the
// original page to open/expand and render those exact bytes, without a re-run.
export async function readWorkbenchReport(page: Page, request: APIRequestContext, caseId: string, runId: string, userQuery?: string) {
  await page.goto(`/eval?case=${caseId}&run=${runId}`);
  await expect(page.getByRole("heading", { name: "运行结果", exact: true })).toBeVisible();
  const chain = page.getByRole("region", { name: "执行链路", exact: true });
  await expect(chain).toBeVisible();
  const database = page.getByRole("region", { name: "数据库预期与实际", exact: true });
  await expect(database.locator("tbody tr").first()).toBeVisible();
  await expect(chain.getByRole("status")).toHaveCount(0);
  await expect(chain.getByRole("alert")).toHaveCount(0);
  const verified: Record<string, unknown> = {};
  if (caseId !== "S13" && userQuery) {
    const actual = await readEvalViewEvidence(request, runId, [userQuery]);
    Object.assign(verified, await expectEvalEvidence(page, actual));
  }
  const evidence = page.getByRole("region", { name: "原始证据", exact: true });
  await evidence.getByRole("button", { name: "浏览文件", exact: true }).click();
  const names = ["before.json", "after.json", "browser.json", "sdk.json", "requests.json", "snapshots.json", "answer.txt",
    caseId === "S13" ? "source_manifest.json" : caseId.startsWith("P") ? "outputs.json" : "messages.json"];
  const contents = new Map<string, unknown>();
  for (const name of names) {
    const response = await request.get(`/api/eval/runs/${runId}/evidence?${new URLSearchParams({ name })}`);
    expect(response.ok(), `${name} 的只读预期必须可取得`).toBe(true);
    const file = await response.json();
    expect(file.name).toBe(name);
    contents.set(name, file.content);
    const expected = typeof file.content === "string" ? file.content : JSON.stringify(file.content, null, 2);
    const item = evidence.locator(".eval-evidence").filter({ has: page.getByRole("button", { name, exact: true }) });
    const reveal = item.getByRole("button", { name, exact: true });
    if ((await reveal.getAttribute("aria-expanded")) !== "true") await reveal.click();
    await expect(item.locator(".eval-long-text pre")).toBeVisible();
    const expand = item.getByRole("button", { name: /^展开全文/ });
    if (await expand.count()) await expand.click();
    await expect.poll(() => item.locator(".eval-long-text pre").textContent(), { message: `${name} 必须完整读取，而不是仅看非空预览` }).toBe(expected);
    await expect(item.getByRole("status")).toHaveCount(0);
    await expect(item.getByRole("alert")).toHaveCount(0);
  }
  // S13 has zero DeepSeek requests by design. Still verify every real DB row
  // count; empty Agent context is not fabricated as a model request.
  if (caseId === "S13") {
    const other = database.getByRole("button", { name: /^其他 \d+ 张表：完整行一致$/ });
    if (await other.count()) await other.click();
    const before = contents.get("before.json") as { database: Record<string, unknown[]> };
    const after = contents.get("after.json") as { database: Record<string, unknown[]> };
    for (const name of Object.keys(after.database)) {
      const row = database.locator("tbody tr").filter({ has: page.getByText(name, { exact: true }) });
      await expect.poll(() => row.getByRole("cell").nth(0).evaluate(element => element.firstChild?.textContent)).toBe(String(before.database[name].length));
      await expect(row.getByRole("cell").nth(1)).toHaveText(String(after.database[name].length));
    }
    verified.database_tables_verified = Object.keys(after.database);
  }
  // Copying a command is not executing it. This must not name the old suite.
  const command = page.locator(".eval-executor code");
  await expect(command).toContainText("eval:workbench");
  return { completed: true, evidence_loaded: true, evidence_files: names, ...verified };
}
