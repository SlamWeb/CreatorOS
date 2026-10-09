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
  return `请帮我修改并保存 CreatorOS 本地 Skill「${value.skillName}」。\nSkill ID：${value.skillId}\n目标文件：${value.path}`
    + (context ? `\n${context}` : "") + `\n\n修改要求：\n${value.request}\n\n`
    + "请用中文回复。先检查当前文件，再按上述明确要求修改并保存；仅在有实质歧义时询问。"
    + "如需分析图片，先核实上述作品版本并委托 Codex 看图讨论；文件路径不代表已看图。"
    + "说明共享 Skill 对后续生产的影响，保留历史 Run 冻结版本。只有写入工具成功才说已保存。";
}
