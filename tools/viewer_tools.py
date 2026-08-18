# -*- coding: utf-8 -*-
"""뷰어/HITL tool.

ask_user는 HITL-2 게이트다 — 배치 결과를 사용자에게 한 번 승인받고,
승인 즉시 코드가 배치를 확정한다 (LLM의 commit 호출에 의존하지 않음).
되묻기는 의도 단계에서 HITL 앞단에 처리한다 → main._ask_clarification."""
from tools import STATE


def _commit_on_approval(message):
    """HITL-2 승인 즉시 코드가 배치를 확정한다.

    main._run_form_layer가 '승인 시엔 ask_user가 이미 커밋했으므로 롤백 대상이 아니다'를
    전제로 짜여 있다 — 여기서 커밋하지 않으면 승인해도 history에 아무것도 남지 않는다.
    commit_if_changed는 (entry, changed)를 주므로 changed로 no-op을 가른다 (§scene.py).
    """
    sc = STATE.get("scene")
    if sc is None:
        return
    intent = STATE.get("intent") or {}
    entry, changed = sc.commit_if_changed(message,
                                          intent.get("intent_type", "new_scene"),
                                          STATE.get("utterance", ""))
    if not changed:
        return                      # 변화 없음 — 재커밋도 안내도 하지 않는다
    print("[commit] turn %d 확정" % entry["turn"])
    viewer = STATE.get("viewer")
    if viewer is not None:
        viewer.chat("system", "배치가 확정되었습니다 (turn %d)" % entry["turn"])


def ask_user(message):
    """결과 승인 요청 (HITL-2). 승인받으면 그 자리에서 코드가 확정(commit)한다."""
    if STATE.get("auto_approve"):
        res = {"approved": True, "feedback": ""}
    else:
        viewer = STATE.get("viewer")
        if viewer is not None and viewer.clients:   # 브라우저가 붙어 있을 때만 (아니면 무한 대기)
            res = viewer.request_approval(message)
        else:
            print("\n[HITL-2 승인 요청] " + str(message))
            for st in STATE["scene"].states():
                print("   ", st)
            ans = input("승인: y / 수정할 점 입력: ").strip()
            if ans.lower() in ("y", "yes", "", "ㅇ", "좋아"):
                res = {"approved": True, "feedback": ""}
            else:
                res = {"approved": False, "feedback": ans}
    if res.get("approved"):
        _commit_on_approval(message)   # 승인 직후 코드가 확정 — 못 박음
    return res


def ask_clarification(type, question, candidates=None):
    """입력 보완. type: missing_info / ambiguous_intent. 턴당 최대 2회 (코드 강제).

    현재 경로에서는 쓰이지 않는다 — 되묻기는 main.handle이 HITL 앞단에서 처리한다.
    콘솔 폴백 경로를 위해 남겨 둔다."""
    STATE["clarify_count"] = STATE.get("clarify_count", 0) + 1
    if STATE["clarify_count"] > 2:   # 최대 2회 코드적으로도 제한
        return {"error": "질문 한도(2회) 초과 — 더 묻지 말고 지금까지의 정보로 가장 합리적인 구성을 진행하라"}
    if STATE.get("auto_answer") is not None:
        return {"answer": STATE["auto_answer"]}
    viewer = STATE.get("viewer")
    if viewer is not None and viewer.clients:
        return {"answer": viewer.ask(question, candidates)}
    print("\n[확인 질문] " + str(question))
    if candidates:
        print("   후보:", candidates)
    return {"answer": input("답변: ").strip()}
