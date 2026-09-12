from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from androidapm_server.artifacts import inspect_native_elf
from androidapm_server.constants import ARTIFACT_TYPE_NATIVE_ELF, SYMBOL_JOB_NATIVE
from androidapm_server.db.models import InboxEvent, SymbolArtifact, SymbolizationJob
from androidapm_server.domain import NativeFrameIdentity
from androidapm_server.symbolization import (
    NativeArtifacts,
    SymbolizationFailure,
    SymbolizationResult,
    symbolize_job,
)


def native_job(tmp_path: Path) -> tuple[SymbolizationJob, NativeArtifacts]:
    artifacts: NativeArtifacts = {}
    frames = []
    for index, name in enumerate(("first", "second"), 1):
        build_id = str(index) * 40
        path = tmp_path / f"lib{name}.so"
        path.write_bytes(b"synthetic test artifact")
        artifact = SymbolArtifact(
            id=index,
            tenant_id="tenant-a",
            app_id="com.example",
            version_code="42",
            app_build="build-1",
            variant="release",
            artifact_type=ARTIFACT_TYPE_NATIVE_ELF,
            abi="arm64-v8a",
            build_id=build_id,
            checksum_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        )
        artifacts[(artifact.abi, build_id)] = artifact, path
        frames.append(
            NativeFrameIdentity(
                abi=artifact.abi,
                module_build_id=build_id,
                module_name=path.name,
                module_relative_pc=index * 16,
            )
        )
    inbox = InboxEvent(
        tenant_id="tenant-a",
        app_id="com.example",
        version_code="42",
        app_build="build-1",
        variant="release",
        release_identity_quality="OCCURRENCE_BOUND",
        native_identity_json=[frame.model_dump() for frame in [*frames, frames[0]]],
    )
    job = SymbolizationJob(inbox_event=inbox, job_type=SYMBOL_JOB_NATIVE)
    refresh_stack(job)
    return job, artifacts


def refresh_stack(job: SymbolizationJob) -> None:
    stack = "\n".join(
        f"#{index:02d} pc {frame['module_relative_pc']:016x} /app/{frame['module_name']} "
        f"(BuildId: {frame['module_build_id']})"
        for index, frame in enumerate(job.inbox_event.native_identity_json)
    )
    job.inbox_event.payload_json = {"fields": {"backtrace": stack}}
    job.input_sha256 = hashlib.sha256(stack.encode()).hexdigest()


async def run_native(
    job: SymbolizationJob, artifacts: NativeArtifacts, command: str = '["llvm-symbolizer"]'
) -> SymbolizationResult:
    artifact, path = next(iter(artifacts.values()))
    return await symbolize_job(job, artifact, path, "[]", "unused", command, "test", 20, artifacts)


async def test_modules_use_exact_elf_and_preserve_interleaved_frame_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    job, artifacts = native_job(tmp_path)
    tool = AsyncMock(
        side_effect=["first\nfirst.c:1:2\n\nfirst\nfirst.c:1:2", "second\nsecond.c:2:3"]
    )
    monkeypatch.setattr("androidapm_server.symbolization._run_tool", tool)
    result = await run_native(job, artifacts)
    assert result.status == "symbolized"
    assert result.result_json["resolvedFrames"] == result.result_json["totalFrames"] == 3
    frames = result.result_json["nativeFrames"]
    assert [frame["function"] for frame in frames] == ["first", "second", "first"]
    assert [frame["artifactId"] for frame in frames] == [1, 2, 1]
    for call, (_, path), pcs in zip(
        tool.call_args_list, artifacts.values(), [b"0x10\n0x10\n", b"0x20\n"], strict=True
    ):
        assert f"--obj={path}" in call.args[0]
        assert "--inlining=false" in call.args[0]
        assert call.args[1] == pcs


@pytest.mark.parametrize(
    ("field", "value"),
    [("module_relative_pc", 33), ("module_name", "wrong.so"), ("module_build_id", "a" * 40)],
)
async def test_raw_and_typed_identity_mismatch_never_runs_tool(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, field: str, value: object
) -> None:
    job, artifacts = native_job(tmp_path)
    job.inbox_event.native_identity_json[1][field] = value
    tool = AsyncMock()
    monkeypatch.setattr("androidapm_server.symbolization._run_tool", tool)
    with pytest.raises(SymbolizationFailure) as error:
        await run_native(job, artifacts)
    assert error.value.code == "native_identity_mismatch"
    tool.assert_not_awaited()


@pytest.mark.parametrize("tenant", ["tenant-b", None])
async def test_missing_or_cross_tenant_module_is_never_resolved_with_first_elf(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tenant: str | None
) -> None:
    job, artifacts = native_job(tmp_path)
    second = ("arm64-v8a", "2" * 40)
    if tenant is None:
        del artifacts[second]
    else:
        artifacts[second][0].tenant_id = tenant
    tool = AsyncMock(return_value="first\nfirst.c:1:2\n\nfirst\nfirst.c:1:2")
    monkeypatch.setattr("androidapm_server.symbolization._run_tool", tool)
    with pytest.raises(SymbolizationFailure) as error:
        await run_native(job, artifacts)
    assert error.value.code == (
        "native_artifact_missing" if tenant is None else "native_artifact_mismatch"
    )
    assert tool.await_count == 1


@pytest.mark.parametrize(
    ("output", "state"),
    [
        ("??\n??:0:0", "UNRESOLVED"),
        ("second\n??:0:0", "FUNCTION_ONLY"),
        ("second\nsecond.c:0:5", "FUNCTION_ONLY"),
    ],
)
async def test_incomplete_output_has_explicit_partial_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, output: str, state: str
) -> None:
    job, artifacts = native_job(tmp_path)
    tool = AsyncMock(side_effect=["first\nfirst.c:1:2\n\nfirst\nfirst.c:1:2", output])
    monkeypatch.setattr("androidapm_server.symbolization._run_tool", tool)
    result = await run_native(job, artifacts)
    assert result.status == "partially_symbolized"
    assert result.result_json["nativeFrames"][1]["state"] == state
    assert result.result_json["resolvedFrames"] == (2 if state == "UNRESOLVED" else 3)


@pytest.mark.parametrize(
    ("outputs", "code"),
    [
        (["??\n??:0:0\n\n??\n??:0:0", "??\n??:0:0"], "native_symbols_unresolved"),
        (["first\nfirst.c:1:2"], "invalid_tool_output"),
        (["first\n\nfirst"], "invalid_tool_output"),
    ],
)
async def test_unresolved_or_malformed_output_is_not_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, outputs: list[str], code: str
) -> None:
    job, artifacts = native_job(tmp_path)
    monkeypatch.setattr("androidapm_server.symbolization._run_tool", AsyncMock(side_effect=outputs))
    with pytest.raises(SymbolizationFailure) as error:
        await run_native(job, artifacts)
    assert error.value.code == code


async def test_real_ndk_resolves_two_distinct_android_elf_modules(tmp_path: Path) -> None:
    ndk = os.environ.get("APM_TEST_NDK_BIN")
    if not ndk:
        pytest.skip("APM_TEST_NDK_BIN is not configured")
    binary_suffix = ".exe" if os.name == "nt" else ""
    bin_dir = Path(ndk)
    job, synthetic = native_job(tmp_path)
    artifacts: NativeArtifacts = {}
    for artifact, path in synthetic.values():
        function = "first" if artifact.id == 1 else "second"
        source = tmp_path / f"{function}.c"
        source.write_text(
            f"int {function}(int x) {{ return x + {artifact.id}; }}\n", encoding="utf-8"
        )
        process = await asyncio.create_subprocess_exec(
            str(bin_dir / f"clang{binary_suffix}"),
            "--target=aarch64-linux-android24",
            "-g",
            "-O0",
            "-fPIC",
            "-shared",
            "-nostdlib",
            "-fuse-ld=lld",
            "-Wl,--build-id=sha1",
            "-o",
            str(path),
            str(source),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await asyncio.wait_for(process.communicate(), 30)
        assert process.returncode == 0, stderr.decode()
        identity = await inspect_native_elf(path)
        process = await asyncio.create_subprocess_exec(
            str(bin_dir / f"llvm-nm{binary_suffix}"),
            "--defined-only",
            "--format=posix",
            str(path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(process.communicate(), 10)
        assert process.returncode == 0, stderr.decode()
        symbol = next(
            line.split() for line in stdout.decode().splitlines() if line.startswith(function + " ")
        )
        for frame in job.inbox_event.native_identity_json:
            if frame["module_name"] == path.name:
                frame["module_build_id"] = identity.build_id
                frame["module_relative_pc"] = int(symbol[2], 16)
        artifact.build_id = identity.build_id
        artifact.checksum_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
        artifacts[(identity.abi, identity.build_id)] = artifact, path
    refresh_stack(job)
    result = await run_native(
        job, artifacts, json.dumps([str(bin_dir / f"llvm-symbolizer{binary_suffix}")])
    )
    assert result.status == "symbolized"
    assert [frame["function"] for frame in result.result_json["nativeFrames"]] == [
        "first",
        "second",
        "first",
    ]
    assert all(frame["state"] == "RESOLVED" for frame in result.result_json["nativeFrames"])
    assert result.result_json["resolvedFrames"] == 3
