import { useQuery } from "@tanstack/react-query";
import { studioApi } from "../api/client";
import type { ProducerSkillItem } from "../api/types";

/** Bindings and old Runs refer to exact versions, not the library's family representative. */
export function SkillReferenceButton({ id, known, fallback, className, onInspect, showVersion = false, resolving = false, creatorId }: {
  id: string; known?: ProducerSkillItem; fallback?: string; className?: string;
  showVersion?: boolean; resolving?: boolean; creatorId?: string | null;
  onInspect: (skill: ProducerSkillItem) => void;
}) {
  const history = useQuery({ queryKey: ["producer-skill-versions", id, creatorId ? `creator:${creatorId}` : "global"],
    queryFn: ({ signal }) => studioApi.producerSkillVersions(id, signal, creatorId ?? undefined), enabled: !known && !resolving, retry: false });
  const skill = known ?? history.data?.items.find(item => item.id === id);
  const loading = !skill && (resolving || history.isPending || history.isFetching);
  const inspect = async () => {
    if (skill) return onInspect(skill);
    const result = await history.refetch();
    const found = result.data?.items.find(item => item.id === id);
    if (found) onInspect(found);
  };
  return <button type="button" className={className} disabled={loading} aria-busy={loading}
    title={loading ? "正在读取 Skill…" : skill ? "查看 Skill 版本" : history.error?.message ?? "查看 Skill 版本"}
    onClick={() => void inspect()}>
    {skill?.display_name ?? skill?.name ?? fallback ?? (loading ? "正在读取 Skill…" : "Skill 读取失败，重试")}
    {showVersion && skill && <small className="skill-reference-version">{skill.id === "knowledge-to-carousel" ? "内置"
      : `v${skill.version_number ?? 1}${skill.version_status === "candidate" ? " · 候选" : ""}`}</small>}
  </button>;
}
