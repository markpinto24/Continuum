"""Back up everything Continuum knows: Qdrant (the memories) and Postgres
(accounts, decisions, rules, feedback).

    uv run python scripts/backup.py                 # -> backups/<UTC timestamp>/
    uv run python scripts/backup.py --out /mnt/usb  # somewhere else

Each Qdrant collection is snapshotted through Qdrant's own API and downloaded,
then the server-side snapshot file is removed (it is a copy, not a memory). The
database is dumped with `pg_dump` inside the compose container, so nothing needs
installing on the host. Restore with scripts/restore.py.

A backup holds every user's memories in plain text: store it like the database.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import httpx

from continuum.config import get_settings

ROOT = Path(__file__).resolve().parents[1]


async def backup_qdrant(url: str, prefix: str, out: Path, api_key: str | None) -> list[str]:
    headers = {"api-key": api_key} if api_key else {}
    saved = []
    async with httpx.AsyncClient(base_url=url, headers=headers, timeout=600) as client:
        collections = (await client.get("/collections")).raise_for_status().json()
        names = [c["name"] for c in collections["result"]["collections"]
                 if c["name"].startswith(prefix)]
        for name in names:
            made = (await client.post(f"/collections/{name}/snapshots")).raise_for_status()
            snapshot = made.json()["result"]["name"]
            target = out / f"qdrant-{name}.snapshot"
            async with client.stream("GET", f"/collections/{name}/snapshots/{snapshot}") as r:
                r.raise_for_status()
                with target.open("wb") as handle:
                    async for chunk in r.aiter_bytes():
                        handle.write(chunk)
            await client.delete(f"/collections/{name}/snapshots/{snapshot}")
            saved.append(name)
            print(f"  qdrant   {name} -> {target.name} ({target.stat().st_size // 1024} KB)")
    return saved


def backup_postgres(container: str, user: str, database: str, out: Path) -> None:
    target = out / "postgres.sql"
    with target.open("wb") as handle:
        subprocess.run(
            ["docker", "exec", container, "pg_dump", "--clean", "--if-exists",
             "-U", user, database],
            stdout=handle, check=True,
        )
    print(f"  postgres {database} -> {target.name} ({target.stat().st_size // 1024} KB)")


async def main() -> None:
    settings = get_settings()
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", type=Path, default=ROOT / "backups")
    parser.add_argument("--qdrant-url", default=settings.qdrant_url)
    parser.add_argument("--postgres-container", default="continuum-postgres")
    parser.add_argument("--postgres-user", default="continuum")
    parser.add_argument("--postgres-db", default="continuum")
    parser.add_argument("--skip-postgres", action="store_true")
    args = parser.parse_args()

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out = args.out / stamp
    out.mkdir(parents=True, exist_ok=False)
    print(f"Backing up to {out}")
    collections = await backup_qdrant(args.qdrant_url, settings.qdrant_collection, out,
                                      settings.qdrant_api_key)
    if not args.skip_postgres:
        backup_postgres(args.postgres_container, args.postgres_user, args.postgres_db, out)
    (out / "manifest.json").write_text(json.dumps({
        "created_at": stamp,
        "qdrant_collections": collections,
        "postgres": None if args.skip_postgres else args.postgres_db,
        "embedding_model": settings.embedding_model,
    }, indent=2))
    print("Done.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (httpx.HTTPError, subprocess.CalledProcessError) as exc:
        sys.exit(f"Backup failed: {exc}")
