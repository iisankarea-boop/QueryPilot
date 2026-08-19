import json
from typing import Any

import httpx
from pydantic import SecretStr
from querypilot.adapters.embeddings import LangChainEmbeddingAdapter


async def test_dashscope_embedding_request_keeps_document_input_as_strings() -> None:
    captured_request: dict[str, Any] = {}

    async def handle(request: httpx.Request) -> httpx.Response:
        captured_request.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "data": [
                    {"embedding": [0.1, 0.2], "index": 0},
                    {"embedding": [0.3, 0.4], "index": 1},
                ],
                "model": "text-embedding-v4",
                "usage": {"prompt_tokens": 2, "total_tokens": 2},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        adapter = LangChainEmbeddingAdapter.for_openai_compatible_api(
            model="text-embedding-v4",
            api_key=SecretStr("test-key"),
            base_url="https://dashscope.example/compatible-mode/v1",
            http_async_client=client,
        )

        vectors = await adapter.embed_documents(["paid orders", "customer profile"])

    assert captured_request["input"] == ["paid orders", "customer profile"]
    assert vectors == [[0.1, 0.2], [0.3, 0.4]]


async def test_dashscope_embedding_requests_do_not_exceed_ten_documents() -> None:
    batch_sizes: list[int] = []

    async def handle(request: httpx.Request) -> httpx.Response:
        request_input = json.loads(request.content)["input"]
        batch_sizes.append(len(request_input))
        return httpx.Response(
            200,
            json={
                "data": [
                    {"embedding": [float(index)], "index": index}
                    for index in range(len(request_input))
                ],
                "model": "text-embedding-v4",
                "usage": {"prompt_tokens": 1, "total_tokens": 1},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        adapter = LangChainEmbeddingAdapter.for_openai_compatible_api(
            model="text-embedding-v4",
            api_key=SecretStr("test-key"),
            base_url="https://dashscope.example/compatible-mode/v1",
            http_async_client=client,
        )

        await adapter.embed_documents([f"document {index}" for index in range(11)])

    assert batch_sizes == [10, 1]
