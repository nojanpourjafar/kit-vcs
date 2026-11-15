"""A remote stored in an S3 bucket.

Works with Amazon S3 and with S3-compatible services such as Cloudflare R2.
Credentials come from the usual AWS places (environment variables or
~/.aws/credentials). For a non-Amazon service, set KIT_S3_ENDPOINT to its URL.

    kit push s3://my-bucket/my-kit

Needs boto3:  pip install boto3
"""

from __future__ import annotations

import os
from pathlib import Path

from .hashing import hash_file, is_digest
from .repo import KitError

_NOT_FOUND = {"404", "NoSuchKey", "NotFound"}


class S3Remote:
    def __init__(self, bucket: str, prefix: str = "", client=None):
        if client is None:
            try:
                import boto3
            except ImportError:
                raise KitError("S3 remotes need boto3; install it with: pip install boto3") from None
            client = boto3.client("s3", endpoint_url=os.environ.get("KIT_S3_ENDPOINT") or None)
        self.client = client
        self.bucket = bucket
        self.prefix = prefix.strip("/") + "/" if prefix.strip("/") else ""

    # ---- keys ------------------------------------------------------------

    def _object_key(self, digest: str) -> str:
        if not is_digest(digest):
            raise ValueError(f"not a SHA-256 hash: {digest!r}")
        return f"{self.prefix}objects/{digest[:2]}/{digest[2:]}"

    def _commit_key(self, commit_id: str) -> str:
        if not is_digest(commit_id):
            raise ValueError(f"not a commit ID: {commit_id!r}")
        return f"{self.prefix}commits/{commit_id}.json"

    def _exists(self, key: str) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket, Key=key)
            return True
        except Exception as err:
            if _error_code(err) in _NOT_FOUND:
                return False
            raise

    def _read(self, key: str) -> bytes | None:
        try:
            return self.client.get_object(Bucket=self.bucket, Key=key)["Body"].read()
        except Exception as err:
            if _error_code(err) in _NOT_FOUND:
                return None
            raise

    # ---- the eight remote methods ----------------------------------------

    def has_object(self, digest: str) -> bool:
        return self._exists(self._object_key(digest))

    def upload_object(self, digest: str, src: Path) -> None:
        if hash_file(src) != digest:
            raise ValueError(f"{src} does not match the hash it was uploaded as")
        self.client.upload_file(str(src), self.bucket, self._object_key(digest))

    def download_object(self, digest: str, dest: Path) -> None:
        self.client.download_file(self.bucket, self._object_key(digest), str(dest))

    def has_commit(self, commit_id: str) -> bool:
        return is_digest(commit_id) and self._exists(self._commit_key(commit_id))

    def read_commit(self, commit_id: str) -> bytes:
        data = self._read(self._commit_key(commit_id))
        if data is None:
            raise FileNotFoundError(f"the remote has no version {commit_id}")
        return data

    def write_commit(self, commit_id: str, data: bytes) -> None:
        self.client.put_object(Bucket=self.bucket, Key=self._commit_key(commit_id), Body=data)

    def read_head(self) -> str | None:
        data = self._read(self.prefix + "HEAD")
        return data.decode().strip() or None if data else None

    def write_head(self, commit_id: str) -> None:
        self.client.put_object(Bucket=self.bucket, Key=self.prefix + "HEAD", Body=commit_id.encode())


def _error_code(err: Exception) -> str:
    """The error code from a botocore ClientError, or "" for anything else."""
    response = getattr(err, "response", None)
    if isinstance(response, dict):
        return str(response.get("Error", {}).get("Code", ""))
    return ""
