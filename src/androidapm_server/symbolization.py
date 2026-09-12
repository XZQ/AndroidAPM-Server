"""Sandbox-conscious adapters for fixed R8 retrace and llvm-symbolizer commands."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from pydantic import ValidationError

from androidapm_server.artifacts import SUPPORTED_ABI_BY_MACHINE
from androidapm_server.constants import (
    SYMBOL_JOB_JAVA,
    SYMBOL_JOB_NATIVE,
    SYMBOL_STATUS_PARTIALLY_SYMBOLIZED,
    SYMBOL_STATUS_SYMBOLIZED,
)
from androidapm_server.db.models import SymbolArtifact, SymbolizationJob
from androidapm_server.domain import NativeFrameIdentity

NATIVE_FRAME_PATTERN = re.compile(r"(?m)^\s*#\d+\s+pc\s+([0-9a-fA-F]+)\s+(\S+)([^\n]*)$")
NATIVE_BUILD_ID_PATTERN = re.compile(r"^[0-9a-f]{8,128}$")
NATIVE_INLINE_BUILD_ID = re.compile(r"\(BuildId:\s*([0-9a-fA-F]+)\)")
MAX_NATIVE_FRAMES = 256
MAX_TOOL_OUTPUT_BYTES = 1 * 1_024 * 1_024
MAX_FINGERPRINT_LINES = 32
NativeArtifacts = dict[tuple[str, str], tuple[SymbolArtifact, Path]]


@dataclass(frozen=True, slots=True)
class SymbolizationResult:
    """Bounded deterministic output retained with tool and artifact provenance."""

    result_json: dict[str, Any]
    fingerprint_sha256: str
    tool_name: str
    tool_version: str
    status: str = SYMBOL_STATUS_SYMBOLIZED


class SymbolizationFailure(Exception):
    """Classified tool or input failure consumed by the durable job state machine."""

    def __init__(self, code: str, message: str, retryable: bool) -> None:
        """Capture a safe bounded reason without raw artifact content."""
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


async def symbolize_job(
    job: SymbolizationJob,
    artifact: SymbolArtifact,
    artifact_path: Path,
    retrace_command_json: str,
    retrace_tool_version: str,
    llvm_command_json: str,
    llvm_tool_version: str,
    timeout_seconds: float,
    native_artifacts: NativeArtifacts | None = None,
) -> SymbolizationResult:
    """Run the configured tool with exact artifact identity and no shell interpretation."""
    if not await asyncio.to_thread(artifact_path.is_file):
        raise SymbolizationFailure(
            "artifact_blob_missing", "The registered symbol artifact file is missing", True
        )
    if job.job_type == SYMBOL_JOB_JAVA:
        stack = _stack_from_payload(job, ("stackTrace", "stack_trace"))
        command = _parse_command(retrace_command_json, "retrace")
        output = await _run_tool([*command, str(artifact_path)], stack.encode(), timeout_seconds)
        return _result(output, Path(command[0]).name, retrace_tool_version)
    if job.job_type == SYMBOL_JOB_NATIVE:
        return await _symbolize_native(
            job,
            native_artifacts
            if native_artifacts is not None
            else {(artifact.abi, artifact.build_id): (artifact, artifact_path)},
            llvm_command_json,
            llvm_tool_version,
            timeout_seconds,
        )
    raise SymbolizationFailure(
        "unsupported_job_type", "The symbolization type is unsupported", False
    )


def native_frames_for_job(job: SymbolizationJob) -> tuple[NativeFrameIdentity, ...]:
    """Require a complete one-to-one match of typed occurrence frames and raw backtrace."""
    stack = _stack_from_payload(job, ("backtrace",))
    matches = NATIVE_FRAME_PATTERN.findall(stack)
    if not matches:
        raise SymbolizationFailure(
            "invalid_native_stack", "The Native backtrace contains no module-relative pc", False
        )
    raw = job.inbox_event.native_identity_json
    if (
        not raw
        or len(raw) > MAX_NATIVE_FRAMES
        or len(raw) != len(matches)
        or len(matches) != sum(line.lstrip().startswith("#") for line in stack.splitlines())
    ):
        raise SymbolizationFailure(
            "native_identity_mismatch",
            "Every Native frame requires matching occurrence identity",
            False,
        )
    try:
        frames = tuple(NativeFrameIdentity.model_validate(value) for value in raw)
    except ValidationError as error:
        raise SymbolizationFailure(
            "invalid_native_identity", "The Native frame identity is invalid", False
        ) from error
    for frame, (pc, module, suffix) in zip(frames, matches, strict=True):
        build_id = frame.module_build_id.lower()
        inline = NATIVE_INLINE_BUILD_ID.search(suffix)
        if (
            frame.abi not in SUPPORTED_ABI_BY_MACHINE.values()
            or not NATIVE_BUILD_ID_PATTERN.fullmatch(build_id)
            or frame.module_relative_pc > 2**64 - 1
            or int(pc, 16) != frame.module_relative_pc
            or PurePosixPath(module.replace("\\", "/")).name != frame.module_name
            or (inline is not None and inline.group(1).lower() != build_id)
        ):
            raise SymbolizationFailure(
                "native_identity_mismatch",
                "Native module, build ID or pc does not match occurrence identity",
                False,
            )
    return frames


async def _symbolize_native(
    job: SymbolizationJob,
    artifacts: NativeArtifacts,
    command_json: str,
    version: str,
    timeout_seconds: float,
) -> SymbolizationResult:
    """Resolve each module with its exact ELF, preserve frame order and resolution coverage."""
    frames = native_frames_for_job(job)
    groups: dict[tuple[str, str], list[tuple[int, NativeFrameIdentity]]] = {}
    for index, frame in enumerate(frames):
        groups.setdefault((frame.abi, frame.module_build_id.lower()), []).append((index, frame))
    command = _parse_command(command_json, "llvm-symbolizer")
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    results: dict[int, dict[str, Any]] = {}
    output_bytes = 0
    for identity, group in groups.items():
        matched = artifacts.get(identity)
        if matched is None:
            raise SymbolizationFailure(
                "native_artifact_missing", "An exact Native module artifact is missing", True
            )
        artifact, path = matched
        inbox = job.inbox_event
        if (
            artifact.tenant_id,
            artifact.app_id,
            artifact.version_code,
            artifact.app_build,
            artifact.variant,
            artifact.abi,
            artifact.build_id.lower(),
        ) != (
            inbox.tenant_id,
            inbox.app_id,
            inbox.version_code,
            inbox.app_build,
            inbox.variant,
            *identity,
        ):
            raise SymbolizationFailure(
                "native_artifact_mismatch",
                "The Native artifact does not match the occurrence scope and build",
                False,
            )
        if not await asyncio.to_thread(path.is_file):
            raise SymbolizationFailure(
                "artifact_blob_missing", "A registered Native artifact file is missing", True
            )
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            raise SymbolizationFailure(
                "tool_timeout", "The symbolization tool exceeded its time limit", True
            )
        output = await _run_tool(
            [
                *command,
                f"--obj={path}",
                "--functions=linkage",
                "--demangle",
                "--inlining=false",
                "--output-style=LLVM",
            ],
            ("\n".join(f"0x{frame.module_relative_pc:x}" for _, frame in group) + "\n").encode(),
            remaining,
        )
        output_bytes += len(output.encode())
        if output_bytes > MAX_TOOL_OUTPUT_BYTES:
            raise SymbolizationFailure(
                "tool_output_too_large", "Combined Native output exceeded its limit", False
            )
        blocks = re.split(r"\n\s*\n", output.strip())
        if len(blocks) != len(group):
            raise SymbolizationFailure(
                "invalid_tool_output",
                "Native output does not match the requested frame count",
                False,
            )
        for (index, frame), block in zip(group, blocks, strict=True):
            lines = block.splitlines()
            if len(lines) != 2:
                raise SymbolizationFailure(
                    "invalid_tool_output",
                    "Native output must contain one function and location per frame",
                    False,
                )
            function, location = (line.strip() for line in lines)
            source = re.fullmatch(r"(.+):(\d+):(\d+)", location)
            state = (
                "UNRESOLVED"
                if not function or function == "??"
                else "FUNCTION_ONLY"
                if source is None or source.group(1) == "??" or int(source.group(2)) == 0
                else "RESOLVED"
            )
            results[index] = {
                "index": index,
                "module": frame.module_name,
                "abi": frame.abi,
                "buildId": frame.module_build_id.lower(),
                "pc": frame.module_relative_pc,
                "artifactId": artifact.id,
                "artifactSha256": artifact.checksum_sha256,
                "function": function,
                "location": location,
                "state": state,
            }
    ordered = [results[index] for index in range(len(frames))]
    resolved = sum(frame["state"] != "UNRESOLVED" for frame in ordered)
    if not resolved:
        raise SymbolizationFailure(
            "native_symbols_unresolved", "No Native frame could be resolved", False
        )
    full = all(frame["state"] == "RESOLVED" for frame in ordered)
    rendered = "\n\n".join(
        f"#{frame['index']:02d} {frame['module']}\n{frame['function']}\n{frame['location']}"
        for frame in ordered
    )
    base = _result(rendered, Path(command[0]).name, version)
    return SymbolizationResult(
        {
            **base.result_json,
            "nativeFrames": ordered,
            "resolvedFrames": resolved,
            "totalFrames": len(frames),
        },
        base.fingerprint_sha256,
        base.tool_name,
        base.tool_version,
        SYMBOL_STATUS_SYMBOLIZED if full else SYMBOL_STATUS_PARTIALLY_SYMBOLIZED,
    )


def _stack_from_payload(job: SymbolizationJob, keys: tuple[str, ...]) -> str:
    """Extract the previously hashed raw stack from the immutable inbox event."""
    fields = job.inbox_event.payload_json.get("fields")
    if not isinstance(fields, dict):
        raise SymbolizationFailure("missing_stack", "The crash fields are missing", False)
    for key in keys:
        value = fields.get(key)
        if isinstance(value, str) and value:
            if hashlib.sha256(value.encode()).hexdigest() != job.input_sha256:
                raise SymbolizationFailure(
                    "input_hash_mismatch",
                    "The crash stack no longer matches its durable hash",
                    False,
                )
            return value
    raise SymbolizationFailure("missing_stack", "The crash stack is missing", False)


def _parse_command(raw: str, label: str) -> list[str]:
    """Parse a fixed JSON argv array; shell strings and blank arguments are rejected."""
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as error:
        raise SymbolizationFailure(
            "invalid_tool_config", f"The {label} command JSON is invalid", False
        ) from error
    if (
        not isinstance(value, list)
        or not value
        or not all(isinstance(item, str) and item for item in value)
    ):
        raise SymbolizationFailure(
            "invalid_tool_config", f"The {label} command must be a non-empty argv array", False
        )
    return value


async def _run_tool(args: list[str], standard_input: bytes, timeout_seconds: float) -> str:
    """Execute a trusted fixed binary directly and bound runtime and retained output."""
    try:
        process = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as error:
        raise SymbolizationFailure(
            "tool_unavailable", "The configured symbolization executable was not found", True
        ) from error
    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(standard_input), timeout=timeout_seconds
        )
    except (TimeoutError, asyncio.CancelledError) as error:
        # Lease deadlines and shutdown cancellation must not leave a tool running.
        if process.returncode is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass  # The process may have exited between the returncode check and kill.
        await process.communicate()
        if isinstance(error, asyncio.CancelledError):
            raise
        raise SymbolizationFailure(
            "tool_timeout", "The symbolization tool exceeded its time limit", True
        ) from error
    if len(stdout) > MAX_TOOL_OUTPUT_BYTES or len(stderr) > MAX_TOOL_OUTPUT_BYTES:
        raise SymbolizationFailure(
            "tool_output_too_large", "The symbolization tool output exceeded its limit", False
        )
    if process.returncode != 0:
        message = stderr.decode("utf-8", errors="replace").strip()[:512]
        raise SymbolizationFailure(
            "tool_rejected_input", message or "The symbolization tool rejected the input", False
        )
    try:
        output = stdout.decode("utf-8", errors="strict").strip()
    except UnicodeDecodeError as error:
        raise SymbolizationFailure(
            "invalid_tool_output", "The symbolization tool output is not UTF-8", False
        ) from error
    if not output:
        raise SymbolizationFailure(
            "empty_tool_output", "The symbolization tool returned no frames", False
        )
    return output


def _result(output: str, tool_name: str, tool_version: str) -> SymbolizationResult:
    """Build a stable fingerprint from bounded non-empty symbolized lines."""
    normalized = "\n".join(line.strip() for line in output.splitlines() if line.strip())
    fingerprint_input = "\n".join(normalized.splitlines()[:MAX_FINGERPRINT_LINES])
    return SymbolizationResult(
        {"symbolizedStack": normalized},
        hashlib.sha256(fingerprint_input.encode()).hexdigest(),
        tool_name,
        tool_version,
    )
