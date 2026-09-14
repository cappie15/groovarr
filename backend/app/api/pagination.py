"""Shared limit/offset pagination for list endpoints (§94: "hundreds of
tracks, thousands of videos" must not mean loading every row into one
response). Added in the Phase 10 hardening audit — see
docs/00-research-and-architecture-review.md §94 and §11's Phase 10 scope.
"""

from dataclasses import dataclass

from fastapi import Query
from sqlalchemy import Select

DEFAULT_LIMIT = 100
MAX_LIMIT = 500


@dataclass(frozen=True)
class Pagination:
    limit: int
    offset: int


def pagination_params(
    limit: int = Query(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT, description="Max rows to return."),
    offset: int = Query(0, ge=0, description="Rows to skip."),
) -> Pagination:
    return Pagination(limit=limit, offset=offset)


def paginate(query: Select, page: Pagination) -> Select:
    return query.limit(page.limit).offset(page.offset)
