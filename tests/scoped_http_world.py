"""Small persisted world for scoped HTTP unit tests, not an Agent benchmark."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from uuid import uuid4

from sqlalchemy import inspect, text

from creatoros.integrations.producer_skills import ProducerSkillCatalog, SkillInstallService, skills_root_for
from creatoros.integrations.skill_extraction import SkillExtractionService
from creatoros.integrations.topic_research import TopicResearchService
from creatoros.runs import ContentRunService, ManagedRunExecutor
from creatoros.storage import ContentRepository, ContentRun, ContentRunStatus, Database, TopicSource, upgrade_database
from creatoros.web.app import create_app


class ScopedHttpWorld:
    def __init__(self, root, provider_factory=None, *, task_history=False):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=False)
        url = f"sqlite:///{(self.root / 'world.db').as_posix()}"
        upgrade_database(url)
        self.database = Database(url)
        self.creator_a, self.creator_b = "http-own-account", "http-foreign-account"
        self.series_id = "http-own-series"
        self.external_attempts = []
        self.catalog = ProducerSkillCatalog(skills_root_for(self.database))
        content = ContentRepository(self.database)
        for creator in (self.creator_a, self.creator_b):
            content.create_creator(creator_id=creator, display_name=creator)
        source = self.root / "input-skill"
        source.mkdir()
        (source / "SKILL.md").write_text(
            "---\nname: scoped-http\ndescription: HTTP test input\n"
            "creatoros-output: social-content-pack.image-carousel\n---\n# Input\n", encoding="utf-8")
        skill = self.catalog.register_local(source, role="legacy_end_to_end")["id"]
        content.create_series(series_id=self.series_id, creator_id=self.creator_a,
                              name="HTTP test", description="", audience="", skill_name=skill)
        content.create_series(series_id="http-foreign-series", creator_id=self.creator_b,
                              name="Foreign test", description="", audience="", skill_name=skill)
        self.runs = ContentRunService(self.database, output_root=self.root / "outputs")
        executor = ManagedRunExecutor(self.runs)
        self.research = TopicResearchService(self.database, self.catalog)
        installs = SkillInstallService(self.catalog)
        extraction = SkillExtractionService(self.catalog)
        for service, method in ((executor, "submit"), (self.research, "submit"),
                                (installs, "submit"), (extraction, "submit")):
            setattr(service, method, self._blocked)
        self.app = create_app(database=self.database, run_service=self.runs, run_executor=executor,
            chat_root=self.root / "sessions", chat_provider_factory=provider_factory or self._blocked,
            skill_install_service=installs, skill_extraction_service=extraction,
            topic_research_service=self.research, eval_root=self.root / "eval")
        self.business_roots = [self.catalog.root, self.research.root, self.runs.output_root]
        if task_history:
            self._seed_tasks(content)

    def _blocked(self, *args, **kwargs):
        self.external_attempts.append("external execution")
        raise AssertionError("HTTP read tests must not execute external work")

    def _seed_tasks(self, content):
        self.failed_tasks, self.nonfailed_task_ids = [], []
        for failed in (False, True):
            topic_id = "http-failed-topic" if failed else "http-queued-topic"
            content.add_topic(topic_id=topic_id, series_id=self.series_id,
                              title=topic_id, source=TopicSource.MANUAL)
            run = self.runs.create(topic_id)
            if failed:
                with self.database.session() as session:
                    session.get(ContentRun, run.id).status = ContentRunStatus.FAILED
                self.failed_tasks.append({"id": run.id, "url": f"/runs/{run.id}"})
            else:
                self.nonfailed_task_ids.append(run.id)
            batch_id = uuid4().hex
            path = self.research._path(batch_id)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"id": batch_id, "series_id": self.series_id,
                "status": "failed" if failed else "ready", "count": 0, "candidates": [],
                "created_at": datetime.now(timezone.utc).isoformat(),
                "snapshot": self.research.snapshot(self.series_id), "note": "Persisted HTTP test input",
                "attempts": [], **({"error": "Seeded failed input"} if failed else {})}), encoding="utf-8")
            if failed:
                self.failed_tasks.append({"id": batch_id, "url": f"/series/{self.series_id}?research={batch_id}"})
            else:
                self.nonfailed_task_ids.append(batch_id)

    def state(self):
        tables = {}
        with self.database.engine.connect() as connection:
            for name in sorted(inspect(self.database.engine).get_table_names()):
                quoted = self.database.engine.dialect.identifier_preparer.quote(name)
                rows = [dict(row) for row in connection.execute(text(f"SELECT * FROM {quoted}")).mappings()]
                tables[name] = sorted(rows, key=lambda row: json.dumps(row, sort_keys=True, default=str))
        files = {path.relative_to(self.root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                 for directory in self.business_roots for path in directory.rglob("*") if path.is_file()}
        return {"database": tables, "files": files}

    def close(self):
        self.database.close()
