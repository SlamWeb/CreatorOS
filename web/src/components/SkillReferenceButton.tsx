import { useQuery } from "@tanstack/react-query";
import { studioApi } from "../api/client";
import type { ProducerSkillItem } from "../api/types";

/** Bindings and old Runs refer to exact versions, not the library's family representative. */
export function SkillReferenceButton({ id, known, fallback, className, onInspect }: {
  id: string; known?: ProducerSkillItem; fallback?: string; className?: string;
  onInspect: (skill: ProducerSkillItem) => void;
}) {
  const history = useQuery({ queryKey: ["producer-skill-versions", id],
    queryFn: ({ signal }) => studioApi.producerSkillVersions(id, signal), enabled: !known, retry: false });
  const skill = known ?? history.data?.items.find(item => item.id === id);
  return <button type="button" className={className} disabled={!skill && history.isPending}
    title={history.error?.message ?? "查看 Skill 版本"} onClick={() => skill ? onInspect(skill) : void history.refetch()}>
    {skill?.display_name ?? skill?.name ?? (history.isPending ? "正在读取 Skill…" : fallback ?? "Skill 读取失败，重试")}
  </button>;
}
