# -*- coding: utf-8 -*-
import json

from starlette.testclient import TestClient

from viewer.popup_viewer import PopupViewer


def test_ws_snapshot_and_feedback():
    v = PopupViewer()
    v.snapshot = {"scene": {"space": "living_room", "width": 400, "depth": 400,
                            "pre_existing_furniture": []},
                  "states": [{"robot": "BOT 1", "x": 20, "y": 20, "rot": 0,
                              "panel_left": 0, "panel_right": 0}]}
    client = TestClient(v.app)
    r = client.get("/")
    assert r.status_code == 200 and "viewer.js" in r.text
    with client.websocket_connect("/ws") as ws:
        snap = json.loads(ws.receive_text())     # 접속 즉시 스냅샷
        assert snap["type"] == "scene_change"
        assert snap["scene"]["space"] == "living_room"
        ws.send_text(json.dumps({"type": "user_feedback",
                                 "approved": False, "feedback": "왼쪽으로"}))
    data = v.feedback_q.get(timeout=3)
    assert data["feedback"] == "왼쪽으로"


def test_models_mounted():
    v = PopupViewer()
    client = TestClient(v.app)
    r = client.get("/models/robot_0x0.glb")
    assert r.status_code == 200 and len(r.content) > 1_000_000


def test_chat_utterance_queue():
    v = PopupViewer()
    client = TestClient(v.app)
    with client.websocket_connect("/ws") as ws:
        ws.receive_text()   # snapshot
        ws.send_text(json.dumps({"type": "user_utterance", "text": "다리 아파"}))
    assert v.utterance_q.get(timeout=3) == "다리 아파"


def test_stt_endpoint():
    v = PopupViewer()
    client = TestClient(v.app)
    r = client.post("/stt", content=b"xxxx")          # 핸들러 미설정 → 503
    assert r.status_code == 503
    v.stt_handler = lambda data, mime: "다리 아파"
    r = client.post("/stt", content=b"fake-audio",
                    headers={"Content-Type": "audio/webm"})
    assert r.status_code == 200 and r.json()["text"] == "다리 아파"
