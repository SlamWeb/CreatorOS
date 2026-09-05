"""Isolated browser-E2E server; the producer is deterministic and never calls Codex."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
from time import sleep

from PIL import Image

from creatoros.content import CarouselCard, PublicationCopy, SocialContentPack
from creatoros.integrations.codex import CodexUsage, ProducedPack, ProductionSession
from creatoros.runs import ContentRunService
from creatoros.storage import Database, upgrade_database
from creatoros.web.app import create_app


class E2EProducer:
    def produce_to(self, **request) -> ProducedPack:
        request["on_thread_started"]("studio-e2e-thread")
        sleep(1.5)
        directory = Path(request["directory"])
        (directory / "images").mkdir(parents=True)
        cards = []
        for order, color in enumerate(("#292837", "#48536c", "#77719a"), 1):
            relative = f"images/{order:02d}.png"
            Image.new("RGB", (720, 960), color).save(directory / relative)
            cards.append(CarouselCard(order=order, kind="cover" if order == 1 else "content", headline=f"E2E 卡片 {order}", image_path=relative))
        pack = SocialContentPack(
            pack_id=request["pack_id"], creator_id=request["creator_id"], series_id=request["series_id"],
            topic_id=request["topic_id"], topic_title=request["topic_title"], skill_name="knowledge-to-carousel",
            generated_at="2026-09-05T12:00:00+08:00", content_summary="[隔离 E2E] 验证生产、返工和批准流程。",
            cards=cards, publish_copy=PublicationCopy(title="Agent 状态怎么理解", body="浏览器 E2E 测试内容。", hashtags=["#Agent"]),
        )
        (directory / "social_content_pack.json").write_text(pack.model_dump_json(indent=2), encoding="utf-8")
        session = ProductionSession(thread_id="studio-e2e-thread", pack_id=pack.pack_id, created_at=pack.generated_at, usage=CodexUsage())
        (directory / "production_session.json").write_text(session.model_dump_json(indent=2), encoding="utf-8")
        return ProducedPack(directory=directory, pack=pack, session=session)


root = Path(os.environ["CREATOROS_E2E_ROOT"]).resolve()
root.mkdir(parents=True, exist_ok=True)
database_url = f"sqlite:///{(root / 'studio.db').as_posix()}"
upgrade_database(database_url)
database = Database(database_url)
runs = ContentRunService(database, producer_factory=E2EProducer, output_root=root / "outputs")
app = create_app(database=database, run_service=runs)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8877)
    args = parser.parse_args()
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
