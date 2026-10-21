export type SkillEditContext = {
  creatorId?: string | null;
  seriesId?: string;
  runId?: string;
  revisionId?: string;
};

export type SkillEditHandoff = SkillEditContext & {
  skillId: string; skillName: string; path: string; request: string;
};

export function skillEditPrompt(value: SkillEditHandoff): string {
  const context = [value.creatorId && `账号 ID：${value.creatorId}`,
    value.seriesId && `栏目 ID：${value.seriesId}`, value.runId && `作品 Run ID：${value.runId}`,
    value.revisionId && `当前查看的 Revision ID：${value.revisionId}`].filter(Boolean).join("\n");
  return `请帮我修改 CreatorOS 本地 Skill「${value.skillName}」，先保存为修改候选。\nSkill ID：${value.skillId}\n目标文件：${value.path}`
    + (context ? `\n${context}` : "") + `\n\n修改要求：\n${value.request}\n\n`
    + "请用中文回复。先检查当前文件，再按上述明确要求修改候选；仅在有实质歧义时询问。"
    + "如需分析图片，先核实上述作品版本并委托 Codex 看图讨论；文件路径不代表已看图。"
    + "正文和 assets 一起保留，跟随写入工具返回的候选 Skill ID 继续修改。不要自动切换栏目，由我确认后再切换；普通版本无需先试产。"
    + "保留旧版本、历史产物及正在跑的任务。只有写入工具成功才说候选已保存。";
}
