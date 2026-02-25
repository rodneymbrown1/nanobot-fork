"""Agent identity sync — pull/push bootstrap files from S3."""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

IDENTITY_FILES = ("SOUL.md", "USER.md", "AGENTS.md")


def _s3_client(region: str):
    """Create a boto3 S3 client."""
    import boto3
    return boto3.client("s3", region_name=region)


def sync_identity(
    workspace: Path,
    bucket: str,
    instance: str,
    region: str = "us-east-1",
) -> list[str]:
    """Download identity files from S3 → workspace.

    Only downloads files that exist in S3. Skips files that already exist locally.

    Returns:
        List of filenames that were downloaded.
    """
    client = _s3_client(region)
    downloaded: list[str] = []

    for filename in IDENTITY_FILES:
        dest = workspace / filename
        if dest.exists():
            logger.debug("Skipping %s (already exists locally)", filename)
            continue

        key = f"{instance}/{filename}"
        try:
            client.download_file(bucket, key, str(dest))
            downloaded.append(filename)
            logger.info("Downloaded s3://%s/%s → %s", bucket, key, dest)
        except client.exceptions.ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code in ("404", "NoSuchKey"):
                logger.warning("s3://%s/%s not found, skipping", bucket, key)
            else:
                logger.error("Failed to download s3://%s/%s: %s", bucket, key, exc)
        except Exception as exc:
            logger.error("Failed to download s3://%s/%s: %s", bucket, key, exc)

    return downloaded


def push_identity(
    workspace: Path,
    bucket: str,
    instance: str,
    region: str = "us-east-1",
) -> list[str]:
    """Upload identity files from workspace → S3.

    Only uploads files that exist locally.

    Returns:
        List of filenames that were uploaded.
    """
    client = _s3_client(region)
    uploaded: list[str] = []

    for filename in IDENTITY_FILES:
        src = workspace / filename
        if not src.exists():
            logger.warning("%s not found in workspace, skipping", filename)
            continue

        key = f"{instance}/{filename}"
        try:
            client.upload_file(str(src), bucket, key)
            uploaded.append(filename)
            logger.info("Uploaded %s → s3://%s/%s", src, bucket, key)
        except Exception as exc:
            logger.error("Failed to upload %s → s3://%s/%s: %s", src, bucket, key, exc)

    return uploaded


def identity_status(
    workspace: Path,
    bucket: str,
    instance: str,
    region: str = "us-east-1",
) -> list[dict]:
    """Compare local vs S3 identity files.

    Returns:
        List of dicts with keys: filename, local (bool), remote (bool).
    """
    client = _s3_client(region)
    results: list[dict] = []

    for filename in IDENTITY_FILES:
        local_exists = (workspace / filename).exists()
        remote_exists = False

        key = f"{instance}/{filename}"
        try:
            client.head_object(Bucket=bucket, Key=key)
            remote_exists = True
        except Exception:
            pass

        results.append({
            "filename": filename,
            "local": local_exists,
            "remote": remote_exists,
        })

    return results
