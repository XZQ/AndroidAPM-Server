"""Guard the checked-in generated Protobuf module against schema drift."""

from __future__ import annotations

from pathlib import Path

from grpc_tools import protoc

ROOT = Path(__file__).resolve().parents[1]


def test_generated_proto_matches_checked_in_schema(tmp_path: Path) -> None:
    """Regenerating from proto/apm_event.proto must reproduce the committed module.

    The pinned grpcio-tools/protobuf versions in uv.lock make this deterministic;
    a schema edit without regeneration (or vice versa) fails here instead of
    silently drifting the wire contract.
    """
    proto_dir = ROOT / "proto"
    generated = ROOT / "src" / "androidapm_server" / "generated"
    result = protoc.main(
        [
            "grpc_tools.protoc",
            f"-I{proto_dir}",
            f"--python_out={tmp_path}",
            f"--pyi_out={tmp_path}",
            str(proto_dir / "apm_event.proto"),
        ]
    )
    assert result == 0
    for name in ("apm_event_pb2.py", "apm_event_pb2.pyi"):
        assert (tmp_path / name).read_bytes() == (generated / name).read_bytes(), name
