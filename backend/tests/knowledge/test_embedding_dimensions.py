"""An embedding that does not fit the column must say so in words.

Live, 2026-07-28: the schema wanted 1536 dimensions (OpenAI's size) while the
configured model produced 768, so every single ingest died with a raw driver
error -- `expected 1536 dimensions, not 768` -- surfaced to the operator as a
bare 500. The knowledge base was unusable in the default configuration and the
message named neither the model nor the setting to change.
"""

from __future__ import annotations

import pytest

from oc8.knowledge.ingest import IngestionError, check_embedding_fits
from oc8.models.knowledge import EMBED_DIM

pytestmark = pytest.mark.asyncio


async def test_a_matching_embedding_passes_through() -> None:
    vector = [0.0] * EMBED_DIM
    assert check_embedding_fits(vector, model="nomic-embed-text") is vector


async def test_a_missing_embedding_is_fine() -> None:
    """Retrieval degrades to nothing found; ingestion still stores the text."""
    assert check_embedding_fits(None, model="nomic-embed-text") is None


async def test_a_mismatch_names_both_numbers_and_the_model() -> None:
    with pytest.raises(IngestionError) as exc:
        check_embedding_fits([0.0] * 1536, model="text-embedding-3-small")
    message = str(exc.value)
    assert "1536" in message and str(EMBED_DIM) in message
    assert "text-embedding-3-small" in message
