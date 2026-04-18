import json
import re
from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionMessage
import config

_client = AsyncOpenAI(
    base_url=config.BASETEN_BASE_URL,
    api_key=config.BASETEN_API_KEY,
)


async def chat(
    messages: list[dict],
    tools: list[dict] | None = None,
    temperature: float = 0.3,
) -> ChatCompletionMessage:
    kwargs: dict = {
        "model": config.BASETEN_MODEL,
        "messages": messages,
        "temperature": temperature,
    }
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = "auto"

    resp = await _client.chat.completions.create(**kwargs)
    return resp.choices[0].message


async def chat_json(messages: list[dict], temperature: float = 0.1) -> dict | list:
    for attempt in range(3):
        msg = await chat(messages, temperature=temperature)
        text = (msg.content or "").strip()

        if not text:
            continue

        # Extract JSON from markdown code fences
        if "```" in text:
            fence_match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
            if fence_match:
                text = fence_match.group(1).strip()

        # Extract first JSON object or array from text
        json_match = re.search(r"(\{[\s\S]*\}|\[[\s\S]*\])", text)
        if json_match:
            text = json_match.group(1)

        try:
            return json.loads(text)
        except json.JSONDecodeError:
            continue

    return {}
