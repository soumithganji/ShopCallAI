import uuid
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from orchestrator import new_session, process_message
from agents.location_agent import reverse_geocode
from agents.call_agent import call_results_store

app = FastAPI(title="Shopping Agent")
app.mount("/static", StaticFiles(directory="static"), name="static")

_sessions: dict[str, dict] = {}


class MessageRequest(BaseModel):
    session_id: str | None = None
    message: str
    lat: float | None = None
    lng: float | None = None


class MessageResponse(BaseModel):
    session_id: str
    reply: str


@app.post("/chat", response_model=MessageResponse)
async def chat(req: MessageRequest):
    sid = req.session_id or str(uuid.uuid4())
    if sid not in _sessions:
        _sessions[sid] = new_session()
    session = _sessions[sid]

    # Inject browser geolocation on first message if location not yet set
    if req.lat is not None and req.lng is not None and not session["location"]:
        loc = reverse_geocode(req.lat, req.lng)
        if loc:
            session["location"] = loc

    try:
        reply = await process_message(req.message, session)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    return MessageResponse(session_id=sid, reply=reply)


@app.delete("/chat/{session_id}")
async def clear_session(session_id: str):
    _sessions.pop(session_id, None)
    return {"status": "cleared"}


@app.post("/webhook")
async def voicerun_webhook(request: Request):
    """Receives VoiceRun call status callbacks."""
    body = await request.json()
    call_id = body.get("telephonyCallId") or body.get("sessionId", "")
    if call_id:
        call_results_store[call_id] = {
            "status": _map_voicerun_status(body.get("status", "")),
            "outcome": body.get("outcome"),
            "transcript": body.get("transcript", ""),
            "price_info": body.get("parameters", {}).get("price_info", ""),
        }
    return {"received": True}


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


@app.get("/", response_class=HTMLResponse)
async def root():
    return _chat_ui()


def _chat_ui() -> str:
    return """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Shopping Agent</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: system-ui, sans-serif; background: #f5f5f5; display: flex; justify-content: center; align-items: center; min-height: 100vh; }
  #app { width: 480px; background: white; border-radius: 12px; box-shadow: 0 4px 24px rgba(0,0,0,0.1); display: flex; flex-direction: column; height: 640px; }
  #header { padding: 16px 20px; border-bottom: 1px solid #eee; font-weight: 600; font-size: 15px; display: flex; align-items: center; gap: 8px; }
  #loc-badge { font-size: 12px; font-weight: 400; color: #666; margin-left: auto; }
  #messages { flex: 1; overflow-y: auto; padding: 16px; display: flex; flex-direction: column; gap: 10px; }
  .msg { max-width: 80%; padding: 10px 14px; border-radius: 16px; font-size: 14px; line-height: 1.5; white-space: pre-wrap; }
  .user { align-self: flex-end; background: #0066ff; color: white; border-bottom-right-radius: 4px; }
  .agent { align-self: flex-start; background: #f0f0f0; color: #111; border-bottom-left-radius: 4px; }
  #input-row { display: flex; gap: 8px; padding: 12px 16px; border-top: 1px solid #eee; }
  #input { flex: 1; border: 1px solid #ddd; border-radius: 8px; padding: 10px 14px; font-size: 14px; outline: none; }
  #input:focus { border-color: #0066ff; }
  #send { background: #0066ff; color: white; border: none; border-radius: 8px; padding: 10px 18px; font-size: 14px; cursor: pointer; }
  #send:disabled { opacity: 0.5; cursor: not-allowed; }
</style>
</head>
<body>
<div id="app">
  <div id="header">🛍 Shopping Agent <span id="loc-badge"></span></div>
  <div id="messages">
    <div class="msg agent">Hi! What are you looking for? I'll find local stores and call them for you.</div>
  </div>
  <div id="input-row">
    <input id="input" type="text" placeholder="e.g. I need duct tape" autocomplete="off" />
    <button id="send">Send</button>
  </div>
</div>
<script>
  const messagesEl = document.getElementById('messages');
  const inputEl = document.getElementById('input');
  const sendBtn = document.getElementById('send');
  const locBadge = document.getElementById('loc-badge');
  let sessionId = null;
  let userLat = null, userLng = null;

  // Request geolocation immediately on load
  if (navigator.geolocation) {
    navigator.geolocation.getCurrentPosition(
      pos => {
        userLat = pos.coords.latitude;
        userLng = pos.coords.longitude;
        locBadge.textContent = '📍 Location detected';
      },
      () => {
        locBadge.textContent = '📍 Location unavailable';
      },
      { timeout: 8000, maximumAge: 300000 }
    );
  }

  function addMsg(text, role) {
    const div = document.createElement('div');
    div.className = 'msg ' + role;
    div.textContent = text;
    messagesEl.appendChild(div);
    messagesEl.scrollTop = messagesEl.scrollHeight;
    return div;
  }

  async function send() {
    const text = inputEl.value.trim();
    if (!text) return;
    inputEl.value = '';
    sendBtn.disabled = true;

    addMsg(text, 'user');
    const loading = addMsg('...', 'agent');

    try {
      const body = { session_id: sessionId, message: text };
      if (userLat !== null) { body.lat = userLat; body.lng = userLng; }

      const res = await fetch('/chat', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(body),
      });
      const data = await res.json();
      sessionId = data.session_id;
      loading.textContent = data.reply;
    } catch (e) {
      loading.textContent = 'Error: ' + e.message;
    }
    sendBtn.disabled = false;
    inputEl.focus();
  }

  sendBtn.addEventListener('click', send);
  inputEl.addEventListener('keydown', e => { if (e.key === 'Enter') send(); });
  inputEl.focus();
</script>
</body>
</html>"""
