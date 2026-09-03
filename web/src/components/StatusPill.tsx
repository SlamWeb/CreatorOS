const labels: Record<string, string> = {
  queued: "待开始", producing: "生产中", validating: "验收中", awaiting_approval: "待批准",
  interrupted: "已中断", failed: "失败", approved: "已批准", cancelled: "已取消",
  active: "运营中", draft: "草稿", ready: "就绪",
};

export function StatusPill({ status }: { status: string | null | undefined }) {
  const value = status ?? "unknown";
  return <span className={`status-pill status-${value}`}><span className="status-pill-dot" />{labels[value] ?? value}</span>;
}

export function formatDate(value: string | null | undefined) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" }).format(date);
}

export function platformLabel(platform: string) {
  const labels: Record<string, string> = { xiaohongshu: "小红书", zhihu: "知乎" };
  return labels[platform] ?? platform;
}
