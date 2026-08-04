# -*- coding: utf-8 -*-
"""FastAPI + WebSocket 뷰어 서버.

파이썬(두뇌)이 push하고 브라우저(three.js)는 받은 대로 그린다 (§9).
- 메시지 파→브: scene_change / state_update / message / approval_request / clarify_request
- 메시지 브→파: user_feedback / clarify_answer / user_utterance
- 재접속 시 즉시 현재 scene + state 스냅샷 push (duration 0) → F5 복구
"""
import asyncio
import json
import os
import queue
import threading
import webbrowser

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.websockets import WebSocket, WebSocketDisconnect
import uvicorn

_HERE = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(_HERE, "static")
MODELS_DIR = os.path.join(os.path.dirname(_HERE), "models")


class PopupViewer:
    def __init__(self, host="127.0.0.1", port=8765):
        self.host, self.port = host, port
        self.snapshot = {"scene": None, "states": []}
        self.feedback_q = queue.Queue()   # HITL-2 승인/피드백
        self.clarify_q = queue.Queue()    # 되묻기 답변
        self.utterance_q = queue.Queue()  # 채팅창에서 입력된 발화
        self.clients = set()
        self.stt_handler = None           # (bytes, mime) -> text. main이 주입
        self.loop = None
        self.pending = None               # 미해결 HITL 요청 (재접속 시 재전송 → F5 데드락 방지)
        self._req_seq = 0                 # 요청 id — 브라우저가 중복 수신을 걸러낸다
        self.app = self._create_app()

    # ---------- FastAPI ----------

    def _create_app(self):
        app = FastAPI()
        app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
        if os.path.isdir(MODELS_DIR):
            app.mount("/models", StaticFiles(directory=MODELS_DIR), name="models")

        @app.get("/")
        def index():
            return FileResponse(os.path.join(STATIC_DIR, "index.html"))

        @app.post("/stt")
        async def stt_endpoint(request: Request):
            """브라우저 push-to-talk 오디오 → 전사 텍스트 (동기 핸들러는 스레드풀에서)."""
            if self.stt_handler is None:
                return JSONResponse({"error": "STT 미설정 (GROQ_API_KEY 확인)"}, status_code=503)
            data = await request.body()
            mime = request.headers.get("content-type", "audio/webm")
            import anyio
            text = await anyio.to_thread.run_sync(self.stt_handler, data, mime)
            return JSONResponse({"text": text})

        @app.websocket("/ws")
        async def ws_endpoint(websocket: WebSocket):
            await websocket.accept()
            self.clients.add(websocket)
            # 접속 즉시 현재 스냅샷 (duration 0 → 애니메이션 없이 그리기)
            await websocket.send_text(json.dumps(
                {"type": "scene_change", "duration": 0, **self.snapshot},
                ensure_ascii=False, default=str))
            if self.pending:   # 승인/되묻기 대기 중 재접속(F5) → 요청을 다시 그린다
                await websocket.send_text(json.dumps(
                    self.pending, ensure_ascii=False, default=str))
            try:
                while True:
                    data = json.loads(await websocket.receive_text())
                    self._on_client_message(data)
            except WebSocketDisconnect:
                pass
            finally:
                self.clients.discard(websocket)

        return app

    def _on_client_message(self, data):
        t = data.get("type")
        if t == "user_feedback":
            self.feedback_q.put(data)
        elif t == "clarify_answer":
            self.clarify_q.put(data)
        elif t == "user_utterance":
            # input: voice(push-to-talk 전사) | typed — 실험 metrics의 입력 구분
            self.utterance_q.put({"text": data.get("text", ""),
                                  "input": data.get("input", "typed")})

    # ---------- 서버 구동 ----------

    def start(self, scene, states, open_browser=True):
        self.snapshot = {"scene": scene, "states": states}

        def run():
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)
            cfg = uvicorn.Config(self.app, host=self.host, port=self.port,
                                 log_level="warning")
            server = uvicorn.Server(cfg)
            self.loop.run_until_complete(server.serve())

        threading.Thread(target=run, daemon=True).start()
        if open_browser:
            webbrowser.open("http://%s:%d" % (self.host, self.port))

    # ---------- push (파이썬 → 브라우저) ----------

    def _broadcast(self, payload):
        if self.loop is None:
            return
        msg = json.dumps(payload, ensure_ascii=False, default=str)

        async def send():
            for ws in list(self.clients):
                try:
                    await ws.send_text(msg)
                except Exception:
                    self.clients.discard(ws)

        asyncio.run_coroutine_threadsafe(send(), self.loop)

    def push_state(self, states, duration=1.2):
        self.snapshot["states"] = states
        self._broadcast({"type": "state_update", "states": states,
                         "duration": duration})

    def push_scene(self, scene, states):
        self.snapshot = {"scene": scene, "states": states}
        self._broadcast({"type": "scene_change", "scene": scene,
                         "states": states, "duration": 0})

    def push_message(self, text):
        self._broadcast({"type": "message", "text": text})

    def chat(self, who, text):
        """채팅창에 말풍선 추가 (who: 'agent' | 'user' | 'system')."""
        self._broadcast({"type": "chat", "who": who, "text": text})

    # ---------- HITL (블로킹 대기) ----------

    def request_approval(self, message):
        with self.feedback_q.mutex:
            self.feedback_q.queue.clear()
        self._req_seq += 1
        self.pending = {"type": "approval_request", "message": message,
                        "req_id": self._req_seq}
        self._broadcast(self.pending)
        data = self.feedback_q.get()   # 사용자가 버튼 누를 때까지 대기
        self.pending = None
        return {"approved": bool(data.get("approved")),
                "feedback": data.get("feedback", "")}

    def ask(self, question, candidates=None):
        with self.clarify_q.mutex:
            self.clarify_q.queue.clear()
        self._req_seq += 1
        self.pending = {"type": "clarify_request", "question": question,
                        "candidates": candidates or [], "req_id": self._req_seq}
        self._broadcast(self.pending)
        answer = self.clarify_q.get().get("answer", "")
        self.pending = None
        return answer
