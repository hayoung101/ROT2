# -*- coding: utf-8 -*-
"""FastAPI + WebSocket 뷰어 서버.

파이썬(두뇌)이 push하고 브라우저(three.js)는 받은 대로 그린다 (§9).
- 메시지 파→브: scene_change / state_update / chat / approval_request / clarify_request
- 메시지 브→파: user_feedback / clarify_answer
- 재접속 시 즉시 현재 scene + state 스냅샷 push (duration 0) → F5 복구
"""
import asyncio
import json
import os
import queue
import threading
import webbrowser

from fastapi import FastAPI
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
                # 끊긴 소켓을 빼야 clients가 '살아 있는 브라우저'의 진실이 된다 —
                # 안 빼면 _wait이 영원히 기다린다(HITL 영구 블로킹).
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

    def chat(self, who, text):
        """채팅창에 말풍선 추가 (who: 'agent' | 'user' | 'system')."""
        self._broadcast({"type": "chat", "who": who, "text": text})

    # ---------- HITL (블로킹 대기) ----------

    def _wait(self, q, poll=0.2):
        """큐를 기다리되 브라우저가 모두 끊기면 포기한다 (영구 블로킹 방지).

        q.get()을 그냥 쓰면 참가자가 창을 닫은 순간 파이썬이 영원히 멈춘다 — 세션이
        통째로 날아가므로 실험에서 가장 비싼 실패다. pending이 남아 있으므로 재접속(F5)
        하면 요청이 다시 그려지고, 그 사이 클라이언트가 0이면 None으로 빠져나온다.
        반환 None = 중단(aborted)."""
        while True:
            if not self.clients:
                return None
            try:
                return q.get(timeout=poll)
            except queue.Empty:
                continue

    def request_approval(self, message):
        """HITL-1/HITL-2 승인 요청 (블로킹). 브라우저가 없으면 aborted로 즉시 반환."""
        with self.feedback_q.mutex:
            self.feedback_q.queue.clear()
        self._req_seq += 1
        self.pending = {"type": "approval_request", "message": message,
                        "req_id": self._req_seq}
        self._broadcast(self.pending)
        data = self._wait(self.feedback_q)   # 사용자가 버튼 누를 때까지 대기
        self.pending = None
        if data is None:                     # 대기 중 브라우저 끊김 → 호출부가 롤백한다
            return {"approved": False, "feedback": "", "aborted": True}
        return {"approved": bool(data.get("approved")),
                "feedback": data.get("feedback", ""), "aborted": False}

    def ask(self, question, candidates=None):
        """되묻기 (블로킹). 브라우저가 없으면 빈 문자열 — main이 무응답/취소로 읽는다."""
        with self.clarify_q.mutex:
            self.clarify_q.queue.clear()
        self._req_seq += 1
        self.pending = {"type": "clarify_request", "question": question,
                        "candidates": candidates or [], "req_id": self._req_seq}
        self._broadcast(self.pending)
        data = self._wait(self.clarify_q)
        self.pending = None
        return "" if data is None else data.get("answer", "")
