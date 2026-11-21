"""`eia run <job>`：跑一次任务，加锁，写运行记录（SPEC-scheduler 第 3 节）。

运行记录是给人看的摘要，不含 key、不含新闻全文。
"""

from __future__ import annotations

import datetime as dt
import json
import os
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path

from eia.schema.io import atomic_write

LOCK_DIR = Path(".eia") / "locks"
RUNS_DIR = Path(".eia") / "runs"
STALE_LOCK = dt.timedelta(hours=2)
KEEP_RUNS = 200


class JobFailed(Exception):
    """任务跑完了但结果算失败（例如所有新闻源都连不上）。消息就是失败原因。"""


@dataclass(frozen=True)
class RunRecord:
    job: str
    started: str  # ISO 8601，UTC
    seconds: float
    ok: bool
    summary: str
    error: str | None = None


def _headlines(root: Path) -> str:
    from eia.headlines.run import run

    result = run(root)
    working = sum(1 for status in result.feeds if not status.error)
    written = result.written
    summary = (
        f"{working}/{len(result.feeds)} 个源可用，{result.qualifying} 组达到门槛："
        f"新建 {len(written.created)}，更新 {len(written.updated)}，未变 {written.unchanged}"
    )
    if result.feeds and working == 0:
        raise JobFailed(f"所有新闻源都连不上（{summary}）")
    return summary


JOBS: dict[str, Callable[[Path], str]] = {"headlines": _headlines}


def run_job(root: Path, job: str, *, now: Callable[[], dt.datetime] | None = None) -> RunRecord:
    """跑一次 job。任何异常都记成失败，不往外抛：定时运行时没人看终端。"""
    clock = now or (lambda: dt.datetime.now(dt.UTC))
    task = JOBS[job]
    started = clock()
    start_mono = time.monotonic()
    lock = root / LOCK_DIR / f"{job}.lock"
    if not _acquire(lock, started):
        record = RunRecord(job, _iso(started), 0.0, False, "上次还在运行，这次跳过", None)
        _append(root, record)
        return record
    try:
        summary = task(root)
        record = RunRecord(job, _iso(started), _elapsed(start_mono), True, summary)
    except Exception as exc:  # 定时任务：把一切失败都记下来，而不是让进程静默退出
        record = RunRecord(job, _iso(started), _elapsed(start_mono), False, "失败", _reason(exc))
    finally:
        lock.unlink(missing_ok=True)
    _append(root, record)
    return record


def read_runs(root: Path, job: str) -> list[RunRecord]:
    path = root / RUNS_DIR / f"{job}.jsonl"
    if not path.is_file():
        return []
    records: list[RunRecord] = []
    for line in path.read_bytes().decode("utf-8", errors="replace").splitlines():
        try:
            records.append(RunRecord(**json.loads(line)))
        except (ValueError, TypeError):
            continue
    return records


def _append(root: Path, record: RunRecord) -> None:
    records = [*read_runs(root, record.job), record][-KEEP_RUNS:]
    path = root / RUNS_DIR / f"{record.job}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "".join(json.dumps(asdict(r), ensure_ascii=False) + "\n" for r in records)
    atomic_write(path, text)


def _acquire(lock: Path, now: dt.datetime) -> bool:
    lock.parent.mkdir(parents=True, exist_ok=True)
    if lock.exists():
        age = now - dt.datetime.fromtimestamp(lock.stat().st_mtime, dt.UTC)
        if age < STALE_LOCK:
            return False
        lock.unlink(missing_ok=True)  # 残留锁：上次被强制结束了
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return False
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(f"{os.getpid()} {_iso(now)}\n")
    return True


def _reason(exc: BaseException) -> str:
    text = " ".join(str(exc).split()) or type(exc).__name__
    return text if len(text) <= 300 else text[:300] + "…"


def _iso(when: dt.datetime) -> str:
    return when.astimezone(dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _elapsed(start: float) -> float:
    return round(time.monotonic() - start, 1)
