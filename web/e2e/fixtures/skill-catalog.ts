import type { Page } from "@playwright/test";

// Test pointer dragging when both endpoints are visible, independent of Skills
// left by earlier isolated tests. Records still come from the real API.
export async function limitVisibleSkillCatalog(page: Page, names: string[]) {
  await page.route("**/api/producer-skills", async route => {
    const response = await route.fetch();
    const result = await response.json();
    result.items = result.items.filter((item: { name: string }) => names.includes(item.name));
    await route.fulfill({ response, json: result });
  });
}
