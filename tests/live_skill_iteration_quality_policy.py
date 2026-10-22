"""Opt-in one real DeepSeek rubric request; no Codex or formal database writes."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil

from creatoros.config import PROJECT_ROOT
from creatoros.integrations.skill_iteration_judge import evaluate, system_prompt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--scope", choices=("content", "presentation", "overall"), default="overall")
    args = parser.parse_args()
    root = args.root.resolve()
    if not root.is_relative_to((PROJECT_ROOT / "tmp").resolve()) or root.exists():
        raise ValueError("Use a new ignored tmp directory, never formal data or old evidence")
    source = args.reference.resolve(strict=True)
    if not source.is_relative_to((PROJECT_ROOT / "tmp").resolve()):
        raise ValueError("Only use an isolated reference, not formal account files")
    root.mkdir(parents=True)
    reference = root / ("reference" + source.suffix)
    shutil.copyfile(source, reference)
    role = {"content": "mind", "presentation": "production", "overall": "legacy_end_to_end"}[args.scope]
    payload = dict(instructions="仅本次候选，最多1轮，不批准、不发布、不切栏目、不覆盖共享Skill。"
        "作品固定2×2四格、4个常用英语单词；文字清楚而且不遮住人物的脸，不添加全图大标题。"
        "每格的简短情境要体现词义差别。", topic="look / see / watch / stare：看与观看",
        topic_brief="四格词汇教学。保持参考的清楚可读和情境教学方式，不照搬参考题材。", role=role,
        judge_policy=dict(version=2, scope=args.scope, role=role))
    def write(name, value):
        (root / name).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    write("request.json", dict(kind="rubric", input=payload, images=[dict(name="优秀参考第1张",
        path=str(reference), sha256=hashlib.sha256(reference.read_bytes()).hexdigest())],
        system=system_prompt("rubric", payload)))
    try:
        result, usage = evaluate("rubric", payload, [("优秀参考第1张", reference)],
            response_sink=lambda raw: write("raw-response.json", raw))
    except Exception as error:
        write("error.json", dict(type=type(error).__name__, message=str(error)))
        raise
    write("result.json", dict(result=result, usage=usage,
        acceptance="Requires inspecting every criterion's meaning; typed scope alone is not semantic proof."))
    print(json.dumps(dict(root=str(root), scope=args.scope, criteria=result["criteria"],
        workflow_constraints=result["workflow_constraints"], usage=usage), ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
