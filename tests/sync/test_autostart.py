"""eia sync autostart：登录 Windows 时在后台启动接收服务。schtasks 用假的，绝不真的登记。"""

from __future__ import annotations

from pathlib import Path

from eia.schedule.windows import WindowsScheduler, task_name
from tests.schedule.test_schedule import FakeSchtasks


def test_F19_10_logon_task_runs_serve_without_time_limit(vault: Path) -> None:
    fake = FakeSchtasks()
    scheduler = WindowsScheduler(fake)
    arguments = f'-m eia sync serve --vault "{vault.resolve()}" --port 27771'
    name = scheduler.install_logon("sync", vault, arguments, "eia sync autostart --remove")
    assert name == task_name("sync", vault) and name.startswith("\\eia\\sync-")
    xml = fake.xml[0]
    for needle in (
        "<LogonTrigger><Enabled>true</Enabled><UserId>",
        "<ExecutionTimeLimit>PT0S</ExecutionTimeLimit>",
        "<RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>",
        "<LogonType>InteractiveToken</LogonType>",
        arguments,
        "eia sync autostart --remove",
    ):
        assert needle in xml, needle
    assert "CalendarTrigger" not in xml
    assert fake.calls[1] == ["/Run", "/TN", name]  # 马上启动一次


def test_F19_10_remove_stops_running_server_first(vault: Path) -> None:
    fake = FakeSchtasks()
    scheduler = WindowsScheduler(fake)
    scheduler.stop("sync", vault)
    scheduler.remove("sync", vault)
    name = task_name("sync", vault)
    assert fake.calls == [["/End", "/TN", name], ["/Delete", "/TN", name, "/F"]]
