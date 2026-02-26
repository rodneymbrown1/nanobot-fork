#!/usr/bin/env python3
"""Migrate from single nanobot/config secret to org + instance split.

Reads the existing nanobot/config secret, splits it into:
  - nanobot/org       (providers, tools, agents defaults, gateway host/port)
  - nanobot/instance/{name}  (channels, gateway apiKey, agent overrides)

Then verifies the deep-merged result matches the original.

Usage:
  python scripts/migrate-to-multi-instance.py [--instance nanobot] [--region us-east-1] [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import sys
from copy import deepcopy


def deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge override into base. Override wins on conflicts."""
    result = dict(base)
    for k, v in override.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = deep_merge(result[k], v)
        else:
            result[k] = v
    return result


# Keys that belong to the instance (everything else goes to org)
INSTANCE_KEYS = {"channels"}
# These are split: some fields go to org, some to instance
SPLIT_KEYS = {"gateway"}


def split_config(config: dict) -> tuple[dict, dict]:
    """Split a monolithic config into (org, instance) dicts."""
    org = {}
    instance = {}

    for key, value in config.items():
        if key in INSTANCE_KEYS:
            instance[key] = deepcopy(value)
        elif key == "gateway":
            # gateway.apiKey → instance, rest → org
            gw_org = {k: v for k, v in value.items() if k != "apiKey"}
            gw_instance = {}
            if "apiKey" in value:
                gw_instance["apiKey"] = value["apiKey"]
            if gw_org:
                org["gateway"] = gw_org
            if gw_instance:
                instance["gateway"] = gw_instance
        else:
            org[key] = deepcopy(value)

    return org, instance


def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate nanobot/config to multi-instance secrets")
    parser.add_argument("--instance", default="nanobot", help="Instance name (default: nanobot)")
    parser.add_argument("--region", default="us-east-1", help="AWS region")
    parser.add_argument("--dry-run", action="store_true", help="Print what would be written without writing")
    parser.add_argument("--delete-old", action="store_true", help="Delete the old nanobot/config after migration")
    args = parser.parse_args()

    import boto3

    sm = boto3.client("secretsmanager", region_name=args.region)

    # ── 1. Read current nanobot/config ───────────────────────────────────
    print("Reading nanobot/config...")
    try:
        resp = sm.get_secret_value(SecretId="nanobot/config")
        original = json.loads(resp["SecretString"])
    except sm.exceptions.ResourceNotFoundException:
        print("ERROR: nanobot/config not found. Nothing to migrate.")
        sys.exit(1)
    except Exception as e:
        print(f"ERROR reading nanobot/config: {e}")
        sys.exit(1)

    print(f"  Found {len(original)} top-level keys: {list(original.keys())}")

    # ── 2. Split into org + instance ─────────────────────────────────────
    org, instance = split_config(original)

    print(f"\nOrg keys:      {list(org.keys())}")
    print(f"Instance keys: {list(instance.keys())}")

    # ── 3. Verify deep-merge matches original ────────────────────────────
    merged = deep_merge(org, instance)
    if merged != original:
        print("\nWARNING: Deep-merge of split configs does not match original!")
        print("  Differences may be in key ordering. Checking values...")
        if json.dumps(merged, sort_keys=True) == json.dumps(original, sort_keys=True):
            print("  Values match (key ordering differs). Proceeding.")
        else:
            print("  Values DO NOT match. Aborting.")
            print(f"  Original: {json.dumps(original, indent=2)}")
            print(f"  Merged:   {json.dumps(merged, indent=2)}")
            sys.exit(1)
    else:
        print("\nVerification passed: deep_merge(org, instance) == original")

    # ── 4. Write to new secrets ──────────────────────────────────────────
    org_name = "nanobot/org"
    instance_name = f"nanobot/instance/{args.instance}"

    if args.dry_run:
        print(f"\n[DRY RUN] Would write to {org_name}:")
        print(json.dumps(org, indent=2))
        print(f"\n[DRY RUN] Would write to {instance_name}:")
        print(json.dumps(instance, indent=2))
        return

    # Write org secret
    print(f"\nWriting {org_name}...")
    try:
        sm.describe_secret(SecretId=org_name)
        sm.put_secret_value(SecretId=org_name, SecretString=json.dumps(org))
        print(f"  Updated existing {org_name}")
    except sm.exceptions.ResourceNotFoundException:
        sm.create_secret(
            Name=org_name,
            Description="Shared nanobot org config — LLM provider keys, integrations, tools.",
            SecretString=json.dumps(org),
        )
        print(f"  Created {org_name}")

    # Write instance secret
    print(f"Writing {instance_name}...")
    try:
        sm.describe_secret(SecretId=instance_name)
        sm.put_secret_value(SecretId=instance_name, SecretString=json.dumps(instance))
        print(f"  Updated existing {instance_name}")
    except sm.exceptions.ResourceNotFoundException:
        sm.create_secret(
            Name=instance_name,
            Description=f"Per-instance config for {args.instance} — channels, gateway key.",
            SecretString=json.dumps(instance),
        )
        print(f"  Created {instance_name}")

    # ── 5. Verify reads match ────────────────────────────────────────────
    print("\nVerifying written secrets...")
    org_read = json.loads(sm.get_secret_value(SecretId=org_name)["SecretString"])
    instance_read = json.loads(sm.get_secret_value(SecretId=instance_name)["SecretString"])
    re_merged = deep_merge(org_read, instance_read)

    if json.dumps(re_merged, sort_keys=True) == json.dumps(original, sort_keys=True):
        print("  Verification passed: written secrets merge back to original.")
    else:
        print("  WARNING: Written secrets don't merge back to original!")
        print("  Check manually before deleting old secret.")

    # ── 6. Optionally delete old secret ──────────────────────────────────
    if args.delete_old:
        confirm = input("\nType 'DELETE' to remove nanobot/config: ")
        if confirm == "DELETE":
            sm.delete_secret(SecretId="nanobot/config", ForceDeleteWithoutRecovery=False)
            print("  nanobot/config scheduled for deletion (30-day recovery window).")
        else:
            print("  Skipped deletion.")
    else:
        print("\nOld nanobot/config left intact. Use --delete-old to remove after verifying.")

    print("\nMigration complete!")


if __name__ == "__main__":
    main()
