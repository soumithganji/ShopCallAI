import uuid
import logging
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from services.geo import reverse_geocode
from orchestrator import new_session, process_message
import state

router = APIRouter()


class MessageRequest(BaseModel):
    session_id: str | None = None
    message: str
    lat: float | None = None
    lng: float | None = None


class MessageResponse(BaseModel):
    session_id: str
    reply: str


@router.post("/chat", response_model=MessageResponse)
async def chat(req: MessageRequest):
    sid = req.session_id or str(uuid.uuid4())
    if sid not in state._sessions:
        state._sessions[sid] = new_session()
    session = state._sessions[sid]

    if req.lat is not None and req.lng is not None and not session["location"]:
        logging.warning(f"[GEO] got lat={req.lat} lng={req.lng}")
        loc = reverse_geocode(req.lat, req.lng)
        logging.warning(f"[GEO] reverse_geocode returned: {loc}")
        if loc:
            session["location"] = loc
    else:
        logging.warning(f"[GEO] no lat/lng in request: lat={req.lat} lng={req.lng} location_set={session['location'] is not None}")

    progress = state._progress.setdefault(sid, {"phase": "idle", "stores": []})

    try:
        reply = await process_message(req.message, session, progress)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    return MessageResponse(session_id=sid, reply=reply)


@router.get("/progress/{session_id}")
async def get_progress(session_id: str):
    return state._progress.get(session_id, {"phase": "idle", "stores": []})


@router.delete("/chat/{session_id}")
async def clear_session(session_id: str):
    state._sessions.pop(session_id, None)
    state._progress.pop(session_id, None)
    return {"status": "cleared"}
