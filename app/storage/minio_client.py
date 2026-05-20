"""S3-compatible object storage client.

Wraps boto3 so the same code talks to MinIO locally and AWS S3 in production.
The only difference between the two is the endpoint URL in app.config.

Content-addressed storage: object keys are SHA256 hashes of file contents.
Same file → same key → re-uploads are no-ops. Original filename is preserved
as S3 metadata, not as the key.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import BinaryIO

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError

from app.config import settings
from app.observability.logging import get_logger

log = get_logger(__name__)

# Default bucket. Multiple buckets are possible (e.g., one per tenant) but
# Phase 2 ships a single bucket; sharding becomes a Phase 11 security concern.
DEFAULT_BUCKET = "documents"


# ---------- Hashing helper ----------------------------------------------------


def content_hash(path: Path, chunk_size: int = 65536) -> str:
    """Compute SHA256 hash of file contents.

    Streams the file in chunks so it works on files larger than RAM.
    The hash is the *object key prefix* — see ObjectStore.key_for_path().
    """
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def key_for_path(path: Path) -> str:
    """The object key a file should be stored under: 'sha256:<hash>.<ext>'.

    The extension is preserved as a suffix so tools that infer content type
    from the URL (browsers, viewers) still work. The hash is the identity;
    the extension is hint only.
    """
    h = content_hash(path)
    return f"sha256:{h}{path.suffix.lower()}"


# ---------- The store ---------------------------------------------------------


class ObjectStore:
    """S3-compatible object store wrapper.

    Construct once at app startup, reuse across requests. The boto3 client
    handles connection pooling internally.
    """

    def __init__(
        self,
        endpoint_url: str | None = None,
        access_key: str | None = None,
        secret_key: str | None = None,
        bucket: str = DEFAULT_BUCKET,
    ):
        self.bucket = bucket
        self.client = boto3.client(
            "s3",
            endpoint_url=endpoint_url or f"http://{settings.minio_endpoint}",
            aws_access_key_id=access_key or settings.minio_access_key,
            aws_secret_access_key=secret_key or settings.minio_secret_key,
            # Path-style addressing works against both MinIO and S3; virtual-host
            # style does not work against MinIO without DNS setup.
            config=Config(s3={"addressing_style": "path"}),
            region_name="us-east-1",  # MinIO ignores; S3 needs a value
        )

    # ---- Bucket lifecycle ----------------------------------------------------

    def ensure_bucket(self, bucket: str | None = None) -> None:
        """Create the bucket if it doesn't exist. Safe to call repeatedly."""
        name = bucket or self.bucket
        try:
            self.client.head_bucket(Bucket=name)
            log.debug("objectstore.bucket_exists", bucket=name)
            return
        except ClientError as e:
            err_code = e.response.get("Error", {}).get("Code", "")
            # 404 means "doesn't exist" — fine, we create it
            # Other errors (403, etc.) should bubble up
            if err_code not in ("404", "NoSuchBucket", "NotFound"):
                raise
        self.client.create_bucket(Bucket=name)
        log.info("objectstore.bucket_created", bucket=name)

    # ---- Uploads -------------------------------------------------------------

    def put_file(
        self,
        local_path: Path,
        key: str | None = None,
        content_type: str | None = None,
        extra_metadata: dict[str, str] | None = None,
    ) -> str:
        """Upload a local file. Returns the object key it was stored under.

        If `key` is None, the content-addressed key (sha256:...) is computed.
        Original filename is preserved as S3 metadata under 'x-amz-meta-filename'.
        """
        self.ensure_bucket()
        key = key or key_for_path(local_path)

        # S3 metadata keys are case-insensitive and must be ASCII
        metadata: dict[str, str] = {"filename": local_path.name}
        if extra_metadata:
            metadata.update(extra_metadata)

        extra_args: dict = {"Metadata": metadata}
        if content_type:
            extra_args["ContentType"] = content_type

        self.client.upload_file(
            Filename=str(local_path),
            Bucket=self.bucket,
            Key=key,
            ExtraArgs=extra_args,
        )
        log.info(
            "objectstore.put_file",
            bucket=self.bucket,
            key=key,
            size_bytes=local_path.stat().st_size,
            content_type=content_type,
        )
        return key

    def put_bytes(
        self,
        data: bytes,
        key: str,
        content_type: str | None = None,
    ) -> str:
        """Upload raw bytes under a given key. Used for generated artifacts."""
        self.ensure_bucket()
        kwargs: dict = {"Bucket": self.bucket, "Key": key, "Body": data}
        if content_type:
            kwargs["ContentType"] = content_type
        self.client.put_object(**kwargs)
        log.info(
            "objectstore.put_bytes",
            bucket=self.bucket,
            key=key,
            size_bytes=len(data),
            content_type=content_type,
        )
        return key

    # ---- Downloads -----------------------------------------------------------

    def get_bytes(self, key: str) -> bytes:
        """Download an object's contents. Raises ClientError on 404."""
        resp = self.client.get_object(Bucket=self.bucket, Key=key)
        data = resp["Body"].read()
        log.debug("objectstore.get_bytes", bucket=self.bucket, key=key, size=len(data))
        return data

    def object_exists(self, key: str) -> bool:
        """Cheap existence check using HEAD. Used by the pipeline for idempotency."""
        try:
            self.client.head_object(Bucket=self.bucket, Key=key)
            return True
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
                return False
            raise

    # ---- Presigned URLs ------------------------------------------------------

    def presigned_url(self, key: str, expires_seconds: int = 3600) -> str:
        """Generate a temporary URL that grants read access to one object.

        Phase 8's PDF download endpoint hands these URLs to the browser so the
        client downloads directly from object storage, not through FastAPI.
        Reduces app server load and supports large files cleanly.
        """
        url = self.client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": key},
            ExpiresIn=expires_seconds,
        )
        log.debug(
            "objectstore.presigned_url",
            bucket=self.bucket,
            key=key,
            expires=expires_seconds,
        )
        return url


# ---------- Module-level singleton --------------------------------------------

# Constructed once on import; reused everywhere.
# Tests can override by constructing their own ObjectStore.
_default_store: ObjectStore | None = None


def get_object_store() -> ObjectStore:
    """Lazily-constructed module-level store. Use this from app code."""
    global _default_store
    if _default_store is None:
        _default_store = ObjectStore()
    return _default_store