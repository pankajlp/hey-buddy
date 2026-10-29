import asyncio
import json
import logging
import os
import re
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from src.agents.orchestrator import Orchestrator
from src.config import config, env
from src.core.ollama_client import OllamaClient
from src.system import transcriber
from src.system.mac_bridge import MacBridge
from src.system.voice import VoiceEngine
from src.web.mascot import MASCOT_FACES

logger = logging.getLogger("heybuddy.web")

app = FastAPI(title="Hey Buddy Local Companion")

# Only the HUD page served by this app may talk to it. Checking Host blocks DNS
# rebinding; checking Origin blocks other websites open in your browser.
ALLOWED_HOSTS = {f"127.0.0.1:{config.web_port}", f"localhost:{config.web_port}"}
ALLOWED_ORIGINS = {f"http://{h}" for h in ALLOWED_HOSTS}

class LocalOriginGuard:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
            host = headers.get("host", "")
            origin = headers.get("origin")
            # Browsers always send Origin on WebSocket handshakes, so require it there.
            origin_ok = origin in ALLOWED_ORIGINS if (origin or scope["type"] == "websocket") else True
            if host not in ALLOWED_HOSTS or not origin_ok:
                logger.warning(f"Rejected {scope['type']} request: host={host!r} origin={origin!r}")
                if scope["type"] == "websocket":
                    await send({"type": "websocket.close", "code": 1008})
                else:
                    await send({"type": "http.response.start", "status": 403,
                                "headers": [(b"content-type", b"text/plain")]})
                    await send({"type": "http.response.body", "body": b"Forbidden"})
                return
        await self.app(scope, receive, send)

app.add_middleware(LocalOriginGuard)

# Seconds to wait for the user to approve a sensitive action before denying it
CONFIRM_TIMEOUT = 120

STATIC_DIR = Path(__file__).parent / "static"
STATIC_DIR.mkdir(parents=True, exist_ok=True)

ollama = OllamaClient()

def _system_speech_language() -> str:
    """The Mac's preferred language as a BCP-47 tag (e.g. 'en-IN'), for speech recognition."""
    try:
        out = subprocess.run(["defaults", "read", "-g", "AppleLanguages"], capture_output=True, text=True, timeout=3).stdout
        match = re.search(r'"?([a-z]{2}(?:-[A-Za-z]{2,4})?)"?', out.split("(", 1)[-1])
        if match:
            return match.group(1)
    except Exception:
        pass
    return "en-US"

SPEECH_LANGUAGE = env("SPEECH_LANG") or _system_speech_language()

class ChatRequest(BaseModel):
    message: str
    mode: str = "auto"  # 'auto' (router picks), or a fixed 'buddy', 'agent', 'coder'
    speak: bool = True

@app.get("/")
async def get_index():
    index_file = STATIC_DIR / "index.html"
    if index_file.exists():
        return FileResponse(index_file)
    return HTMLResponse("<h1>Hey Buddy Companion</h1>")

@app.get("/api/status")
async def get_status():
    ctx = MacBridge.get_screen_context()
    models = ollama.list_models()
    return {
        "ollama_online": ollama.is_alive(),
        "models": models,
        "active_app": ctx.active_app,
        "window_title": ctx.active_window,
        "screen_size": f"{ctx.screen_width}x{ctx.screen_height}",
        "mouse": f"{ctx.mouse_x}, {ctx.mouse_y}",
        "buddy_model": config.default_buddy_model,
        "agent_model": config.default_agent_model,
        "coder_model": config.default_coder_model,
        "voice_enabled": config.voice_enabled,
        "speech_lang": SPEECH_LANGUAGE,
    }

@app.post("/api/speak")
async def speak_text(data: Dict[str, str]):
    text = data.get("text", "")
    if text:
        VoiceEngine.speak(text)
    return {"status": "ok"}

@app.on_event("startup")
async def load_speech_model():
    # Load Whisper in the background so the first voice request isn't slow
    async def warm():
        try:
            await asyncio.to_thread(transcriber.warm_up)
        except Exception as e:
            logger.error(f"Whisper failed to load: {e}")
    asyncio.create_task(warm())

MAX_UTTERANCE_BYTES = 16000 * 2 * 30  # 30 s of 16 kHz 16-bit audio

@app.post("/api/transcribe")
async def transcribe(request: Request):
    """Transcribe one utterance: raw mono 16 kHz 16-bit PCM in the request body."""
    pcm = await request.body()
    if len(pcm) > MAX_UTTERANCE_BYTES:
        pcm = pcm[:MAX_UTTERANCE_BYTES]
    try:
        result = await asyncio.to_thread(transcriber.transcribe_pcm16, pcm)
    except Exception as e:
        logger.error(f"Transcription failed: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)
    dropped = f" (discarded: {result['dropped']})" if result.get("dropped") else ""
    logger.info(f"Transcribed {len(pcm) / 32000:.1f}s of audio in {result['seconds']}s{dropped}")
    return result

@app.post("/api/clientlog")
async def client_log(data: Dict[str, str]):
    """Diagnostic events from the HUD page (e.g. hands-free listening state)."""
    logger.info(f"[page] {data.get('msg', '')[:300]}")
    return {"status": "ok"}

def _mode_and_message(mode: str, message: str):
    """'agent: do X' typed in any mode forces the general agent."""
    if message.lower().startswith("agent:"):
        return "agent", message[6:].strip()
    return mode or "auto", message

def without_speech_tool(agent) -> None:
    # The page reads replies aloud itself. Speech from the Mac's speakers (the 'say' tool)
    # would also be picked up by the hands-free mic, e.g. as a spoken "yes".
    agent.tools = [t for t in agent.tools if t["function"]["name"] != "speak_to_user"]

@app.post("/api/chat")
async def http_chat(req: ChatRequest):
    """
    Robust HTTP chat endpoint that works even if WebSockets are blocked or disconnected.
    """
    user_msg = req.message.strip()
    if not user_msg:
        return JSONResponse({"error": "Empty message"}, status_code=400)

    thoughts = []
    actions = []

    def on_thought(t: str):
        thoughts.append(t)

    def on_action(name: str, args: dict):
        actions.append({"name": name, "args": args})

    routes = []
    mode, clean_msg = _mode_and_message(req.mode, user_msg)
    orchestrator = Orchestrator(
        on_route=lambda key, label, method: routes.append(label),
        tool_filter=without_speech_tool,
        on_thought=on_thought,
        on_action=on_action,
    )
    try:
        # No confirmation channel over plain HTTP: sensitive tools are blocked in safe mode.
        response = await asyncio.to_thread(orchestrator.handle, clean_msg, mode, req.speak)
        return {
            "content": response,
            "thoughts": thoughts,
            "actions": actions,
            "route": " → ".join(routes),
            "mascot": MASCOT_FACES["happy"],
            "speak_text": response if req.speak else "",
        }
    except Exception as e:
        logger.error(f"HTTP Chat error: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)

# Only one open page listens for "Hey Buddy" at a time; otherwise every open page would
# hear the same request and run it again. connection id -> function that notifies that page.
_listeners: Dict[str, Any] = {}
_active_listener: Optional[str] = None

def _claim_microphone(conn_id: str) -> None:
    global _active_listener
    previous = _active_listener
    _active_listener = conn_id
    if previous and previous != conn_id and previous in _listeners:
        logger.info("Hands-free moved to a newer page; the older one stops listening")
        _listeners[previous]("mic_taken", {})

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    conn_id = uuid.uuid4().hex

    # Thread-safe event queue: agent callbacks in worker threads put events here,
    # and a single dedicated coroutine sends them sequentially over websocket.
    send_queue: asyncio.Queue = asyncio.Queue()

    async def queue_writer():
        try:
            while True:
                event = await send_queue.get()
                if event is None:
                    break
                await websocket.send_text(json.dumps(event))
                send_queue.task_done()
        except Exception as e:
            logger.debug(f"Queue writer closed: {e}")

    writer_task = asyncio.create_task(queue_writer())
    loop = asyncio.get_running_loop()

    def sync_send(event_type: str, payload: Any):
        loop.call_soon_threadsafe(send_queue.put_nowait, {"type": event_type, "data": payload})

    _listeners[conn_id] = sync_send

    # Pending approval requests, keyed by request id
    pending_confirms: Dict[str, asyncio.Future] = {}
    closed = False

    async def ask_user(request: Dict[str, Any]) -> bool:
        if closed:
            return False
        request_id = uuid.uuid4().hex
        fut = loop.create_future()
        pending_confirms[request_id] = fut
        await send_queue.put({"type": "confirm_request", "data": {"id": request_id, **request}})
        try:
            return bool(await asyncio.wait_for(fut, timeout=CONFIRM_TIMEOUT))
        except asyncio.TimeoutError:
            await send_queue.put({"type": "confirm_expired", "data": {"id": request_id}})
            return False
        finally:
            pending_confirms.pop(request_id, None)

    def ask_user_blocking(request: Dict[str, Any]) -> bool:
        # Called from the agent's worker thread; blocks it until the user answers.
        return asyncio.run_coroutine_threadsafe(ask_user(request), loop).result()

    def confirm(tool_name: str, args: Dict[str, Any], reason: Optional[str]) -> bool:
        # One action; high-risk ones must be approved by click, never by voice
        return ask_user_blocking({"kind": "action", "name": tool_name, "args": args,
                                  "reason": reason, "voice_ok": reason is None})

    def confirm_task(plan: str, tool_name: str, args: Dict[str, Any]) -> bool:
        # The whole task; can be approved by voice ("yes, go ahead")
        return ask_user_blocking({"kind": "task", "plan": plan, "name": tool_name, "args": args,
                                  "voice_ok": True})

    callbacks = dict(
        on_thought=lambda t: sync_send("thought", t),
        on_action=lambda name, args: sync_send("action", {"name": name, "args": args}),
        on_observation=lambda o: sync_send("observation", o),
        confirm=confirm,
        confirm_task=confirm_task,
    )
    orchestrator = Orchestrator(
        on_route=lambda key, label, method: sync_send("route", {"agent": key, "label": label, "method": method}),
        tool_filter=without_speech_tool,
        **callbacks,
    )

    # Chat messages run one at a time; the reader below keeps handling approvals meanwhile.
    chat_queue: asyncio.Queue = asyncio.Queue()

    async def chat_worker():
        while True:
            data = await chat_queue.get()
            user_msg = data.get("message", "").strip()
            speak = data.get("speak", True)
            mode, clean_msg = _mode_and_message(data.get("mode", "auto"), user_msg)

            sync_send("status", {"state": "working", "mascot": MASCOT_FACES["agent_working"]})

            # Run in worker thread so event loop remains responsive
            started = time.monotonic()
            logger.info(f"Request received ({mode} mode)")
            try:
                response = await asyncio.to_thread(orchestrator.handle, clean_msg, mode, speak)
                logger.info(f"Request answered in {time.monotonic() - started:.1f}s")
                sync_send("response", {
                    "content": response,
                    "mode": mode,
                    "mascot": MASCOT_FACES["happy"],
                    "speak_text": response if speak else "",
                })
            except Exception as e:
                logger.error(f"Agent execution error: {e}")
                sync_send("response", {
                    "content": f"Sorry, encountered an error: {e}",
                    "mode": mode,
                    "mascot": MASCOT_FACES["confused"],
                })

            sync_send("status", {"state": "idle", "mascot": MASCOT_FACES["idle"]})

    worker_task = asyncio.create_task(chat_worker())

    try:
        while True:
            data = json.loads(await websocket.receive_text())

            if data.get("type") == "hands_free":
                if data.get("on"):
                    _claim_microphone(conn_id)
                continue

            if data.get("type") == "confirm_response":
                fut = pending_confirms.get(data.get("id", ""))
                if fut and not fut.done():
                    fut.set_result(data.get("approved") is True)
                continue

            if data.get("message", "").strip():
                await chat_queue.put(data)

    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
    finally:
        closed = True
        _listeners.pop(conn_id, None)
        # Deny anything still waiting for approval so the agent thread can finish
        for fut in pending_confirms.values():
            if not fut.done():
                fut.set_result(False)
        worker_task.cancel()
        await send_queue.put(None)
        writer_task.cancel()
