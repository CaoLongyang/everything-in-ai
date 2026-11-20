"""一条命令跑完全部检查。最后一行是 ALL PASSED，或者列出失败的项目。

用法：python scripts/check.py        （Windows 上也可以运行 .\\check.ps1）
"""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def tool(name: str) -> list[str] | None:
    """优先用当前 Python 环境里的工具，找不到再用 PATH 里的。"""
    if importlib.util.find_spec(name) is not None:
        return [sys.executable, "-m", name]
    found = shutil.which(name)
    return [found] if found else None


def run(label: str, command: list[str] | None) -> bool:
    print(f"\n=== {label} ===", flush=True)
    if command is None:
        print('没有安装这个工具。请先运行：pip install -e ".[dev]"')
        return False
    return subprocess.run(command, cwd=ROOT).returncode == 0


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(errors="replace")

    ruff, mypy, pytest = tool("ruff"), tool("mypy"), tool("pytest")
    (ROOT / "reports").mkdir(exist_ok=True)
    pytest_args = ["-q", "--junitxml", "reports/junit.xml"]
    has_cov = importlib.util.find_spec("pytest_cov") is not None
    if has_cov:
        pytest_args += ["--cov=eia", "--cov-report=term-missing"]

    steps = [
        ("代码规范 (ruff check)", ruff and [*ruff, "check", "."]),
        ("代码格式 (ruff format)", ruff and [*ruff, "format", "--check", "."]),
        ("类型检查 (mypy)", mypy and [*mypy]),
        ("自动测试 (pytest)", pytest and [*pytest, *pytest_args]),
        ("验收报告", [sys.executable, "scripts/acceptance_report.py"]),
    ]
    failed = [label for label, command in steps if not run(label, command)]

    print()
    if not has_cov:
        print("提示：没有安装 pytest-cov，这次没有统计覆盖率。")
    if failed:
        print("FAILED: " + "；".join(failed))
        return 1
    print("ALL PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
