import asyncio
import httpx
import logging
from datetime import datetime, timezone
from urllib.parse import parse_qs
from fastapi import APIRouter, Request
from services.calls import call_results_store, phone_outcomes
import config

router = APIRouter()


@router.post("/outcome")
async def voicerun_outcome(request: Request):
    """Direct outcome POST from VoiceRun handler when stock decision is made."""
    body = await request.json()
    phone = body.get("store_phone", "")
    if phone:
        phone_outcomes[phone] = {
            "in_stock": body.get("in_stock", "unknown"),
            "price_info": body.get("price_info", ""),
        }
    return {"received": True}


@router.post("/save-outcome")
async def save_outcome(request: Request):
    """Persist call outcome to MongoDB and update phone_outcomes for in-memory race."""
    body = await request.json()
    phone = body.get("store_phone", "")
    if phone:
        phone_outcomes[phone] = {
            "in_stock": body.get("in_stock", "unknown"),
            "price_info": body.get("price_info", ""),
        }
    doc = {
        "request_id": body.get("request_id", ""),
        "store_phone": phone,
        "store_name": body.get("store_name", ""),
        "product": body.get("product", ""),
        "in_stock": body.get("in_stock", "unknown"),
        "price_info": body.get("price_info", ""),
        "created_at": datetime.now(timezone.utc),
    }
    db = request.app.state.mongo[config.MONGODB_DB]
    await db.call_outcomes.insert_one(doc)
    return {"saved": True}


@router.get("/results/{request_id}")
async def get_results(request_id: str, request: Request):
    """Fetch all store call outcomes for a request_id."""
    db = request.app.state.mongo[config.MONGODB_DB]
    cursor = db.call_outcomes.find({"request_id": request_id}, {"_id": 0})
    results = await cursor.to_list(length=100)
    return {"request_id": request_id, "results": results}


@router.post("/webhook")
async def voicerun_webhook(request: Request):
    """Receives Twilio status callbacks fired by VoiceRun when call ends."""
    raw = await request.body()
    try:
        body = await request.json()
    except Exception:
        parsed = parse_qs(raw.decode("utf-8", errors="replace"))
        body = {k: v[0] if len(v) == 1 else v for k, v in parsed.items()}

    call_sid = body.get("CallSid") or body.get("telephonyCallId") or body.get("sessionId", "")
    raw_status = body.get("CallStatus") or body.get("status", "")
    mapped_status = _map_voicerun_status(raw_status)

    if call_sid and mapped_status in ("completed", "no_answer", "busy", "failed"):
        outcomes = await _fetch_voicerun_outcomes(call_sid)
        call_results_store[call_sid] = {
            "status": mapped_status,
            "outcome": outcomes.get("in_stock", "unknown"),
            "transcript": "",
            "price_info": outcomes.get("price_info", ""),
        }

    return {"received": True}


async def _fetch_voicerun_outcomes(telephony_call_id: str) -> dict:
    """Poll VoiceRun until session is completed, then extract outcomes."""
    _TERMINAL = {"completed", "ended", "failed", "cancelled", "no_answer", "busy"}
    headers = {"Authorization": f"Bearer {config.VOICERUN_API_KEY}"}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            session_id = None
            for _ in range(20):
                r = await client.get(
                    f"https://api.voicerun.com/v1/agents/{config.VOICERUN_AGENT_ID}/sessions",
                    headers=headers,
                )
                if r.status_code != 200:
                    break
                for s in r.json().get("data", []):
                    if s.get("telephonyCallId") == telephony_call_id:
                        session_id = s["id"]
                        if s.get("status", "").lower() in _TERMINAL:
                            break
                        session_id = None
                if session_id:
                    break
                await asyncio.sleep(2)

            if not session_id:
                return {}

            r2 = await client.get(f"https://api.voicerun.com/v1/sessions/{session_id}", headers=headers)
            if r2.status_code == 200:
                d = r2.json().get("data", {})
                logging.warning(f"FINAL SESSION: testing={d.get('testing')} outcomes={d.get('outcomes')} events_count={len(d.get('events') or [])}")
                outcomes = {}
                for o in (d.get("outcomes") or []):
                    outcomes[o.get("name")] = o.get("value")
                testing = d.get("testing") or {}
                if isinstance(testing, dict):
                    outcomes.update(testing)
                return outcomes
    except Exception:
        pass
    return {}


def _map_voicerun_status(vr_status: str) -> str:
    mapping = {
        "completed": "completed",
        "ended": "completed",
        "no_answer": "no_answer",
        "busy": "busy",
        "failed": "failed",
        "cancelled": "failed",
    }
    return mapping.get(vr_status.lower(), vr_status)
