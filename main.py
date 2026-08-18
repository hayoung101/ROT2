# -*- coding: utf-8 -*-
"""진입점: 조립 + 발화 루프.

python main.py            # 브라우저 채팅창이 유일한 인터페이스
                          #   - 타이핑 입력 + 🎤/스페이스바 push-to-talk 음성 입력
python main.py --noview   # 뷰어 없이 콘솔 타이핑 (개발용)

수동 조작 비교군(baseline)은 별도 모듈로 분리됨 — baseline/ 폴더 (§14-1, §16).
"""
import json
import math
import os
import queue
import sys
import time
import traceback
from datetime import datetime

import config
import tools
from agent import ask_form, ask_function, ask_intent, ask_place
from services import eventlog, layout
from services.scene import SceneState
from tools import context_tools, placement_tools, viewer_tools

FORM_BAND_EXPAND = 120.0   # 공집합 시 앵커 밴드 상한을 한시적으로 넓힌다 (§6.7 폴백 1단계)
FORM_HITL2_MAX = 2         # HITL-2 승인 거부 피드백 재구상 예산 (§6.7 LLM 예산과 별개 — 사용자 요청).
                           # [미정] 파일럿 보정 — B로 속도가 잡히면 3으로 올려도 된다.

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
        if res.get("aborted"):   # 대기 중 브라우저 끊김 → 취소로 종료 (영구 블로킹 없음)
            from services import eventlog
            eventlog.record("hitl1_aborted", message=message)
            return False, ""
        return bool(res.get("approved")), res.get("feedback", "")
    ans = input("[HITL-1] 맞으면 y / 고칠 점 입력: ").strip()   # 콘솔 fallback
    if ans.lower() in ("y", "yes", "", "ㅇ", "네", "좋아"):
        # 오직 이 다섯가지 경우만 채팅에서 승인
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
    """revert를 결정론적으로 처리 (형태층 LLM 스킵). 대상 turn은 의도층이 고른다.

    복원 자체는 revert_to tool(context_tools)과 같은 한 벌을 쓴다 — 방 전환 여부에 따른
    push_scene/push_state 분기까지 그쪽이 처리하므로 여기서 중복 구현하지 않는다."""
    target = intent.get("revert_to_turn")
    if target is not None:   # LLM이 고른 turn이 현재 상태와 같으면(무변화) 안전망으로 재선택
        entry = next((h for h in scene_state.history if h["turn"] == int(target)), None)
        if entry is not None and entry["state"] == scene_state.robots:
            target = None
    if target is None:
        target = _pick_revert_target(scene_state)   # fallback: 현재와 다른 가장 최근 커밋
    res = context_tools.revert_to(target) if target is not None else {"error": "대상 없음"}
    if "error" in res:
        print("[revert] 실패: target=%s" % target)
        if viewer:
            viewer.chat("system", "되돌릴 대상을 찾지 못했어요.")
        return
    print("[revert] turn %d로 복원" % res["turn"])
    if viewer:
        viewer.chat("agent", "이전 배치로 되돌렸어요.")


def _referenced_motifs(intent):
    """확정 가구가 참조하는 motif 상세만 뽑아 형태층 입력에 주입한다 """
    data = _load_motifs() or {}
    table = data.get("motifs", {})
    keys = {f.get("motif") for f in (intent.get("furniture") or []) if f.get("motif")}
    return {k: table[k] for k in keys if k in table} or None


def _slim_states(scene_state):
    """형태층용 로봇 상태 — active·현재 형태(panels·furniture)만. panels는 [right, left] 순서쌍"""
    return [{"robot": s["robot"], "active": s.get("active"),
             "furniture": s.get("furniture"),
             "panels": [s.get("panel_right", 0), s.get("panel_left", 0)]}
            for s in scene_state.states()]


def _room_desc(scene_state):
    """기존 가구 이름 & description(능력 서술만)"""
    return [{"id": f.get("id"), "label": f.get("label"),
             "description": f.get("description")} for f in scene_state.furniture()]


def _validate_form(form, room_furniture=None):
    """스키마가 못 막는 도메인 규약. 위반 시 사유 문자열, 통과면 None.

    room_furniture는 Phase A가 실제로 본 목록(_room_desc)을 그대로 받는다 — 검증 기준과
    LLM이 본 세계가 갈리면 안 된다. id뿐 아니라 label도 통과시킨다: anchor_geometry가
    id·label 둘 다로 조회하므로 label로 온 앵커는 실제로 동작한다. 검증은 '규약에 안 맞는
    것'이 아니라 '쓸 수 없는 것'을 막는다 — 도는 입력을 거부하면 재호출 예산만 태운다.
    규약 안내는 프롬프트의 몫이다."""
    robots = form.get("robots") or []
    names = [r.get("robot") for r in robots]
    if len(names) != len(set(names)):
        return "같은 로봇을 두 번 지정했다. 로봇당 한 번만 지정하라."
    if len(robots) > len(config.ROBOT_NAMES):
        return "가용 로봇 수를 초과했다."
    for r in robots:
        p = r.get("panels")
        if not isinstance(p, list) or len(p) != 2:
            return "panels는 정확히 두 각도의 배열이어야 한다."
    # relation.anchor는 방의 기존 가구만. 로봇을 앵커로 쓰면 후보 생성 시점에 그 로봇이
    # 아직 도크에 있어 '도크 주변 밴드'가 기준이 된다 — 실측(파일럿 1턴): BOT 1이 도크
    # (380,20) rot0일 때 BOT 2를 그 곁에 놓았고, BOT 1은 나중에 (300,240) rot90으로 가서
    # 둘이 70cm 떨어진 채 서로 수직이 됐다. rot 일치 판정도 도크 rot과 맞은 것이었다.
    # 그리고 "바로 옆에서 꺼낼 수 있게"라는 틀린 설명이 사용자에게 나갔다.
    # 로봇끼리의 관계는 connection이 순차 배치(anchor를 먼저 놓고 moving을 상대 배치)라 정확하다.
    room_ids = set()
    for f in room_furniture or ():
        room_ids.update(v for v in (f.get("id"), f.get("label")) if v)
    for r in robots:
        rel = r.get("relation") or {}
        if rel.get("mode") in ("facing", "alongside"):
            a = rel.get("anchor")
            if a in names:
                return ("relation.anchor에 로봇을 쓸 수 없다. 로봇끼리 관계 지으려면 "
                        "connection을 써라(mode 'side'가 나란히 붙이기다).")
            if room_ids and a not in room_ids:
                return "relation.anchor가 방에 없는 가구다: %s" % a
    c = form.get("connection")
    if c and c.get("anchor") == c.get("moving"):
        return "로봇은 자기 자신과 연결할 수 없다."
    if c and (c.get("anchor") not in names or c.get("moving") not in names):
        return "연결 대상이 robots 목록에 없다."
    return None


def _parse_form(form, intent_type, scene_state):
    """형태층 출력 → (units, stores, connection). 누락의 의미는 intent_type이 정한다.

    robots에 담긴 로봇 중 mode:store는 정리 대상, 나머지는 배치 단위. robots에서 빠진
    로봇은 new_scene이고 현재 active면 정리, modify/add/remove면 손대지 않는다."""
    robots = form.get("robots") or []
    units, stores = [], []
    for r in robots:
        if (r.get("relation") or {}).get("mode") == "store":
            stores.append(r["robot"])
        else:
            units.append(r)
    present = {r["robot"] for r in robots}
    if intent_type == "new_scene":
        active = {s["robot"] for s in scene_state.states() if s.get("active") == "active"}
        for name in config.ROBOT_NAMES:
            if name not in present and name in active:   # 누락된 active → 도크 정리
                stores.append(name)
    return units, stores, form.get("connection")


_MODE_KO = {"facing": "마주 보는", "alongside": "곁에 나란한"}


def _relax_note(units, connection, stage):
    """폴백으로 무엇을 완화했는지 사람의 문장 하나로 (위치층 입력 전용, 사실만).

    위치층은 형태층 원안과 후보 목록을 나란히 본다. fallback을 타면 그 둘이 어긋나는데
    (Ex.원안은 '소파를 마주 봐라', 후보는 소파와 무관한 free 자리들), 왜 어긋나는지를 알려줌.
    사용될 케이스가 많지 않기에 각각을 명시함"""
    if stage == "band":
        return ("원안의 관계는 지켰지만 앵커 바로 곁에는 자리가 없어, 앵커에서 더 떨어진 "
                "자리까지 범위를 넓혀 찾았습니다. 후보가 원안이 그리던 것보다 멀 수 있습니다.")
    bits = []
    dropped = ["%s을(를) %s 자리" % (r["anchor"], _MODE_KO[r["mode"]])
               for r in ((u.get("relation") or {}) for u in units)
               if r.get("mode") in _MODE_KO and r.get("anchor")]
    if dropped:
        bits.append("원안의 관계(%s)대로는 놓을 자리가 없어, 관계를 풀고 빈 공간에서 "
                    "찾았습니다" % ", ".join(dropped))
    if connection:
        bits.append("두 대를 연결한 구성으로는 놓을 자리가 없어, 각각 따로 놓는 후보로 "
                    "찾았습니다")
    if not bits:
        return "원안 그대로는 후보가 없어 조건을 풀고 빈 공간에서 찾았습니다."
    return ". ".join(bits) + "."


def _enumerate_with_fallback(units, connection, env, states):
    """후보가 0개일 때: 밴드 확장 → free 폴백. 반환 (combos, relaxed) — 각 단계는 이벤트로.

    relaxed는 완화한 조건의 서술(fallback 없으면 None)이다. 호출부가 위치층 입력에 실어
    '원안과 후보의 불일치'가 코드의 완화 때문임을 알린다 (_relax_note 참조).
    states에서 정리 대상 로봇은 이미 빠져 있다(성공 후 store하므로 후보는 그 자리를 비운다)."""
    combos = layout.enumerate_units(units, env, states, connection=connection)
    if combos:
        return combos, None
    old = layout.BAND_MAX                     # 1단계: 앵커 밴드 확장
    try:
        layout.BAND_MAX = FORM_BAND_EXPAND
        combos = layout.enumerate_units(units, env, states, connection=connection)
    finally:
        layout.BAND_MAX = old
    if combos:
        eventlog.record("band_expand", to=FORM_BAND_EXPAND)
        print("[LAYOUT] 공집합 → 밴드 확장 %g→%g" % (old, FORM_BAND_EXPAND))
        return combos, _relax_note(units, connection, "band")
    free_units = [dict(u, relation={"mode": "free", "anchor": None}) for u in units]
    combos = layout.enumerate_units(free_units, env, states, connection=None)
    if not combos:
        return [], None
    eventlog.record("free_fallback")
    print("[LAYOUT] 공집합 → free 폴백")
    return combos, _relax_note(units, connection, "free")


def _diversity(combos):
    """제시 다양성 지표 : 코드가 몇가지의 후보를 제시했는지 기록

    min_apart·spread(자리들 사이 최소·최대 거리, cm)를 함께 남긴다. positions=6은 방 전체에
    퍼진 6곳인지 벽면 한 줄에 붙은 6곳인지 구분하지 못하는데, Stage 1의 첫 결함(raster 순서로
    벽에 뭉침)이 정확히 후자였고 그때도 positions는 6이었을 것이다. 지금은 _rank_thin의
    min_apart=80이 구조적으로 막지만 그건 코드가 지키는 것이지 로그로 증명되는 것이 아니다 —
    "후보 집합이 실제로 다양했다"를 논문에 보고하려면 '한 구석 아니었나'에 답할 숫자가
    필요하다. 자리가 1곳이면 쌍이 없어 둘 다 0이다."""
    positions, per_robot, rots_at = set(), {}, {}
    for c in combos:
        for p in c["placements"]:
            xy = (p["x"], p["y"])
            positions.add(xy)
            per_robot.setdefault(p["robot"], set()).add(xy)
            rots_at.setdefault(xy, set()).add(p["rot"])
    pts = sorted(positions)                   # 정렬 = 쌍 나열 순서 고정 (결정론)
    dists = [math.hypot(a[0] - b[0], a[1] - b[1])
             for i, a in enumerate(pts) for b in pts[i + 1:]]
    return {"combos": len(combos), "positions": len(positions),
            "per_robot_positions": {r: len(s) for r, s in per_robot.items()},
            "max_rots_per_position": max((len(s) for s in rots_at.values()), default=0),
            "min_apart": int(min(dists)) if dists else 0,
            "spread": int(max(dists)) if dists else 0}


_BODY_FACING_PANELS = (135, 180)   # 앞면이 본체 쪽인 각도 — 배향에 따라 사용면이 뒤집힌다.
                                   # 용도(등받이·독서대·파티션…)는 이 각도가 정하지 않는다.


def _directional_faces_ref(p):
    """이 placement의 135°·180° 패널 앞면이 ref를 향하는가. 그런 패널이 없으면 None.

    '방향성'은 배향에 따라 앞면이 뒤집힌다는 기하 서술이지 용도가 아니다 (§2 원칙 5)."""
    toward = (p.get("panel_faces") or {}).get("toward") or {}
    ff = toward.get("front_faces_ref") or {}
    angle = {"right": p["panels"][0], "left": p["panels"][1]}
    hits = [bool(ff.get(s)) for s in ("right", "left") if angle[s] in _BODY_FACING_PANELS]
    return any(hits) if hits else None


def _record_face_away(combos, place, chosen, units):
    """선택된 배치에서 앞면이 본체 쪽인 패널이 기준을 등졌고, 같은 자리에 향하는 배향이
    후보에 있었다 — 기하 사실이다. 오독인지 의도(파티션 등)인지는 사람이 판단한다.

    '오독 관측치'라고 부르지 않는다: 각도는 용도를 결정하지 않는다(§2 원칙 5). 180°는
    등받이일 수도 파티션·가림막·물건 기대는 면일 수도 있고, FORM_PROMPT가 파티션을
    "활동 구역을 나누는 경계"로 명시하는데 그 구성은 기준을 등지는 것이 정상이다.
    그래서 이벤트 이름의 뒷말을 '불일치'(오류 함의)에서 away(등짐=사실)로 바꿨다. 판단 재료(mode·furniture·asserted)를 이벤트에 실어 사람에게 넘긴다 —
    §17.4의 "코더는 감사하여 오류율만 보고한다"와 같은 구조다.

    facing/alongside에서만 기록한다. '등졌다'가 무언가를 뜻하려면 기준이 **향하기로 고른
    대상**이어야 한다 — free의 ref는 가장 가까운 벽이고(§1 벽 기준), 작업대가 벽 쪽에
    가림막을 세우면 front_faces_ref=false가 정상이다. 그걸 세면 관측이 노이즈가 된다.
    mode는 units에서 로봇 이름으로 조회한다 — placement payload에는 넣지 않는다(B-2 감축).
    막지 않는 것은 그대로다 — 막으면 코드가 방향을 정하게 되어 원칙 4의 역방향 위반이다."""
    modes = {u.get("robot"): (u.get("relation") or {}).get("mode") for u in units or []}
    for p in chosen["placements"]:
        mode = modes.get(p["robot"])
        if mode not in ("facing", "alongside"):
            continue
        if _directional_faces_ref(p) is not False:
            continue
        flip = any(_directional_faces_ref(q) for c in combos for q in c["placements"]
                   if q["robot"] == p["robot"] and (q["x"], q["y"]) == (p["x"], p["y"]))
        toward = (p.get("panel_faces") or {}).get("toward") or {}
        eventlog.record("place_face_away", robot=p["robot"], chosen_rot=p["rot"],
                        ref=toward.get("ref"), panels=list(p["panels"]),
                        mode=mode, furniture=p.get("furniture"),
                        flip_available=bool(flip),
                        asserted=(place.get("reason") or "")[:60])
        print("[EXEC] ⓘ %s(%s) 앞면이 %s를 등짐 (뒤집힌 짝 %s) — 관측만, 막지 않음"
              % (p["robot"], p.get("furniture"), toward.get("ref"),
                 "있었음" if flip else "없었음"))


def _execute(scene_state, combos, place, stores, forced, units):
    """선택된 조합을 실행한다 (정리→move→transform, §2-5·6.8). HITL-2(ask_user)는 호출부가 한다.

    정리(store)는 여기서 실행한다 — 성공(후보 확정) 경로에서만 상태를 바꿔, 공집합 포기
    시 로봇이 조용히 치워지는 일을 막는다. checks가 false여도 코드는 막지 않는다(로그만,
    원칙 4 역방향 위반 금지 §2-4). units는 _record_face_away의 mode 조회에만 쓴다.
    반환 msg는 HITL-2 문구."""
    idx = place.get("choice") or 0
    idx = idx if 0 <= idx < len(combos) else 0
    combo = combos[idx]
    print("[EXEC] 조합 #%d 선택 · checks=%s%s"
          % (idx, place.get("checks") or {}, " (강행)" if forced else ""))
    for name in stores:                       # 정리 대상 먼저 도크로 (성공 확정 후에만)
        placement_tools.store_robot(name)
        print("[EXEC] store %s" % name)
    if stores:
        eventlog.record("store", robots=stores)
    eventlog.record("phase_b_checks", checks=place.get("checks") or {}, forced=forced,
                    chosen=idx)
    _record_face_away(combos, place, combo, units)
    for p in combo["placements"]:
        placement_tools.move_robot(p["robot"], p["x"], p["y"], p["rot"])   # move 먼저
        st = placement_tools.transform_robot(p["robot"], panel_left=p["panels"][1],
                                             panel_right=p["panels"][0], furniture=p["furniture"])
        print("[EXEC] %s → (%d,%d) rot%d panels%s '%s'"
              % (p["robot"], p["x"], p["y"], p["rot"], p["panels"], p["furniture"]))
        if st.get("issues"):
            print("[EXEC] ⚠ %s 잔여 issues: %s" % (p["robot"], st["issues"]))
    return place.get("message") or "이렇게 배치해봤어요. 이대로 괜찮을까요?"


def _form_pipeline(openai_client, intent, sc, it, room, motifs, seed_reason):
    """형태층 1회 (LLM 재호출 예산 1회 §6.7 내장). 반환 (needs_hitl2, msg).

    seed_reason은 첫 Phase A에 실을 재구상 사유 (HITL-2 피드백 등). needs_hitl2=False면
    HITL-2 없이 종료(giveup·Phase A/B 실패), True면 msg를 HITL-2에 올려야 한다."""
    # units도 combos·stores와 같은 수명으로 둔다 — 강행 착지 분기가 루프 밖에 있어서
    # 루프 변수 누출에 의존하면 첫 시도가 continue로 끝난 경우 NameError가 된다.
    recall_reason, combos, stores, units = seed_reason, [], [], []
    for attempt in range(2):                  # 초기 1 + 재호출 1 (LLM 실패용)
        if attempt == 1 or recall_reason:
            if attempt == 1:
                tools.metric("form_recalls")
                eventlog.record("form_recall", reason=recall_reason)
            print("[RECALL] Phase A 사유: %s" % recall_reason)
        ss = layout.space_summary(sc.environment(), sc.states())
        print("[SPACE] span=%s pair(side/face)=%s/%s"
              % (ss["largest_fit"]["span"], ss["pair_connect_fits"]["side"],
                 ss["pair_connect_fits"]["face"]))
        form = ask_form(openai_client, intent, room_furniture=room, space_summary=ss,
                        states=_slim_states(sc), motifs=motifs, recall_reason=recall_reason)
        if not form:
            return False, "(형태층 Phase A 실패)"
        invalid = _validate_form(form, room)  # 스키마가 못 막는 규약 → 재호출 사유로 (§6.7)
        if invalid:
            eventlog.record("form_invalid", reason=invalid)
            print("[FORM] 규약 위반: %s" % invalid)
            recall_reason = invalid
            continue
        units, stores, connection = _parse_form(form, it, sc)
        print("[FORM] 단위 %d %s / 정리 %s / 연결 %s"
              % (len(units), [u.get("robot") for u in units], stores or "-", connection or "-"))
        if not units:                         # 순수 정리 (예: '다 치워') — 즉시 확정 (HITL-2로)
            for name in stores:
                placement_tools.store_robot(name)
            eventlog.record("store_only", robots=stores)
            return True, "말씀하신 로봇을 정리해 도크로 보냈어요. 이대로 괜찮을까요?"
        # 정리 대상은 후보 생성 시 '도크로 치운 상태'로 치환 — 도크 바닥을 차지(§12)하되 실제
        # store는 성공 확정 후에만. store와 같은 소스(dock_state, scene의 dock 우선 §10).
        store_set = set(stores)
        place_states = [sc.dock_state(s["robot"]) if s["robot"] in store_set else s
                        for s in sc.states()]
        combos, relaxed = _enumerate_with_fallback(units, connection,
                                                   sc.environment(), place_states)
        if not combos:
            tools.metric("empty_set")
            eventlog.record("empty_set", intent_type=it)
            recall_reason = "제시할 후보가 없었다. 관계를 free로 풀거나 더 작은 구성으로 바꿔라."
            continue
        d = _diversity(combos)
        # modes를 함께 남긴다 — 이것도 소급 불가다. 후보 집합이 사라지면 어느 모드였는지
        # 복원할 방법이 없고(Phase A 출력은 이벤트로 남지 않는다), 그러면 combos=3이
        # 'facing이라 띠가 좁은 것'인지 '방이 빡빡한 것'인지 사후에 가를 수 없다.
        eventlog.record("form_diversity",
                        modes=[(u.get("relation") or {}).get("mode") for u in units], **d)
        print("[LAYOUT] 조합 %d · 자리 %d곳(이격 %d~%d) · 자리당 rot 최대 %d · 로봇별 자리 %s"
              % (d["combos"], d["positions"], d["min_apart"], d["spread"],
                 d["max_rots_per_position"], d["per_robot_positions"]))
        if relaxed:
            print("[LAYOUT] 완화 사실을 Phase B에 전달: %s" % relaxed)
        # 폴백을 탔으면 그 사실을 함께 넘긴다 — 원안과 후보의 불일치를 Phase B가 '후보가
        # 나쁘다'로 오독해 거부(=Phase A 재구상 한 라운드)를 태우던 낭비를 막는다.
        place = ask_place(openai_client, form, combos, intent, relaxed=relaxed)
        if not place:
            return False, "(형태층 Phase B 실패)"
        choice, reject = place.get("choice"), place.get("reject_reason")
        if choice is not None and reject is not None:   # 스키마가 못 막는 규약 — 코드가 검사
            eventlog.record("phase_b_malformed", choice=choice, reject=reject)
        if choice is None:                    # 거부 → Phase A 재구상
            tools.metric("phase_b_rejects")
            eventlog.record("phase_b_reject", reason=reject)
            recall_reason = "Phase B가 후보를 거부: " + str(reject)
            continue
        return True, _execute(sc, combos, place, stores, forced=False, units=units)

    if combos:                                # 예산 소진 → 강행 착지 (§6.7) — 정리 포함
        tools.metric_set("forced_landing", True)
        eventlog.record("forced_landing")
        print("[LAND] 예산 소진 → 최고 점수 후보로 강행")
        # 문구에 '자리를 찾기 어려웠지만' 같은 예고를 붙이지 않는다 — 강행 착지는 배치가
        # 나쁠 가능성이 가장 높은 경우고 그래서 참가자의 순수한 반응이 가장 값진 지점이다.
        # 미리 기대를 낮추면 이후의 거부가 배치 때문인지 예고 때문인지 갈라낼 수 없다(부정적
        # 프라이밍). 강행이었다는 사실은 forced_landing 이벤트에 남으므로 분석 때 다 안다 —
        # relaxed와 같은 분리다: 코드는 알고, 참가자는 배치만 본다 (§15.6·§17.5).
        forced = {"choice": 0, "checks": {},
                  "message": "이렇게 배치해봤어요. 이대로 괜찮을까요?"}
        return True, _execute(sc, combos, forced, stores, forced=True, units=units)

    eventlog.record("form_giveup", intent_type=it)
    viewer = tools.STATE.get("viewer")
    if viewer:
        viewer.chat("agent", "이번 요청에 맞는 배치를 찾지 못했어요. 조금 다르게 말씀해 주실 수 있을까요?")
    return False, "(형태층 후보 없음)"


def _run_form_layer(openai_client, intent, utterance):
    """형태층 (§6). space_summary → Phase A → 코드 열거 → Phase B → 실행 → HITL-2.

    HITL-2 승인 거부 + 피드백이면 그 피드백을 사유로 Phase A부터 다시 구상한다 (FORM_HITL2_MAX
    회, §6.7 LLM 예산과 별개). HITL-1 피드백 재분석(handle)과 대칭. run_agent(tool 루프)를 대체."""
    sc = tools.STATE["scene"]
    # commit_layout(HITL-2 승인 경로)이 읽는다 — 구 run_agent가 하던 배선. 없으면 모든 커밋이
    # intent_type="new_scene", utterance=""로 기록되어 상황 경계(§5)·history 발화 연결이 무너진다.
    tools.STATE["intent"] = intent
    tools.STATE["utterance"] = utterance
    it = intent.get("intent_type")
    room, motifs = _room_desc(sc), _referenced_motifs(intent)
    seed_reason = None
    # _execute가 이동·변형·정리를 '먼저' 하고 승인을 나중에 받으므로, 승인 없이 끝나면
    # 거부된 배치가 다음 턴의 시작 상태가 된다. 규칙: **루프 안에서는 유지, 루프를 벗어나는
    # 순간 승인이 아니면 롤백**. 루프 안 유지는 의도한 설계다 — 거부+피드백 재구상에서
    # Phase A가 _slim_states로 '무엇을 거부당했는지' 봐야 하고(A), 뷰어가 도크로 갔다
    # 40초 뒤 다시 배치되면 참가자에게 깜빡임으로 보인다. 승인 시엔 ask_user가 이미
    # 커밋했으므로 롤백 대상이 아니다.
    baseline = sc.snapshot()

    def rollback():
        if sc.space == baseline["space"] and sc.robots == baseline["robots"]:
            return            # 실행된 것이 없다 — 되돌릴 것도, 기록할 것도 없다
        if sc.restore(baseline):
            tools.push_scene()
        else:
            tools.push_state(duration=0)      # 되돌리기는 애니메이션 없이
        eventlog.record("form_rollback")

    try:
        for hitl2_round in range(FORM_HITL2_MAX):
            needs_hitl2, msg = _form_pipeline(openai_client, intent, sc, it, room,
                                              motifs, seed_reason)
            if not needs_hitl2:               # giveup·Phase A/B 실패 — HITL-2 없이 종료
                rollback()                    # 1라운드면 no-op, 재구상 라운드면 앞 라운드 실행분 회수
                return msg
            res = viewer_tools.ask_user(msg)  # HITL-2 (+승인 시 자동 commit)
            if res.get("approved"):
                return msg
            if res.get("aborted") or not res.get("feedback"):
                rollback()                    # 중단·피드백 없는 거부 → 실행분 회수
                return msg
            # 거부 + 피드백 → 그 피드백을 사유로 Phase A부터 다시 (라운드마다 신선한 LLM 예산)
            seed_reason = "사용자가 이 배치를 거부하며 요청함: " + res["feedback"]
            eventlog.record("hitl2_feedback", round=hitl2_round + 1, feedback=res["feedback"])
            tools.metric_set("hitl2_feedback_depth", hitl2_round + 1)
    except Exception:
        rollback()                            # 예외로 중단돼도 반쪽 배치를 남기지 않는다
        raise

    # 예산 소진 — 반드시 안내한다. 조용히 끝내면 "말했는데 반응 없음"(A를 고친 이유)이 재발한다.
    rollback()
    eventlog.record("hitl2_budget_exhausted")
    viewer = tools.STATE.get("viewer")
    if viewer:
        viewer.chat("agent", "여러 번 맞춰봤는데 잘 안 되네요. 조금 다르게 말씀해 주시겠어요?")
    return "(HITL-2 피드백 예산 소진)"


def handle(openai_client, scene_state, text, last_intent):
    """발화 하나 처리: 의도층 → HITL-1 → 라우팅 → 형태층.

    HITL-1에서 거부되고 피드백이 있으면 그 피드백을 새 발화로 삼아 같은 루프를 다시 돈다
    (최초 1회 + 재시도 3회). metrics는 handle_logged가 발화 단위로 관리하므로 루프 내내 누적된다."""
    viewer = tools.STATE.get("viewer")

    def reask(t):
        """의도층 재분석 — 호출 형태가 세 군데 모두 같아 한 벌로 묶었다."""
        return ask_intent(openai_client, t, last_intent,
                          room_furniture=scene_state.furniture(),
                          recent_history=_slim_history(scene_state))

    for attempt in range(4):   # 최초 1회 + HITL-1 거부 피드백 재시도 3회
        intent = reask(text)
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
            intent = reask(text)
            if not intent:
                return last_intent

        # 되묻기 한도(2회) 도달 후에도 미해소면: 더 묻지 않고 LLM이 남은 정보를 추론해 채우게 한다.
        if intent.get("needs_clarification"):
            tools.metric_set("clarify_limit_hit", True)
            text = text + " / (되묻기 한도 도달)"
            intent = reask(text) or intent

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
                if feedback and attempt < 3:   # 피드백을 새 발화로 재분석 (거부된 intent를 맥락으로)
                    tools.metric_set("hitl1_feedback_depth", attempt + 1)
                    text = feedback
                    last_intent = intent
                    continue
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

        answer = _run_form_layer(openai_client, intent, text)
        # 형태층의 마무리 발화는 채팅에 올리지 않는다 — ask_user 승인 문구·확정 안내와
        # 내용이 중복되기 때문. 콘솔 로그로만 남긴다.
        print("[form] " + str(answer))
        return intent

    # 루프의 모든 경로가 return/continue이므로 도달하지 않는다 — 정적 안전망.
    return last_intent


def handle_logged(openai_client, scene_state, session_id, seq, text, input_mode,
                  last_intent):
    """handle()을 감싸 발화 1건 = metrics.jsonl 1줄을 남긴다 (실험 데이터, §14-2).

    HITL-1 거부 피드백의 재시도 재분석도 같은 발화 레코드에 누적된다.
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
    try:
        # events는 commit과 수명이 다르다(§17.6 D2) — 커밋 없이 끝난 턴(HITL-2 거부·form_giveup·
        # empty_set)이 바로 T10(기하 간극)의 신호이므로 커밋 여부와 무관하게 반드시 남긴다.
        scene_state.save()
    except OSError as e:
        # 저장 실패가 세션을 죽이면 안 된다 — 참가자 데이터는 재수집 불가이고, 여기서 죽으면
        # '왜 멈췄는지'조차 안 남는다. main()의 루프는 queue.get만 감싸므로 여기서 던지면
        # 프로세스가 끝난다. 호출 빈도가 커밋 수에서 발화 수로 늘어 노출도 커졌다.
        # eventlog는 메모리 append라 이 실패와 무관하게 남는다 — 다음 턴 save가 성공하면 실린다.
        traceback.print_exc()
        eventlog.record("persistence_error", error=str(e))
        tools.metric_set("persistence_error", True)
        viewer = tools.STATE.get("viewer")
        if viewer:
            viewer.chat("system", "배치는 처리됐지만 세션 기록을 저장하지 못했어요.")
    finally:
        tools.STATE["metrics"] = None
    return result


def main():
    scene_state = SceneState()
    scene_state.load_scene(DEFAULT_SPACE)
    # 실험 세션 식별자 — metrics.jsonl의 모든 레코드에 박힌다 (조건은 mode로 구분)
    session_id = time.strftime("%Y%m%d_%H%M%S") + "_agent"
    eventlog.reset()   # append-only 이벤트 로그의 서버 시각 원점 (§17.7)

    # 프로그램 재시작은 도크에서 새로 시작한다 (resume 안 함). 브라우저 새로고침(F5)은
    # 파이썬 프로세스가 살아 있어 뷰어가 현재 스냅샷을 다시 push하므로 가구·로봇이 유지된다.
    # commit 시 logs/session.json은 계속 기록된다 (로그·디버깅용).
    from openai import OpenAI   # 지연 import — mock 테스트가 openai 없이 main을 import할 수 있게
    openai_client = OpenAI(api_key=config.OPENAI_API_KEY)

    viewer = None
    if "--noview" not in sys.argv:
        from viewer.popup_viewer import PopupViewer
        viewer = PopupViewer()
        viewer.start(scene_state.environment(), scene_state.states())
        print("[viewer] http://127.0.0.1:8765 — 채팅창에 발화를 입력하세요")
    tools.init(scene_state, viewer)

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
