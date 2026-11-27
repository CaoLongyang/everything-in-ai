"""Windows 任务计划程序（docs/specs/scheduler.md 第 4 节）。

用 XML 登记，因为「错过补跑 / 需要联网 / 用电池也跑 / 超时结束」只有 XML 能设。
任务放在 \\eia\\ 文件夹下，名字带 vault 路径的短哈希，同一台电脑可以给多个 vault 各登记一个。
"""

from __future__ import annotations

import csv
import datetime as dt
import hashlib
import io
import locale
import re
import subprocess
import sys
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from xml.sax.saxutils import escape

FOLDER = "\\eia\\"
TIME_LIMIT = "PT30M"
_TIME = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")
_VAULT_ARG = re.compile(r'--vault\s+"([^"]+)"')

Runner = Callable[[Sequence[str]], subprocess.CompletedProcess[bytes]]


class ScheduleError(Exception):
    """登记 / 查询 / 删除定时任务失败，或者这个系统还不支持。"""


@dataclass(frozen=True)
class ScheduledTask:
    name: str
    job: str
    vault: str | None
    next_run: str
    last_run: str
    last_result: str
    status: str

    @property
    def vault_exists(self) -> bool:
        return self.vault is not None and (Path(self.vault) / "config.yaml").is_file()


def _run_schtasks(args: Sequence[str]) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(["schtasks", *args], capture_output=True, check=False)


def _decode(data: bytes) -> str:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode(locale.getpreferredencoding(False), errors="replace")


def task_name(job: str, vault: Path) -> str:
    digest = hashlib.sha256(str(vault.resolve()).lower().encode("utf-8")).hexdigest()[:8]
    return f"{FOLDER}{job}-{digest}"


def pythonw() -> Path:
    """项目环境里的 pythonw.exe：运行时不弹出命令行窗口。没有就退回 python.exe。"""
    current = Path(sys.executable)
    candidate = current.with_name("pythonw.exe")
    return candidate if candidate.is_file() else current


def parse_times(times: Sequence[str]) -> list[tuple[int, int]]:
    parsed = []
    for value in times or ("08:00",):
        match = _TIME.match(value.strip())
        if match is None:
            raise ScheduleError(f"时间「{value}」看不懂，请写成 08:00 这样的 24 小时制")
        parsed.append((int(match.group(1)), int(match.group(2))))
    return sorted(set(parsed))


def task_xml(
    job: str, vault: Path, times: Sequence[tuple[int, int]], python: Path, today: dt.date
) -> str:
    vault = vault.resolve()
    triggers = "".join(
        f"<CalendarTrigger><StartBoundary>{today.isoformat()}T{h:02d}:{m:02d}:00</StartBoundary>"
        "<Enabled>true</Enabled><ScheduleByDay><DaysInterval>1</DaysInterval></ScheduleByDay>"
        "</CalendarTrigger>"
        for h, m in times
    )
    return _xml(
        triggers=triggers,
        arguments=f'-m eia run {job} --vault "{vault}"',
        description=f"everything-in-ai：{job}（vault：{vault}）。用 eia schedule remove 删除。",
        vault=vault,
        python=python,
        time_limit=TIME_LIMIT,
        network=True,
    )


def logon_task_xml(job: str, vault: Path, python: Path, arguments: str, removal: str) -> str:
    """登录 Windows 时启动、一直运行的任务（eia sync serve）：不限时长、不要求联网。"""
    vault = vault.resolve()
    return _xml(
        triggers="<LogonTrigger><Enabled>true</Enabled></LogonTrigger>",
        arguments=arguments,
        description=f"everything-in-ai：{job}（vault：{vault}）。用 {removal} 删除。",
        vault=vault,
        python=python,
        time_limit="PT0S",
        network=False,
    )


def _xml(
    *,
    triggers: str,
    arguments: str,
    description: str,
    vault: Path,
    python: Path,
    time_limit: str,
    network: bool,
) -> str:
    arguments = escape(arguments)
    description = escape(description)
    network_only = "true" if network else "false"
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>{description}</Description>
  </RegistrationInfo>
  <Triggers>{triggers}</Triggers>
  <Principals>
    <Principal id="Author">
      <LogonType>InteractiveToken</LogonType><RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>{network_only}</RunOnlyIfNetworkAvailable>
    <ExecutionTimeLimit>{time_limit}</ExecutionTimeLimit>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <WakeToRun>false</WakeToRun>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{escape(str(python))}</Command>
      <Arguments>{arguments}</Arguments>
      <WorkingDirectory>{escape(str(vault))}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"""


class WindowsScheduler:
    def __init__(self, runner: Runner = _run_schtasks) -> None:
        self._run = runner

    def install(
        self, job: str, vault: Path, times: Sequence[str], *, today: dt.date | None = None
    ) -> str:
        name = task_name(job, vault)
        xml = task_xml(job, vault, parse_times(times), pythonw(), today or dt.date.today())
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "task.xml"
            path.write_bytes(xml.encode("utf-16"))  # schtasks 要求 UTF-16
            result = self._run(["/Create", "/TN", name, "/XML", str(path), "/F"])
        if result.returncode != 0:
            raise ScheduleError(f"登记失败：{_decode(result.stderr or result.stdout).strip()}")
        return name

    def install_logon(self, job: str, vault: Path, arguments: str, removal: str) -> str:
        """登记登录时启动的任务，并马上启动一次（不用等下次登录）。"""
        name = task_name(job, vault)
        xml = logon_task_xml(job, vault, pythonw(), arguments, removal)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "task.xml"
            path.write_bytes(xml.encode("utf-16"))
            result = self._run(["/Create", "/TN", name, "/XML", str(path), "/F"])
        if result.returncode != 0:
            raise ScheduleError(f"登记失败：{_decode(result.stderr or result.stdout).strip()}")
        started = self._run(["/Run", "/TN", name])
        if started.returncode != 0:
            raise ScheduleError(f"登记好了，但启动失败：{_decode(started.stderr).strip()}")
        return name

    def stop(self, job: str, vault: Path) -> None:
        """结束正在运行的任务（删除任务不会结束已经在跑的程序）。没在跑也不算错。"""
        self._run(["/End", "/TN", task_name(job, vault)])

    def remove(self, job: str, vault: Path) -> str:
        name = task_name(job, vault)
        result = self._run(["/Delete", "/TN", name, "/F"])
        if result.returncode != 0:
            raise ScheduleError(f"没有找到这个 vault 的 {job} 定时任务")
        return name

    def tasks(self) -> list[ScheduledTask]:
        result = self._run(["/Query", "/FO", "CSV", "/V"])
        if result.returncode != 0:
            raise ScheduleError(f"查询失败：{_decode(result.stderr or result.stdout).strip()}")
        tasks: dict[str, ScheduledTask] = {}
        for row in csv.reader(io.StringIO(_decode(result.stdout))):
            # 列的位置固定（表头随系统语言变）：
            # 1 任务名 2 下次运行 3 状态 5 上次运行 6 上次结果 8 命令 9 起始于
            if len(row) < 9 or not row[1].startswith(FOLDER):
                continue
            name = row[1]
            job = name.removeprefix(FOLDER).rsplit("-", 1)[0]
            # 9 是「起始于」，登记时设成了 vault 路径。
            # 命令那一列里的引号会被 schtasks 弄乱，只作后备（ADR 0005）
            start_in = row[9].strip() if len(row) > 9 else ""
            match = _VAULT_ARG.search(row[8])
            vault = (
                start_in if start_in and start_in != "N/A" else match.group(1) if match else None
            )
            tasks.setdefault(
                name,
                ScheduledTask(
                    name=name,
                    job=job,
                    vault=vault,
                    next_run=row[2],
                    last_run=row[5],
                    last_result=row[6],
                    status=row[3],
                ),
            )
        return sorted(tasks.values(), key=lambda task: task.name)

    def find(self, job: str, vault: Path) -> ScheduledTask | None:
        name = task_name(job, vault)
        return next((task for task in self.tasks() if task.name == name), None)


MANUAL_HINT = """\
这个系统还不支持 eia schedule（V1 只做了 Windows）。可以自己用系统的定时工具每天运行：
  {python} -m eia run {job} --vault "{vault}"
例如 Linux / macOS 的 crontab -e 里加一行（每天 8:00）：
  0 8 * * * {python} -m eia run {job} --vault "{vault}"
"""


def supported() -> bool:
    return sys.platform == "win32"


def manual_hint(job: str, vault: Path) -> str:
    return MANUAL_HINT.format(python=sys.executable, job=job, vault=vault.resolve())
