"""Generic paged-list envelope shared by every list-query endpoint (Design
System Consistency plan). One shape, reused everywhere, so the frontend's
<ListToolbar> is written once against a single response contract."""

from __future__ import annotations

from typing import Generic, TypeVar

from oc8.schemas.base import CamelModel

T = TypeVar("T")


class Page(CamelModel, Generic[T]):
    items: list[T]
    total_count: int
