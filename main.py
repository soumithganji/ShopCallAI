import uuid
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from orchestrator import new_session, process_message
from agents.location_agent import reverse_geocode
from agents.call_agent import call_results_store, phone_outcomes

app = FastAPI(title="Shopping Agent")
app.mount("/static", StaticFiles(directory="static"), name="static")

_sessions: dict[str, dict] = {}
_progress: dict[str, dict] = {}  # session_id -> {phase, stores: [{name,address,phone,status}]}


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

    progress = _progress.setdefault(sid, {"phase": "idle", "stores": []})

    try:
        reply = await process_message(req.message, session, progress)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    return MessageResponse(session_id=sid, reply=reply)


@app.post("/outcome")
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


@app.get("/progress/{session_id}")
async def get_progress(session_id: str):
    return _progress.get(session_id, {"phase": "idle", "stores": []})


@app.delete("/chat/{session_id}")
async def clear_session(session_id: str):
    _sessions.pop(session_id, None)
    _progress.pop(session_id, None)
    return {"status": "cleared"}


@app.post("/webhook")
async def voicerun_webhook(request: Request):
    """Receives Twilio status callbacks (fired by VoiceRun when call ends)."""
    raw = await request.body()
    try:
        body = await request.json()
    except Exception:
        from urllib.parse import parse_qs
        parsed = parse_qs(raw.decode("utf-8", errors="replace"))
        body = {k: v[0] if len(v) == 1 else v for k, v in parsed.items()}

    # Twilio form-encoded: CallSid = telephonyCallId, CallStatus = status
    call_sid = body.get("CallSid") or body.get("telephonyCallId") or body.get("sessionId", "")
    raw_status = body.get("CallStatus") or body.get("status", "")
    mapped_status = _map_voicerun_status(raw_status)

    if call_sid and mapped_status in ("completed", "no_answer", "busy", "failed"):
        # Fetch VoiceRun session to get conversation outcomes
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
    import asyncio, httpx, config as cfg, logging
    _TERMINAL = {"completed", "ended", "failed", "cancelled", "no_answer", "busy"}
    headers = {"Authorization": f"Bearer {cfg.VOICERUN_API_KEY}"}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            # Find session id from telephonyCallId
            session_id = None
            for _ in range(20):  # poll up to ~40s
                r = await client.get(
                    f"https://api.voicerun.com/v1/agents/{cfg.VOICERUN_AGENT_ID}/sessions",
                    headers=headers,
                )
                if r.status_code != 200:
                    break
                for s in r.json().get("data", []):
                    if s.get("telephonyCallId") == telephony_call_id:
                        session_id = s["id"]
                        if s.get("status", "").lower() in _TERMINAL:
                            break
                        session_id = None  # still in progress
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
  #app { width: 520px; background: white; border-radius: 12px; box-shadow: 0 4px 24px rgba(0,0,0,0.1); display: flex; flex-direction: column; height: 680px; }
  #header { padding: 16px 20px; border-bottom: 1px solid #eee; font-weight: 600; font-size: 15px; display: flex; align-items: center; gap: 8px; }
  #loc-badge { font-size: 12px; font-weight: 400; color: #666; margin-left: auto; }
  #messages { flex: 1; overflow-y: auto; padding: 16px; display: flex; flex-direction: column; gap: 10px; }
  .msg { max-width: 85%; padding: 10px 14px; border-radius: 16px; font-size: 14px; line-height: 1.5; white-space: pre-wrap; }
  .user { align-self: flex-end; background: #0066ff; color: white; border-bottom-right-radius: 4px; }
  .agent { align-self: flex-start; background: #f0f0f0; color: #111; border-bottom-left-radius: 4px; }
  #input-row { display: flex; gap: 8px; padding: 12px 16px; border-top: 1px solid #eee; }
  #input { flex: 1; border: 1px solid #ddd; border-radius: 8px; padding: 10px 14px; font-size: 14px; outline: none; }
  #input:focus { border-color: #0066ff; }
  #send { background: #0066ff; color: white; border: none; border-radius: 8px; padding: 10px 18px; font-size: 14px; cursor: pointer; }
  #send:disabled { opacity: 0.5; cursor: not-allowed; }

  /* Store cards */
  .stores-panel { align-self: flex-start; width: 100%; max-width: 85%; display: flex; flex-direction: column; gap: 6px; }
  .store-card { background: white; border: 1px solid #e0e0e0; border-radius: 10px; padding: 10px 12px; display: flex; align-items: center; gap: 10px; font-size: 13px; transition: border-color 0.3s; }
  .store-card.calling { border-color: #f59e0b; background: #fffbeb; }
  .store-card.in_call { border-color: #3b82f6; background: #eff6ff; }
  .store-card.completed { border-color: #10b981; background: #f0fdf4; }
  .store-card.in_stock { border-color: #10b981; background: #f0fdf4; }
  .store-card.out_of_stock { border-color: #6b7280; background: #f9fafb; }
  .store-card.failed, .store-card.no_answer, .store-card.busy, .store-card.unreachable, .store-card.timeout { border-color: #ef4444; background: #fef2f2; }
  .store-info { flex: 1; min-width: 0; }
  .store-name { font-weight: 600; color: #111; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .store-addr { color: #666; font-size: 12px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .store-badge { flex-shrink: 0; font-size: 11px; font-weight: 600; padding: 3px 8px; border-radius: 20px; white-space: nowrap; }
  .badge-pending { background: #f3f4f6; color: #6b7280; }
  .badge-calling { background: #fef3c7; color: #92400e; }
  .badge-in_call { background: #dbeafe; color: #1e40af; }
  .badge-completed { background: #d1fae5; color: #065f46; }
  .badge-in_stock { background: #d1fae5; color: #065f46; }
  .badge-out_of_stock { background: #f3f4f6; color: #374151; }
  .badge-failed, .badge-no_answer, .badge-busy, .badge-unreachable, .badge-timeout { background: #fee2e2; color: #991b1b; }
  .badge-skipped { background: #f3f4f6; color: #6b7280; }
  @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:0.5} }
  .pulsing { animation: pulse 1.2s ease-in-out infinite; }
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
  let pollTimer = null;
  let storesPanelEl = null;

  if (navigator.geolocation) {
    navigator.geolocation.getCurrentPosition(
      pos => { userLat = pos.coords.latitude; userLng = pos.coords.longitude; locBadge.textContent = '📍 Location detected'; },
      () => { locBadge.textContent = '📍 Location unavailable'; },
      { timeout: 8000, maximumAge: 300000 }
    );
  }

  const STATUS_LABEL = {
    pending: 'Pending', calling: 'Calling...', in_call: 'In call',
    completed: 'Reached', in_stock: 'In Stock', out_of_stock: 'Out of Stock',
    failed: 'Failed', no_answer: 'No Answer',
    busy: 'Busy', unreachable: 'Unreachable', timeout: 'Timeout', skipped: 'Skipped',
  };
  const STATUS_ICON = {
    pending: '⏳', calling: '📞', in_call: '🔊',
    completed: '✅', in_stock: '✅', out_of_stock: '✗',
    failed: '❌', no_answer: '📵',
    busy: '📵', unreachable: '❌', timeout: '⏱', skipped: '–',
  };

  function addMsg(text, role) {
    const div = document.createElement('div');
    div.className = 'msg ' + role;
    div.textContent = text;
    messagesEl.appendChild(div);
    messagesEl.scrollTop = messagesEl.scrollHeight;
    return div;
  }

  function renderStores(stores) {
    if (!storesPanelEl) {
      storesPanelEl = document.createElement('div');
      storesPanelEl.className = 'stores-panel';
      messagesEl.appendChild(storesPanelEl);
    }
    storesPanelEl.innerHTML = '';
    for (const s of stores) {
      const card = document.createElement('div');
      const st = s.status || 'pending';
      card.className = 'store-card ' + st;
      const isPulsing = st === 'calling' || st === 'in_call';
      card.innerHTML = `
        <div class="store-info">
          <div class="store-name">${s.name}</div>
          <div class="store-addr">${s.address}</div>
        </div>
        <span class="store-badge badge-${st} ${isPulsing ? 'pulsing' : ''}">${STATUS_ICON[st] || ''} ${STATUS_LABEL[st] || st}</span>
      `;
      storesPanelEl.appendChild(card);
    }
    messagesEl.scrollTop = messagesEl.scrollHeight;
  }

  function startPolling(sid) {
    stopPolling();
    pollTimer = setInterval(async () => {
      try {
        const r = await fetch('/progress/' + sid);
        const data = await r.json();
        if (data.stores && data.stores.length > 0) {
          renderStores(data.stores);
        }
      } catch (_) {}
    }, 1500);
  }

  function stopPolling() {
    if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
  }

  async function send() {
    const text = inputEl.value.trim();
    if (!text) return;
    inputEl.value = '';
    sendBtn.disabled = true;
    storesPanelEl = null;

    addMsg(text, 'user');
    const loading = addMsg('...', 'agent');

    // Need a session_id to poll before /chat returns — pre-generate one
    if (!sessionId) sessionId = crypto.randomUUID();
    startPolling(sessionId);

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

      // Keep polling until all stores reach terminal state (only if stores exist)
      const TERMINAL = new Set(['completed','in_stock','out_of_stock','failed','no_answer','busy','unreachable','timeout','skipped']);
      const allDone = stores => stores.length > 0 && stores.every(s => TERMINAL.has(s.status));
      stopPolling();
      const prCheck = await fetch('/progress/' + sessionId);
      const pdCheck = await prCheck.json();
      if (pdCheck.stores && pdCheck.stores.length > 0) {
        renderStores(pdCheck.stores);
        if (!allDone(pdCheck.stores)) {
          startPolling(sessionId);
          await new Promise(resolve => {
            const check = setInterval(async () => {
              try {
                const pr = await fetch('/progress/' + sessionId);
                const pd = await pr.json();
                if (pd.stores && pd.stores.length > 0) renderStores(pd.stores);
                if (allDone(pd.stores || [])) { clearInterval(check); stopPolling(); resolve(); }
              } catch(_) {}
            }, 1000);
            setTimeout(() => { clearInterval(check); stopPolling(); resolve(); }, 30000);
          });
        }
      }

      loading.textContent = data.reply;
    } catch (e) {
      stopPolling();
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
