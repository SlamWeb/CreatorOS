import { useEffect, useId, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { ChevronDown } from "lucide-react";
import { contextUsageView, isReasoningEffort, REASONING_EFFORTS, type ContextUsage, type ReasoningEffort } from "./agentComposerState";
import "./agentComposerControls.css";

const labels: Record<ReasoningEffort, string> = { low: "低", medium: "中", high: "高", xhigh: "极高", ultra: "ultra" };
export function AgentComposerControls({ usage, effort, options, actualEffortMap, disabled, onChange }: {
  usage?: ContextUsage | null; effort: string; options?: string[]; disabled: boolean;
  actualEffortMap?: Record<string, string>;
  onChange: (value: ReasoningEffort) => void;
}) {
  const helpId = useId();
  const available = options ?? [...REASONING_EFFORTS];
  const valid = isReasoningEffort(effort) && available.includes(effort);
  const legacy = effort === "none" || effort === "max";
  const mapping = actualEffortMap ? available.filter(isReasoningEffort).map(option =>
    `${labels[option]} → ${actualEffortMap[option] ?? "尚未提供"}`).join("；") : "实际档位由服务端确认";
  const shared = actualEffortMap?.medium && actualEffortMap.medium === actualEffortMap.high &&
    actualEffortMap.high === actualEffortMap.xhigh ? `。中、高、极高当前实际同为 ${actualEffortMap.high}` : "";
  const explanation = `DeepSeek 实际推理强度：${mapping}${shared}。`;
  return <div className="agent-composer-controls">
    <ContextRing usage={usage} />
    <label className="agent-reasoning-control">
      <span>推理</span>
      <select aria-label="推理程度" aria-describedby={helpId} title={explanation}
        value={valid || legacy ? effort : ""} disabled={disabled}
        onChange={event => { if (isReasoningEffort(event.target.value)) onChange(event.target.value); }}>
        {legacy && <option value={effort} disabled>旧请求：{effort === "none" ? "关闭" : "最高"}</option>}
        {!valid && !legacy && <option value="" disabled>请选择</option>}
        {available.filter(isReasoningEffort).map(option => <option key={option} value={option}>{labels[option]}</option>)}
      </select>
      <ChevronDown size={12} aria-hidden="true" />
    </label>
    <span id={helpId} className="sr-only">{explanation}</span>
  </div>;
}

function ContextRing({ usage }: { usage?: ContextUsage | null }) {
  const id = useId();
  const trigger = useRef<HTMLButtonElement>(null);
  const [position, setPosition] = useState<{ left: number; bottom: number } | null>(null);
  const [pinned, setPinned] = useState(false);
  const view = contextUsageView(usage);
  const show = () => {
    const rect = trigger.current?.getBoundingClientRect();
    if (rect) setPosition({ left: Math.max(8, Math.min(rect.left, window.innerWidth - 248)), bottom: window.innerHeight - rect.top + 8 });
  };
  useEffect(() => {
    if (!position) return;
    const close = (event: Event) => {
      if (event.type === "pointerdown" && trigger.current?.contains(event.target as Node)) return;
      setPinned(false); setPosition(null);
    };
    window.addEventListener("pointerdown", close);
    window.addEventListener("resize", close);
    return () => { window.removeEventListener("pointerdown", close); window.removeEventListener("resize", close); };
  }, [position]);
  return <>
    <button ref={trigger} type="button" className="agent-context-ring" aria-label={view?.label ?? "上下文用量：暂无统计"}
      aria-describedby={position ? id : undefined} aria-expanded={!!position}
      onMouseEnter={show} onMouseLeave={() => { if (!pinned && document.activeElement !== trigger.current) setPosition(null); }} onFocus={show}
      onBlur={() => { setPinned(false); setPosition(null); }}
      onClick={() => { if (pinned) { setPinned(false); setPosition(null); } else { setPinned(true); show(); } }}
      onKeyDown={event => { if (event.key === "Escape") { setPinned(false); setPosition(null); } }}>
      <svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true">
        <circle cx="12" cy="12" r="8" className="agent-context-track" />
        {view && <circle cx="12" cy="12" r="8" className="agent-context-value" pathLength="100"
          strokeDasharray={`${view.ratio * 100} 100`} transform="rotate(-90 12 12)" />}
      </svg>
    </button>
    {position && createPortal(<div id={id} role="tooltip" className="agent-context-tooltip" style={position}>
      <strong>{view?.label ?? "上下文用量暂无统计"}</strong>
      <span>{view?.source ?? "首次请求返回用量后显示"}</span>
      <span>显示单次输入，不累计历史用量</span>
    </div>, document.body)}
  </>;
}
