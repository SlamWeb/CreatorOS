"""Experimental per-trial research call limiter; not a complete sandbox.

This wrapper is not installed automatically.  It must be paired with an
independently verified tool policy and disabled production/publication paths.
The exclusive receipt consumes the budget before dispatch, including on error.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import stat

from creatoros.evaluation.benchmark.manifest import _reject_links
from creatoros.integrations.topic_research import CodexTopicResearcher

VERSION = "research-budget-v1"
_BATCH = re.compile(r"^[a-f0-9]{32}$")


class ResearchBudgetExceeded(RuntimeError):
    pass


class GuardedResearcher(CodexTopicResearcher):
    """Allow one call for one isolated account, series, candidate and workspace."""

    def __init__(self, service, delegate, *, trial_root: Path, creator_id: str, series_id: str):
        self.service, self.delegate = service, delegate
        if not Path(trial_root).is_absolute() or not Path(service.root).is_absolute():
            raise ValueError("research guard requires absolute isolated paths")
        self.trial_root = Path(os.path.abspath(trial_root))
        self.creator_id, self.series_id = creator_id, series_id
        root = Path(os.path.abspath(service.root))
        _reject_links(self.trial_root, Path(self.trial_root.anchor))
        info = self.trial_root.lstat()
        if (not self.trial_root.is_dir() or not root.is_dir() or stat.S_ISLNK(info.st_mode)
                or getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)):
            raise ValueError("research guard requires existing isolated directories")
        _reject_links(root, self.trial_root)
        self.root = root
        self.claim = root / ".benchmark-research-dispatch-claimed"

    def preflight(self):
        if hasattr(self.delegate, "preflight"):
            self.delegate.preflight()

    def research(self, snapshot, count, instructions, workspace, cancel, *, public_observer=None):
        workspace = Path(os.path.abspath(workspace))
        _reject_links(workspace, self.root)
        try:
            parts = workspace.relative_to(self.root).parts
        except ValueError as error:
            raise ResearchBudgetExceeded("research workspace is outside isolated root") from error
        if (len(parts) != 3 or parts[0] != "work" or not _BATCH.fullmatch(parts[1])
                or parts[2] != "1" or not workspace.is_dir()):
            raise ResearchBudgetExceeded("research workspace is not a first attempt")
        batch = self.service._load(parts[1])
        series = self.service.repository.get_series(self.series_id)
        creator = snapshot.get("creator") if isinstance(snapshot, dict) else None
        series_context = snapshot.get("series") if isinstance(snapshot, dict) else None
        if (not isinstance(creator, dict) or creator.get("id") != self.creator_id
                or not isinstance(series_context, dict)
                or ("id" in series_context and series_context["id"] != self.series_id)
                or series is None or series.creator_id != self.creator_id
                or batch.get("id") != parts[1] or batch.get("series_id") != self.series_id
                or type(batch.get("count")) is not int or batch["count"] != 1
                or type(count) is not int or count != 1 or cancel.is_set()):
            raise ResearchBudgetExceeded("research target, account or candidate budget differs")
        # Production's SeriesResearchContext intentionally excludes the ID.
        # Verify the entire supplied context against a fresh snapshot of the
        # exact bound series instead of inventing an ID that was never captured.
        # Identical same-account contexts cannot by themselves prove origin;
        # the record and in-process service boundary remain required as well.
        if snapshot != self.service.snapshot(self.series_id):
            raise ResearchBudgetExceeded("research snapshot differs from the bound series")
        _reject_links(self.claim, self.root)
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
        try:
            fd = os.open(self.claim, flags, 0o600)
        except FileExistsError as error:
            raise ResearchBudgetExceeded("research dispatch budget already consumed") from error
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump({"version": VERSION, "batch_id": parts[1], "creator_id": self.creator_id,
                       "series_id": self.series_id}, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        result = self.delegate.research(snapshot, count, instructions, workspace, cancel,
                                        public_observer=public_observer)
        candidates = getattr(getattr(result, "receipt", None), "candidates", None)
        if not isinstance(candidates, list) or len(candidates) > 1:
            # The external call has already happened; quarantine its result and
            # keep the claim consumed rather than reporting it within budget.
            raise ResearchBudgetExceeded("research result exceeds candidate budget or is malformed")
        return result
