from collections.abc import Sequence

import httpx
from langchain_core.embeddings import Embeddings
from langchain_openai import OpenAIEmbeddings
from pydantic import SecretStr


class LangChainEmbeddingAdapter:
    def __init__(self, embeddings: Embeddings) -> None:
        self._embeddings = embeddings

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return await self._embeddings.aembed_documents(list(texts))

    async def embed_query(self, text: str) -> list[float]:
        return await self._embeddings.aembed_query(text)

    @classmethod
    def for_openai_compatible_api(
        cls,
        *,
        model: str,
        api_key: SecretStr,
        base_url: str,
        http_async_client: httpx.AsyncClient | None = None,
    ) -> "LangChainEmbeddingAdapter":
        embeddings = OpenAIEmbeddings(
            model=model,
            api_key=api_key,
            base_url=base_url,
            check_embedding_ctx_length=False,
            chunk_size=10,
            http_async_client=http_async_client,
        )
        return cls(embeddings)
