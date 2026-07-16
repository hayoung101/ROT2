# -*- coding: utf-8 -*-
"""진입점: 조립 + 발화 루프.

python main.py            # 브라우저 채팅창이 유일한 인터페이스
                          #   - 타이핑 입력 + 🎤/스페이스바 push-to-talk 음성 입력
python main.py --noview   # 뷰어 없이 콘솔 타이핑 (개발용)
python main.py --baseline # 실험 비교군: LLM 없이 뷰어 수동 패널로 직접 배치 (§14-1)
"""
import json
import os
import queue
import sys
import time
import traceback
from datetime import datetime

from openai import OpenAI

import config
import tools
from agent import ask_function, ask_intent, run_agent
from services.scene import SceneState

DEFAULT_SPACE = "living_room"
METRICS_PATH = os.path.join("logs", "metrics.jsonl")


def _append_metrics(rec):
    """논문 분석용 레코드 1건 = JSONL 1줄 append (pandas.read_json(lines=True)로 읽음).

    session.json과 분리하는 이유: 커밋 없이 끝난 발화(HITL-1 취소 등)도 남아야
    통과율의 분모가 잡히고, flat한 구조여야 조건 간 비교가 바로 된다."""
    try:
        os.makedirs(os.path.dirname(METRICS_PATH) or ".", exist_ok=True)
        with open(METRICS_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
    except Exception:
        traceback.print_exc()


def _load_motifs():
    """기능층 입력용 가구 참고표 (reference)."""
    try:
        with open(os.path.join("data", "furniture_motifs.json"), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


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
    #오직 이 다섯가지 경우만 채팅에서 승인
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


def _should_change_space(intent, current_space):
    """HITL-1에서 승인된 방이 현재 방과 다르면 실행 경로와 무관하게 전환한다.

    confirm은 현재 배치 승인이고, revert는 저장된 turn의 방까지 자체 복원하므로
    두 경로에서는 일반 방 전환을 적용하지 않는다.
    """
    space = intent.get("space")
    return (space not in (None, "unknown")
            and space != current_space
            and intent.get("intent_type") not in ("confirm", "revert"))


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
        tools.metric("clarify_rounds")
        text = text + " / (확인 답변) " + ans
        intent = ask_intent(openai_client, text, last_intent,
                            room_furniture=scene_state.furniture(),
                            recent_history=_slim_history(scene_state))
        if not intent:
            return last_intent

    # 되묻기 한도(2회) 도달 후에도 미해소면: 더 묻지 않고 LLM이 남은 정보를 추론해 채우게 한다.
    if intent.get("needs_clarification"):
        tools.metric_set("clarify_limit_hit", True)
        text = text + " / (되묻기 한도 도달)"
        intent = ask_intent(openai_client, text, last_intent,
                            room_furniture=scene_state.furniture(),
                            recent_history=_slim_history(scene_state)) or intent

    confirmation = intent.get("confirmation_message", "")
    it = intent.get("intent_type")
    tools.metric_set("intent_type", it)

    # HITL-1 언어 게이트: 의도를 실행하기 전에 사용자에게 확인받는다.
    # confirm은 그 자체가 이전 배치에 대한 승인이므로 다시 게이트하지 않는다.
    if it != "confirm":
        tools.metric("hitl1_attempts")
        approved, feedback = _hitl1_confirm(viewer, confirmation)
        if not approved:
            tools.metric("hitl1_rejects")
            if feedback and _depth < 3:   # 피드백을 새 발화로 재분석 (거부된 intent를 맥락으로)
                tools.metric_set("hitl1_feedback_depth", _depth + 1)
                return handle(openai_client, scene_state, feedback,
                              intent, _depth + 1)
            if viewer:
                viewer.chat("system", "요청을 취소했습니다.")
            print("[HITL-1] 취소")
            tools.metric_set("outcome", "cancelled")
            return last_intent
    else:
        print("[HITL-1] " + confirmation)
        if viewer:
            viewer.chat("agent", confirmation)

    if it == "confirm":   # 승인 → 스냅샷 확정 (변화 없으면 재커밋 안 함)
        if not scene_state.history and not any(s["active"] == "active" for s in scene_state.states()):
            # 확정할 배치가 아직 없음 (예: 첫 발화가 "응"처럼 confirm으로 분류된 경우) — 빈 상태를 커밋하지 않는다.
            print("[HITL-1] 확정할 배치가 없어 무시")
            if viewer:
                viewer.chat("system", "아직 확정할 배치가 없어요. 먼저 원하시는 상황을 말씀해주세요.")
            tools.metric_set("outcome", "no_change")
            return last_intent
        entry, changed = scene_state.commit_if_changed("사용자 승인: " + text, "confirm", text)
        if changed:   # 새로 확정됐을 때만 안내. turn 번호는 내부 개념 — 채팅에 노출하지 않는다
            print("[commit] turn %d 확정" % entry["turn"])
            if viewer:
                viewer.chat("system", "배치가 확정되었습니다.")
        else:
            tools.metric_set("outcome", "no_change")
        return intent

    if it == "revert":   # 결정론적 복원 (형태층 LLM 스킵)
        _do_revert(scene_state, viewer, intent)
        tools.metric_set("outcome", "reverted")
        return intent

    space = intent.get("space")
    if _should_change_space(intent, scene_state.space):
        scene_state.load_scene(space)   # 방 전환 (로봇은 새 방 도크에서 시작)
        tools.push_scene()
        print("[scene] %s(으)로 전환" % space)

    # 기능층: 가구 목록 확정 + 구현 가능성 판정 (new_scene/add만 — 조정성 발화는 기존 목록 유지).
    # 방 전환 '후'에 호출해야 새 방의 기존 가구를 근거로 판단한다.
    if it in ("new_scene", "add"):
        motifs = _load_motifs()
        func = ask_function(openai_client, intent, scene_state.furniture(), motifs)
        if func:
            items = func.get("furniture") or []
            valid_keys = set((motifs or {}).get("motifs", {}))
            feasible = []
            for f in items:
                if not f.get("feasible"):
                    continue
                m = f.get("motif")
                if m is not None and m not in valid_keys:
                    # 존재하지 않는 motif 키(오타·환각) → 코드가 null(맞춤 형태)로 교정 (참조 무결성 보장)
                    print("[FUNCTION] 알 수 없는 motif 키 '%s' → null 교정" % m)
                    m = None
                feasible.append({"item": f["item"], "count": f["count"], "motif": m})
            excluded = [{"item": f["item"], "reason": f.get("reason")}
                        for f in items if not f.get("feasible")]
            tools.metric("func_excluded", len(excluded))
            if items and not feasible:   # 전부 구현 불가 → 형태층 스킵, 사용자에게 바로 알림
                msg = " ".join(e["reason"] or ("%s은(는) 로봇 가구로 만들 수 없어요." % e["item"])
                               for e in excluded)
                print("[FUNCTION] 전부 구현 불가 — 형태층 스킵")
                if viewer:
                    viewer.chat("agent", msg)
                tools.metric_set("func_all_infeasible", True)
                return intent
            intent["furniture"] = feasible            # 형태층은 확정 목록을 받는다
            if excluded:   # 제외 항목은 형태층에 넘기지 않는다 (넘기면 시키지 않아도 언급함) — 로그만
                print("[FUNCTION] 구현 불가로 제외: %s" % excluded)
            if func.get("complement_note"):
                intent["complement_note"] = func["complement_note"]   # 보완 이유 고지용

    answer = run_agent(openai_client, intent, text)
    # 형태층의 마무리 발화는 채팅에 올리지 않는다 — ask_user 승인 문구·확정 안내와
    # 내용이 중복되기 때문. 콘솔 로그로만 남긴다.
    print("[agent] " + str(answer))
    return intent


def handle_logged(openai_client, scene_state, session_id, seq, text, input_mode,
                  last_intent):
    """handle()을 감싸 발화 1건 = metrics.jsonl 1줄을 남긴다 (실험 데이터, §14-2).

    HITL-1 거부 피드백의 재귀 재분석도 같은 발화 레코드에 누적된다.
    커밋된 턴은 session.json entry["metrics"]에도 같은 스냅샷을 흡수한다."""
    from services import collision

    tools.STATE["metrics"] = tools.new_metrics()
    t0 = time.time()
    turn_before = scene_state.turn
    error = None
    try:
        result = handle(openai_client, scene_state, text, last_intent)
    except Exception as e:
        error = str(e)
        result = last_intent
        traceback.print_exc()
        viewer = tools.STATE.get("viewer")
        if viewer:
            viewer.chat("system", "처리 중 오류가 발생했어요. 다시 말씀해 주세요.")
    m = tools.STATE.get("metrics") or {}
    committed = scene_state.turn > turn_before
    outcome = ("error" if error else "committed" if committed
               else m.get("outcome") or "no_commit")
    issues = (collision.validate_layout(scene_state.states(),
                                        scene_state.environment())
              if committed else None)   # 커밋 시점 잔여 물리 위반 = 품질 지표
    counters = {k: v for k, v in m.items() if k not in ("outcome", "intent_type")}
    rec = {"session_id": session_id, "mode": "agent", "seq": seq,
           "utterance": text, "input_mode": input_mode,
           "space": scene_state.space, "intent_type": m.get("intent_type"),
           "outcome": outcome,
           "turn": scene_state.turn if committed else None,
           "t_received": datetime.fromtimestamp(t0).isoformat(timespec="seconds"),
           "duration_s": round(time.time() - t0, 1),
           "issues_at_commit": issues, "error": error}
    rec.update(counters)
    _append_metrics(rec)
    if committed:
        scene_state.history[-1]["metrics"] = dict(counters,
                                                  duration_s=rec["duration_s"])
        scene_state.save()
    tools.STATE["metrics"] = None
    return result


def baseline_loop(scene_state, viewer, session_id):
    """--baseline: 사용자가 LLM 없이 로봇을 직접 조작하는 실험 비교군.

    뷰어 수동 패널의 manual_command만 소비한다. clamp·자동 검증 등 코드 보장
    레이어는 agent 경로와 동일하게 적용하되, 겹침은 차단하지 않고 자막 경고만
    띄운다 — '완료' 시 잔여 issues를 metrics로 기록해 품질 지표로 쓴다."""
    from services import collision

    ops = {"move": 0, "rotate": 0, "panel": 0, "store": 0, "scene": 0}
    warn_count = 0
    seq = 0
    t_start = time.time()

    def _validate(warn=True):
        nonlocal warn_count
        issues = collision.validate_layout(scene_state.states(),
                                           scene_state.environment())
        if issues and warn:
            warn_count += 1
            kinds = ", ".join(sorted({i["type"] for i in issues}))
            viewer.push_message("⚠ 배치 경고: " + kinds)
        return issues

    print("[baseline] 수동 모드 — 브라우저 패널로 조작하고 '완료'로 확정하세요")
    while True:
        cmd = viewer.manual_q.get()
        try:
            action = cmd.get("action")
            name = cmd.get("robot")
            if action == "move_delta":       # 이동·회전 (버튼 1회 = 조작 1회)
                st = scene_state.robots.get(name)
                if st is None:
                    continue
                dx, dy = cmd.get("dx", 0), cmd.get("dy", 0)
                drot = cmd.get("drot", 0)
                scene_state.move(name, st["x"] + dx, st["y"] + dy,
                                 st["rot"] + drot)
                ops["rotate" if drot else "move"] += 1
                tools.push_state(0.3)
                _validate()
            elif action == "panel":          # 패널 각도 (한쪽만 갱신)
                st = scene_state.robots.get(name)
                if st is None:
                    continue
                angle = cmd.get("angle", 0)
                left = angle if cmd.get("side") == "left" else st["panel_left"]
                right = angle if cmd.get("side") == "right" else st["panel_right"]
                scene_state.transform(name, left, right)
                ops["panel"] += 1
                tools.push_state(0.5)
                _validate()
            elif action == "store":          # 도크 복귀 + 초기화
                if name in scene_state.robots:
                    scene_state.store(name)
                    ops["store"] += 1
                    tools.push_state(0.8)
            elif action == "scene":          # 방 전환 (로봇은 새 방 도크에서 시작)
                space = cmd.get("space")
                if space and space != scene_state.space:
                    scene_state.load_scene(space)
                    tools.push_scene()
                    ops["scene"] += 1
            elif action == "commit":         # '완료' = 확정 + metrics 흡수
                issues = _validate(warn=False)
                entry, changed = scene_state.commit_if_changed(
                    "수동 배치 (baseline)", "manual", "(baseline)")
                if not changed:
                    viewer.chat("system", "마지막 확정 이후 변화가 없어요.")
                    continue
                elapsed = round(time.time() - t_start, 1)
                seq += 1
                entry["metrics"] = {"mode": "baseline", "ops": dict(ops),
                                    "ops_total": sum(ops.values()),
                                    "warnings": warn_count,
                                    "issues_at_commit": issues,
                                    "duration_s": elapsed}
                scene_state.save()           # metrics 포함해 다시 저장
                # agent 경로와 같은 최상위 스키마로 metrics.jsonl에도 기록 → 조건 간 비교
                _append_metrics({
                    "session_id": session_id, "mode": "baseline", "seq": seq,
                    "utterance": None, "input_mode": "manual",
                    "space": scene_state.space, "intent_type": "manual",
                    "outcome": "committed", "turn": entry["turn"],
                    "t_received": datetime.fromtimestamp(t_start)
                                          .isoformat(timespec="seconds"),
                    "duration_s": elapsed, "issues_at_commit": issues,
                    "error": None, "ops": dict(ops),
                    "ops_total": sum(ops.values()), "warnings": warn_count})
                print("[baseline] turn %d 확정 — 조작 %d회, %.0f초, 잔여 issues %d"
                      % (entry["turn"], sum(ops.values()), elapsed, len(issues)))
                viewer.chat("system", "배치가 확정되었습니다. (조작 %d회 · %.0f초%s)"
                            % (sum(ops.values()), elapsed,
                               " · 미해결 경고 %d건" % len(issues) if issues else ""))
                ops = {k: 0 for k in ops}    # 다음 배치 과제를 위해 리셋
                warn_count = 0
                t_start = time.time()
        except Exception:                    # 조작 1건의 실패가 세션을 죽이지 않게
            traceback.print_exc()


def main():
    scene_state = SceneState()
    scene_state.load_scene(DEFAULT_SPACE)
    # 실험 세션 식별자 — metrics.jsonl의 모든 레코드에 박힌다 (조건은 mode로 구분)
    session_id = time.strftime("%Y%m%d_%H%M%S") + \
        ("_baseline" if "--baseline" in sys.argv else "_agent")

    if "--baseline" in sys.argv:   # 실험 비교군: LLM·STT 미로드, 수동 패널만
        from viewer.popup_viewer import PopupViewer
        viewer = PopupViewer(baseline=True)
        viewer.start(scene_state.environment(), scene_state.states())
        print("[viewer] http://127.0.0.1:8765 — baseline 수동 모드")
        tools.init(scene_state, None, viewer)
        baseline_loop(scene_state, viewer, session_id)
        return
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
        print("[viewer] http://127.0.0.1:8765 — 채팅창에 입력하거나 🎤로 말하세요")
    tools.init(scene_state, openai_client, viewer)

    last_intent = None
    seq = 0
    if viewer is not None:
        while True:                        # 발화는 전부 브라우저에서 온다
            try:
                item = viewer.utterance_q.get(timeout=0.5)
            except queue.Empty:
                continue
            # 뷰어는 {"text","input"}(voice|typed)을 보낸다 — 옛 형식(str)도 허용
            text, input_mode = ((item.get("text", ""), item.get("input", "typed"))
                                if isinstance(item, dict) else (item, "typed"))
            if not text:
                continue
            seq += 1
            # 발화 하나의 실패가 세션을 죽이지 않게 — 예외는 handle_logged가 격리·기록
            last_intent = handle_logged(openai_client, scene_state, session_id,
                                        seq, text, input_mode, last_intent)
    else:
        print("콘솔 모드. 빈 입력 또는 Ctrl+C로 종료.")
        while True:
            text = input("\n발화> ").strip()
            if not text:
                break
            seq += 1
            last_intent = handle_logged(openai_client, scene_state, session_id,
                                        seq, text, "typed", last_intent)


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        print("\nStopped.")
