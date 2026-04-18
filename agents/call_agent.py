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
# Shared dict: store_phone -> outcome dict (populated by /outcome endpoint)
phone_outcomes: dict[str, dict] = {}


async def call_store(
    store: dict,
    product: str,
    product_details: str = "",
    max_retries: int = config.CALL_MAX_RETRIES,
    retry_delay: int = config.CALL_RETRY_DELAY_SECONDS,
    on_status=None,
) -> dict:
    async def _emit(status: str):
        if on_status:
            await on_status(store, status)

    if not _VOICERUN_READY:
        await _emit("skipped")
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
            "store_phone": store.get("phone", ""),
            "outcome_webhook_url": config.VOICERUN_WEBHOOK_URL.replace("/webhook", "/outcome") if config.VOICERUN_WEBHOOK_URL else "",
        },
    }

    if config.VOICERUN_WEBHOOK_URL:
        payload["inputParameters"]["statusCallbackUrl"] = config.VOICERUN_WEBHOOK_URL

    url = f"{config.VOICERUN_API_BASE}/agents/{config.VOICERUN_AGENT_ID}/sessions/start"

    await _emit("calling")
    last_status = "unknown"
    async with httpx.AsyncClient(timeout=30) as client:
        for attempt in range(1, max_retries + 1):
            try:
                resp = await client.post(url, json=payload, headers=headers)

                if resp.status_code in _NON_RETRIABLE_HTTP:
                    await _emit("failed")
                    return _result(store, "skipped", f"http_{resp.status_code}")

                resp.raise_for_status()
                data = resp.json()

                if not data.get("success", False):
                    await _emit("failed")
                    return _result(store, "failed", "api_returned_failure")

                call_id = data.get("telephonyCallId") or data.get("sessionId", "")
                session_id = data.get("sessionId") or data.get("id", "")

                await _emit("in_call")
                outcome = await _wait_for_result(call_id, session_id, store.get("phone", ""))
                last_status = outcome.get("status", "unknown")

                if last_status == "completed":
                    await _emit("completed")
                    return {
                        "store": store,
                        "status": "completed",
                        "outcome": outcome.get("outcome"),
                        "transcript": outcome.get("transcript", ""),
                        "price_info": outcome.get("price_info", ""),
                        "attempts": attempt,
                    }

                await _emit(last_status)
                if last_status in ("no_answer", "busy") and attempt < max_retries:
                    await _emit("calling")
                    await asyncio.sleep(retry_delay)

            except httpx.HTTPStatusError as e:
                if e.response.status_code in _NON_RETRIABLE_HTTP:
                    await _emit("failed")
                    return _result(store, "skipped", f"http_{e.response.status_code}")
                last_status = f"http_error_{e.response.status_code}"
                await _emit("failed")
                if attempt < max_retries:
                    await _emit("calling")
                    await asyncio.sleep(retry_delay)
            except httpx.RequestError:
                last_status = "network_error"
                await _emit("failed")
                if attempt < max_retries:
                    await _emit("calling")
                    await asyncio.sleep(retry_delay)

    return _result(store, "unreachable", last_status)


async def _wait_for_result(call_id: str, session_id: str = "", store_phone: str = "") -> dict:
    """
    1. Poll phone_outcomes for direct outcome POSTs from handler (fastest).
    2. Poll call_results_store for Twilio webhook status.
    3. Fall back to VoiceRun API polling.
    """
    if not call_id and not session_id:
        await asyncio.sleep(config.CALL_POLL_TIMEOUT_SECONDS)
        return {"status": "timeout"}

    deadline = time.monotonic() + config.CALL_POLL_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        # Direct outcome from handler POST (has in_stock + price_info)
        if store_phone and store_phone in phone_outcomes:
            o = phone_outcomes.pop(store_phone)
            return {"status": "completed", "outcome": o.get("in_stock", "unknown"), "price_info": o.get("price_info", ""), "transcript": ""}

        # Twilio webhook fast path (has status but no outcome)
        for key in (call_id, session_id):
            if key and key in call_results_store:
                result = call_results_store.pop(key)
                # Grace period: wait for /outcome POST from handler
                if store_phone and result.get("status") == "completed":
                    grace_deadline = time.monotonic() + 5
                    while time.monotonic() < grace_deadline:
                        if store_phone in phone_outcomes:
                            break
                        await asyncio.sleep(0.5)
                if store_phone and store_phone in phone_outcomes:
                    o = phone_outcomes.pop(store_phone)
                    result["outcome"] = o.get("in_stock", result.get("outcome", "unknown"))
                    result["price_info"] = o.get("price_info", "")
                return result

        # API polling fallback
        api_result = await _poll_voicerun_api(call_id, session_id)
        if api_result:
            # Grace period: wait for /outcome POST from handler before returning
            if store_phone and api_result.get("status") == "completed":
                grace_deadline = time.monotonic() + 5
                while time.monotonic() < grace_deadline:
                    if store_phone in phone_outcomes:
                        break
                    await asyncio.sleep(0.5)
            if store_phone and store_phone in phone_outcomes:
                o = phone_outcomes.pop(store_phone)
                api_result["outcome"] = o.get("in_stock", "unknown")
                api_result["price_info"] = o.get("price_info", "")
            return api_result

        await asyncio.sleep(config.CALL_POLL_INTERVAL_SECONDS)

    return {"status": "timeout"}


async def _poll_voicerun_api(call_id: str, session_id: str) -> dict | None:
    """Check VoiceRun sessions for terminal status + outcomes. Returns result or None."""
    try:
        headers = {"Authorization": f"Bearer {config.VOICERUN_API_KEY}"}
        async with httpx.AsyncClient(timeout=10) as client:
            # First find session via list endpoint
            list_url = f"{config.VOICERUN_API_BASE}/agents/{config.VOICERUN_AGENT_ID}/sessions"
            resp = await client.get(list_url, headers=headers)
            if resp.status_code != 200:
                return None
            sessions = resp.json().get("data", [])
            matched_id = None
            raw_status = None
            for s in sessions:
                if s.get("telephonyCallId") == call_id or s.get("id") == session_id:
                    raw_status = s.get("status", "").lower()
                    matched_id = s.get("id")
                    break

            if not matched_id or raw_status not in _TERMINAL_STATUSES:
                return None

            # Fetch individual session for outcomes
            detail_resp = await client.get(
                f"{config.VOICERUN_API_BASE}/sessions/{matched_id}",
                headers=headers,
            )
            outcomes = {}
            if detail_resp.status_code == 200:
                detail = detail_resp.json().get("data", {})
                for o in (detail.get("outcomes") or []):
                    outcomes[o.get("name")] = o.get("value")
                testing = detail.get("testing") or {}
                if isinstance(testing, dict):
                    outcomes.update(testing)

            return {
                "status": _map_voicerun_status(raw_status),
                "outcome": outcomes.get("in_stock", "unknown"),
                "transcript": "",
                "price_info": outcomes.get("price_info", ""),
            }
    except Exception:
        pass
    return None


def _map_voicerun_status(vr_status: str) -> str:
    mapping = {
        "completed": "completed",
        "ended": "completed",
        "no_answer": "no_answer",
        "busy": "busy",
        "failed": "failed",
        "cancelled": "failed",
    }
    return mapping.get(vr_status, vr_status)


async def call_all_stores(
    stores: list[dict],
    product: str,
    product_details: str = "",
    max_parallel: int = config.MAX_STORES_TO_CALL,
    on_status=None,
) -> list[dict]:
    targets = stores[:max_parallel]
    return await asyncio.gather(*[
        call_store(s, product, product_details, on_status=on_status) for s in targets
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
