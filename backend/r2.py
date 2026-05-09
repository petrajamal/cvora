"""
r2.py — Cloudflare R2 storage helpers (S3-compatible).

All file I/O goes through this module. If R2 env vars are not set,
falls back to local disk so local dev still works without credentials.
"""

import os
import io
import tempfile
import boto3
from botocore.config import Config

R2_ACCOUNT_ID       = os.getenv("R2_ACCOUNT_ID", "")
R2_ACCESS_KEY_ID    = os.getenv("R2_ACCESS_KEY_ID", "")
R2_SECRET_ACCESS_KEY = os.getenv("R2_SECRET_ACCESS_KEY", "")
R2_BUCKET           = os.getenv("R2_BUCKET", "cvora")

_USE_R2 = bool(R2_ACCOUNT_ID and R2_ACCESS_KEY_ID and R2_SECRET_ACCESS_KEY)

if _USE_R2:
    _client = boto3.client(
        "s3",
        endpoint_url=f"https://{R2_ACCOUNT_ID}.r2.cloudflarestorage.com",
        aws_access_key_id=R2_ACCESS_KEY_ID,
        aws_secret_access_key=R2_SECRET_ACCESS_KEY,
        config=Config(signature_version="s3v4"),
        region_name="auto",
    )
    print(f"[R2] Connected to bucket '{R2_BUCKET}'", flush=True)
else:
    _client = None
    print("[R2] Env vars not set — using local filesystem fallback", flush=True)


def upload_bytes(key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
    """Upload bytes to R2 (or local disk). Returns the key."""
    if _USE_R2:
        _client.put_object(Bucket=R2_BUCKET, Key=key, Body=data, ContentType=content_type)
    else:
        local_path = _local_path(key)
        os.makedirs(os.path.dirname(local_path), exist_ok=True)
        with open(local_path, "wb") as f:
            f.write(data)
    return key


def download_bytes(key: str) -> bytes:
    """Download object from R2 (or local disk) and return raw bytes."""
    if _USE_R2:
        resp = _client.get_object(Bucket=R2_BUCKET, Key=key)
        return resp["Body"].read()
    else:
        with open(_local_path(key), "rb") as f:
            return f.read()


def download_to_tempfile(key: str, suffix: str = ".pdf") -> str:
    """
    Download object to a NamedTemporaryFile and return the temp path.
    Caller is responsible for deleting the file after use.
    """
    data = download_bytes(key)
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    tmp.write(data)
    tmp.flush()
    tmp.close()
    return tmp.name


def delete(key: str):
    """Delete an object (best-effort, no error on missing)."""
    try:
        if _USE_R2:
            _client.delete_object(Bucket=R2_BUCKET, Key=key)
        else:
            local = _local_path(key)
            if os.path.exists(local):
                os.remove(local)
    except Exception as exc:
        print(f"[R2] delete failed for {key}: {exc}", flush=True)


def _local_path(key: str) -> str:
    return os.path.join(os.path.dirname(__file__), key)
