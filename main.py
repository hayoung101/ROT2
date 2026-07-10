# -*- coding: utf-8 -*-
"""진입점: 조립 + 발화 루프.

python main.py            # 브라우저 채팅창이 유일한 인터페이스
                          #   - 타이핑 입력 + 🎤/스페이스바 push-to-talk 음성 입력
python main.py --noview   # 뷰어 없이 콘솔 타이핑 (개발용)
"""
import queue
import sys
import traceback

from openai import OpenAI

import config
import tools
from agent import ask_intent, run_agent
from services.scene import SceneState

DEFAULT_SPACE = "living_room"


def _hitl1_confirm(viewer, message):
    """HITL-1 언어 게이트: 분석된 의도를 사용자에게 확인받는다 (블로킹).

    반환: (approved: bool, feedback: str)."""
    print("[HITL-1] " + message)
    if viewer is not None and viewer.clients:
        # approval_request가 메시지+승인/수정 버튼을 한 말풍선으로 그린다 (chat 중복 금지)
        res = viewer.request_approval(message)   # 브라우저 승인/피드백 대기
        return bool(res.get("approved")), res.get("feedback", "")
    ans = input("[HITL-1] 맞으면 y / 고칠 점 입력: ").strip()   # 콘솔 fallback
    if ans.lower() in ("y", "yes", "", "ㅇ", "네", "좋아"):
        return True, ""
    return False, ans


def _slim_history(scene_state, n=8):
    """의도층에 넘길 최근 history 요약 (state 스냅샷 제외 — turn 선택에만 쓰인다)."""
    return [{"turn": h["turn"], "space": h["space"], "intent_type": h["intent_type"],
             "utterance": h["utterance"], "description": h["description"]}
            for h in scene_state.recent(n)]


def _ask_clarification(viewer, question, candidates=None):
    """HITL 앞단 되묻기 — 답 문자열 반환 (빈 문자열이면 무응답/취소)."""
    print("[확인 질문] " + str(question))
    if viewer is not None and viewer.clients:
        # clarify_request가 질문을 말풍선으로 그린다 (chat 중복 금지)
        return viewer.ask(question, candidates)
    if candidates:
        print("   후보:", candidates)
    return input("답변: ").strip()


def _pick_revert_target(scene_state):
    """현재 상태와 '다른' 가장 최근 커밋 turn — 결정론적 '되돌리기' 대상.
    (승인 시 자동 commit되므로 가장 최근 커밋 == 현재 상태인 경우가 대부분이라,
    최신 커밋으로 복원하면 no-op이 된다. 실제로 상태가 바뀌는 turn을 고른다.)"""
    for h in reversed(scene_state.history):
        if h["state"] != scene_state.robots:
            return h["turn"]
    return None


def _do_revert(scene_state, viewer, intent):
    """revert를 결정론적으로 처리 (형태층 LLM 스킵). 대상 turn은 의도층이 고른다."""
    target = intent.get("revert_to_turn")
    if target is not None:   # LLM이 고른 turn이 현재 상태와 같으면(무변화) 안전망으로 재선택
        entry = next((h for h in scene_state.history if h["turn"] == int(target)), None)
        if entry is not None and entry["state"] == scene_state.robots:
            target = None
    if target is None:
        target = _pick_revert_target(scene_state)   # fallback: 현재와 다른 가장 최근 커밋
    before_space = scene_state.space
    entry = scene_state.revert_to(int(target)) if target is not None else None
    if entry is None:
        print("[revert] 실패: target=%s" % target)
        if viewer:
            viewer.chat("system", "되돌릴 대상을 찾지 못했어요.")
        return
    if scene_state.space != before_space:
        tools.push_scene()          # 방까지 바뀌면 scene_change
    else:
        tools.push_state()
    print("[revert] turn %d로 복원" % entry["turn"])
    if viewer:
        viewer.chat("agent", "이전 배치로 되돌렸어요.")


def handle(openai_client, scene_state, text, last_intent, _depth=0):
    """발화 하나 처리: 의도층 → HITL-1 → 라우팅 → 형태층."""
    viewer = tools.STATE.get("viewer")

    intent = ask_intent(openai_client, text, last_intent,
                        room_furniture=scene_state.furniture(),
                        recent_history=_slim_history(scene_state))
    if not intent:
        return last_intent

    # 되묻기(clarification): 의도층이 필요하다고 판단하면 HITL 앞단에서 먼저 해소한다 (최대 2회).
    # 답을 발화에 보태 의도를 재분석 → 정보가 채워진 intent로 HITL-1에 들어간다.
    for _ in range(2):
        if not intent.get("needs_clarification"):
            break
        q = intent.get("clarification_question") or "조금만 더 자세히 말씀해 주시겠어요?"
        ans = _ask_clarification(viewer, q)
        if not ans:
            break
        text = text + " / (확인 답변) " + ans
        intent = ask_intent(openai_client, text, last_intent,
                            room_furniture=scene_state.furniture(),
                            recent_history=_slim_history(scene_state))
        if not intent:
            return last_intent

    # 되묻기 한도(2회) 도달 후에도 미해소면: 더 묻지 않고 LLM이 남은 정보를 추론해 채우게 한다.
    if intent.get("needs_clarification"):
        text = text + " / (되묻기 한도 도달)"
        intent = ask_intent(openai_client, text, last_intent,
                            room_furniture=scene_state.furniture(),
                            recent_history=_slim_history(scene_state)) or intent

    confirmation = intent.get("confirmation_message", "")
    it = intent.get("intent_type")

    # HITL-1 언어 게이트: 의도를 실행하기 전에 사용자에게 확인받는다.
    # confirm은 그 자체가 이전 배치에 대한 승인이므로 다시 게이트하지 않는다.
    if it != "confirm":
        approved, feedback = _hitl1_confirm(viewer, confirmation)
        if not approved:
            if feedback and _depth < 3:   # 피드백을 새 발화로 재분석 (거부된 intent를 맥락으로)
                return handle(openai_client, scene_state, feedback,
                              intent, _depth + 1)
            if viewer:
                viewer.chat("system", "요청을 취소했습니다.")
            print("[HITL-1] 취소")
            return last_intent
    else:
        print("[HITL-1] " + confirmation)
        if viewer:
            viewer.chat("agent", confirmation)

    if it == "confirm":   # 승인 → 스냅샷 확정 (변화 없으면 재커밋 안 함)
        entry, changed = scene_state.commit_if_changed("사용자 승인: " + text, "confirm", text)
        if changed:   # 새로 확정됐을 때만 안내. turn 번호는 내부 개념 — 채팅에 노출하지 않는다
            print("[commit] turn %d 확정" % entry["turn"])
            if viewer:
                viewer.chat("system", "배치가 확정되었습니다.")
        return intent

    if it == "revert":   # 결정론적 복원 (형태층 LLM 스킵)
        _do_revert(scene_state, viewer, intent)
        return intent

    space = intent.get("space")
    if it == "new_scene" and space not in (None, "unknown") and space != scene_state.space:
        scene_state.load_scene(space)   # 방 전환 (로봇은 새 방 도크에서 시작)
        tools.push_scene()
        print("[scene] %s(으)로 전환" % space)

    answer = run_agent(openai_client, intent, text)
    # 형태층의 마무리 발화는 채팅에 올리지 않는다 — ask_user 승인 문구·확정 안내와
    # 내용이 중복되기 때문. 콘솔 로그로만 남긴다.
    print("[agent] " + str(answer))
    return intent


def main():
    scene_state = SceneState()
    scene_state.load_scene(DEFAULT_SPACE)
    # 프로그램 재시작은 도크에서 새로 시작한다 (resume 안 함). 브라우저 새로고침(F5)은
    # 파이썬 프로세스가 살아 있어 뷰어가 현재 스냅샷을 다시 push하므로 가구·로봇이 유지된다.
    # commit 시 logs/session.json은 계속 기록된다 (로그·디버깅용).
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
            try:                           # 발화 하나의 실패가 세션을 죽이지 않게
                last_intent = handle(openai_client, scene_state, text, last_intent)
            except Exception:
                traceback.print_exc()
                viewer.chat("system", "처리 중 오류가 발생했어요. 다시 말씀해 주세요.")
    else:
        print("콘솔 모드. 빈 입력 또는 Ctrl+C로 종료.")
        while True:
            text = input("\n발화> ").strip()
            if not text:
                break
            try:
                last_intent = handle(openai_client, scene_state, text, last_intent)
            except Exception:
                traceback.print_exc()
                print("[error] 처리 실패 — 다음 발화로 계속")


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        print("\nStopped.")
