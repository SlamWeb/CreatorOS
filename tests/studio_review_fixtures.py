"""Isolated artifact/HTTP fixtures, never a claim of real Codex generation."""
from pathlib import Path
import shutil

from PIL import Image

from creatoros.content import CarouselCard, PublicationCopy, SocialContentPack, SourceRef
from creatoros.integrations.codex import CodexUsage, ProducedPack, ProductionSession
from creatoros.runs import ContentRunService
from creatoros.storage import ContentRepository, CreatorPlatform, Database, TopicSource, upgrade_database
from creatoros.web.app import create_app


class ReviewProducer:
    def __init__(self, count=5, source: Path | None = None):
        self.count, self.source, self.calls = count, source, 0

    def produce_to(self, **request):
        self.calls += 1
        request["on_thread_started"]("isolated-review-thread")
        root = Path(request["directory"])
        (root / "images").mkdir(parents=True)
        if self.source:
            original = SocialContentPack.load(self.source)
            cards = []
            for card in original.cards[:self.count]:
                relative = f"images/{card.order:02d}{Path(card.image_path).suffix}"
                shutil.copyfile(self.source / card.image_path, root / relative)
                cards.append(card.model_copy(update={"image_path": relative}))
            copy, summary, sources = original.publish_copy, original.content_summary, original.sources
        else:
            cards = []
            for index in range(self.count):
                path = f"images/{index+1:02d}.png"
                Image.new("RGB", (320 + index * 20, 460), (35 + index * 25, 55, 90)).save(root / path)
                cards.append(CarouselCard(order=index+1, kind="content", headline=f"测试卡片 {index+1}", image_path=path))
            copy = PublicationCopy(title="理解 Agent 的状态与上下文", body="测试文案。\n这些图片仅用于接口与浏览器验收。", hashtags=["#Agent", "#学习笔记"])
            summary, sources = "[隔离测试] 图片验收与人工批准，不涉及真实发布。", [SourceRef(source_id="one", title="安全链接", url="https://example.com"), SourceRef(source_id="two", title="不可点击链接", url="javascript:alert(1)")]
        pack = SocialContentPack(pack_id=request["pack_id"], creator_id=request["creator_id"],
            series_id=request["series_id"], topic_id=request["topic_id"], topic_title=request["topic_title"],
            skill_name="knowledge-to-carousel", generated_at="2026-09-04T12:00:00+08:00", content_summary=summary,
            cards=cards, publish_copy=copy, sources=sources)
        (root / "social_content_pack.json").write_text(pack.model_dump_json(indent=2), encoding="utf-8")
        session = ProductionSession(thread_id="isolated-review-thread", pack_id=pack.pack_id,
            created_at=pack.generated_at, usage=CodexUsage())
        return ProducedPack(directory=root, pack=pack, session=session)


def make_fixture(root: Path, *, source: Path | None = None):
    url = f"sqlite:///{(root / 'review.db').as_posix()}"
    upgrade_database(url)
    database = Database(url)
    content = ContentRepository(database)
    content.create_creator(creator_id="review-lab", display_name="[隔离测试] 知识实验室", platform=CreatorPlatform.XIAOHONGSHU)
    content.create_series(series_id="agent-notes", creator_id="review-lab", name="把 Agent 讲明白", description="用图讲清一个知识点", audience="Agent 开发学习者", skill_name="knowledge-to-carousel")
    for number in (1, 2):
        content.add_topic(topic_id=f"review-{number}", series_id="agent-notes", title="Agent State、Context 和 Messages" if number == 1 else "从失败里恢复一次生产", source=TopicSource.MANUAL)
    producer = ReviewProducer(source=source)
    service = ContentRunService(database, producer_factory=lambda: producer, output_root=root / "outputs")
    return database, service, producer, create_app(database=database, run_service=service)
