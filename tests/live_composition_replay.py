"""Explicit real SDK replay of frozen failed inputs, outside the formal DB.

--produce runs the original image request (paid); otherwise only reads/checks.
Old failures, shared Skills, running service and task state are never modified.
"""
import argparse
import asyncio
import hashlib
import json
import shutil
from pathlib import Path
from time import monotonic

from openai_codex import ApprovalMode, Sandbox

from creatoros.config import PROJECT_ROOT
from creatoros.integrations.codex import (
    CODEX_MODEL, PRODUCTION_RULES, CodexSdkProducer, _production_client,
)
from creatoros.integrations.native_production import (
    Checkpoint, NativeProgress, has_production_images, load_checkpoint, produce_native, request_digest,
    require_ready, review_composition, skill_refs,
)
from creatoros.integrations.producer_skills import _digest, freeze_skill
from creatoros.integrations.skill_pair import SkillPair
from creatoros.integrations.topic_references import frozen_references, prepare_references
from creatoros.integrations.atomic_file import atomic_write_text


def hashes(root):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob("*") if p.is_file()}


def save_report(root, report):
    atomic_write_text(root / "report.json", json.dumps(report, ensure_ascii=False, indent=2))


def copy_reference_bundle(source, root):
    reference = frozen_references(source)
    if reference is None:
        return None
    bundle = root / "reference-bundles" / reference.id
    bundle.mkdir(parents=True)
    images = []
    for image in reference.images:
        target = bundle / Path(image.path).name
        shutil.copyfile(image.path, target)
        images.append(image.model_copy(update={"path": str(target)}))
    copied = reference.model_copy(update={"images": images})
    (bundle / "input.json").write_text(copied.model_dump_json(indent=2), encoding="utf-8")
    return copied


async def review(producer, directory, request):
    deadline = monotonic() + 300
    async with _production_client(deadline) as client:
        thread = await client.thread_start(
            cwd=str(directory / "work"), model=CODEX_MODEL, sandbox=Sandbox.read_only,
            approval_mode=ApprovalMode.deny_all, developer_instructions=PRODUCTION_RULES)
        checkpoint = Checkpoint(input_digest=request_digest(directory), thread_id=thread.id,
            skill_digests={role: _digest(path.parent) for role, path in skill_refs(directory)})
        progress = NativeProgress(directory, checkpoint)
        result = await review_composition(thread, producer, directory, skill_refs(directory),
                                         request, progress, deadline)
        require_ready(result)
        return {"review": result.model_dump(), "thread_id": thread.id,
                "usage": checkpoint.usage.model_dump()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-attempt", type=Path, required=True)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--produce", action="store_true")
    args = parser.parse_args()
    source, root = args.source_attempt.resolve(), args.directory.resolve()
    if not root.is_relative_to((PROJECT_ROOT / "tmp").resolve()) or root.exists():
        parser.error("必须使用tmp下尚不存在的隔离目录；旧失败不覆盖、不自动重试。")
    prior = load_checkpoint(source)
    producer = CodexSdkProducer.from_defaults()
    if prior is None or has_production_images(source, prior, producer.generated_images_root):
        parser.error("只接受尚未交付图片的有效失败Attempt。")
    before = hashes(source)
    root.mkdir(parents=True)
    report = {"source": str(source), "formal_data_written": False, "status": "running",
              "mode": "production" if args.produce else "review", "source_files": before}
    save_report(root, report)
    request_text = (source / "production_request.txt").read_text(encoding="utf-8")
    request = json.loads(request_text)
    directory = root / "run/revision-001/attempt-001"
    try:
        references = copy_reference_bundle(source, root)
        if args.produce:
            snapshot = root / "run/skill-snapshot"
            pair = SkillPair.model_validate_json(
                (source.parent.parent / "skill-snapshot/pair.json").read_text(encoding="utf-8"))
            for role, path in skill_refs(source):
                digest = _digest(path.parent)
                assert digest == getattr(pair, role).digest
                freeze_skill(path.parent, snapshot / "skills" / role, digest)
            result = produce_native(producer, **request, directory=directory, composition=pair,
                                    task_references=references)
            report.update(status="passed", images=len(result.pack.cards),
                          artifact_directory=str(result.directory),
                          thread_id=result.session.thread_id, usage=result.session.usage.model_dump())
        else:
            (directory / "work").mkdir(parents=True)
            for role, path in skill_refs(source):
                freeze_skill(path.parent, directory / "skills" / role, _digest(path.parent))
            if references:
                prepare_references(references, directory)
            (directory / "production_request.txt").write_text(request_text, encoding="utf-8")
            report.update(asyncio.run(review(producer, directory, request_text)), status="passed")
    except BaseException as error:
        report.update(status="failed", error_type=getattr(error, "error_type", type(error).__name__),
                      error=str(error))
        raise
    finally:
        report["source_unchanged"] = hashes(source) == before
        save_report(root, report)
        print(json.dumps({key: value for key, value in report.items() if key != "source_files"},
                         ensure_ascii=False, indent=2), flush=True)
        assert report["source_unchanged"], "旧失败证据发生变化"


if __name__ == "__main__":
    main()
