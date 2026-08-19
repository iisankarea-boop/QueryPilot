import argparse
import asyncio
import json
from dataclasses import asdict
from uuid import uuid4

import uvicorn

from querypilot.bootstrap import build_container
from querypilot.config import get_settings
from querypilot.domain.query import AskCommand


def main() -> None:
    parser = argparse.ArgumentParser(prog="querypilot")
    subparsers = parser.add_subparsers(dest="command", required=True)

    publish_parser = subparsers.add_parser("catalog-publish")
    publish_parser.set_defaults(handler=_publish_catalog)

    ask_parser = subparsers.add_parser("ask")
    ask_parser.add_argument("question")
    ask_parser.add_argument("--source", default="commerce")
    ask_parser.add_argument("--thread")
    ask_parser.set_defaults(handler=_ask)

    serve_parser = subparsers.add_parser("serve")
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--port", type=int, default=8000)
    serve_parser.set_defaults(handler=_serve)

    args = parser.parse_args()
    args.handler(args)


def _publish_catalog(_: argparse.Namespace) -> None:
    async def publish() -> None:
        container = build_container(get_settings())
        report = await container.catalog.publish(container.manifest.release)
        print(json.dumps(asdict(report), ensure_ascii=False))

    asyncio.run(publish())


def _ask(args: argparse.Namespace) -> None:
    async def ask() -> None:
        container = build_container(get_settings())
        command = AskCommand(
            run_id=str(uuid4()),
            thread_id=args.thread or str(uuid4()),
            source_id=args.source,
            question=args.question,
        )
        agent = container.sources.agent_for(args.source)
        async for event in agent.stream(command):
            print(
                json.dumps(
                    {
                        "seq": event.seq,
                        "type": event.type,
                        "payload": event.payload,
                    },
                    ensure_ascii=False,
                    default=str,
                )
            )

    asyncio.run(ask())


def _serve(args: argparse.Namespace) -> None:
    uvicorn.run(
        "querypilot.transport.http:app",
        host=args.host,
        port=args.port,
        reload=False,
    )


if __name__ == "__main__":
    main()
