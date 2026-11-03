import { expect, type APIRequestContext } from "@playwright/test";

// Bind the built-in to this isolated account before a Run selects it.
export async function ensureBuiltinImageSkill(request: APIRequestContext, accountId: string) {
  const catalogResponse = await request.get("/api/producer-skills?include_versions=true");
  expect(catalogResponse.ok(), await catalogResponse.text()).toBeTruthy();
  const catalog = await catalogResponse.json();
  const skill = catalog.items.find((item: { id: string }) => item.id === "knowledge-to-carousel") as
    { id: string; name: string; digest: string } | undefined;
  expect(skill).toBeTruthy();
  if (!skill) throw new Error("隔离测试未找到图片 Skill。");
  const libraryResponse = await request.get(`/api/creators/${accountId}/skills`);
  expect(libraryResponse.ok(), await libraryResponse.text()).toBeTruthy();
  const library = await libraryResponse.json();
  if (!library.items.some((item: { id: string }) => item.id === skill.id)) {
    const added = await request.post(`/api/creators/${accountId}/skills`, { data: { skill_id: skill.id, request_id: crypto.randomUUID() } });
    expect(added.ok(), await added.text()).toBeTruthy();
  }
  return skill;
}

// Some tests also configure the default for this particular column.
export async function configureImageDefault(request: APIRequestContext, accountId: string, seriesId: string) {
  const skill = await ensureBuiltinImageSkill(request, accountId);
  const pool = await request.get(`/api/series/${seriesId}/skills`).then(response => response.json());
  const saved = await request.put(`/api/series/${seriesId}/skills`, { data: { expected_version: pool.version,
    skill_ids: [skill.id], defaults: { image: [{ skill_id: skill.id, role: "primary" }], video: [] } } });
  expect(saved.ok(), await saved.text()).toBeTruthy();
}
