"""CLI entrypoint for Wiki MVP experiments."""

from __future__ import annotations

import argparse
import asyncio
from typing import Any

from openviking import AsyncOpenViking

from .config import WikiMVPConfig, WikiMVPGenerationLimits
from .oarel_input import load_oarel_mvp_documents
from .pipeline import WikiMVPPipeline


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Wiki MVP batch generation")
    parser.add_argument("--input", required=True, help="Path to OARelatedWork MVP jsonl")
    parser.add_argument("--storage-path", default=None, help="OpenViking local storage path")
    parser.add_argument("--max-samples", type=int, default=None)
    parser.add_argument("--wiki-root-uri", default="viking://wiki/")
    parser.add_argument("--resource-root-uri", default="viking://resources/")
    parser.add_argument("--max-depth", type=int, default=2)
    parser.add_argument("--max-active-nodes", type=int, default=20)
    parser.add_argument("--min-child-nodes-per-parent", type=int, default=3)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


async def main_async(args: argparse.Namespace) -> int:
    limits = WikiMVPGenerationLimits(
        max_depth=args.max_depth,
        max_active_nodes=args.max_active_nodes,
        min_child_nodes_per_parent=args.min_child_nodes_per_parent,
    )
    config = WikiMVPConfig(
        resource_root_uri=args.resource_root_uri,
        wiki_root_uri=args.wiki_root_uri,
        limits=limits,
        dry_run=args.dry_run,
    )
    docs = load_oarel_mvp_documents(args.input, max_samples=args.max_samples, config=config)
    client: Any
    if args.dry_run:
        client = _NoopClient()
    else:
        client = AsyncOpenViking(path=args.storage_path)
        await client.initialize()

    pipeline = WikiMVPPipeline(client=client, config=config)
    artifacts = await pipeline.run(docs)
    print(
        "Wiki MVP generation completed: "
        f"docs={len(docs)} cards={len(artifacts.cards)} nodes={len(artifacts.nodes)}"
    )
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(main_async(parse_args())))


class _NoopClient:
    async def mkdir(self, *_: Any, **__: Any) -> None:
        return None

    async def write(self, *_: Any, **__: Any) -> dict[str, Any]:
        return {}


if __name__ == "__main__":
    main()
