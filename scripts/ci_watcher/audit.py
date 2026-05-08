"""JSONL audit log for CI watcher triage decisions.

Lives on the long-lived `ci-watcher-log` branch in the repo. Every decision
the watcher makes appends a record here; the worker commits and pushes after
each event. Recent signatures are queried to drive the decision matrix.
"""
from __future__ import annotations

import calendar
import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterator, Optional


@dataclass(frozen=True)
class TriageRecord:
    ts: str
    run_id: int
    run_attempt: int
    workflow: str
    job: str
    pr: Optional[int]
    head_sha: str
    classification: str  # "pipeline" | "code" | "uncertain"
    signature: str
    action: str  # "rerun" | "push-fix" | "comment-only" | "open-issue" | "fresh-pr"
    rerun_count: int
    fix_pushed: bool
    comment_url: Optional[str]
    claude_run_dur_sec: float


def _parse_iso8601(ts: str) -> int:
    """Parse ISO-8601 'Z'-suffixed UTC timestamp to epoch seconds."""
    return calendar.timegm(time.strptime(ts, "%Y-%m-%dT%H:%M:%SZ"))


class AuditLog:
    def __init__(self, path: Path | str) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)

    @property
    def path(self) -> Path:
        return self._path

    def append(self, record: TriageRecord) -> None:
        line = json.dumps(asdict(record), separators=(",", ":"), ensure_ascii=False)
        with self._path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")

    def read_all(self) -> Iterator[TriageRecord]:
        if not self._path.exists():
            return iter(())
        with self._path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                yield TriageRecord(**json.loads(line))

    def distinct_prs_with_signature(self, signature: str, *, within_seconds: int) -> int:
        cutoff = int(time.time()) - within_seconds
        prs: set[int] = set()
        for r in self.read_all():
            if r.signature != signature:
                continue
            if r.pr is None:
                continue
            try:
                if _parse_iso8601(r.ts) < cutoff:
                    continue
            except ValueError:
                continue
            prs.add(r.pr)
        return len(prs)

    def rerun_count_for_pr_signature(self, *, pr: int, signature: str) -> int:
        return sum(
            1
            for r in self.read_all()
            if r.pr == pr and r.signature == signature and r.action == "rerun"
        )
