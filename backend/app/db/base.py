"""SQLAlchemy declarative base.

All ORM models (added starting Phase 2 — see
docs/00-research-and-architecture-review.md §7/§11) inherit from `Base`.
Alembic's `env.py` targets `Base.metadata` for autogeneration.
"""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Base class for all Groovarr ORM models."""
