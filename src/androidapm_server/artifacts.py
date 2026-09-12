"""Bounded local artifact storage and format-level symbol validation."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import tempfile
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from elftools.elf.elffile import ELFFile

from androidapm_server.errors import ApiError

# R8 mappings use dotted JVM binary names, including Unicode and generated names.
# A class segment cannot contain a package separator or JVM descriptor punctuation.
JAVA_CLASS_SEGMENT = r"[^\s.:;/\[\]]+"
JAVA_CLASS_NAME = rf"{JAVA_CLASS_SEGMENT}(?:\.{JAVA_CLASS_SEGMENT})*"
JAVA_CLASS_MAPPING = re.compile(rf"^{JAVA_CLASS_NAME} -> {JAVA_CLASS_NAME}:$")
SUPPORTED_ABI_BY_MACHINE = {
    "EM_AARCH64": "arm64-v8a",
    "EM_ARM": "armeabi-v7a",
    "EM_X86_64": "x86_64",
    "EM_386": "x86",
}


@dataclass(frozen=True, slots=True)
class ArtifactIdentity:
    """Exact immutable build identity used for uniqueness and lookup."""

    artifact_type: str
    tenant_id: str
    app_id: str
    version_code: str
    app_build: str
    variant: str
    abi: str = ""
    build_id: str = ""

    def storage_key(self, checksum_sha256: str, extension: str) -> str:
        """Return a path-safe deterministic key without exposing raw tenant/build labels."""
        canonical = json.dumps(
            {
                "tenantId": self.tenant_id,
                "artifactType": self.artifact_type,
                "appId": self.app_id,
                "versionCode": self.version_code,
                "appBuild": self.app_build,
                "variant": self.variant,
                "abi": self.abi,
                "buildId": self.build_id,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        identity_hash = hashlib.sha256(canonical).hexdigest()
        tenant_hash = hashlib.sha256(self.tenant_id.encode()).hexdigest()[:16]
        return f"{tenant_hash}/{self.artifact_type}/{identity_hash}/{checksum_sha256}.{extension}"


@dataclass(frozen=True, slots=True)
class StagedArtifact:
    """Temporary upload plus its server-computed integrity metadata."""

    path: Path
    checksum_sha256: str
    size_bytes: int


@dataclass(frozen=True, slots=True)
class NativeElfIdentity:
    """Identity extracted from a validated unstripped ELF file."""

    abi: str
    build_id: str


class LocalArtifactStore:
    """Private filesystem store with bounded staging and atomic same-volume promotion."""

    def __init__(self, root: Path) -> None:
        """Bind the store to one configured non-public root."""
        self.root = root.resolve()
        self.staging_root = self.root / ".staging"

    async def stage(
        self,
        chunks: AsyncIterator[bytes],
        max_bytes: int,
        declared_length: int | None,
    ) -> StagedArtifact:
        """Stream request chunks to a private temp file while enforcing size and SHA-256."""
        if declared_length is not None and (declared_length < 0 or declared_length > max_bytes):
            raise ApiError(413, "artifact_too_large", "The symbol artifact is too large")
        await asyncio.to_thread(self.staging_root.mkdir, parents=True, exist_ok=True)
        descriptor, raw_path = await asyncio.to_thread(
            tempfile.mkstemp, prefix="upload-", suffix=".tmp", dir=self.staging_root
        )
        path = Path(raw_path)
        digest = hashlib.sha256()
        size = 0
        try:
            with os.fdopen(descriptor, "wb") as output:
                async for chunk in chunks:
                    size += len(chunk)
                    if size > max_bytes:
                        raise ApiError(
                            413, "artifact_too_large", "The symbol artifact is too large"
                        )
                    digest.update(chunk)
                    await asyncio.to_thread(_write_all, output, chunk)
                await asyncio.to_thread(output.flush)
                await asyncio.to_thread(os.fsync, output.fileno())
            if size == 0:
                raise ApiError(400, "invalid_artifact", "The symbol artifact is empty")
            return StagedArtifact(path, digest.hexdigest(), size)
        except BaseException:
            await asyncio.to_thread(path.unlink, missing_ok=True)
            raise

    async def promote(self, staged: StagedArtifact, storage_key: str) -> Path:
        """Atomically promote a validated temporary file to its immutable content key."""
        destination = self.path_for(storage_key)
        await asyncio.to_thread(destination.parent.mkdir, parents=True, exist_ok=True)
        await asyncio.to_thread(os.replace, staged.path, destination)
        return destination

    async def discard(self, path: Path) -> None:
        """Best-effort remove a staging or unregistered immutable file."""
        await asyncio.to_thread(path.unlink, missing_ok=True)

    def path_for(self, storage_key: str) -> Path:
        """Resolve an internal storage key and reject any traversal outside the root."""
        candidate = (self.root / storage_key).resolve()
        if not candidate.is_relative_to(self.root):
            raise ValueError("artifact storage key escapes the configured root")
        return candidate


async def validate_java_mapping(path: Path) -> None:
    """Require strict UTF-8 ProGuard/R8 class mappings without loading the file at once."""
    await asyncio.to_thread(_validate_java_mapping_sync, path)


async def inspect_native_elf(path: Path) -> NativeElfIdentity:
    """Extract ABI and GNU build-id and require an unstripped symbol table."""
    return await asyncio.to_thread(_inspect_native_elf_sync, path)


def _write_all(output: BinaryIO, chunk: bytes) -> None:
    """Write a complete chunk or fail instead of accepting a short write."""
    written = output.write(chunk)
    if written != len(chunk):
        raise OSError("short artifact write")


def _validate_java_mapping_sync(path: Path) -> None:
    """Synchronous mapping validation used behind the async file boundary."""
    found_class = False
    try:
        with path.open("r", encoding="utf-8", errors="strict", newline=None) as source:
            for line in source:
                if "\x00" in line:
                    raise ApiError(400, "invalid_artifact", "The Java mapping contains NUL bytes")
                if JAVA_CLASS_MAPPING.match(line.rstrip("\r\n")):
                    found_class = True
    except UnicodeDecodeError as error:
        raise ApiError(400, "invalid_artifact", "The Java mapping must be UTF-8") from error
    if not found_class:
        raise ApiError(400, "invalid_artifact", "No ProGuard/R8 class mapping was found")


def _inspect_native_elf_sync(path: Path) -> NativeElfIdentity:
    """Synchronous ELF parser that never executes uploaded content."""
    try:
        with path.open("rb") as source:
            elf = ELFFile(source)
            machine = str(elf.header["e_machine"])
            abi = SUPPORTED_ABI_BY_MACHINE.get(machine)
            if abi is None:
                raise ApiError(400, "unsupported_elf", "The ELF architecture is not supported")
            if elf.get_section_by_name(".symtab") is None:
                raise ApiError(400, "stripped_elf", "An unstripped ELF with .symtab is required")
            build_id = _find_gnu_build_id(elf)
    except ApiError:
        raise
    except Exception as error:
        raise ApiError(400, "invalid_artifact", "The Native artifact is not a valid ELF") from error
    if build_id is None:
        raise ApiError(400, "missing_build_id", "The ELF does not contain a GNU build-id")
    return NativeElfIdentity(abi, build_id)


def _find_gnu_build_id(elf: ELFFile) -> str | None:
    """Return the normalized GNU build-id from ELF note sections."""
    for section in elf.iter_sections():
        iterator = getattr(section, "iter_notes", None)
        if iterator is None:
            continue
        for note in iterator():
            if note["n_type"] != "NT_GNU_BUILD_ID":
                continue
            description = note["n_desc"]
            if isinstance(description, bytes):
                return description.hex().lower()
            return str(description).replace(" ", "").lower()
    return None
