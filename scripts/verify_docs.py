"""Verify required documentation, local links, and cross-repository ownership rules."""

from __future__ import annotations

import os
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
    "11-本地产品功能.md",
    "12-统计契约与口径.md",
    "云端待建设清单.md",
)
MARKDOWN_LINK = re.compile(r"\[[^]]+]\(([^)]+)\)")
IGNORED_MARKDOWN_DIRS = frozenset({".git", ".local", ".venv", "node_modules"})
# Additive server-side occurrence fields are intentionally absent from older SDKs.
# No existing field, scalar type, number or repetition may change.
OPTIONAL_OCCURRENCE_EXTENSIONS = frozenset(
    {
        ("ApmOccurrenceContext", "optional string", "session_id", "7"),
        ("ApmOccurrenceContext", "optional string", "device_model", "8"),
        ("ApmOccurrenceContext", "optional string", "os_version", "9"),
    }
)


def main() -> int:
    """Return non-zero when required files, links, or canonical ownership drift."""
    failures: list[str] = []
    docs_dir = ROOT / "docs"
    for name in REQUIRED_DOCS:
        if not (docs_dir / name).is_file():
            failures.append(f"missing required document: docs/{name}")

    for markdown in markdown_files(ROOT):
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
        ROOT.parent / "AndroidAPM" / "apm-model" / "src" / "main" / "proto" / "apm_event.proto"
    )
    server_proto = ROOT / "proto" / "apm_event.proto"
    if client_proto.exists() and not compatible_proto_fields(client_proto, server_proto):
        failures.append("server compatibility proto fields differ from the Android client")

    if failures:
        for failure in failures:
            print(f"ERROR: {failure}", file=sys.stderr)
        return 1
    print(f"Documentation verification passed ({len(REQUIRED_DOCS)} required documents).")
    return 0


def markdown_files(root: Path) -> list[Path]:
    """Prune private Git/runtime/dependency trees before inspecting repository documentation."""
    result: list[Path] = []
    for directory, children, files in os.walk(root):
        children[:] = [name for name in children if name not in IGNORED_MARKDOWN_DIRS]
        result.extend(Path(directory) / name for name in files if name.endswith(".md"))
    return result


def compatible_proto_fields(client: Path, server: Path) -> bool:
    """Accept only the reviewed optional occurrence extension over the legacy wire contract."""
    client_fields = _field_signature(client)
    server_fields = _field_signature(server)
    # Preserve the complete legacy signature/order; only known absent optional fields differ.
    return client_fields == [
        field for field in server_fields
        if field in client_fields or field not in OPTIONAL_OCCURRENCE_EXTENSIONS
    ]


def _field_signature(path: Path) -> list[tuple[str, str, str, str]]:
    """Compare message/type/name/number for the project's flat compatibility schemas."""
    pattern = re.compile(
        r"^\s*((?:(?:optional|repeated)\s+)?(?:map<[^>]+>|\w+))\s+(\w+)\s*=\s*(\d+)\s*;"
    )
    fields = []
    message_name = ""
    for line in path.read_text(encoding="utf-8").splitlines():
        if message := re.match(r"^\s*message\s+(\w+)\s*\{", line):
            message_name = message.group(1)
        if match := pattern.match(line):
            fields.append((message_name, match.group(1), match.group(2), match.group(3)))
    return fields


if __name__ == "__main__":
    raise SystemExit(main())
