"""Sandbox-conscious adapters for fixed R8 retrace and llvm-symbolizer commands."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

from androidapm_server.constants import SYMBOL_JOB_JAVA, SYMBOL_JOB_NATIVE
from androidapm_server.db.models import SymbolArtifact, SymbolizationJob

NATIVE_PC_PATTERN = re.compile(r"(?m)^\s*#\d+\s+pc\s+([0-9a-fA-F]+)\b")
MAX_TOOL_OUTPUT_BYTES = 1 * 1_024 * 1_024
MAX_FINGERPRINT_LINES = 32


@dataclass(frozen=True, slots=True)
class SymbolizationResult:
    """Bounded deterministic output retained with tool and artifact provenance."""

    result_json: dict[str, str]
    fingerprint_sha256: str
    tool_name: str
    tool_version: str


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
) -> SymbolizationResult:
    """Run the configured tool with exact artifact identity and no shell interpretation."""
    if not await asyncio.to_thread(artifact_path.is_file):
        raise SymbolizationFailure(
            "artifact_blob_missing", "The registered symbol artifact file is missing", True
        )
    if job.job_type == SYMBOL_JOB_JAVA:
        stack = _stack_from_payload(job, ("stackTrace", "stack_trace"))
        command = _parse_command(retrace_command_json, "retrace")
        output = await _run_tool(
            [*command, str(artifact_path)], stack.encode(), timeout_seconds
        )
        return _result(output, Path(command[0]).name, retrace_tool_version)
    if job.job_type == SYMBOL_JOB_NATIVE:
        stack = _stack_from_payload(job, ("backtrace",))
        addresses = NATIVE_PC_PATTERN.findall(stack)
        if not addresses:
            raise SymbolizationFailure(
                "invalid_native_stack", "The Native backtrace contains no module-relative pc", False
            )
        command = _parse_command(llvm_command_json, "llvm-symbolizer")
        output = await _run_tool(
            [*command, f"--obj={artifact_path}", "--functions=linkage", "--demangle"],
            ("\n".join(f"0x{address}" for address in addresses) + "\n").encode(),
            timeout_seconds,
        )
        return _result(output, Path(command[0]).name, llvm_tool_version)
    raise SymbolizationFailure(
        "unsupported_job_type", "The symbolization type is unsupported", False
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
    except TimeoutError as error:
        process.kill()
        await process.communicate()
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
    normalized = "\n".join(
        line.strip() for line in output.splitlines() if line.strip()
    )
    fingerprint_input = "\n".join(normalized.splitlines()[:MAX_FINGERPRINT_LINES])
    return SymbolizationResult(
        {"symbolizedStack": normalized},
        hashlib.sha256(fingerprint_input.encode()).hexdigest(),
        tool_name,
        tool_version,
    )
