"""Keep local/private artifacts outside docs and preserve the legacy wire contract."""

import importlib.util
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "verify_docs", Path(__file__).resolve().parents[1] / "scripts/verify_docs.py"
)
assert SPEC is not None and SPEC.loader is not None
VERIFIER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VERIFIER)
compatible_proto_fields = VERIFIER.compatible_proto_fields
markdown_files = VERIFIER.markdown_files


def test_markdown_scan_prunes_private_trees_but_checks_nested_docs(tmp_path: Path) -> None:
    for folder in ("docs/adr", ".git/backup", ".local", ".venv", "web/node_modules"):
        path = tmp_path / folder
        path.mkdir(parents=True)
        (path / "test.md").write_text("[broken](missing.md)", encoding="utf-8")
    assert markdown_files(tmp_path) == [tmp_path / "docs/adr/test.md"]


@pytest.mark.parametrize(
    ("extension", "compatible"),
    [
        ("optional string session_id = 7;", True),
        ("optional string device_model = 8;", True),
        ("optional string os_version = 9;", True),
        ("string session_id = 7;", False),
        ("optional int64 session_id = 7;", False),
        ("optional string session_id = 10;", False),
        ("optional string arbitrary = 7;", False),
    ],
)
def test_only_reviewed_optional_occurrence_extensions_are_compatible(
    tmp_path: Path, extension: str, compatible: bool
) -> None:
    client, server = tmp_path / "client.proto", tmp_path / "server.proto"
    old = "message ApmOccurrenceContext {\n string installation_id = 5;\n}"
    client.write_text(old, encoding="utf-8")
    server.write_text(old.replace("\n}", f"\n {extension}\n}}"), encoding="utf-8")
    assert compatible_proto_fields(client, server) is compatible
    server.write_text(old.replace("= 5", "= 6"), encoding="utf-8")
    assert not compatible_proto_fields(client, server)


def test_reviewed_field_cannot_be_added_to_a_different_message(tmp_path: Path) -> None:
    client, server = tmp_path / "client.proto", tmp_path / "server.proto"
    client.write_text("message ApmResource {\n string installation_id = 4;\n}", encoding="utf-8")
    server.write_text(
        "message ApmResource {\n string installation_id = 4;\n optional string session_id = 7;\n}",
        encoding="utf-8",
    )
    assert not compatible_proto_fields(client, server)
