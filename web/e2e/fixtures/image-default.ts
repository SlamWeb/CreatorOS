import { expect, type APIRequestContext } from "@playwright/test";

// Configure the built-in Skill through the same isolated API used by the UI.
export async function configureImageDefault(request: APIRequestContext, accountId: string, seriesId: string) {
  const catalog = await request.get("/api/producer-skills?include_versions=true").then(response => response.json());
  const skill = catalog.items.find((item: { id: string }) => item.id === "knowledge-to-carousel");
  expect(skill).toBeTruthy();
  const library = await request.get(`/api/creators/${accountId}/skills`).then(response => response.json());
  if (!library.items.some((item: { id: string }) => item.id === skill.id)) {
    const added = await request.post(`/api/creators/${accountId}/skills`, { data: { skill_id: skill.id, request_id: crypto.randomUUID() } });
    expect(added.ok(), await added.text()).toBeTruthy();
  }
  const pool = await request.get(`/api/series/${seriesId}/skills`).then(response => response.json());
  const saved = await request.put(`/api/series/${seriesId}/skills`, { data: { expected_version: pool.version,
    skill_ids: [skill.id], defaults: { image: [{ skill_id: skill.id, role: "primary" }], video: [] } } });
  expect(saved.ok(), await saved.text()).toBeTruthy();
}
