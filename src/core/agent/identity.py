"""Agent identity sync — pull/push workspace .md files from/to S3."""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

IDENTITY_FILES = ("SOUL.md", "USER.md", "AGENTS.md")


def _s3_client(region: str):
    """Create a boto3 S3 client."""
    import boto3
    return boto3.client("s3", region_name=region)


def push_file(
    workspace: Path,
    bucket: str,
    instance: str,
    file_path: Path,
    region: str = "us-east-1",
) -> bool:
    """Push a single workspace file to S3.

    The S3 key is computed from the file's path relative to the workspace.
    E.g. workspace/memory/MEMORY.md → s3://{bucket}/{instance}/memory/MEMORY.md

    Returns True on success, False on failure.
    """
    client = _s3_client(region)
    try:
        resolved = file_path.resolve()
        ws_resolved = workspace.expanduser().resolve()
        relative = resolved.relative_to(ws_resolved)
    except ValueError:
        logger.warning("push_file: %s is outside workspace %s", file_path, workspace)
        return False

    key = f"{instance}/{relative.as_posix()}"
    try:
        client.upload_file(str(resolved), bucket, key)
        logger.info("Pushed %s → s3://%s/%s", relative, bucket, key)
        return True
    except Exception as exc:
        logger.error("Failed to push %s → s3://%s/%s: %s", relative, bucket, key, exc)
        return False


def sync_identity(
    workspace: Path,
    bucket: str,
    instance: str,
    region: str = "us-east-1",
) -> list[str]:
    """Download all .md files from S3 prefix → workspace.

    Lists all objects under s3://{bucket}/{instance}/ and downloads any .md
    file that doesn't exist locally. This enables full state recovery.

    Returns:
        List of relative paths that were downloaded.
    """
    client = _s3_client(region)
    downloaded: list[str] = []
    prefix = f"{instance}/"

    try:
        paginator = client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                key = obj["Key"]
                # Strip the instance prefix to get relative path
                relative = key[len(prefix):]
                if not relative.endswith(".md"):
                    continue

                dest = workspace / relative
                if dest.exists():
                    logger.debug("Skipping %s (already exists locally)", relative)
                    continue

                dest.parent.mkdir(parents=True, exist_ok=True)
                try:
                    client.download_file(bucket, key, str(dest))
                    downloaded.append(relative)
                    logger.info("Downloaded s3://%s/%s → %s", bucket, key, dest)
                except Exception as exc:
                    logger.error("Failed to download s3://%s/%s: %s", bucket, key, exc)
    except Exception as exc:
        logger.error("Failed to list s3://%s/%s: %s", bucket, prefix, exc)

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
    """Compare local vs S3 for all .md files in workspace and S3 prefix.

    Returns:
        List of dicts with keys: filename, local (bool), remote (bool).
    """
    client = _s3_client(region)
    prefix = f"{instance}/"

    # Collect remote .md files
    remote_files: set[str] = set()
    try:
        paginator = client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                key = obj["Key"]
                relative = key[len(prefix):]
                if relative.endswith(".md"):
                    remote_files.add(relative)
    except Exception as exc:
        logger.error("Failed to list s3://%s/%s: %s", bucket, prefix, exc)

    # Collect local .md files
    ws_resolved = workspace.expanduser().resolve()
    local_files: set[str] = set()
    for md_file in ws_resolved.rglob("*.md"):
        try:
            relative = md_file.relative_to(ws_resolved).as_posix()
            local_files.add(relative)
        except ValueError:
            pass

    all_files = sorted(local_files | remote_files)
    results: list[dict] = []
    for filename in all_files:
        results.append({
            "filename": filename,
            "local": filename in local_files,
            "remote": filename in remote_files,
        })

    return results
