"""Restore a backup made by scripts/backup.py. REPLACES the current data.

    uv run python scripts/restore.py backups/20261003T120000Z --yes

Stop the api first (`docker-compose -f local.yml stop api`) so nothing writes
while the restore runs, and start it again afterwards — it re-checks the
schema and the embedding collection on start.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
from pathlib import Path

import httpx

from continuum.config import get_settings


async def restore_qdrant(url: str, folder: Path, names: list[str], api_key: str | None) -> None:
    headers = {"api-key": api_key} if api_key else {}
    async with httpx.AsyncClient(base_url=url, headers=headers, timeout=1800) as client:
        for name in names:
            path = folder / f"qdrant-{name}.snapshot"
            with path.open("rb") as handle:
                response = await client.post(
                    f"/collections/{name}/snapshots/upload",
                    params={"priority": "snapshot"},
                    files={"snapshot": (path.name, handle, "application/octet-stream")},
                )
            response.raise_for_status()
            print(f"  qdrant   {path.name} -> {name}")


def restore_postgres(container: str, user: str, database: str, folder: Path) -> None:
    with (folder / "postgres.sql").open("rb") as handle:
        subprocess.run(
            ["docker", "exec", "-i", container, "psql", "-q", "-v", "ON_ERROR_STOP=1",
             "-U", user, database],
            stdin=handle, check=True,
        )
    print(f"  postgres postgres.sql -> {database}")


async def main() -> None:
    settings = get_settings()
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("folder", type=Path)
    parser.add_argument("--yes", action="store_true", help="Confirm replacing current data.")
    parser.add_argument("--qdrant-url", default=settings.qdrant_url)
    parser.add_argument("--postgres-container", default="continuum-postgres")
    parser.add_argument("--postgres-user", default="continuum")
    parser.add_argument("--postgres-db", default="continuum")
    args = parser.parse_args()

    manifest = json.loads((args.folder / "manifest.json").read_text())
    print(f"Backup from {manifest['created_at']}: {', '.join(manifest['qdrant_collections'])}"
          + (f" + {manifest['postgres']}" if manifest["postgres"] else ""))
    if not args.yes:
        sys.exit("This replaces the current memories and accounts. Re-run with --yes.")
    await restore_qdrant(args.qdrant_url, args.folder, manifest["qdrant_collections"],
                         settings.qdrant_api_key)
    if manifest["postgres"]:
        restore_postgres(args.postgres_container, args.postgres_user, args.postgres_db,
                         args.folder)
    print("Done. Start the api again.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (httpx.HTTPError, subprocess.CalledProcessError, FileNotFoundError) as exc:
        sys.exit(f"Restore failed: {exc}")
