import os
import ssl

import httpx
import truststore
from dotenv import load_dotenv
from typing import Any

from openai import APIConnectionError, APIStatusError, APITimeoutError
from openai import AsyncOpenAI


load_dotenv()

HPS_AI_URL = os.getenv("HPS_AI_URL")
HPS_AI_TOKEN = os.getenv("HPS_AI_TOKEN")
HPS_AI_MODEL = os.getenv(
    "HPS_AI_MODEL",
    os.getenv("OCEAN_AI_MODEL", "openai:gpt-4o-mini"),
)
HPS_EMBEDDING_MODEL = os.getenv(
    "HPS_EMBEDDING_MODEL",
    os.getenv("OCEAN_EMBEDDING_MODEL", "openai:text-embedding-3-small"),
)
OCEAN_AI_BASE_URL = os.getenv("OCEAN_AI_BASE_URL")
OCEAN_AI_API_KEY = os.getenv("OCEAN_AI_API_KEY")


class HpsAiConfigurationError(RuntimeError):
    """Raised when the HPS/Ocean AI configuration is incomplete."""


class HpsAiRequestError(RuntimeError):
    """Raised when HPS/Ocean AI cannot return a usable response."""


def resolve_ocean_base_url() -> str | None:
    configured_url = OCEAN_AI_BASE_URL or HPS_AI_URL

    if not configured_url:
        return None

    normalized_url = configured_url.rstrip("/")

    if normalized_url.endswith("/chat/completions"):
        normalized_url = normalized_url.removesuffix(
            "/chat/completions"
        )

    if normalized_url.endswith("/api/v1") or normalized_url.endswith("/v1"):
        return normalized_url

    return f"{normalized_url}/api/v1"


def get_ocean_client() -> AsyncOpenAI:
    base_url = resolve_ocean_base_url()
    api_token = OCEAN_AI_API_KEY or HPS_AI_TOKEN

    if not base_url or not api_token:
        raise HpsAiConfigurationError(
            "HPS_AI_URL/HPS_AI_TOKEN or OCEAN_AI_BASE_URL/OCEAN_AI_API_KEY "
            "is missing in backend/.env."
        )

    ssl_context = truststore.SSLContext(
        ssl.PROTOCOL_TLS_CLIENT
    )

    return AsyncOpenAI(
        api_key=api_token,
        base_url=base_url,
        timeout=85.0,
        http_client=httpx.AsyncClient(
            verify=ssl_context,
            timeout=85.0,
        ),
    )


async def call_hps_ai(
    messages: list[dict[str, Any]],
    temperature: float | None = None,
    frequency_penalty: float = 0.8,
    presence_penalty: float = 0.3,
) -> str:
    client = get_ocean_client()

    request_payload = {
        "model": HPS_AI_MODEL,
        "messages": messages,
        "frequency_penalty": frequency_penalty,
        "presence_penalty": presence_penalty,
    }

    if temperature is not None:
        request_payload["temperature"] = temperature

    try:
        response = await client.chat.completions.create(
            **request_payload
        )
    except APIStatusError as error:
        raise HpsAiRequestError(
            f"HPS AI returned HTTP {error.status_code}: {error.response.text}"
        ) from error
    except (APIConnectionError, APITimeoutError) as error:
        raise HpsAiRequestError(
            f"Unable to reach HPS AI: {error}"
        ) from error
    except Exception as error:
        raise HpsAiRequestError(
            f"HPS AI request failed: {error}"
        ) from error

    content = response.choices[0].message.content

    if not content:
        raise HpsAiRequestError(
            "HPS AI returned an empty response."
        )

    return content.strip()


async def create_hps_embeddings(
    texts: list[str],
) -> list[list[float]]:
    if not texts:
        return []

    client = get_ocean_client()

    try:
        response = await client.embeddings.create(
            model=HPS_EMBEDDING_MODEL,
            input=texts,
        )
    except APIStatusError as error:
        raise HpsAiRequestError(
            f"HPS embeddings returned HTTP {error.status_code}: {error.response.text}"
        ) from error
    except (APIConnectionError, APITimeoutError) as error:
        raise HpsAiRequestError(
            f"Unable to reach HPS embeddings: {error}"
        ) from error
    except Exception as error:
        raise HpsAiRequestError(
            f"HPS embeddings request failed: {error}"
        ) from error

    return [
        item.embedding
        for item in sorted(
            response.data,
            key=lambda embedding: embedding.index,
        )
    ]
