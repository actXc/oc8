from __future__ import annotations


def test_ingestion_worker_wiring_imports() -> None:
    # The pieces the CLI wires must import and be callable together.
    from oc8.knowledge.worker import get_ingestion_queue, ingest_job, recover_ingestion
    from oc8.runtime.queue import RunQueue

    assert callable(ingest_job)
    assert callable(recover_ingestion)
    q = get_ingestion_queue()
    assert isinstance(q, RunQueue)
    assert q._key == "oc8:ingestion:stream"
