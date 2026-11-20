"""把测试结果按 TEST-PLAN.md 里的功能编号汇总成 reports/acceptance.md。

测试函数名里带编号（test_F2_04_...），所以每条验收标准都能对上它的测试。
"""

from __future__ import annotations

import datetime as dt
import re
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

ROW_RE = re.compile(r"^\|\s*(F\d+-\d+)\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|\s*$")
SAME_AS_RE = re.compile(r"与 F(\d+)-(\d+) 至 F\d+-(\d+) 相同，编号 F(\d+)-\d+ 至 F\d+-\d+")
HEADING_RE = re.compile(r"^### (F\d+) (.+)$")
TEST_ID_RE = re.compile(r"test_F(\d+)_(\d+)_")

PASSED, FAILED, MISSING = "通过", "失败", "未实现"


@dataclass(frozen=True)
class Criterion:
    id: str
    text: str


def read_plan(plan: Path) -> tuple[dict[str, str], list[Criterion]]:
    features: dict[str, str] = {}
    criteria: dict[str, Criterion] = {}
    for line in plan.read_text(encoding="utf-8").splitlines():
        if heading := HEADING_RE.match(line):
            features[heading.group(1)] = heading.group(2)
        elif row := ROW_RE.match(line):
            criteria[row.group(1)] = Criterion(row.group(1), row.group(2))
        elif same := SAME_AS_RE.search(line):
            source, first, last, target = (int(part) for part in same.groups())
            for number in range(first, last + 1):
                original = criteria.get(f"F{source}-{number:02d}")
                if original is not None:
                    new_id = f"F{target}-{number:02d}"
                    criteria[new_id] = Criterion(new_id, original.text)
    return features, list(criteria.values())


def read_results(junit: Path) -> dict[str, list[bool]]:
    results: dict[str, list[bool]] = defaultdict(list)
    if not junit.is_file():
        return results
    for case in ET.parse(junit).getroot().iter("testcase"):
        match = TEST_ID_RE.search(case.get("name", ""))
        if match is None:
            continue
        failed = any(case.find(tag) is not None for tag in ("failure", "error", "skipped"))
        results[f"F{int(match.group(1))}-{int(match.group(2)):02d}"].append(not failed)
    return results


def status_of(outcomes: list[bool]) -> str:
    if not outcomes:
        return MISSING
    return PASSED if all(outcomes) else FAILED


def build(plan: Path, junit: Path) -> tuple[str, dict[str, int], list[str]]:
    features, criteria = read_plan(plan)
    results = read_results(junit)
    counts = {PASSED: 0, FAILED: 0, MISSING: 0}
    lines = [
        "# 验收报告",
        "",
        f"生成时间：{dt.datetime.now().astimezone().strftime('%Y-%m-%d %H:%M %z')}",
        "本文件由 check 脚本自动生成，请勿手改。验收标准的原文见 TEST-PLAN.md。",
        "",
    ]
    current = ""
    for criterion in criteria:
        feature = criterion.id.split("-")[0]
        if feature != current:
            current = feature
            lines += ["", f"## {feature} {features.get(feature, '')}", ""]
            lines += ["| 编号 | 验收标准 | 测试数 | 结果 |", "|---|---|---|---|"]
        outcomes = results.get(criterion.id, [])
        status = status_of(outcomes)
        counts[status] += 1
        lines.append(f"| {criterion.id} | {criterion.text} | {len(outcomes)} | {status} |")
    known = {criterion.id for criterion in criteria}
    orphans = sorted(set(results) - known)
    total = sum(counts.values())
    summary = (
        f"共 {total} 条验收标准：{counts[PASSED]} 条通过，"
        f"{counts[FAILED]} 条失败，{counts[MISSING]} 条未实现。"
    )
    lines[5:5] = [f"**{summary}**"]
    if orphans:
        lines += ["", "## 没有登记在 TEST-PLAN.md 里的测试编号", "", "、".join(orphans)]
    lines += ["", "手动验收（M-01 等）不在本报告内，见 TEST-PLAN.md。", ""]
    return "\n".join(lines), counts, orphans


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    text, counts, orphans = build(root / "TEST-PLAN.md", root / "reports" / "junit.xml")
    target = root / "reports" / "acceptance.md"
    target.parent.mkdir(exist_ok=True)
    target.write_text(text, encoding="utf-8")
    print(
        f"验收标准：通过 {counts[PASSED]}，失败 {counts[FAILED]}，未实现 {counts[MISSING]}"
        f"（详见 reports/acceptance.md）"
    )
    if orphans:
        print(f"有测试编号没登记在 TEST-PLAN.md：{'、'.join(orphans)}")
    return 1 if counts[FAILED] or orphans else 0


if __name__ == "__main__":
    sys.exit(main())
