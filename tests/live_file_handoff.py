"""Explicit isolated file-reference Codex trial and real HTTP readback; incurs model usage."""
import argparse
import hashlib
import json
from pathlib import Path

import httpx

from creatoros.config import PROJECT_ROOT
from creatoros.integrations.native_image_assets import ImageAssetStore
from creatoros.integrations.native_production import load_checkpoint
from creatoros.integrations.producer_skills import ProducerSkillCatalog, skills_root_for
from creatoros.runs import ContentRunService
from creatoros.storage import ContentRepository, CreatorPlatform, Database, TopicSource, upgrade_database
from creatoros.web import create_app
from tests.agent_studio_support import serve


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--images", type=int, choices=[1, 2], default=1)
    parser.add_argument("--rich-copy", action="store_true", help="Verify Chinese page text and publication copy")
    args = parser.parse_args()
    root = args.directory.resolve()
    if not root.is_relative_to((PROJECT_ROOT / "tmp").resolve()) or root.exists():
        parser.error("请使用tmp内尚不存在的隔离目录；失败目录保留，不自动重试。")
    root.mkdir(parents=True)
    author = root / "authoring"
    author.mkdir()
    (author / "SKILL.md").write_text(
        "---\nname: image-assets-trial\ndescription: 制作一张清楚简洁的儿童英语词汇图。\ncreatoros-output: social-content-pack.image-carousel\n---\n\n"
        "使用原生生图工具制作一张左右两格的英语词汇插画：左边猫和cat/猫，右边狗和dog/狗。"
        "明亮、简洁、文字清楚。只生成一次，不重画，不联网研究，不调用其他Skill。"
        "发布文案简短，交付实际生图工具返回的图片路径，不搬运文件。不批准、不发布。\n", encoding="utf-8")
    if args.images == 2:
        (author / "SKILL.md").write_text(
            "---\nname: image-assets-trial\ndescription: 两张独立动物词汇图。\ncreatoros-output: social-content-pack.image-carousel\n---\n"
            "用原生生图工具生成两张独立图片：一张猫cat，一张狗dog，简洁儿童插画。"
            "先生成猫，再生成狗；最终交付顺序反过来：狗是order 1，猫是order 2。"
            "每个动物生成一次，不重画，不联网；交付真实图片路径，不搬图片，不读取generated-assets.json。",
            encoding="utf-8")
    copy_title = "猫和狗，一起学英语"
    copy_body = "cat 是猫，dog 是狗。\n\n每天认识两个小伙伴！"
    page_body = "左边：cat / 猫；右边：dog / 狗。"
    if args.rich_copy:
        assert args.images == 1, "图文保真探针使用一张图"
        skill_file = author / "SKILL.md"
        skill_file.write_text(skill_file.read_text(encoding="utf-8") +
            "\n交付文案必须逐字使用以下JSON对应值，不添加解释，page_content填入artifacts[0].content：\n" +
            json.dumps({"title": copy_title, "text": copy_body, "hashtags": ["#英语启蒙", "#猫狗"],
                        "page_content": page_body}, ensure_ascii=False), encoding="utf-8")
    url = "sqlite:///" + (root / "trial.sqlite").as_posix()
    upgrade_database(url)
    database = Database(url)
    service = ContentRunService(database, output_root=root / "outputs")
    report = {"status": "started", "directory": str(root), "formal_data_written": False}
    try:
        catalog = ProducerSkillCatalog(skills_root_for(database))
        skill = catalog.register_local(author, role="production")
        repository = ContentRepository(database)
        repository.create_creator(creator_id="asset-trial", display_name="收图隔离验证", platform=CreatorPlatform.XIAOHONGSHU)
        repository.create_series(series_id="asset-series", creator_id="asset-trial", name="收图验证",
            description="验证图片由宿主接收、由模型按真实路径交付。", audience="儿童", skill_name=skill["id"])
        repository.add_topic(topic_id="asset-topic", series_id="asset-series", source=TopicSource.MANUAL,
            title="cat与dog", brief=f"制作{args.images}张图，严格按Skill中的数量、生成与交付顺序。用实际工具返回的source_image_path交付，不读取generated-assets.json。不要重画。")
        run = service.create("asset-topic")
        report["run_id"] = run.id
        (root / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False), flush=True)
        result = service.execute(run.id)
        attempt = Path(result.artifact_directory)
        checkpoint = load_checkpoint(attempt)
        assert checkpoint and checkpoint.turn_completed
        store = ImageAssetStore(attempt, Path(), checkpoint.thread_id, checkpoint.input_digest)
        received = store.read().assets
        delivery = json.loads((attempt / "work/delivery.json").read_text(encoding="utf-8-sig"))
        assert len(received) == args.images and len(checkpoint.pages) == args.images, "接收和交付数量不符合要求"
        assert all(item.get("asset_id") and not item.get("source_image_path") for item in delivery["artifacts"]), "宿主未转换为内部图片编号"
        envelope = json.loads((attempt / "work/structured-response.json").read_text(encoding="utf-8"))
        response = json.loads(envelope["response"])
        assert all(item.get("source_image_path") and "asset_id" not in item for item in response["artifacts"])
        assert [item["order"] for item in response["artifacts"]] == list(range(1, args.images + 1))
        if args.images == 2:
            assert [p.asset_id for p in checkpoint.pages] == [a.asset_id for a in reversed(received)], "交付未按要求逆序"
        if args.rich_copy:
            from creatoros.content import SocialContentPack
            pack = SocialContentPack.load(attempt)
            assert pack.publish_copy.title == copy_title
            assert pack.publish_copy.body == copy_body
            assert pack.publish_copy.hashtags == ["#英语启蒙", "#猫狗"]
            assert pack.cards[0].body == page_body
        with serve(create_app(database=database, run_service=service, chat_root=root / "chats")) as base:
            with httpx.Client(base_url=base, timeout=15) as client:
                detail_response = client.get("/api/runs/" + run.id)
                detail_response.raise_for_status()
                detail = detail_response.json()
                assert detail["status"] == "awaiting_approval"
                assert detail["revisions"][0]["approved_at"] is None
                cards = detail["revisions"][0]["cards"]
                assert len(cards) == len(checkpoint.pages)
                for card, page in zip(cards, checkpoint.pages):
                    response = client.get(card["url"])
                    response.raise_for_status()
                    assert hashlib.sha256(response.content).hexdigest() == page.sha256
        report.update(status="passed", result=result.model_dump(), candidate_images=len(received),
            selected_images=len(checkpoint.pages), reversed_order_checked=args.images == 2, asset_ids=[page.asset_id for page in checkpoint.pages],
            raw_delivery_has_image_paths=True, rich_copy_checked=args.rich_copy, http_detail=detail_response.status_code,
            http_image=response.status_code, received_sha256=checkpoint.pages[0].sha256,
            sdk_usage=json.loads((attempt / "production_session.json").read_text(encoding="utf-8"))["usage"],
            approved=False, published=False)
    except BaseException as error:
        report.update(status="failed", error_type=type(error).__name__, error=str(error))
        raise
    finally:
        (root / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
        database.close()


if __name__ == "__main__":
    main()
