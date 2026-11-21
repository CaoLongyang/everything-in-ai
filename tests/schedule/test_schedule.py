"""F12：定时运行。schtasks 全部替换成假的，不会真的登记任务。"""

from __future__ import annotations

import datetime as dt
import os
import subprocess
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from eia.cli import main
from eia.headlines.config import Feed
from eia.headlines.run import FeedStatus, RunResult
from eia.headlines.writer import WriteResult
from eia.schedule import runs, windows
from eia.schedule.runs import KEEP_RUNS, JobFailed, read_runs, run_job
from eia.schedule.windows import ScheduleError, WindowsScheduler, task_name
from eia.schema.vault import init_vault

ROOT = Path(__file__).resolve().parents[2]
T0 = dt.datetime(2026, 11, 22, 0, 0, tzinfo=dt.UTC)


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    init_vault(root)
    return root


def _cli(*args: str) -> Any:
    return CliRunner().invoke(main, list(args))


# ---------- F12-01 运行与记录 ----------


def test_F12_01_success_and_failure_are_recorded(
    vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(runs.JOBS, "headlines", lambda root: "新建 2，更新 1")
    ok = run_job(vault, "headlines", now=lambda: T0)
    assert ok.ok and ok.summary == "新建 2，更新 1" and ok.started == "2026-11-22T00:00:00Z"

    def broken(root: Path) -> str:
        raise RuntimeError("Ollama 没开\n  第二行")

    monkeypatch.setitem(runs.JOBS, "headlines", broken)
    failed = run_job(vault, "headlines", now=lambda: T0)
    assert not failed.ok and failed.error == "Ollama 没开 第二行"
    assert [r.ok for r in read_runs(vault, "headlines")] == [True, False]
    first = read_runs(vault, "headlines")[0]
    assert (first.job, first.started, first.ok, first.summary) == (
        "headlines",
        "2026-11-22T00:00:00Z",
        True,
        "新建 2，更新 1",
    )
    assert not (vault / ".eia" / "locks" / "headlines.lock").exists()


def test_F12_01_cli_exit_codes(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(runs.JOBS, "headlines", lambda root: "新建 0")
    result = _cli("run", "headlines", "--vault", str(vault))
    assert result.exit_code == 0 and result.output.startswith("成功  headlines")

    def fail(root: Path) -> str:
        raise JobFailed("所有新闻源都连不上")

    monkeypatch.setitem(runs.JOBS, "headlines", fail)
    result = _cli("run", "headlines", "--vault", str(vault))
    assert result.exit_code == 1 and "失败  headlines" in result.output
    assert "所有新闻源都连不上" in result.output


def test_F12_01_not_a_vault_writes_nothing(tmp_path: Path) -> None:
    result = _cli("run", "headlines", "--vault", str(tmp_path))
    assert result.exit_code == 1 and "不是一个 vault" in result.output
    assert not (tmp_path / ".eia").exists()


def _radar_result(working: int, total: int) -> RunResult:
    feeds = [
        FeedStatus(Feed(f"https://f{i}", "P"), error=None if i < working else "HTTP 500")
        for i in range(total)
    ]
    written = WriteResult(created=["a.md"], updated=[], unchanged=4)
    return RunResult(feeds, in_window=10, groups=8, qualifying=5, written=written)


def test_F12_01_headlines_job_summary(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("eia.headlines.run.run", lambda root: _radar_result(12, 13))
    record = run_job(vault, "headlines")
    assert record.ok
    assert record.summary == "12/13 个源可用，5 组达到门槛：新建 1，更新 0，未变 4"

    monkeypatch.setattr("eia.headlines.run.run", lambda root: _radar_result(0, 13))
    record = run_job(vault, "headlines")
    assert not record.ok and "所有新闻源都连不上" in str(record.error)


def test_F12_01_headlines_job_records_config_errors(vault: Path) -> None:
    record = run_job(vault, "headlines")  # 新 vault 没有 headlines 配置
    assert not record.ok and "没有 headlines 配置" in str(record.error)


def test_F12_01_python_dash_m_eia_works() -> None:
    out = subprocess.run(
        [sys.executable, "-m", "eia", "run", "--help"], capture_output=True, cwd=ROOT, check=True
    )
    assert b"JOB" in out.stdout


# ---------- F12-02 锁 ----------


def test_F12_02_busy_lock_skips(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[Path] = []
    monkeypatch.setitem(runs.JOBS, "headlines", lambda root: calls.append(root) or "ok")
    lock = vault / ".eia" / "locks" / "headlines.lock"
    lock.parent.mkdir(parents=True)
    lock.write_text("1234", encoding="utf-8")
    record = run_job(vault, "headlines")
    assert not record.ok and record.summary == "上次还在运行，这次跳过"
    assert calls == [] and lock.exists()


def test_F12_02_stale_lock_is_cleared(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(runs.JOBS, "headlines", lambda root: "ok")
    lock = vault / ".eia" / "locks" / "headlines.lock"
    lock.parent.mkdir(parents=True)
    lock.write_text("1234", encoding="utf-8")
    old = time.time() - 3 * 3600
    os.utime(lock, (old, old))
    assert run_job(vault, "headlines").ok
    assert not lock.exists()


# ---------- F12-03 记录条数 ----------


def test_F12_03_keeps_only_recent_runs(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    counter = iter(range(1000))
    monkeypatch.setitem(runs.JOBS, "headlines", lambda root: f"第 {next(counter)} 次")
    for _ in range(KEEP_RUNS + 5):
        run_job(vault, "headlines")
    records = read_runs(vault, "headlines")
    assert len(records) == KEEP_RUNS
    assert records[0].summary == "第 5 次" and records[-1].summary == f"第 {KEEP_RUNS + 4} 次"


# ---------- F12-04 登记 ----------


class FakeSchtasks:
    def __init__(self, query_output: str = "", fail: bool = False) -> None:
        self.calls: list[list[str]] = []
        self.xml: list[str] = []
        self.query_output = query_output
        self.fail = fail

    def __call__(self, args: Sequence[str]) -> subprocess.CompletedProcess[bytes]:
        self.calls.append(list(args))
        if "/XML" in args:
            path = Path(args[list(args).index("/XML") + 1])
            self.xml.append(path.read_bytes().decode("utf-16"))
        out = self.query_output.encode("utf-8") if args[0] == "/Query" else b"SUCCESS"
        code = 1 if self.fail else 0
        return subprocess.CompletedProcess(["schtasks", *args], code, out, b"ERROR: boom")


def test_F12_04_install_writes_the_right_xml(vault: Path) -> None:
    fake = FakeSchtasks()
    scheduler = WindowsScheduler(fake)
    name = scheduler.install("headlines", vault, ["8:00", "18:30"], today=T0.date())
    assert name == task_name("headlines", vault) and name.startswith("\\eia\\headlines-")
    assert fake.calls[0][:3] == ["/Create", "/TN", name] and fake.calls[0][-1] == "/F"
    xml = fake.xml[0]
    for needle in (
        "<StartWhenAvailable>true</StartWhenAvailable>",
        "<RunOnlyIfNetworkAvailable>true</RunOnlyIfNetworkAvailable>",
        "<DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>",
        "<StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>",
        "<ExecutionTimeLimit>PT30M</ExecutionTimeLimit>",
        "<LogonType>InteractiveToken</LogonType>",
        "2026-11-22T08:00:00",
        "2026-11-22T18:30:00",
        f'-m eia run headlines --vault "{vault.resolve()}"',
    ):
        assert needle in xml, needle
    assert "pythonw.exe" in xml or Path(sys.executable).name in xml
    # 再登记一次：同一个名字，/F 覆盖
    scheduler.install("headlines", vault, [], today=T0.date())
    assert fake.calls[1][2] == name and "T08:00:00" in fake.xml[1] and "T18:30" not in fake.xml[1]


@pytest.mark.parametrize("bad", ["25:00", "8点", "08:60"])
def test_F12_04_bad_times(vault: Path, bad: str) -> None:
    with pytest.raises(ScheduleError, match="看不懂"):
        WindowsScheduler(FakeSchtasks()).install("headlines", vault, [bad])


def test_F12_04_install_failure_is_reported(vault: Path) -> None:
    with pytest.raises(ScheduleError, match="登记失败：ERROR: boom"):
        WindowsScheduler(FakeSchtasks(fail=True)).install("headlines", vault, [])


def test_F12_04_cli_install(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeSchtasks()
    monkeypatch.setattr(windows, "supported", lambda: True)
    monkeypatch.setattr("eia.cli.WindowsScheduler", lambda: WindowsScheduler(fake))
    result = _cli("schedule", "install", "headlines", "--vault", str(vault), "--at", "07:30")
    assert result.exit_code == 0, result.output
    assert "每天 07:30" in result.output and "eia run headlines" in result.output
    not_vault = _cli("schedule", "install", "headlines", "--vault", str(vault / "nope"))
    assert not_vault.exit_code == 1 and "不是一个 vault" in not_vault.output


# ---------- F12-05 查看与删除 ----------


def _row(*cells: str) -> str:
    return ",".join('"' + cell.replace('"', '""') + '"' for cell in cells)


def _csv(vault: Path, missing: Path) -> str:
    header = _row(
        "HostName",
        "TaskName",
        "Next Run Time",
        "Status",
        "Logon Mode",
        "Last Run Time",
        "Last Result",
        "Author",
        "Task To Run",
        "Start In",
    )
    command = 'C:\\py\\pythonw.exe -m eia run headlines --vault "{}"'
    # 2026-11-21 在作者电脑上看到的真实样子：schtasks 把命令里的引号弄乱了，只有「起始于」可靠
    mangled = 'C:\\py\\pythonw.exe -m eia run headlines --vault {}""'
    rows = [
        _row(
            "PC",
            task_name("headlines", vault),
            "2026/11/22 8:00:00",
            "Ready",
            "Interactive",
            "N/A",
            "267011",
            "me",
            mangled.format(vault.resolve()),
            str(vault.resolve()),
        ),
        _row(
            "PC",
            task_name("headlines", missing),
            "2026/11/22 8:00:00",
            "Ready",
            "Interactive",
            "2026/11/21 8:00:00",
            "0",
            "me",
            command.format(missing),
            "N/A",
        ),
        _row(
            "PC",
            "\\Microsoft\\Windows\\Other",
            "N/A",
            "Ready",
            "x",
            "N/A",
            "0",
            "MS",
            "x.exe",
            "N/A",
        ),
        header,  # schtasks /V 每个文件夹都会重复表头
    ]
    return "\r\n".join([header, *rows]) + "\r\n"


def test_F12_05_list_status_remove(
    vault: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    missing = tmp_path / "gone"
    fake = FakeSchtasks(_csv(vault, missing))
    scheduler = WindowsScheduler(fake)
    tasks = scheduler.tasks()
    assert len(tasks) == 2 and {t.job for t in tasks} == {"headlines"}
    by_vault = {t.vault: t for t in tasks}
    assert by_vault[str(vault.resolve())].vault_exists
    assert not by_vault[str(missing)].vault_exists
    assert scheduler.find("headlines", vault) is not None

    monkeypatch.setattr(windows, "supported", lambda: True)
    monkeypatch.setattr("eia.cli.WindowsScheduler", lambda: WindowsScheduler(fake))
    listed = _cli("schedule", "list")
    assert "⚠ vault 已经不存在" in listed.output and str(vault.resolve()) in listed.output

    monkeypatch.setitem(runs.JOBS, "headlines", lambda root: "新建 3")
    run_job(vault, "headlines")
    status = _cli("schedule", "status", "--vault", str(vault))
    assert "[headlines] 已登记，下次 2026/11/22 8:00:00" in status.output
    assert "成功" in status.output and "新建 3" in status.output

    removed = _cli("schedule", "remove", "headlines", "--vault", str(vault))
    assert removed.exit_code == 0 and fake.calls[-1][0] == "/Delete"


def test_F12_05_empty_and_failures(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(windows, "supported", lambda: True)
    monkeypatch.setattr("eia.cli.WindowsScheduler", lambda: WindowsScheduler(FakeSchtasks("")))
    assert "还没有登记任何定时任务" in _cli("schedule", "list").output
    status = _cli("schedule", "status", "--vault", str(vault))
    assert "没有登记定时运行" in status.output and "还没有运行记录" in status.output

    broken = FakeSchtasks(fail=True)
    monkeypatch.setattr("eia.cli.WindowsScheduler", lambda: WindowsScheduler(broken))
    assert _cli("schedule", "remove", "headlines", "--vault", str(vault)).exit_code == 1
    assert _cli("schedule", "list").exit_code == 1
    assert "查不到定时任务" in _cli("schedule", "status", "--vault", str(vault)).output


# ---------- F12-06 其他系统 ----------


def test_F12_06_other_systems_get_manual_instructions(
    vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(windows, "supported", lambda: False)
    for args in (
        ["install", "headlines", "--vault", str(vault)],
        ["list"],
        ["remove", "headlines"],
    ):
        result = _cli("schedule", *args)
        assert result.exit_code == 1
        assert "还不支持 eia schedule" in result.stderr and "crontab" in result.stderr
    status = _cli("schedule", "status", "--vault", str(vault))
    assert status.exit_code == 0 and "没有登记定时运行" in status.output
    monkeypatch.setitem(runs.JOBS, "headlines", lambda root: "ok")
    assert _cli("run", "headlines", "--vault", str(vault)).exit_code == 0
