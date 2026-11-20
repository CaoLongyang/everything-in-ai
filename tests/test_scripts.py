"""检查脚本本身的回归测试。"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_acceptance_report_survives_a_console_without_chinese(tmp_path: Path) -> None:
    """2026-11-20 GitHub Actions 的 Windows 机器（cp1252 控制台）上打印中文时崩溃。"""
    (tmp_path / "scripts").mkdir()
    shutil.copy(ROOT / "scripts" / "acceptance_report.py", tmp_path / "scripts")
    shutil.copy(ROOT / "TEST-PLAN.md", tmp_path)
    env = {**os.environ, "PYTHONIOENCODING": "cp1252"}
    result = subprocess.run(
        [sys.executable, "scripts/acceptance_report.py"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
    assert (
        (tmp_path / "reports" / "acceptance.md")
        .read_text(encoding="utf-8")
        .startswith("# 验收报告")
    )
