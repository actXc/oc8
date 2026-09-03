"""Object storage for file attachments (chat + agent Instructions uploads).
See docs/superpowers/specs/2026-09-03-chat-and-instruction-file-attachments-design.md.

boto3 is synchronous; every call here runs through asyncio.to_thread, the
same pattern this codebase already uses to keep a synchronous library out of
the async request path. No presigned URLs are ever generated -- every byte
this module returns comes back through an oc8 backend endpoint that has
already run its own ownership/RLS checks (see api/v1/files.py), never handed
directly to a browser.
"""

from __future__ import annotations

import asyncio
import functools
from typing import Any, cast

import boto3
from botocore.client import Config as BotoConfig
from botocore.exceptions import ClientError

from oc8.config import get_settings


@functools.lru_cache(maxsize=1)
def _client() -> Any:
    settings = get_settings()
    return boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint,
        aws_access_key_id=settings.s3_access_key,
        aws_secret_access_key=settings.s3_secret_key,
        region_name=settings.s3_region,
        config=BotoConfig(signature_version="s3v4"),
    )


def _bucket() -> str:
    return get_settings().s3_bucket


class ObjectNotFound(Exception):
    pass


def _put_object_sync(key: str, body: bytes, content_type: str) -> None:
    _client().put_object(Bucket=_bucket(), Key=key, Body=body, ContentType=content_type)


def _get_object_sync(key: str) -> bytes:
    try:
        resp = _client().get_object(Bucket=_bucket(), Key=key)
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") in ("NoSuchKey", "404"):
            raise ObjectNotFound(key) from exc
        raise
    return cast(bytes, resp["Body"].read())


def _delete_object_sync(key: str) -> None:
    _client().delete_object(Bucket=_bucket(), Key=key)


def _object_exists_sync(key: str) -> bool:
    try:
        _client().head_object(Bucket=_bucket(), Key=key)
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") in ("NoSuchKey", "404"):
            return False
        raise
    return True


async def put_object(key: str, body: bytes, content_type: str) -> None:
    await asyncio.to_thread(_put_object_sync, key, body, content_type)


async def get_object(key: str) -> bytes:
    return await asyncio.to_thread(_get_object_sync, key)


async def delete_object(key: str) -> None:
    await asyncio.to_thread(_delete_object_sync, key)


async def object_exists(key: str) -> bool:
    return await asyncio.to_thread(_object_exists_sync, key)
