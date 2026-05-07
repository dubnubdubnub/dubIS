"""Build the JSON payload that feeds the triage prompt.

Calls `gh` to pull run metadata + failed-job logs + PR metadata. Derives a
stable signature `(workflow, job, normalized_first_error_line)` so the
audit log can answer "have we seen this failure before?".
"""
from __future__ import annotations

import json
import re
import subprocess
from typing import Any, Optional

_MAX_CHARS_DEFAULT = 8000

_TIMESTAMP_RE = re.compile(
    r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?\b"
)
_PID_RE = re.compile(r"\b(?:pid|PID)[:= ]?\d+\b")
_NUMBER_RUN_RE = re.compile(r"\b(?:run[-_ ]?id|workflow[-_ ]?run)[:= ]?\d+\b", re.IGNORECASE)
_ABS_PATH_RE = re.compile(r"(?:/Users/[^\s:]+|/home/[^\s:]+|[A-Z]:\\[^\s:]+)")
_HEX_SHA_RE = re.compile(r"\b[0-9a-f]{7,40}\b")


def trim_log(text: str, *, max_chars: int = _MAX_CHARS_DEFAULT) -> str:
    """Trim log to last `max_chars`, preserving end (where errors live)."""
    if len(text) <= max_chars:
        return text
    suffix = text[-max_chars:]
    return f"[truncated {len(text) - max_chars} chars from start]\n{suffix}"


def _normalize_error_line(line: str) -> str:
    out = line
    out = _TIMESTAMP_RE.sub("<TS>", out)
    out = _PID_RE.sub("<PID>", out)
    out = _NUMBER_RUN_RE.sub("<RUN>", out)
    out = _ABS_PATH_RE.sub("<PATH>", out)
    out = _HEX_SHA_RE.sub("<SHA>", out)
    return out.strip()


def derive_signature(*, workflow: str, job: str, log_excerpt: str) -> str:
    """First non-empty line that looks like an error becomes the signature tail."""
    error_line = ""
    for raw in log_excerpt.splitlines():
        s = raw.strip()
        if not s:
            continue
        # Heuristic: first line containing 'error', 'Error', 'fail', or a stack indicator.
        if re.search(r"error|Error|FAIL|Traceback|Exception|ECONN|ETIMED|killed", s):
            error_line = s
            break
    if not error_line:
        # Fall back to last non-empty line of the log.
        for raw in reversed(log_excerpt.splitlines()):
            s = raw.strip()
            if s:
                error_line = s
                break
    return f"{workflow}|{job}|{_normalize_error_line(error_line)}"


def _run_gh(args: list[str], *, capture: bool = True) -> str:
    """Wrapper for `gh` calls. Patched in tests."""
    result = subprocess.run(
        ["gh", *args],
        check=True,
        capture_output=capture,
        text=True,
    )
    return result.stdout


def build_payload(*, run_id: int, pr: Optional[int]) -> dict[str, Any]:
    """Pull run metadata, failed-job logs, and PR meta. Trim and assemble."""
    run_json = json.loads(_run_gh([
        "run", "view", str(run_id),
        "--json", "status,conclusion,headSha,headBranch,event,workflowName,jobs",
    ]))
    failed_log = _run_gh(["run", "view", str(run_id), "--log-failed"])
    log_excerpt = trim_log(failed_log, max_chars=_MAX_CHARS_DEFAULT)

    failed_jobs = [j for j in run_json.get("jobs", []) if j.get("conclusion") == "failure"]
    primary_job = failed_jobs[0]["name"] if failed_jobs else "unknown"

    payload: dict[str, Any] = {
        "run_id": run_id,
        "pr": pr,
        "workflow": run_json.get("workflowName", "unknown"),
        "job": primary_job,
        "head_sha": run_json.get("headSha", ""),
        "head_branch": run_json.get("headBranch", ""),
        "event": run_json.get("event", ""),
        "log_excerpt": log_excerpt,
        "signature": derive_signature(
            workflow=run_json.get("workflowName", "unknown"),
            job=primary_job,
            log_excerpt=log_excerpt,
        ),
        "all_failed_jobs": [j.get("name") for j in failed_jobs],
    }

    if pr is not None:
        pr_json = json.loads(_run_gh([
            "pr", "view", str(pr),
            "--json", "number,headRefName,title,author,labels",
        ]))
        payload["pr_meta"] = pr_json
    else:
        payload["pr_meta"] = None

    return payload
