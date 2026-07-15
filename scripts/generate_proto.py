"""Generate the AndroidAPM compatibility Protobuf module."""

from __future__ import annotations

from pathlib import Path

from grpc_tools import protoc


def main() -> int:
    """Compile the checked-in schema into the package generated directory."""
    root = Path(__file__).resolve().parents[1]
    proto_dir = root / "proto"
    output_dir = root / "src" / "androidapm_server" / "generated"
    output_dir.mkdir(parents=True, exist_ok=True)
    result = protoc.main(
        [
            "grpc_tools.protoc",
            f"-I{proto_dir}",
            f"--python_out={output_dir}",
            f"--pyi_out={output_dir}",
            str(proto_dir / "apm_event.proto"),
        ]
    )
    if result != 0:
        raise SystemExit(result)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
