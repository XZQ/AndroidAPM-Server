"""Privileged bounded upload endpoints for Java mappings and unstripped Native ELFs."""

from __future__ import annotations

import re
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from androidapm_server.api.ingest import _required_header
from androidapm_server.artifacts import (
    ArtifactIdentity,
    LocalArtifactStore,
    StagedArtifact,
    inspect_native_elf,
    validate_java_mapping,
)
from androidapm_server.ci_auth import authenticate_ci_key
from androidapm_server.config import Settings, get_settings
from androidapm_server.constants import (
    ARTIFACT_TYPE_JAVA_MAPPING,
    ARTIFACT_TYPE_NATIVE_ELF,
    CI_SCOPE_ARTIFACT_WRITE,
    HEADER_ABI,
    HEADER_APP_BUILD,
    HEADER_APP_ID,
    HEADER_BUILD_ID,
    HEADER_CHECKSUM_SHA256,
    HEADER_VARIANT,
    HEADER_VERSION_CODE,
)
from androidapm_server.db.artifacts import register_artifact
from androidapm_server.db.models import AuditLog
from androidapm_server.db.session import get_session
from androidapm_server.db.symbolization import requeue_matching_missing_jobs
from androidapm_server.errors import ApiError

router = APIRouter(prefix="/v1/artifacts", tags=["artifacts"])

SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
BUILD_ID_PATTERN = re.compile(r"^[0-9a-f]{8,128}$")
SUPPORTED_ABIS = frozenset({"arm64-v8a", "armeabi-v7a", "x86_64", "x86"})
JAVA_MAPPING_CONTENT_TYPES = frozenset({"text/plain", "application/octet-stream"})
NATIVE_ELF_CONTENT_TYPES = frozenset({"application/x-elf", "application/octet-stream"})


class ArtifactAck(BaseModel):
    """Stable response for inserted and byte-identical artifact replays."""

    artifactId: int
    status: str = "accepted"
    duplicate: bool
    checksumSha256: str
    sizeBytes: int


@router.post("/java-mapping", response_model=ArtifactAck)
async def upload_java_mapping(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> ArtifactAck:
    """Validate and register one exact R8/ProGuard mapping identity."""
    return await _upload(request, session, settings, ARTIFACT_TYPE_JAVA_MAPPING)


@router.post("/native-elf", response_model=ArtifactAck)
async def upload_native_elf(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> ArtifactAck:
    """Validate ABI/build-id and register one unstripped Native ELF."""
    return await _upload(request, session, settings, ARTIFACT_TYPE_NATIVE_ELF)


async def _upload(
    request: Request,
    session: AsyncSession,
    settings: Settings,
    artifact_type: str,
) -> ArtifactAck:
    """Authenticate before body reads, stage bytes, validate, promote, and register."""
    app_id = _required_header(request, HEADER_APP_ID, 256)
    version_code = _required_header(request, HEADER_VERSION_CODE, 64)
    app_build = _required_header(request, HEADER_APP_BUILD, 128)
    variant = _required_header(request, HEADER_VARIANT, 128)
    expected_checksum = _required_header(request, HEADER_CHECKSUM_SHA256, 64).lower()
    if not SHA256_PATTERN.fullmatch(expected_checksum):
        raise ApiError(400, "invalid_checksum", "The expected SHA-256 is invalid")
    principal = await authenticate_ci_key(
        session,
        request.headers.get("authorization"),
        CI_SCOPE_ARTIFACT_WRITE,
        app_id,
    )
    if request.headers.get("content-encoding"):
        raise ApiError(415, "unsupported_content_encoding", "Artifact uploads must not be encoded")
    content_type = request.headers.get("content-type", "").split(";", maxsplit=1)[0].lower()
    supported_content_types = (
        JAVA_MAPPING_CONTENT_TYPES
        if artifact_type == ARTIFACT_TYPE_JAVA_MAPPING
        else NATIVE_ELF_CONTENT_TYPES
    )
    if content_type not in supported_content_types:
        raise ApiError(415, "unsupported_media_type", "The artifact Content-Type is not supported")

    abi = ""
    build_id = ""
    extension = "txt"
    max_bytes = settings.max_java_mapping_bytes
    if artifact_type == ARTIFACT_TYPE_NATIVE_ELF:
        abi = _required_header(request, HEADER_ABI, 32)
        build_id = _required_header(request, HEADER_BUILD_ID, 128).lower()
        if abi not in SUPPORTED_ABIS:
            raise ApiError(400, "unsupported_abi", "The Native ABI is not supported")
        if not BUILD_ID_PATTERN.fullmatch(build_id):
            raise ApiError(400, "invalid_build_id", "The GNU build-id is invalid")
        extension = "elf"
        max_bytes = settings.max_native_elf_bytes

    declared_length = _content_length(request)
    store = LocalArtifactStore(settings.artifact_storage_path)
    staged = await store.stage(request.stream(), max_bytes, declared_length)
    if staged.checksum_sha256 != expected_checksum:
        await store.discard(staged.path)
        raise ApiError(400, "checksum_mismatch", "The uploaded SHA-256 does not match")
    try:
        if artifact_type == ARTIFACT_TYPE_JAVA_MAPPING:
            await validate_java_mapping(staged.path)
        else:
            extracted = await inspect_native_elf(staged.path)
            if extracted.abi != abi or extracted.build_id != build_id:
                raise ApiError(
                    409,
                    "artifact_identity_mismatch",
                    "The ELF ABI or GNU build-id does not match the declared identity",
                )
    except Exception:
        await store.discard(staged.path)
        raise

    identity = ArtifactIdentity(
        artifact_type,
        principal.tenant_id,
        app_id,
        version_code,
        app_build,
        variant,
        abi,
        build_id,
    )
    storage_key = identity.storage_key(staged.checksum_sha256, extension)
    promoted = await store.promote(staged, storage_key)
    registration = await register_artifact(
        session, identity, staged, storage_key, principal.key_id
    )
    request_id = request.state.request_id
    if registration.conflict:
        # A different checksum uses a different immutable path and is safe to discard.
        await store.discard(promoted)
        session.add(
            _audit(
                identity,
                principal.key_id,
                request_id,
                "conflict",
                staged,
                registration.artifact.id,
            )
        )
        await session.commit()
        raise ApiError(
            409,
            "artifact_identity_conflict",
            "This build identity is already registered with different content",
        )
    await requeue_matching_missing_jobs(session, identity)
    session.add(
        _audit(
            identity,
            principal.key_id,
            request_id,
            "duplicate" if not registration.inserted else "success",
            staged,
            registration.artifact.id,
        )
    )
    await session.commit()
    return ArtifactAck(
        artifactId=registration.artifact.id,
        duplicate=not registration.inserted,
        checksumSha256=registration.artifact.checksum_sha256,
        sizeBytes=registration.artifact.size_bytes,
    )


def _content_length(request: Request) -> int | None:
    """Parse a non-negative Content-Length before accepting artifact bytes."""
    value = request.headers.get("content-length")
    if value is None:
        return None
    try:
        length = int(value)
    except ValueError as error:
        raise ApiError(400, "invalid_request", "Content-Length must be an integer") from error
    if length < 0:
        raise ApiError(400, "invalid_request", "Content-Length must not be negative")
    return length


def _audit(
    identity: ArtifactIdentity,
    actor: str,
    request_id: str,
    result: str,
    staged: StagedArtifact,
    artifact_id: int,
) -> AuditLog:
    """Create a non-secret artifact upload audit entry."""
    return AuditLog(
        tenant_id=identity.tenant_id,
        actor=actor,
        action="symbol_artifact.upload",
        object_type=identity.artifact_type,
        object_id=str(artifact_id),
        result=result,
        request_id=request_id,
        details_json={
            "app_id": identity.app_id,
            "version_code": identity.version_code,
            "app_build": identity.app_build,
            "variant": identity.variant,
            "abi": identity.abi or None,
            "build_id": identity.build_id or None,
            "checksum_sha256": staged.checksum_sha256,
            "size_bytes": staged.size_bytes,
        },
    )
