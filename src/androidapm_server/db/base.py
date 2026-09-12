"""SQLAlchemy declarative base and portable column helpers."""

from __future__ import annotations

from sqlalchemy import BigInteger, Integer, MetaData
from sqlalchemy.orm import DeclarativeBase

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

BIGINT_PRIMARY_KEY = BigInteger().with_variant(Integer, "sqlite")


class Base(DeclarativeBase):
    """Base class using deterministic migration constraint names."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)
