# -*- coding: utf-8 -*-
"""LLM 호출 계층 — 네 개의 구조화 출력 호출의 집합 (중계자가 아니다, §7).

- ask_intent()   : 의도층 — 발화 → intent JSON
- ask_function() : 기능층 — intent → 로봇 무관 가구 요구 목록 + 구현 가능성
- ask_form()     : 형태층 Phase A — 형태(panels)·관계(relation) 결정 (좌표 없음, §6.2)
- ask_place()    : 형태층 Phase B — 코드가 만든 후보 중 하나를 인덱스로 선택 (§6.6)

형태층 실행(후보 생성·검증·move/transform·commit)은 코드(services + main)가 한다 —
agent.py는 tool을 갖지 않는다.
"""
import json
import re
import time

import config
import tools
from services import eventlog
from prompts import (FORM_PROMPT, FORM_SCHEMA, FUNCTION_PROMPT, FUNCTION_SCHEMA,
                     INTENT_PROMPT, INTENT_SCHEMA, PLACE_PROMPT, PLACE_SCHEMA,
                     ROBOT_MECHANISM)

# message에 새면 안 되는 내부 용어·수치 (§6.6): 숫자+단위 / rot / 패널
_MESSAGE_BANNED = re.compile(r"\d+\s*(cm|도|°)|rot|패널", re.IGNORECASE)
# message에 새면 안 되는 '시스템 내부 사정' — 원안·후보 부족·완화 (§15.6, §17.5).
# 완화 사실을 Phase B 입력에 넣은 뒤로 그것이 message로 흘러나올 수 있게 됐다. 참가자는
# Phase A 원안을 본 적이 없으므로 "원래는 소파 앞에 놓으려 했는데 자리가 없어서"는 정직함이
# 아니라 혼란이고, 참가자를 시스템 디버깅 대화("소파를 옮기면 되나?")로 끌어들여 §17.5가
# 지키려는 것(관측된 사실만 문구에 넣어 유도 편향을 피한다)을 깬다. 배치가 안 맞으면
# 참가자가 HITL-2에서 말하고 그 발화가 데이터다 — 시스템이 선수 치면 안 된다.
# '자리를 만들었어요'(정상 문구)와 갈리도록 부정·실패 표현에만 건다.
_MESSAGE_LEAK = re.compile(r"원안|원래는|자리가 없|공간이 없|자리를 찾(기|지)|"
                           r"찾지 못|찾기 어|놓을 수 없|배치할 수 없|여의치")


def _structured(client, developer, user_obj, name, schema, model=None, layer=None):
    """구조화 출력 1회 호출 공통 골격. 예외는 호출부가 잡는다(여기선 전파).

    layer별 소요를 llm_call 이벤트로 계측한다(§B-1) — 어디가 느린지 실측 없이 추측 금지."""
    model = model or config.OPENAI_MODEL
    t0 = time.time()
    response = client.responses.create(
        model=model,
        input=[
            {"role": "developer", "content": developer},
            {"role": "user", "content": json.dumps(user_obj, ensure_ascii=False)},
        ],
        text={"format": {"type": "json_schema", "name": name,
                         "strict": True, "schema": schema}},
    )
    tools.metric("llm_calls")   # 실험 metrics
    eventlog.record("llm_call", layer=layer, model=model,
                    elapsed=round(time.time() - t0, 2))
    return json.loads(response.output_text)


# 전사된 텍스트를 OpenAI LLM를 통해 의도 분석
def ask_intent(client, usertext, prev_intent=None, room_furniture=None,
               recent_history=None):
    try:
        result = _structured(client, INTENT_PROMPT, {
            "직전 상황(prev_intent)": prev_intent,
            "방의 기존 가구(pre_existing_furniture)": room_furniture,
            "최근 history(recent_history)": recent_history,
            "새 발화(utterance)": usertext,
        }, "intent_result", INTENT_SCHEMA, layer="intent")
        print("[INTENT] LLM이 도출한 의도 결과:")
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return result
    except Exception as e:
        print("Sorry, an error occurred while asking OpenAI: {0}".format(e))
    return None


def ask_function(client, intent, room_furniture=None, motifs=None):
    """기능층 — 의도를 로봇 무관 가구 요구 목록으로 확정하고 구현 가능성을 판정한다.

    의도층(상황 파악)과 형태층(로봇 구성) 사이의 중간층. HITL-1 승인 뒤,
    intent_type이 new_scene/add일 때만 호출된다 (main.py)."""
    try:
        result = _structured(client, ROBOT_MECHANISM + FUNCTION_PROMPT, {
            "파악된 의도(intent)": {k: intent.get(k) for k in
                                ("number", "situation", "activity",
                                 "posture", "space", "furniture")},
            "방의 기존 가구(pre_existing_furniture)": room_furniture,
            "가구 참고표(motifs)": motifs,
        }, "function_result", FUNCTION_SCHEMA, layer="function")
        print("[FUNCTION] 기능층 판정:")
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return result
    except Exception as e:
        print("기능층 호출 중 오류: {0}".format(e))
    return None


def ask_form(client, intent, room_furniture=None, space_summary=None,
             states=None, motifs=None, recall_reason=None):
    """형태층 Phase A — 각 로봇의 형태(panels)와 관계(relation)를 정한다 (좌표 없음).

    입력: ROBOT_MECHANISM + 기존 가구 description + 공간 요약(용량만) + 현재 로봇 상태 +
    intent_type. new_scene/add는 기능층 확정 가구(motif 상세)를, modify/remove는 그 대신
    '현재 구성'(states)을 근거로 삼는다 — modify에 의도층 초안을 '확정 가구'로 주면 Phase A가
    없던 가구를 새로 만들려 든다. recall_reason은 재호출 시 사유(§6.7)."""
    it = intent.get("intent_type")
    user_obj = {
        "요청 종류(intent_type)": it,
        "상황": {k: intent.get(k) for k in ("number", "situation", "activity", "posture")},
        "방의 기존 가구(description)": room_furniture,
        "공간 요약(용량만)": space_summary,
        "현재 구성(로봇별 형태·active)": states,
        "재구상 사유(있으면 반영)": recall_reason,
    }
    if it in ("new_scene", "add"):   # 기능층이 상황 단위로 확정한 필요 가구 (modify/remove는 상황 불변)
        user_obj["확정 가구(furniture)"] = intent.get("furniture")
        user_obj["보완 이유(complement_note)"] = intent.get("complement_note")
        user_obj["가구 참고표(motifs)"] = motifs
    try:
        result = _structured(client, ROBOT_MECHANISM + FORM_PROMPT, user_obj,
                             "form_result", FORM_SCHEMA, layer="form")
        print("[FORM] Phase A 형태·관계:")
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return result
    except Exception as e:
        print("형태층 Phase A 호출 중 오류: {0}".format(e))
    return None


def ask_place(client, form, combos, intent, relaxed=None):
    """형태층 Phase B — 후보/조합 목록에서 인덱스 하나를 고른다 (§6.6).

    relaxed는 코드가 후보를 만들며 완화한 조건(main._relax_note). 있으면 원안 바로 뒤,
    후보 목록 **앞에** 넣는다 — 원안과 후보가 어긋난 이유를 후보를 읽기 전에 알아야
    '계획에 안 맞는 후보들'로 오독해 거부하지 않는다 (거부는 Phase A 재구상 한 라운드다).
    message에 내부 용어·수치가 새면 1회 재생성한다(§6.6). choice/reject_reason의
    정확히-하나-non-null 검사는 호출부(main)가 제어 흐름으로 처리한다."""
    user_obj = {"Phase A 원안(rationale 포함)": form}
    if relaxed:
        user_obj["후보 생성 시 완화된 조건"] = relaxed
    user_obj.update({
        "유효 후보/조합 목록(주석 포함)": combos,
        "활동·인원·자세": {k: intent.get(k) for k in ("activity", "number", "posture")},
        "보완 이유(complement_note)": intent.get("complement_note"),
    })
    try:
        result = _structured(client, PLACE_PROMPT, user_obj, "place_result", PLACE_SCHEMA,
                             model=config.MODEL_PLACE, layer="place")
        msg = result.get("message") or ""
        # 위반 종류(kind)를 카운터까지 끌고 간다. 둘을 한 카운터로 합치면 파일럿에서 regen이
        # 올라도 수치가 샌 건지(term — 정상 동작) 누출 정규식 오탐인지(leak) 구분할 수 없다.
        # leak 필터는 예측 기반이라 오탐률을 재는 것이 그 수정의 정산인데, 합치면 그 신호가
        # 무효가 된다. 합계는 나중에 더할 수 있지만 합쳐진 숫자는 나중에 못 나눈다 (§17.5).
        kind = fix = None
        if _MESSAGE_BANNED.search(msg):
            kind = "term"
            fix = ("직전 message에 수치·단위(cm/도/°)·rot·'패널' 같은 내부 용어가 있었다. "
                   "그 표현을 빼고 사람의 언어로 message만 다시 써라.")
        elif _MESSAGE_LEAK.search(msg):
            kind = "leak"
            fix = ("직전 message에 사용자가 알 수 없는 내부 사정(원안·자리 부족·조건 완화)이 "
                   "드러났다. 사용자는 그 계획을 본 적이 없어 설명이 아니라 혼란이 된다. "
                   "그 사정을 빼고, 지금 놓인 배치만 서술해 message만 다시 써라.")
        if kind:                          # 내부 용어·내부 사정 → 1회만 재생성 (message만)
            print("[PLACE] message 정규식 위반(%s) → 재생성 1회 (message만 교체)" % kind)
            retry = dict(user_obj)
            retry["재작성 지시"] = fix
            result2 = _structured(client, PLACE_PROMPT, retry, "place_result", PLACE_SCHEMA,
                                  model=config.MODEL_PLACE, layer="place")
            tools.metric("place_regen_" + kind)
            # message만 취한다 — 전체 교체는 로그에 남긴 근거(checks·reason)와 실제 선택(choice·
            # reject_reason)을 어긋나게 한다. 「프롬프트로 부탁 말고 구조로 강제」의 반례를 막는다.
            if result2 and result2.get("message"):
                result["message"] = result2["message"]
                if (_MESSAGE_BANNED.search(result["message"])
                        or _MESSAGE_LEAK.search(result["message"])):
                    # 2회차도 위반 → 진행, 로그만. 실패도 종류별로 — term은 프롬프트가 약한
                    # 것이고 leak은 정규식이 과하게 넓은 것일 수 있어 처방이 다르다.
                    tools.metric("place_regen_%s_failed" % kind)
        print("[PLACE] Phase B 선택:")
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return result
    except Exception as e:
        print("형태층 Phase B 호출 중 오류: {0}".format(e))
    return None
