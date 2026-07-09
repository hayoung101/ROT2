# -*- coding: utf-8 -*-
"""진입점: 조립 + 발화 루프.

python main.py            # 브라우저 채팅창이 유일한 인터페이스
                          #   - 타이핑 입력 + 🎤/스페이스바 push-to-talk 음성 입력
python main.py --noview   # 뷰어 없이 콘솔 타이핑 (개발용)
"""
import queue
import sys

from openai import OpenAI

import config
import tools
from agent import ask_intent, run_agent
from services.scene import SceneState

DEFAULT_SPACE = "living_room"


def handle(openai_client, scene_state, text, last_intent):
    """발화 하나 처리: 의도층 → HITL-1 → 라우팅 → 형태층."""
    viewer = tools.STATE.get("viewer")

    intent = ask_intent(openai_client, text, last_intent,
                        room_furniture=scene_state.furniture())
    if not intent:
        return last_intent
    confirmation = intent.get("confirmation_message", "")
    print("[HITL-1] " + confirmation)
    if viewer:
        viewer.chat("agent", confirmation)   # 의도 확인 발화 (HITL-1)

    it = intent.get("intent_type")
    if it == "confirm":   # 승인 → 스냅샷 확정 (LLM 불필요)
        entry = scene_state.commit("사용자 승인: " + text, "confirm", text)
        print("[commit] turn %d 확정" % entry["turn"])
        if viewer:
            viewer.chat("system", "배치가 확정되었습니다 (turn %d)" % entry["turn"])
        return intent

    space = intent.get("space")
    if it == "new_scene" and space not in (None, "unknown") and space != scene_state.space:
        scene_state.load_scene(space)   # 방 전환 (로봇은 새 방 도크에서 시작)
        tools.push_scene()
        print("[scene] %s(으)로 전환" % space)

    answer = run_agent(openai_client, intent, text)
    print("[agent] " + str(answer))
    if viewer and answer:
        viewer.chat("agent", str(answer))
    return intent


def main():
    scene_state = SceneState()
    scene_state.load_scene(DEFAULT_SPACE)
    scene_state.resume()   # 이전 세션이 있으면 이어서
    openai_client = OpenAI(api_key=config.OPENAI_API_KEY)

    viewer = None
    if "--noview" not in sys.argv:
        from viewer.popup_viewer import PopupViewer
        viewer = PopupViewer()
        if config.GROQ_API_KEY:            # 브라우저 push-to-talk STT 연결
            from groq import Groq
            from services import stt
            groq_client = Groq(api_key=config.GROQ_API_KEY)
            viewer.stt_handler = (lambda data, mime:
                                  stt.transcribe_bytes(groq_client, data, mime))
        viewer.start(scene_state.environment(), scene_state.states())
        print("[viewer] http://127.0.0.1:8765 — 채팅창에 입력하거나 🎤/스페이스바로 말하세요")
    tools.init(scene_state, openai_client, viewer)

    last_intent = None
    if viewer is not None:
        while True:                        # 발화는 전부 브라우저에서 온다
            try:
                text = viewer.utterance_q.get(timeout=0.5)
            except queue.Empty:
                continue
            last_intent = handle(openai_client, scene_state, text, last_intent)
    else:
        print("콘솔 모드. 빈 입력 또는 Ctrl+C로 종료.")
        while True:
            text = input("\n발화> ").strip()
            if not text:
                break
            last_intent = handle(openai_client, scene_state, text, last_intent)


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        print("\nStopped.")
