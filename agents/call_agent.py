import asyncio
import time
import httpx
import config

_VOICERUN_READY = (
    config.VOICERUN_API_KEY not in ("your_voicerun_api_key", "")
    and config.VOICERUN_AGENT_ID not in ("your_deployed_agent_id", "")
)

_NON_RETRIABLE_HTTP = {400, 401, 403, 404, 422}
_TERMINAL_STATUSES = {"completed", "ended", "no_answer", "busy", "failed", "cancelled"}

# Shared dict: telephonyCallId -> result dict (populated by webhook in main.py)
call_results_store: dict[str, dict] = {}


async def call_store(
    store: dict,
    product: str,
    product_details: str = "",
    max_retries: int = config.CALL_MAX_RETRIES,
    retry_delay: int = config.CALL_RETRY_DELAY_SECONDS,
) -> dict:
    if not _VOICERUN_READY:
        return _result(store, "skipped", "voicerun_not_configured")

    headers = {
        "Authorization": f"Bearer {config.VOICERUN_API_KEY}",
        "Content-Type": "application/json",
    }

    payload = {
        "environment": "production",
        "inputType": "phone",
        "inputParameters": {
            "toPhoneNumber": store["phone"],
            "fromPhoneNumber": config.VOICERUN_FROM_PHONE,
            "timeout": 40,
            "statusCallLimit": 3600,
        },
        "parameters": {
            "product": product,
            "product_details": product_details,
            "store_name": store.get("name", "the store"),
        },
    }

    if config.VOICERUN_WEBHOOK_URL:
        payload["inputParameters"]["statusCallbackUrl"] = config.VOICERUN_WEBHOOK_URL

    url = f"{config.VOICERUN_API_BASE}/agents/{config.VOICERUN_AGENT_ID}/sessions/start"

    last_status = "unknown"
    async with httpx.AsyncClient(timeout=30) as client:
        for attempt in range(1, max_retries + 1):
            try:
                resp = await client.post(url, json=payload, headers=headers)

                if resp.status_code in _NON_RETRIABLE_HTTP:
                    return _result(store, "skipped", f"http_{resp.status_code}")

                resp.raise_for_status()
                data = resp.json()

                if not data.get("success", False):
                    return _result(store, "failed", "api_returned_failure")

                call_id = data.get("telephonyCallId") or data.get("sessionId", "")
                session_id = data.get("sessionId") or data.get("id", "")

                # Wait for result via webhook, then fall back to API polling
                outcome = await _wait_for_result(call_id, session_id)
                last_status = outcome.get("status", "unknown")

                if last_status == "completed":
                    return {
                        "store": store,
                        "status": "completed",
                        "outcome": outcome.get("outcome"),
                        "transcript": outcome.get("transcript", ""),
                        "price_info": outcome.get("price_info", ""),
                        "attempts": attempt,
                    }

                if last_status in ("no_answer", "busy") and attempt < max_retries:
                    await asyncio.sleep(retry_delay)

            except httpx.HTTPStatusError as e:
                if e.response.status_code in _NON_RETRIABLE_HTTP:
                    return _result(store, "skipped", f"http_{e.response.status_code}")
                last_status = f"http_error_{e.response.status_code}"
                if attempt < max_retries:
                    await asyncio.sleep(retry_delay)
            except httpx.RequestError:
                last_status = "network_error"
                if attempt < max_retries:
                    await asyncio.sleep(retry_delay)

    return _result(store, "unreachable", last_status)


async def _wait_for_result(call_id: str) -> dict:
    """
    Wait for webhook to populate call_results_store, or timeout.
    If no webhook configured, just wait the timeout and return timeout status.
    """
    if not call_id:
        await asyncio.sleep(config.CALL_POLL_TIMEOUT_SECONDS)
        return {"status": "timeout"}

    deadline = time.monotonic() + config.CALL_POLL_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if call_id in call_results_store:
            return call_results_store.pop(call_id)
        await asyncio.sleep(config.CALL_POLL_INTERVAL_SECONDS)

    return {"status": "timeout"}


async def call_all_stores(
    stores: list[dict],
    product: str,
    product_details: str = "",
    max_parallel: int = config.MAX_STORES_TO_CALL,
) -> list[dict]:
    targets = stores[:max_parallel]
    return await asyncio.gather(*[
        call_store(s, product, product_details) for s in targets
    ])


def _result(store: dict, status: str, outcome: str) -> dict:
    return {
        "store": store,
        "status": status,
        "outcome": outcome,
        "transcript": "",
        "price_info": "",
        "attempts": 0,
    }
