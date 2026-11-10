"""Versioned test requirements for a declared change, not proof of execution."""
from __future__ import annotations

import argparse
import json


VERSION = "change-scope-v1"

CHECKS = {
    "tool_contract": ("基础测试", "工具契约及错误处理"),
    "tool_safety": ("安全边界", "工具授权及边界"),
    "tool_calling": ("专项评测", "工具选择、参数与结果使用"),
    "affected_chain": ("完整链路", "受影响的用户任务链路"),
    "business_contract": ("基础测试", "业务规则及接口契约"),
    "business_specialist": ("专项评测", "对应业务专项"),
    "business_safety": ("安全边界", "业务授权及副作用"),
    "agent_specialist": ("专项评测", "Agent 共享能力专项"),
    "agent_safety": ("安全边界", "Agent 安全与边界"),
    "cross_domain": ("完整链路", "跨场景任务链路"),
    "common_regression": ("常规回归", "常规全量回归"),
    "judge_calibration": ("评分校准", "评分器固定正反例与证据重放"),
    "ui_behavior": ("基础测试", "界面操作、状态及响应式"),
    "full_regression": ("完整回归", "含重型资源的完整回归"),
}

# Rules describe necessary coverage, not a promise that a runnable suite exists.
RULES = {
    "tool": {"required": ("tool_contract", "tool_safety", "tool_calling", "affected_chain"),
             "recommended": ("common_regression",)},
    "business": {"required": ("business_contract", "business_specialist", "business_safety", "affected_chain"),
                 "recommended": ("common_regression",)},
    "agent_shared": {"required": ("agent_specialist", "agent_safety", "cross_domain", "common_regression"),
                     "recommended": ("full_regression",)},
    "judge": {"required": ("judge_calibration",), "recommended": ()},
    "ui": {"required": ("ui_behavior",), "recommended": ()},
}


def plan(scopes):
    if not isinstance(scopes, list) or not scopes or any(not isinstance(scope, str) or not scope for scope in scopes):
        raise ValueError("Declare at least one change scope")
    known, unknown = [], []
    for scope in scopes:
        bucket = known if scope in RULES else unknown
        if scope not in bucket:
            bucket.append(scope)
    required, recommended = [], []
    for scope in known:
        for check in RULES[scope]["required"]:
            if check not in required:
                required.append(check)
        for check in RULES[scope]["recommended"]:
            if check not in recommended:
                recommended.append(check)
    def row(key):
        level, title = CHECKS[key]
        return {"id": key, "level": level, "title": title}
    return {"rule_version": VERSION, "declared_scopes": scopes, "known_scopes": known,
            "unknown_scopes": unknown, "coverage_known": not unknown,
            "required": [row(key) for key in required],
            "recommended": [row(key) for key in recommended if key not in required],
            "note": "测试要求不代表已运行；未覆盖的必要项不能视为通过。"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scopes", nargs="+", help="tool, business, agent_shared, judge, ui")
    args = parser.parse_args()
    print(json.dumps(plan(args.scopes), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
