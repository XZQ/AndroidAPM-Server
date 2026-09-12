"""Verify required documentation, local links, and cross-repository ownership rules."""

from __future__ import annotations

import re
import sys
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
REQUIRED_DOCS = (
    "00-总体规划.md",
    "01-总体架构.md",
    "02-SDK-Collector协议.md",
    "03-安全与租户.md",
    "04-OTLP映射与SigNoz.md",
    "05-Dashboard与告警.md",
    "06-符号化与远程配置.md",
    "07-测试与验收.md",
    "08-部署与运维.md",
    "09-实施路线图.md",
    "10-Web控制台与本地联调.md",
    "云端待建设清单.md",
)
MARKDOWN_LINK = re.compile(r"\[[^]]+]\(([^)]+)\)")
IGNORED_MARKDOWN_DIRS = frozenset({".venv", "node_modules"})


def main() -> int:
    """Return non-zero when required files, links, or canonical ownership drift."""
    failures: list[str] = []
    docs_dir = ROOT / "docs"
    for name in REQUIRED_DOCS:
        if not (docs_dir / name).is_file():
            failures.append(f"missing required document: docs/{name}")

    for markdown in ROOT.rglob("*.md"):
        if IGNORED_MARKDOWN_DIRS.intersection(markdown.parts):
            continue
        text = markdown.read_text(encoding="utf-8")
        for raw_target in MARKDOWN_LINK.findall(text):
            target = unquote(raw_target.split("#", maxsplit=1)[0])
            if not target or "://" in target or target.startswith("mailto:"):
                continue
            if not (markdown.parent / target).resolve().exists():
                failures.append(f"broken local link in {markdown.relative_to(ROOT)}: {raw_target}")

    versions = (ROOT / "deploy" / "signoz" / "VERSIONS.env").read_text(encoding="utf-8")
    plan = (docs_dir / "00-总体规划.md").read_text(encoding="utf-8")
    for version in ("v0.133.0", "v0.2.13"):
        if version not in versions or version not in plan:
            failures.append(f"SigNoz version drift: {version}")

    client_cloud_list = ROOT.parent / "AndroidAPM" / "docs" / "云端待建设清单.md"
    if client_cloud_list.exists():
        failures.append("client repository still contains a duplicate cloud backlog")

    client_proto = (
        ROOT.parent
        / "AndroidAPM"
        / "apm-model"
        / "src"
        / "main"
        / "proto"
        / "apm_event.proto"
    )
    server_proto = ROOT / "proto" / "apm_event.proto"
    if client_proto.exists() and _field_signature(client_proto) != _field_signature(server_proto):
        failures.append("server compatibility proto fields differ from the Android client")

    if failures:
        for failure in failures:
            print(f"ERROR: {failure}", file=sys.stderr)
        return 1
    print(f"Documentation verification passed ({len(REQUIRED_DOCS)} required documents).")
    return 0


def _field_signature(path: Path) -> list[tuple[str, str, str]]:
    """Compare only wire-significant field type/name/number across comment differences."""
    pattern = re.compile(r"^\s*((?:map<[^>]+>)|\w+)\s+(\w+)\s*=\s*(\d+)\s*;")
    return [
        (match.group(1), match.group(2), match.group(3))
        for line in path.read_text(encoding="utf-8").splitlines()
        if (match := pattern.match(line))
    ]


if __name__ == "__main__":
    raise SystemExit(main())
