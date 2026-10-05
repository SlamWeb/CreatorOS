"""Opt-in real Codex fusion probe: isolated Skills, no images generated or library save."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from tempfile import mkdtemp
from time import monotonic
from uuid import uuid4

from PIL import Image

from creatoros.integrations.producer_skills import ProducerSkillCatalog, _digest, inherit_copy_permissions
from creatoros.integrations.skill_extraction import SkillExtractionService


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", help="Use the local Codex login and real model quota.")
    args = parser.parse_args()
    if not args.run:
        parser.error("Real model use requires --run; this probe never produces images or saves a draft into the library.")
    root = Path(mkdtemp(prefix="skill-merge-live-", dir=Path("tmp").resolve()))
    # mkdtemp on Windows may create a protected ACL. Make the isolated evidence
    # root inherit the workspace ACL before catalog/job folders are created.
    inherit_copy_permissions(root)
    catalog = ProducerSkillCatalog(root / "catalog")
    ids = []
    for name, role, body in (
        ("probe-word-method", "mind", "Explain one confusing pair of English words using two everyday examples. Give each meaning in Chinese and English. Use new words supplied by the user, not only the examples."),
        ("probe-clean-layout", "production", "Present the supplied material as a clean image carousel with blue accents. Use assets/reference.png only as a palette reference. Preserve the content method and bilingual labels. Do not invent research or change the word meanings."),
    ):
        folder = root / name
        folder.mkdir()
        output_contract = "creatoros-output: social-content-pack.image-carousel\n" if role == "production" else ""
        (folder / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: Reusable isolated fusion probe.\n{output_contract}---\n\n{body}\n",
            encoding="utf-8")
        (folder / "assets").mkdir()
        Image.new("RGB", (40, 40), "#c9e1ff").save(folder / "assets" / "reference.png")
        ids.append(catalog.register_local(folder, role=role)["id"])
    before = {item["id"]: _digest(Path(item["local_path"])) for item in catalog.list() if item["id"] in ids}
    service = SkillExtractionService(catalog)
    service.start()
    started = monotonic()
    job = service.submit_merge(uuid4().hex, ids, "融合成可复用的中英双语单词教学 Skill。保留示例图片相对引用，不生图、不试产、不入库。")
    print(f"probe_directory={root}", flush=True)
    try:
        while service.worker and service.worker.is_alive():
            service.worker.join(5)
            if monotonic() - started > 600:
                service.cancel(job["id"])
                service.worker.join(10)
                break
        result = service.get(job["id"])
        assert result["status"] == "ready", result.get("error")
        assert result["task_kind"] == "merge" and not result["saved_skills"]
        assert len(result["skills"]) == 1 and result["source_skills"]
        assert any(file["path"].endswith("reference.png") for file in result["files"])
        assert {item["id"]: _digest(Path(item["local_path"])) for item in catalog.list() if item["id"] in ids} == before
        assert len(catalog.list()) == len(ids) + 1  # Includes the immutable builtin.
        print(json.dumps({"status": result["status"], "seconds": round(monotonic() - started, 1),
                          "thread_id": result["thread_id"], "skill": result["skills"][0]["name"],
                          "file_count": len(result["files"]), "registered": False}, ensure_ascii=False))
    finally:
        service.shutdown()


if __name__ == "__main__":
    main()
