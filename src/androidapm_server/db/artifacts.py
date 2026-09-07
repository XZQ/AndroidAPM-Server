"""Transactional immutable artifact registration and identity conflict handling."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from androidapm_server.artifacts import ArtifactIdentity, StagedArtifact
from androidapm_server.constants import ARTIFACT_STATUS_ACTIVE
from androidapm_server.db.models import SymbolArtifact, utc_now


@dataclass(frozen=True, slots=True)
class ArtifactRegistration:
    """Database result distinguishing insertion, replay, and identity conflict."""

    artifact: SymbolArtifact
    inserted: bool
    conflict: bool


async def register_artifact(
    session: AsyncSession,
    identity: ArtifactIdentity,
    staged: StagedArtifact,
    storage_key: str,
    uploaded_by: str,
) -> ArtifactRegistration:
    """UPSERT one exact build identity and compare checksum after concurrent races."""
    values = {
        "tenant_id": identity.tenant_id,
        "artifact_type": identity.artifact_type,
        "app_id": identity.app_id,
        "version_code": identity.version_code,
        "app_build": identity.app_build,
        "variant": identity.variant,
        "abi": identity.abi,
        "build_id": identity.build_id,
        "checksum_sha256": staged.checksum_sha256,
        "size_bytes": staged.size_bytes,
        "storage_key": storage_key,
        "status": ARTIFACT_STATUS_ACTIVE,
        "uploaded_by": uploaded_by,
        "created_at": utc_now(),
    }
    identity_columns = [
        "tenant_id",
        "artifact_type",
        "app_id",
        "version_code",
        "app_build",
        "variant",
        "abi",
        "build_id",
    ]
    dialect = session.bind.dialect.name if session.bind is not None else ""
    if dialect == "postgresql":
        statement = (
            postgresql_insert(SymbolArtifact)
            .values(values)
            .on_conflict_do_nothing(index_elements=identity_columns)
            .returning(SymbolArtifact.id)
        )
    elif dialect == "sqlite":
        statement = (
            sqlite_insert(SymbolArtifact)
            .values(values)
            .on_conflict_do_nothing(index_elements=identity_columns)
            .returning(SymbolArtifact.id)
        )
    else:
        raise RuntimeError(f"Unsupported database dialect: {dialect}")
    inserted_id = (await session.execute(statement)).scalar_one_or_none()
    artifact = await session.scalar(select(SymbolArtifact).where(_identity_filter(identity)))
    if artifact is None:
        raise RuntimeError("Artifact identity was not visible after UPSERT")
    conflict = (
        artifact.checksum_sha256 != staged.checksum_sha256
        or artifact.size_bytes != staged.size_bytes
    )
    return ArtifactRegistration(artifact, inserted_id is not None, conflict)


def _identity_filter(identity: ArtifactIdentity) -> ColumnElement[bool]:
    """Build the exact identity predicate shared by insertion and resolution."""
    return (
        (SymbolArtifact.tenant_id == identity.tenant_id)
        & (SymbolArtifact.artifact_type == identity.artifact_type)
        & (SymbolArtifact.app_id == identity.app_id)
        & (SymbolArtifact.version_code == identity.version_code)
        & (SymbolArtifact.app_build == identity.app_build)
        & (SymbolArtifact.variant == identity.variant)
        & (SymbolArtifact.abi == identity.abi)
        & (SymbolArtifact.build_id == identity.build_id)
    )
