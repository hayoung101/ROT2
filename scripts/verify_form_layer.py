# -*- coding: utf-8 -*-
"""형태층 Phase A/B 회귀 테스트 — mock Phase A/B (실키 호출 없음).

python scripts/verify_form_layer.py

_parse_form의 누락 시맨틱·폴백 체인·강행 착지·store 실행 시점을 코드 읽기로는 놓친다
(Stage 1의 세 결함도 전부 실행해서 나왔다). 이 스크립트는 그 경로들을 실제로 태운다.
"""
import os
import sys
import json
import tempfile
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tools          # noqa: E402
import main           # noqa: E402
import agent          # noqa: E402
from services import eventlog, layout   # noqa: E402
from services.scene import SceneState   # noqa: E402


class FakeViewer:
    def __init__(self): self.pushes = []; self.chats = []; self.clients = [1]
    def push_state(self, states, duration=1.2): self.pushes.append(("state", round(duration, 2)))
    def push_scene(self, env, states): self.pushes.append(("scene",))
    def chat(self, who, msg): self.chats.append((who, msg))
    def request_approval(self, m): return {"approved": True, "feedback": ""}


def fresh(space="living_room"):
    sc = SceneState(session_path=tempfile.mktemp(suffix=".json"))
    sc.load_scene(space)
    tools.init(sc, FakeViewer())
    tools.STATE["auto_approve"] = True
    tools.STATE["metrics"] = tools.new_metrics()
    tools.STATE["intent"] = {"intent_type": "new_scene"}
    tools.STATE["utterance"] = "test"
    eventlog.reset()
    return sc


def ev(): return [e["type"] for e in eventlog.events()]
def line(t): print("\n=== %s ===" % t)
def verdict(ok): print("  =>", "PASS" if ok else "FAIL"); return ok


def form1(robot, panels, mode, anchor=None):
    return lambda *a, **k: {"robots": [{"robot": robot, "furniture": "f", "panels": panels,
            "relation": {"mode": mode, "anchor": anchor}, "rationale": "r"}], "connection": None}


PLACE_OK = {"checks": {"blocks_path": False, "within_reach": True, "matches_count": True},
            "reason": "ok", "choice": 0, "reject_reason": None, "message": "소파 곁에 자리를 만들었어요"}
INTENT = lambda t: {"intent_type": t, "activity": "차", "number": 2, "posture": "sitting", "furniture": []}
results = []


# A. new_scene 누락(active) → 성공 시 store
line("A. new_scene 누락(active) → 배치 성공하면 store")
sc = fresh(); sc.transform("BOT 2", 90, 90); sc.move("BOT 2", 60, 120, 0)
main.ask_form = form1("BOT 1", [90, 90], "facing", "sofa_1"); main.ask_place = lambda *a, **k: dict(PLACE_OK)
main._run_form_layer(None, INTENT("new_scene"), "차")
st = {s["robot"]: s["active"] for s in sc.states()}
print("  상태:", st)
results.append(verdict(st["BOT 2"] == "inactive" and st["BOT 1"] == "active"))

# B. modify 누락 → 유지
line("B. modify 누락 → 유지")
sc = fresh()
sc.transform("BOT 1", 0, 0); sc.move("BOT 1", 100, 100, 0)
sc.transform("BOT 2", 90, 90); sc.move("BOT 2", 60, 300, 0)
before = dict(next(s for s in sc.states() if s["robot"] == "BOT 2"))
main.ask_form = form1("BOT 1", [180, 0], "free"); main.ask_place = lambda *a, **k: dict(PLACE_OK)
main._run_form_layer(None, INTENT("modify"), "옮겨")
results.append(verdict(next(s for s in sc.states() if s["robot"] == "BOT 2") == before))

# C. remove 두 대 store → 둘 다 inactive
line("C. remove: 두 대 모두 mode:store → 둘 다 inactive")
sc = fresh()
sc.transform("BOT 1", 90, 90); sc.move("BOT 1", 150, 150, 0)
sc.transform("BOT 2", 90, 90); sc.move("BOT 2", 60, 300, 0)
main.ask_form = lambda *a, **k: {"robots": [
    {"robot": r, "furniture": "정리", "panels": [0, 0], "relation": {"mode": "store", "anchor": None}, "rationale": "r"}
    for r in ("BOT 1", "BOT 2")], "connection": None}
main._run_form_layer(None, INTENT("remove"), "다 치워")
st = {s["robot"]: s["active"] for s in sc.states()}
results.append(verdict(st["BOT 1"] == "inactive" and st["BOT 2"] == "inactive"))

# D. reject×2 → 강행 착지
line("D. reject → 재호출 → reject → 강행 착지")
sc = fresh()
main.ask_form = form1("BOT 1", [90, 90], "facing", "sofa_1")
main.ask_place = lambda *a, **k: {"checks": {"blocks_path": True, "within_reach": False, "matches_count": True},
                                  "reason": "별로", "choice": None, "reject_reason": "전부 통로를 막음", "message": ""}
msg_d = main._run_form_layer(None, INTENT("new_scene"), "앉자")
b1 = next(s for s in sc.states() if s["robot"] == "BOT 1")
print("  이벤트:", ev())
results.append(verdict(ev().count("phase_b_reject") == 2 and "form_recall" in ev()
                       and "forced_landing" in ev() and b1["active"] == "active"))

# WW. 강행 착지 문구에 예고(부정적 프라이밍)가 없다 — 가장 값진 데이터 포인트(강행 배치에
#     대한 순수 반응)를 오염시킨다. 강행이었다는 사실은 forced_landing 이벤트에 남는다.
line("WW. 강행 착지 message에 '찾기 어려웠'·'자리가 없' 류가 없다")
print("  강행 문구:", msg_d)
results.append(verdict("찾기 어려웠" not in msg_d and "자리가 없" not in msg_d
                       and "어려웠지만" not in msg_d
                       and not agent._MESSAGE_LEAK.search(msg_d)
                       and not agent._MESSAGE_BANNED.search(msg_d)
                       and "forced_landing" in ev()))

# E. 공집합 → 폴백 → 재호출 → 포기 (Phase B 미호출)
line("E. 공집합 → 밴드/free 폴백 → 재호출 → 포기")
sc = fresh(); real = layout.enumerate_units; layout.enumerate_units = lambda *a, **k: []
calls = {"p": 0}
main.ask_form = form1("BOT 1", [90, 90], "facing", "sofa_1")
def _p(*a, **k): calls["p"] += 1; return dict(PLACE_OK)
main.ask_place = _p
main._run_form_layer(None, INTENT("new_scene"), "앉자")
layout.enumerate_units = real
print("  이벤트:", ev(), "Phase B 호출:", calls["p"])
results.append(verdict(ev().count("empty_set") == 2 and ev()[-1] == "form_giveup" and calls["p"] == 0))

# F. message 정규식 → 재생성 1회
line("F. message '90도' → 재생성 1회 후 클린")
tools.STATE["metrics"] = tools.new_metrics()
def _mk(msg): return json.dumps({"checks": {"blocks_path": False, "within_reach": True, "matches_count": True},
                                 "reason": "r", "choice": 0, "reject_reason": None, "message": msg}, ensure_ascii=False)
class R:
    def __init__(s, o): s.o = o; s.i = 0
    def create(s, **k):
        v = s.o[min(s.i, len(s.o) - 1)]; s.i += 1; return types.SimpleNamespace(output_text=v)
res = agent.ask_place(types.SimpleNamespace(responses=R([_mk("패널을 90도로 폈어요"), _mk("소파 곁에 자리를 만들었어요")])),
                      {"robots": []}, [{"placements": []}], {"activity": "x"})
print("  최종:", res["message"], "| regen(term/leak): %d/%d"
      % (tools.STATE["metrics"]["place_regen_term"], tools.STATE["metrics"]["place_regen_leak"]))
results.append(verdict(res["message"] == "소파 곁에 자리를 만들었어요"
                       and tools.STATE["metrics"]["place_regen_term"] == 1
                       and tools.STATE["metrics"]["place_regen_leak"] == 0))

# G. events sibling + revert 후 유지
line("G. events가 commits 형제 + revert 후 유지")
sc = fresh(); eventlog.record("diversity", positions=6)
sc.commit("t1", "new_scene", "u"); sc.save()
d = json.load(open(sc.session_path, encoding="utf-8")); n1 = len(d["events"])
sc.revert_to(1); sc.save(); n2 = len(json.load(open(sc.session_path, encoding="utf-8"))["events"])
print("  필드:", sorted(d.keys()), "events:", n1, "→", n2)
results.append(verdict("events" in d and "history" in d and n1 >= 1 and n2 == n1))

# H. 공집합 포기 시 로봇이 안 치워진다 (실패 경로가 상태를 바꾸지 않는다)
line("H. 공집합 포기 → 누락된 active 로봇이 그대로 남는다")
sc = fresh(); sc.transform("BOT 2", 90, 90); sc.move("BOT 2", 60, 120, 0)
real = layout.enumerate_units; layout.enumerate_units = lambda *a, **k: []
main.ask_form = form1("BOT 1", [90, 90], "facing", "sofa_1"); main.ask_place = lambda *a, **k: dict(PLACE_OK)
main._run_form_layer(None, INTENT("new_scene"), "앉자")
layout.enumerate_units = real
b2 = next(s for s in sc.states() if s["robot"] == "BOT 2")
print("  BOT 2 active:", b2["active"], "(active 유지여야 = 실패가 상태를 안 바꿈)")
print("  store 이벤트 없음:", "store" not in ev())
results.append(verdict(b2["active"] == "active" and "store" not in ev()))

# happy path: llm_calls = 2
line("happy path llm_calls = 2 (ask_form + ask_place)")
tools.STATE["metrics"] = tools.new_metrics()
fj = json.dumps({"robots": [{"robot": "BOT 1", "furniture": "t", "panels": [90, 90],
        "relation": {"mode": "facing", "anchor": "sofa_1"}, "rationale": "r"}], "connection": None}, ensure_ascii=False)
c = types.SimpleNamespace(responses=R([fj, _mk("소파 곁에 자리를 만들었어요")]))
f = agent.ask_form(c, {"intent_type": "new_scene", "furniture": []})
agent.ask_place(c, f, [{"placements": []}], {"activity": "x"})
print("  llm_calls:", tools.STATE["metrics"]["llm_calls"])
results.append(verdict(tools.STATE["metrics"]["llm_calls"] == 2))

# ── handle_logged 통과 헬퍼 (I·J·K는 실제 커밋/저장 경로를 태운다) ──────────
class TurnViewer(FakeViewer):
    def __init__(self, approvals):
        super().__init__(); self.approvals = list(approvals); self.i = 0
    def request_approval(self, message, timeout=None):
        v = self.approvals[min(self.i, len(self.approvals) - 1)]; self.i += 1
        return {"approved": v, "feedback": ""}
    def ask(self, q, c=None, timeout=None): return ""


NEW_INTENT = {"intent_type": "new_scene", "space": "living_room", "number": 2,
              "situation": "차", "activity": "차", "posture": "sitting",
              "furniture": [{"item": "티테이블", "count": 1}], "revert_to_turn": None,
              "needs_clarification": False, "clarification_question": None,
              "confirmation_message": "차 준비할게요"}
FUNC_OK = {"furniture": [{"item": "티테이블", "count": 1, "motif": None,
                          "feasible": True, "reason": None}], "complement_note": None}


def run_turn(text, approvals, auto_approve, form, place, enumerate_empty=False):
    sc = SceneState(session_path=tempfile.mktemp(suffix=".json"))
    sc.load_scene("living_room")
    tools.init(sc, TurnViewer(approvals))
    tools.STATE["auto_approve"] = auto_approve
    eventlog.reset()
    main.ask_intent = lambda *a, **k: dict(NEW_INTENT)
    main.ask_function = lambda *a, **k: dict(FUNC_OK)
    main.ask_form = form
    main.ask_place = place
    real = layout.enumerate_units
    if enumerate_empty:
        layout.enumerate_units = lambda *a, **k: []
    try:
        main.handle_logged(None, sc, "sess", 1, text, "typed", None)
    finally:
        layout.enumerate_units = real
    return sc, json.load(open(sc.session_path, encoding="utf-8"))


FORM = form1("BOT 1", [90, 90], "facing", "sofa_1")

# I. happy path 커밋 후 intent_type·utterance가 실제 값으로 기록된다
line("I. 커밋 history[-1] intent_type·utterance 배선")
sc, d = run_turn("차 마시자", [True], True, FORM, lambda *a, **k: dict(PLACE_OK))
h = d["history"][-1]
print("  history[-1]:", {"intent_type": h["intent_type"], "utterance": h["utterance"]})
results.append(verdict(h["intent_type"] == "new_scene" and h["utterance"] == "차 마시자"))

# J. HITL-2 거부 턴 후 session.json에 phase_b_checks 존재 (커밋 안 됨)
line("J. HITL-2 거부 턴 → phase_b_checks가 파일에 남는다")
sc, d = run_turn("차 마시자", [True, False], False, FORM, lambda *a, **k: dict(PLACE_OK))
types_ = [e["type"] for e in d["events"]]
print("  events:", types_, "| turn:", d["turn"])
results.append(verdict("phase_b_checks" in types_ and d["turn"] == 0))

# K. 공집합 포기 턴 후 session.json에 empty_set·form_giveup 존재 (커밋 안 됨)
line("K. 공집합 포기 턴 → empty_set·form_giveup이 파일에 남는다")
sc, d = run_turn("차 마시자", [True], False, FORM, lambda *a, **k: dict(PLACE_OK), enumerate_empty=True)
types_ = [e["type"] for e in d["events"]]
print("  events:", types_, "| turn:", d["turn"])
results.append(verdict("empty_set" in types_ and "form_giveup" in types_ and d["turn"] == 0))

# K-static. save() 호출이 if committed 밖에 있다 (1-3 되돌림 방지)
#   들여쓰기 문자열 대신 AST로 본다 — save()가 try 블록에 들어가며(2차리뷰 2번) 들여쓰기가
#   깊어졌지만, 검사해야 할 것은 "커밋 여부에 걸려 있지 않은가"이지 열 위치가 아니다.
line("K-static. handle_logged의 save()가 if committed에 걸려 있지 않다")
import ast as _ast   # noqa: E402
_tree = _ast.parse(open("main.py", encoding="utf-8").read())
_fn = next(n for n in _ast.walk(_tree)
           if isinstance(n, _ast.FunctionDef) and n.name == "handle_logged")


def _has_save(node):
    return any(isinstance(c, _ast.Call) and isinstance(c.func, _ast.Attribute)
               and c.func.attr == "save" for c in _ast.walk(node))


_committed_ifs = [n for n in _ast.walk(_fn) if isinstance(n, _ast.If)
                  and isinstance(n.test, _ast.Name) and n.test.id == "committed"]
_in_if = any(_has_save(n) for n in _committed_ifs)
print("  save() 존재:", _has_save(_fn), "| if committed 안:", _in_if,
      "| if committed 블록 수:", len(_committed_ifs))
results.append(verdict(_has_save(_fn) and _committed_ifs and not _in_if))

# FF. save() 실패가 턴을 죽이지 않는다 (2차리뷰 2번)
line("FF. save()가 OSError → 정상 반환 + metrics 초기화 + persistence_error 이벤트")
sc = SceneState(session_path=tempfile.mktemp(suffix=".json"))
sc.load_scene("living_room")
tools.init(sc, TurnViewer([True]))
tools.STATE["auto_approve"] = True
eventlog.reset()


def _boom():
    raise OSError("[Errno 13] Permission denied: 'logs/session.json'")


main.ask_intent = lambda *a, **k: dict(NEW_INTENT)
main.ask_function = lambda *a, **k: dict(FUNC_OK)
main.ask_form = FORM
main.ask_place = lambda *a, **k: dict(PLACE_OK)
# 턴 안의 커밋 save는 정상, handle_logged '마지막' save만 실패시킨다 — 검증 대상이 그 지점이다
_real_handle = main.handle


def _handle_then_break(*a, **k):
    r = _real_handle(*a, **k)
    sc.save = _boom
    return r


main.handle = _handle_then_break
try:
    _ret = main.handle_logged(None, sc, "sess", 1, "차 마시자", "typed", None)
finally:
    main.handle = _real_handle
_pe = [e for e in eventlog.events() if e["type"] == "persistence_error"]
print("  반환:", type(_ret).__name__, "| metrics:", tools.STATE["metrics"],
      "| persistence_error:", _pe)
results.append(verdict(_ret is not None and tools.STATE["metrics"] is None
                       and len(_pe) == 1 and "Permission denied" in _pe[0]["error"]))

# L. clients 빈 상태 request_approval → 1초 내 aborted (영구 블로킹 없음)
line("L. 연결 끊김 시 request_approval이 aborted로 즉시 반환")
try:
    import time as _t
    from viewer.popup_viewer import PopupViewer
    pv = PopupViewer()                      # start() 안 함 — 서버·브라우저 없음
    pv.clients = set()                      # 아무도 안 붙어 있음
    t0 = _t.time(); res = pv.request_approval("승인?"); dt = _t.time() - t0
    print("  결과:", res, "| %.2fs" % dt)
    results.append(verdict(res.get("aborted") is True and dt < 1.0))
except ImportError as e:
    print("  SKIP (뷰어 의존성 없음):", e); results.append(True)

# M. message 재생성 시 choice·checks·reason은 1회차 유지 (message만 교체)
line("M. 재생성 후 choice·checks·reason 불변, message만 교체")
tools.STATE["metrics"] = tools.new_metrics()
first = json.dumps({"checks": {"blocks_path": True, "within_reach": False, "matches_count": True},
                    "reason": "근거1", "choice": 0, "reject_reason": None, "message": "패널 90도로"}, ensure_ascii=False)
second = json.dumps({"checks": {"blocks_path": False, "within_reach": True, "matches_count": False},
                     "reason": "근거2", "choice": 1, "reject_reason": None, "message": "소파 곁에 자리를 만들었어요"}, ensure_ascii=False)
r = agent.ask_place(types.SimpleNamespace(responses=R([first, second])), {"robots": []}, [{"placements": []}], {"activity": "x"})
print("  choice:", r["choice"], "| reason:", r["reason"], "| blocks_path:", r["checks"]["blocks_path"], "| message:", r["message"])
results.append(verdict(r["choice"] == 0 and r["reason"] == "근거1" and r["checks"]["blocks_path"] is True
                       and r["message"] == "소파 곁에 자리를 만들었어요"))

# N. 연결 조합 주석이 짝 로봇을 본다 (nearby에 상대 출현 — 2-2 회귀)
line("N. 연결 조합의 두 placements 모두 nearby에 상대 로봇이 나타난다")
scn = json.load(open("scenes/living_room.json", encoding="utf-8"))
st = [{"robot": "BOT 1", "active": "inactive", "x": 380, "y": 20, "rot": 0, "panel_left": 0, "panel_right": 0},
      {"robot": "BOT 2", "active": "inactive", "x": 320, "y": 20, "rot": 0, "panel_left": 0, "panel_right": 0}]
uu = [{"robot": "BOT 1", "furniture": "a", "panels": [45, 45], "relation": {"mode": "pair", "anchor": None}, "rationale": "r"},
      {"robot": "BOT 2", "furniture": "b", "panels": [45, 45], "relation": {"mode": "pair", "anchor": None}, "rationale": "r"}]
cn = {"anchor": "BOT 1", "moving": "BOT 2", "mode": "face", "side": "both"}   # 맞댈 각도는 panels에서 유도
p = layout.enumerate_units(uu, scn, st, connection=cn)[0]["placements"]
n0 = {x["id"] for x in p[0]["nearby"]}; n1 = {x["id"] for x in p[1]["nearby"]}
print("  BOT1 nearby:", n0, "clearance:", p[0]["clearance"])
print("  BOT2 nearby:", n1, "clearance:", p[1]["clearance"])
results.append(verdict("BOT 2" in n0 and "BOT 1" in n1))

# N-2. pair 조합이 Phase B 경로를 통과해 실행된다 (2-3 프롬프트 변경이 경로/스키마 안 깸)
line("N-2. pair 조합이 거부 없이 실행된다 (경로 확인 — 품질은 실키)")
sc = fresh()
main.ask_form = lambda *a, **k: {"robots": uu, "connection": cn}
main.ask_place = lambda *a, **k: dict(PLACE_OK)
main._run_form_layer(None, INTENT("new_scene"), "책장")
st2 = {s["robot"]: s["active"] for s in sc.states()}
print("  상태:", st2, "| phase_b_reject:", "phase_b_reject" in ev())
results.append(verdict(st2["BOT 1"] == "active" and st2["BOT 2"] == "active" and "phase_b_reject" not in ev()))

# R. HITL-2 거부+피드백 → Phase A 재구상 → 승인 → 커밋
line("R. HITL-2 거부+피드백 → 재구상 → 승인 → 커밋")
sc = SceneState(session_path=tempfile.mktemp(suffix=".json")); sc.load_scene("living_room")


class FBViewer(FakeViewer):
    def __init__(self): super().__init__(); self.seq = [{"approved": False, "feedback": "더 크게 해줘"},
                                                        {"approved": True, "feedback": ""}]; self.k = 0
    def request_approval(self, message, timeout=None):
        r = self.seq[min(self.k, len(self.seq) - 1)]; self.k += 1; return r


tools.init(sc, FBViewer())
tools.STATE["auto_approve"] = False; tools.STATE["metrics"] = tools.new_metrics(); eventlog.reset()
calls = []
def form_cap(*a, **k):
    calls.append(k.get("recall_reason"))
    return {"robots": [{"robot": "BOT 1", "furniture": "t", "panels": [90, 90],
                        "relation": {"mode": "facing", "anchor": "sofa_1"}, "rationale": "r"}], "connection": None}
main.ask_form = form_cap; main.ask_place = lambda *a, **k: dict(PLACE_OK)
main._run_form_layer(None, dict(NEW_INTENT), "차 마시자")
fb_ev = [e for e in eventlog.events() if e["type"] == "hitl2_feedback"]
print("  ask_form recall_reason:", calls)
print("  hitl2_feedback:", fb_ev, "| turn:", sc.turn, "| depth:", tools.STATE["metrics"]["hitl2_feedback_depth"])
results.append(verdict(len(calls) >= 2 and calls[0] is None and "더 크게" in (calls[1] or "")
                       and len(fb_ev) == 1 and sc.turn == 1
                       and tools.STATE["metrics"]["hitl2_feedback_depth"] == 1))

# S. A-2 — 함께 배치될 로봇이 서로의 후보를 안 막는다 (2라운드 자리 = 1라운드)
line("S. A-2 — 2라운드(방 한가운데) 후보 자리가 1라운드(도크)와 동등")
scn = json.load(open("scenes/living_room.json", encoding="utf-8"))
uu2 = [{"robot": "BOT 1", "furniture": "a", "panels": [90, 90], "relation": {"mode": "free", "anchor": None}, "rationale": "r"},
       {"robot": "BOT 2", "furniture": "b", "panels": [90, 90], "relation": {"mode": "free", "anchor": None}, "rationale": "r"}]
def bot1_pos(states):
    s = set()
    for c in layout.enumerate_units(uu2, scn, states):
        for p in c["placements"]:
            if p["robot"] == "BOT 1": s.add((p["x"], p["y"]))
    return s
dock = [{"robot": "BOT 1", "active": "inactive", "x": 380, "y": 20, "rot": 0, "panel_left": 0, "panel_right": 0},
        {"robot": "BOT 2", "active": "inactive", "x": 320, "y": 20, "rot": 0, "panel_left": 0, "panel_right": 0}]
mid = [{"robot": "BOT 1", "active": "active", "x": 240, "y": 120, "rot": 0, "panel_left": 90, "panel_right": 90},
       {"robot": "BOT 2", "active": "active", "x": 80, "y": 160, "rot": 0, "panel_left": 90, "panel_right": 90}]
d1, d2 = bot1_pos(dock), bot1_pos(mid)
print("  1라운드:", sorted(d1))
print("  2라운드:", sorted(d2))
results.append(verdict(d1 == d2 and len(d1) > 0))

# T. B-1 — llm_call 이벤트에 layer·model·elapsed 기록
line("T. B-1 — llm_call 이벤트에 layer·model·elapsed")
import config as _cfg
eventlog.reset(); tools.STATE["metrics"] = tools.new_metrics()
agent.ask_place(types.SimpleNamespace(responses=R([_mk("소파 곁에 자리를 만들었어요")])),
                {"robots": []}, [{"placements": []}], {"activity": "x"})
lc = [e for e in eventlog.events() if e["type"] == "llm_call"]
print("  llm_call:", lc)
results.append(verdict(len(lc) == 1 and lc[0]["layer"] == "place"
                       and lc[0]["model"] == _cfg.MODEL_PLACE and "elapsed" in lc[0]))

# ── U·V·W: 배향(rot vs rot+180) 다양성 — facing 필터가 배향을 한쪽으로 강제하던 결함 ──
import math as _m   # noqa: E402

DOCK = [{"robot": "BOT 1", "active": "inactive", "x": 380, "y": 20, "rot": 0, "panel_left": 0, "panel_right": 0},
        {"robot": "BOT 2", "active": "inactive", "x": 320, "y": 20, "rot": 0, "panel_left": 0, "panel_right": 0}]


def cands(panels, mode, anchor=None):
    scn = json.load(open("scenes/living_room.json", encoding="utf-8"))
    u = {"robot": "BOT 1", "furniture": "a", "panels": panels,
         "relation": {"mode": mode, "anchor": anchor}, "rationale": "r"}
    return scn, layout._unit_candidates(u, scn, DOCK, ["BOT 1", "BOT 2"])


def dots(scn, cs, anchor):
    (ax, ay), _, _ = layout.placement.anchor_geometry(scn, anchor, DOCK)
    return [(c, _m.cos(_m.radians(c["rot"])) * (ax - c["x"])
             + _m.sin(_m.radians(c["rot"])) * (ay - c["y"])) for c in cs]


# U. facing 후보 집합에 u·앵커 > 0과 < 0이 둘 다 존재한다
line("U. facing 후보에 양쪽 배향(u·앵커 부호 +/−)이 모두 존재")
ok_u = True
for anc in ("table_1", "sofa_1"):
    scn, cs = cands([180, 0], "facing", anc)
    ds = [round(d, 1) for _, d in dots(scn, cs, anc)]
    has = any(d > 0 for d in ds) and any(d < 0 for d in ds)
    print("  %s: n=%d dots=%s | 양쪽=%s" % (anc, len(cs), ds, has))
    ok_u = ok_u and has
results.append(verdict(ok_u))

# V. [180,0] facing 후보에서 자리당 rot이 배향 짝(앵커 쪽/반대쪽)으로 남는다
line("V. [180,0] facing — 자리당 rot이 배향 짝으로 남는다")
scn, cs = cands([180, 0], "facing", "table_1")
by_pos = {}
for c, d in dots(scn, cs, "table_1"):
    by_pos.setdefault((c["x"], c["y"]), []).append((c["rot"], d))
paired = [xy for xy, v in by_pos.items() if len(v) > 1]
ok_v = bool(paired) and all(any(d > 0 for _, d in v) and any(d < 0 for _, d in v)
                            for xy, v in by_pos.items() if len(v) > 1)
for xy, v in by_pos.items():
    print("  %s: %s" % (xy, [(r, round(d, 1)) for r, d in v]))
results.append(verdict(ok_v))

# ── PP~TT: free의 기준은 nearby[0]가 아니라 가장 가까운 벽이다 (구 W를 대체) ──
#   구 W는 toward.ref == nearby[0].id를 고정하고 있었다 — 그게 지금 걷어내는 동작이라
#   남겨두면 회귀 방지가 아니라 개선 방지가 된다. QQ가 그 자리를 받는다.

# QQ. 거실 free 후보의 ref가 사물이 아니라 wall_*이고 모두 ref_dist를 갖는다
line("QQ. free 후보 — ref가 wall_*, 모든 후보가 ref_dist 보유")
_, cs = cands([90, 0], "free")
bad = [c for c in cs
       if not str((c["panel_faces"]["toward"] or {}).get("ref", "")).startswith("wall_")
       or (c["panel_faces"]["toward"] or {}).get("ref_dist") is None]
print("  n=%d | 샘플 toward: %s" % (len(cs), cs[0]["panel_faces"]["toward"] if cs else None))
print("  ref 종류:", sorted({c["panel_faces"]["toward"]["ref"] for c in cs}))
print("  위반:", len(bad))
results.append(verdict(bool(cs) and not bad))

# PP. 가구가 하나도 없는 방에서도 toward가 채워진다 (구 nearby[0] 구멍)
line("PP. 가구 없는 방 — free 후보의 toward가 wall_*로 채워진다")
EMPTY_ROOM = {"width": 400, "depth": 300, "pre_existing_furniture": []}
u_pp = {"robot": "BOT 1", "furniture": "a", "panels": [90, 0],
        "relation": {"mode": "free", "anchor": None}, "rationale": "r"}
cs_pp = layout._unit_candidates(u_pp, EMPTY_ROOM, DOCK, ["BOT 1", "BOT 2"])
ok_pp = bool(cs_pp) and all(c["panel_faces"]["toward"]
                            and str(c["panel_faces"]["toward"]["ref"]).startswith("wall_")
                            for c in cs_pp)
print("  n=%d | nearby 비어있음: %s | 샘플 toward: %s"
      % (len(cs_pp), all(not c["nearby"] for c in cs_pp),
         cs_pp[0]["panel_faces"]["toward"] if cs_pp else None))
results.append(verdict(ok_pp))

# PP-2. toward가 None인 후보가 존재하지 않는다 (모든 모드·모든 방 + 앵커 조회 실패)
line("PP-2. toward None인 후보 없음 — 모든 모드·방, 앵커 조회 실패 포함")
scn_pp = json.load(open("scenes/living_room.json", encoding="utf-8"))
sweep = []
for panels, mode, anc in (([90, 0], "free", None), ([180, 0], "facing", "table_1"),
                          ([180, 0], "alongside", "sofa_1"), ([90, 90], "facing", "sofa_1")):
    _, cc = cands(panels, mode, anc)
    sweep.append(("%s/%s" % (mode, anc), len(cc),
                  all(c["panel_faces"]["toward"] for c in cc)))
sweep.append(("empty_room/free", len(cs_pp), all(c["panel_faces"]["toward"] for c in cs_pp)))
# 앵커를 못 찾는 경우도 벽으로 폴백한다 (band_filter가 먼저 걸러 실무상 도달 불가에 가깝지만
# 'toward는 항상 non-null'을 payload 계약으로 만든다)
a_bad = layout.annotate({"x": 100, "y": 60, "rot": 0}, scn_pp, DOCK, [90, 0],
                        anchor_id="없는가구_999")
sweep.append(("anchor_miss", 1, bool(a_bad["panel_faces"]["toward"])))
for name, n, ok in sweep:
    print("  %-22s n=%-3d toward 전부 non-null=%s" % (name, n, ok))
print("  앵커 조회 실패 시 ref:", a_bad["panel_faces"]["toward"]["ref"])
results.append(verdict(all(ok for _, _, ok in sweep)
                       and str(a_bad["panel_faces"]["toward"]["ref"]).startswith("wall_")))

# RR. 같은 자리의 뒤집힌 배향 짝이 벽 기준으로도 갈린다
line("RR. 같은 자리 rot vs rot+180 — 벽 기준에서 front_faces_ref가 서로 다르다")
ok_rr = True
for rot in (0, 45, 90, 135):
    t1 = layout.annotate({"x": 100, "y": 60, "rot": rot}, scn_pp, DOCK,
                         [180, 0])["panel_faces"]["toward"]
    t2 = layout.annotate({"x": 100, "y": 60, "rot": rot + 180}, scn_pp, DOCK,
                         [180, 0])["panel_faces"]["toward"]
    same_ref = t1["ref"] == t2["ref"] and t1["ref_dist"] == t2["ref_dist"]
    flipped = (t1["panel_on_ref_side"] != t2["panel_on_ref_side"]
               and t1["front_faces_ref"] != t2["front_faces_ref"])
    print("  rot %3d/%3d ref=%s: on_ref %s→%s | faces %s→%s"
          % (rot, rot + 180, t1["ref"], t1["panel_on_ref_side"], t2["panel_on_ref_side"],
             t1["front_faces_ref"], t2["front_faces_ref"]))
    ok_rr = ok_rr and same_ref and flipped
results.append(verdict(ok_rr))

# SS. facing/alongside는 앵커가 ref이고 ref_dist가 앵커까지의 rect_gap이다
line("SS. facing — ref=앵커, ref_dist == 앵커까지 rect_gap")
scn_ss, cs_ss = cands([180, 0], "facing", "table_1")
_, arect_ss, _ = layout.placement.anchor_geometry(scn_ss, "table_1", DOCK)
bad_ss = []
for c in cs_ss:
    t = c["panel_faces"]["toward"]
    rects_ss = layout.collision.footprint_rects(
        layout._proxy(c["x"], c["y"], c["rot"], [180, 0]))
    want = int(round(min(layout.collision.rect_gap(r, arect_ss) for r in rects_ss)))
    if t["ref"] != "table_1" or t["ref_dist"] != want:
        bad_ss.append((c["x"], c["y"], c["rot"], t["ref"], t["ref_dist"], want))
print("  n=%d | 샘플: %s | 불일치: %s"
      % (len(cs_ss), cs_ss[0]["panel_faces"]["toward"] if cs_ss else None, bad_ss))
results.append(verdict(bool(cs_ss) and not bad_ss))

# TT. 모서리(두 벽 등거리)에서 같은 후보가 항상 같은 ref를 낸다 (결정론)
line("TT. 두 벽 등거리 — 반복 호출이 같은 ref (결정론적 tie-break)")
rects_tt = layout.collision.footprint_rects(layout._proxy(100, 100, 0, [0, 0]))
tie = layout._nearest_wall(rects_tt, 400, 300)
same_all = {layout.annotate({"x": 100, "y": 100, "rot": 0},
                            {"width": 400, "depth": 300, "pre_existing_furniture": []},
                            [], [0, 0])["panel_faces"]["toward"]["ref"] for _ in range(5)}
print("  (100,100) 40×40 → south=80 west=80 동거리 | _nearest_wall:", tie)
print("  annotate 5회 ref 집합:", same_all)
results.append(verdict(len(same_all) == 1 and tie[0] == "wall_south" and tie[1] == 80))

# X. front_faces_ref가 각도별 가정표를 대체한다 (확정 사실 + 필드 순감)
line("X. front_faces_ref — 실제 각도로 확정된 불리언, 가정표 필드 삭제")
truth = [(0, True, False), (0, False, False), (90, True, False), (90, False, False),
         (45, True, True), (45, False, False), (135, True, False), (135, False, True),
         (180, True, False), (180, False, True)]
unit_ok = all(layout.front_faces_ref(a, s) is e for a, s, e in truth)
print("  각도표:", [(a, s, layout.front_faces_ref(a, s)) for a, s, _ in truth])
scn, cs = cands([180, 0], "facing", "table_1")
ok_x = unit_ok and bool(cs)
for c in cs:
    t = c["panel_faces"]["toward"]
    ang = {"right": 180, "left": 0}              # panels[0]=right, panels[1]=left
    near = t["panel_on_ref_side"]
    # 독립 재기술: 45°는 ref 쪽에, 135°·180°는 ref 반대쪽에 달렸을 때 앞면이 ref를 향한다
    want = {s: (ang[s] in (135, 180) and s != near) or (ang[s] == 45 and s == near)
            for s in ("right", "left")}
    ok_x = ok_x and t["front_faces_ref"] == want
    ok_x = ok_x and "panel_on_opposite_side" not in t and "front_face_toward_ref" not in t
print("  샘플 toward:", cs[0]["panel_faces"]["toward"] if cs else None)
print("  가정표 필드 제거:", all("front_face_toward_ref" not in c["panel_faces"]["toward"]
                          and "panel_on_opposite_side" not in c["panel_faces"]["toward"] for c in cs))
results.append(verdict(ok_x))

# VV. _diversity에 흩어짐 지표 — positions만으로는 '한 구석 6곳'과 구분이 안 된다
line("VV. form_diversity — min_apart·spread (자리 1곳이면 둘 다 0)")


def _combo_at(*xys):
    return [{"placements": [{"robot": "BOT 1", "x": x, "y": y, "rot": 0}]} for x, y in xys]


d_one = main._diversity(_combo_at((100, 100)))
d_many = main._diversity(_combo_at((100, 100), (100, 200), (400, 100)))
# 쌍거리: 100 / 300 / hypot(300,100)=316.2 → min 100, spread 316
print("  1곳:", {k: d_one[k] for k in ("positions", "min_apart", "spread")})
print("  3곳:", {k: d_many[k] for k in ("positions", "min_apart", "spread")})
sc = fresh()
main.ask_form = form1("BOT 1", [90, 90], "facing", "sofa_1")
main.ask_place = lambda *a, **k: dict(PLACE_OK)
main._run_form_layer(None, INTENT("new_scene"), "차")
dv = next(e for e in eventlog.events() if e["type"] == "form_diversity")
print("  실제 턴:", {k: dv[k] for k in ("modes", "combos", "positions", "min_apart", "spread")})
# modes도 소급 불가 — 이게 없으면 combos=3이 facing 탓인지 방 탓인지 사후에 못 가른다
results.append(verdict(d_one["min_apart"] == 0 and d_one["spread"] == 0
                       and d_many["min_apart"] == 100 and d_many["spread"] == 316
                       and "min_apart" in dv and "spread" in dv
                       and dv.get("modes") == ["facing"]
                       and 0 < dv["min_apart"] <= dv["spread"]))

# ── UU: place_face_away — 관측만 하되, 기준이 '고른 대상'일 때만 (구 Y를 확장) ──
picked = {}


def place_bad(cl, form, combos, intent, **k):
    """뒤집힌 짝이 있는데도 앞면이 ref를 등진 후보를 일부러 고른다."""
    for i, c in enumerate(combos):
        if main._directional_faces_ref(c["placements"][0]) is False:
            picked["idx"] = i
            return dict(PLACE_OK, choice=i, reason="등받이 앞면이 테이블을 향합니다")
    picked["idx"] = None
    return dict(PLACE_OK)


# UU-2. facing에서 등지면 기록 + 판단 재료(mode·furniture)가 실린다 (게이트 아님)
line("UU-2. facing에서 등진 후보 → place_face_away 기록 + mode·furniture, 실행은 정상")
sc = fresh()
main.ask_form = form1("BOT 1", [180, 0], "facing", "table_1")
main.ask_place = place_bad
main._run_form_layer(None, INTENT("new_scene"), "보드게임")
mm = [e for e in eventlog.events() if e["type"] == "place_face_away"]
b1 = next(s for s in sc.states() if s["robot"] == "BOT 1")
print("  고른 인덱스:", picked.get("idx"), "| 이벤트:", mm)
print("  BOT 1:", {k: b1[k] for k in ("active", "x", "y", "rot", "panel_right", "panel_left")})
results.append(verdict(len(mm) == 1 and mm[0]["flip_available"] is True
                       and mm[0]["robot"] == "BOT 1" and "등받이" in mm[0]["asserted"]
                       and mm[0]["mode"] == "facing" and mm[0]["furniture"] == "f"
                       and b1["active"] == "active"))

# UU. free에서는 기록하지 않는다 — 벽을 등지는 것은 정상이다(작업대 가림막)
line("UU. free에서 기준(벽)을 등진 후보를 골라도 place_face_away 없음")
sc = fresh()
picked.clear()
main.ask_form = form1("BOT 1", [180, 0], "free")
main.ask_place = place_bad
main._run_form_layer(None, INTENT("new_scene"), "작업대")
mm_free = [e for e in eventlog.events() if e["type"] == "place_face_away"]
b1f = next(s for s in sc.states() if s["robot"] == "BOT 1")
print("  등진 후보 선택:", picked.get("idx") is not None, "| 이벤트:", mm_free)
print("  BOT 1 active:", b1f["active"])
results.append(verdict(picked.get("idx") is not None and not mm_free
                       and b1f["active"] == "active"))

# UU-3. 구 이벤트명이 코드베이스에서 사라졌다 (needle을 조립해 검사 자신이 걸리지 않게)
_OLD_EVENT = "place_face_" + "mismatch"
line("UU-3. 구 이벤트명(%s) 문자열이 코드베이스에 없다" % _OLD_EVENT)
_srcs = ["main.py", "agent.py", "prompts.py", "config.py", "tools/__init__.py",
         "services/layout.py", "services/placement.py", "services/collision.py",
         "services/scene.py", "services/eventlog.py", "scripts/verify_form_layer.py"]
_hits = [f for f in _srcs if os.path.exists(f)
         and _OLD_EVENT in open(f, encoding="utf-8").read()]
print("  잔존 파일:", _hits)
results.append(verdict(not _hits))

# ── AA~EE: pair 연결이 실제로 닿는가 (2차리뷰 1번) ──────────────────────────
from services import collision as _col   # noqa: E402

PAIR_ST = [{"robot": "BOT 1", "active": "inactive", "x": 380, "y": 20, "rot": 0, "panel_left": 0, "panel_right": 0},
           {"robot": "BOT 2", "active": "inactive", "x": 320, "y": 20, "rot": 0, "panel_left": 0, "panel_right": 0}]


def pair_combos(panels_a, panels_b, mode="face", side="both"):
    scn = json.load(open("scenes/living_room.json", encoding="utf-8"))
    uu = [{"robot": "BOT 1", "furniture": "a", "panels": panels_a,
           "relation": {"mode": "pair", "anchor": None}, "rationale": "r"},
          {"robot": "BOT 2", "furniture": "b", "panels": panels_b,
           "relation": {"mode": "pair", "anchor": None}, "rationale": "r"}]
    cn = {"anchor": "BOT 1", "moving": "BOT 2", "mode": mode, "side": side}
    return layout.enumerate_units(uu, scn, PAIR_ST, connection=cn)


def as_state(p):
    return {"robot": p["robot"], "active": "active", "x": p["x"], "y": p["y"],
            "rot": p["rot"], "panel_right": p["panels"][0], "panel_left": p["panels"][1]}


def span_uv(combo):
    """조합이 만든 도형을 앵커 로컬축(u=패널 축, v=앞뒤)으로 잰다 — probe와 같은 계산."""
    import math as _mm
    ps = combo["placements"]
    th = _mm.radians(ps[0]["rot"])
    u, v = (_mm.cos(th), _mm.sin(th)), (-_mm.sin(th), _mm.cos(th))
    pts = [c for p in ps for r in _col.footprint_rects(as_state(p))
           for c in _col.rect_corners(*r)]
    def _sp(ax):
        vals = [px * ax[0] + py * ax[1] for px, py in pts]
        return int(round(max(vals) - min(vals)))
    return _sp(u), _sp(v)


# AA. 접힌 패널(0°)은 '연결 불가'가 아니라 '본체 맞댐'이다 (구 AA를 대체 — 그 테스트는
#     panels_touching의 구현 한계를 물리적 사실로 오인해 조합 0개를 정답으로 고정했다.
#     find_connect는 dist = 40+0+0 = 40으로 이미 본체를 gap 0에 붙여 놓고 있었다.)
line("AA. panels[0,0] + face → 본체끼리 맞닿은 조합이 생성된다")
c00 = pair_combos([0, 0], [0, 0])
ok_aa = bool(c00)
for c in c00:
    a, b = (as_state(p) for p in c["placements"])
    ok_aa = ok_aa and _col.connection_touching(a, "right", b, "right")
print("  조합: %d | 전부 인접: %s | 도형(u×v): %s"
      % (len(c00), ok_aa, span_uv(c00[0]) if c00 else None))
results.append(verdict(ok_aa and c00 and span_uv(c00[0]) == (80, 40)))

# AA-2. large_worktable 구성(inner 0 / outer 90) → 긴 상판 140 (30+40+40+30)
line("AA-2. inner 0·outer 90 face → u축 140의 긴 상판 (large_worktable)")
c_lw = pair_combos([0, 90], [0, 90], side="right")
sp_lw = span_uv(c_lw[0]) if c_lw else None
print("  조합: %d | 도형(u×v): %s" % (len(c_lw), sp_lw))
results.append(verdict(bool(c_lw) and sp_lw == (140, 40)))

# BB. 생성된 face 조합은 전부 connection_touching True
line("BB. panels[90,0] side=right face → 모든 조합에서 connection_touching True")
cbb = pair_combos([90, 0], [90, 0], side="right")
ok_bb = bool(cbb)
for c in cbb:
    a, b = (as_state(p) for p in c["placements"])
    t = _col.connection_touching(a, "right", b, "right")
    ok_bb = ok_bb and t
print("  조합:", len(cbb), "| 전부 touching:", ok_bb)
if cbb:
    p0, p1 = cbb[0]["placements"]
    print("  샘플: %s(%d,%d)rot%d %s / %s(%d,%d)rot%d %s"
          % (p0["robot"], p0["x"], p0["y"], p0["rot"], p0["panels"],
             p1["robot"], p1["x"], p1["y"], p1["rot"], p1["panels"]))
results.append(verdict(ok_bb))

# CC. Phase A가 준 panels가 덮어써지지 않는다
line("CC. 조합의 panels == Phase A의 panels (덮어쓰기 없음)")
ok_cc = bool(cbb) and all(p["panels"] == [90, 0] for c in cbb for p in c["placements"])
print("  panels 목록:", sorted({tuple(p["panels"]) for c in cbb for p in c["placements"]}))
results.append(verdict(ok_cc))

# DD. 스키마에 anchor_panel·moving_panel이 없다
line("DD. _CONNECTION 스키마에서 anchor_panel·moving_panel 제거")
import prompts as _pr   # noqa: E402
props = set(_pr._CONNECTION["properties"]) | set(_pr._CONNECTION["required"])
print("  properties+required:", sorted(props))
results.append(verdict("anchor_panel" not in props and "moving_panel" not in props))

# EE. side 모드는 connection_touching을 요구하지 않고 정상 생성된다
line("EE. mode=side → 패널 접촉 요구 없이 조합 생성")
cee = pair_combos([0, 0], [0, 0], mode="side")
print("  조합:", len(cee), "| 샘플:",
      [(p["robot"], p["x"], p["y"], p["rot"], p["panels"]) for p in cee[0]["placements"]] if cee else None)
results.append(verdict(len(cee) > 0))

# ── GG~II: 승인 없이 끝난 턴은 baseline으로 롤백된다 (2차리뷰 3번) ──────────
def rollback_case(approvals, place=None):
    """HITL-2 응답 시퀀스를 주고 (baseline, 최종 상태, 이벤트, turn)을 돌려준다."""
    sc = SceneState(session_path=tempfile.mktemp(suffix=".json"))
    sc.load_scene("living_room")

    class Seq(FakeViewer):
        def __init__(s): super().__init__(); s.seq = list(approvals); s.i = 0
        def request_approval(s, message, timeout=None):
            r = s.seq[min(s.i, len(s.seq) - 1)]; s.i += 1; return r

    tools.init(sc, Seq())
    tools.STATE["auto_approve"] = False
    tools.STATE["metrics"] = tools.new_metrics()
    eventlog.reset()
    base = sc.snapshot()
    main.ask_form = FORM
    main.ask_place = place or (lambda *a, **k: dict(PLACE_OK))
    main._run_form_layer(None, dict(NEW_INTENT), "차 마시자")
    return base, sc, ev()


# GG. 피드백 없는 거부 → baseline 복귀, turn 안 늘어남
line("GG. 피드백 없는 거부 → 로봇이 baseline과 동일, turn 그대로")
base, sc, evs = rollback_case([{"approved": False, "feedback": ""}])
same = sc.robots == base["robots"]
print("  롤백:", same, "| turn:", sc.turn, "| 이벤트:", evs)
print("  BOT 1:", {k: sc.robots["BOT 1"][k] for k in ("active", "x", "y", "rot")})
results.append(verdict(same and sc.turn == 0 and "form_rollback" in evs))

# GG-2. aborted(브라우저 끊김)도 롤백
line("GG-2. aborted → 롤백")
base, sc, evs = rollback_case([{"approved": False, "aborted": True, "feedback": ""}])
print("  롤백:", sc.robots == base["robots"], "| 이벤트:", evs)
results.append(verdict(sc.robots == base["robots"] and "form_rollback" in evs))

# HH. 거부 + 피드백 → 롤백하지 않고 재구상 (기존 R과 양립)
line("HH. 거부+피드백 → 롤백 없이 Phase A 재호출, 승인 시 커밋")
base, sc, evs = rollback_case([{"approved": False, "feedback": "더 크게 해줘"},
                               {"approved": True, "feedback": ""}])
print("  이벤트:", evs, "| turn:", sc.turn)
print("  BOT 1 active:", sc.robots["BOT 1"]["active"])
results.append(verdict("form_rollback" not in evs and "hitl2_feedback" in evs
                       and sc.turn == 1 and sc.robots["BOT 1"]["active"] == "active"))

# II. 예산 소진 → 롤백 + hitl2_budget_exhausted
line("II. 피드백 반복으로 예산 소진 → 롤백 + hitl2_budget_exhausted")
base, sc, evs = rollback_case([{"approved": False, "feedback": "계속 아니야"}])
print("  롤백:", sc.robots == base["robots"], "| 이벤트:", evs)
results.append(verdict(sc.robots == base["robots"] and "form_rollback" in evs
                       and "hitl2_budget_exhausted" in evs and sc.turn == 0))

# II-2. 재구상 라운드가 실패로 끝나도 앞 라운드 실행분이 회수된다 (표에 없던 구멍)
line("II-2. 거부+피드백 → 2라운드 공집합 포기 → 1라운드 실행분 롤백")
real_enum = layout.enumerate_units
calls = {"n": 0}


def _first_ok_then_empty(*a, **k):
    calls["n"] += 1
    return real_enum(*a, **k) if calls["n"] == 1 else []


layout.enumerate_units = _first_ok_then_empty
try:
    base, sc, evs = rollback_case([{"approved": False, "feedback": "다르게 해줘"}])
finally:
    layout.enumerate_units = real_enum
print("  롤백:", sc.robots == base["robots"], "| 이벤트:", evs, "| turn:", sc.turn)
results.append(verdict(sc.robots == base["robots"] and "form_giveup" in evs
                       and "form_rollback" in evs and sc.turn == 0))

# ── JJ~KK: 스키마가 못 막는 규약 위반은 재호출 사유가 된다 (2차리뷰 4번) ────
GOOD_ROBOT = {"robot": "BOT 1", "furniture": "f", "panels": [90, 90],
              "relation": {"mode": "free", "anchor": None}, "rationale": "r"}


def invalid_then_good(bad_form):
    """1회차에 규약 위반 form, 2회차에 정상 form. (이벤트, 호출 횟수, BOT 1 상태)."""
    sc = fresh()
    n = {"c": 0, "reasons": []}

    def _form(*a, **k):
        n["c"] += 1
        n["reasons"].append(k.get("recall_reason"))
        return bad_form if n["c"] == 1 else {"robots": [dict(GOOD_ROBOT)], "connection": None}

    main.ask_form = _form
    main.ask_place = lambda *a, **k: dict(PLACE_OK)
    main._run_form_layer(None, INTENT("new_scene"), "앉자")
    return ev(), n, next(s for s in sc.states() if s["robot"] == "BOT 1")


# JJ. panels 길이 1 → IndexError 없이 form_invalid + 재호출
line("JJ. panels[90] → IndexError 없이 form_invalid + Phase A 재호출")
bad = {"robots": [{"robot": "BOT 1", "furniture": "f", "panels": [90],
                   "relation": {"mode": "free", "anchor": None}, "rationale": "r"}],
       "connection": None}
evs, n, b1 = invalid_then_good(bad)
print("  이벤트:", evs)
print("  ask_form 호출:", n["c"], "| 2회차 사유:", n["reasons"][1] if n["c"] > 1 else None)
print("  BOT 1 active:", b1["active"])
results.append(verdict("form_invalid" in evs and n["c"] == 2
                       and "panels" in (n["reasons"][1] or "") and b1["active"] == "active"))

# KK. 같은 로봇 2회 / 자기 연결도 동일하게 재호출 사유
line("KK. 같은 로봇 2회 · 자기 자신과 연결 → 같은 처리")
dup = {"robots": [dict(GOOD_ROBOT), dict(GOOD_ROBOT)], "connection": None}
evs1, n1, _ = invalid_then_good(dup)
selfconn = {"robots": [dict(GOOD_ROBOT), dict(GOOD_ROBOT, robot="BOT 2")],
            "connection": {"anchor": "BOT 1", "moving": "BOT 1", "mode": "face", "side": "both"}}
evs2, n2, _ = invalid_then_good(selfconn)
print("  중복 로봇: form_invalid=%s 호출=%d 사유=%s"
      % ("form_invalid" in evs1, n1["c"], n1["reasons"][1] if n1["c"] > 1 else None))
print("  자기 연결: form_invalid=%s 호출=%d 사유=%s"
      % ("form_invalid" in evs2, n2["c"], n2["reasons"][1] if n2["c"] > 1 else None))
results.append(verdict("form_invalid" in evs1 and n1["c"] == 2
                       and "form_invalid" in evs2 and n2["c"] == 2
                       and "자기 자신" in (n2["reasons"][1] or "")))

# ── LL~NN: 폴백 사실이 Phase B에 전달된다 (원안·후보 불일치의 오독 방지) ────
def capture_relaxed(enum_stub):
    """enumerate_units를 stub으로 갈고 _run_form_layer를 태워 ask_place가 받은 relaxed를 돌려준다."""
    fresh()
    got = {}

    def _cap_place(cl, form, combos, intent, relaxed=None):
        got["relaxed"] = relaxed
        return dict(PLACE_OK)

    main.ask_form = form1("BOT 1", [90, 90], "facing", "sofa_1")
    main.ask_place = _cap_place
    real_enum = layout.enumerate_units
    layout.enumerate_units = enum_stub(real_enum)
    try:
        main._run_form_layer(None, INTENT("new_scene"), "앉자")
    finally:
        layout.enumerate_units = real_enum
    return got.get("relaxed"), ev()


# LL. free 폴백 → 관계를 풀었다는 사실 + 앵커 이름이 실린다
line("LL. free 폴백 → ask_place가 '완화된 조건'(관계 해제 + 앵커)을 받는다")


def _only_free(real):
    def f(units, env, states, connection=None, band_max=None):
        if connection or any((u.get("relation") or {}).get("mode") != "free" for u in units):
            return []                      # facing은 밴드 확장으로도 실패시킨다
        return real(units, env, states, connection=None)
    return f


rlx, evs = capture_relaxed(_only_free)
print("  relaxed:", rlx)
print("  이벤트:", evs)
results.append(verdict(bool(rlx) and "sofa_1" in rlx and "관계를 풀고" in rlx
                       and "free_fallback" in evs))

# LL-2. 밴드 확장 단계 → 관계는 유지, '범위를 넓혔다'로 서술된다
line("LL-2. 밴드 확장 폴백 → 관계 유지 + 범위 확장 서술")


def _fail_first(real):
    n = {"c": 0}

    def f(units, env, states, connection=None, band_max=None):
        n["c"] += 1
        return [] if n["c"] == 1 else real(units, env, states, connection=connection,
                                           band_max=band_max)
    return f


rlx2, evs2 = capture_relaxed(_fail_first)
print("  relaxed:", rlx2)
results.append(verdict(bool(rlx2) and "범위를 넓혀" in rlx2 and "관계는 지켰" in rlx2
                       and "band_expand" in evs2 and "free_fallback" not in evs2))

# MM. 폴백이 없으면 relaxed=None (평상시 입력이 늘어나지 않는다)
line("MM. 폴백 없음 → relaxed None")
rlx3, evs3 = capture_relaxed(lambda real: real)
print("  relaxed:", rlx3, "| 폴백 이벤트:", [e for e in evs3 if e in ("band_expand", "free_fallback")])
results.append(verdict(rlx3 is None))

# NN. ask_place 입력에서 '완화된 조건'이 후보 목록보다 앞에 온다 (읽는 순서)
line("NN. ask_place payload — '후보 생성 시 완화된 조건'이 후보 목록 앞")
cap = {}
_real_struct = agent._structured


def _cap_struct(client, developer, user_obj, name, schema, model=None, layer=None):
    cap["obj"] = user_obj
    return _real_struct(client, developer, user_obj, name, schema, model=model, layer=layer)


agent._structured = _cap_struct
try:
    agent.ask_place(types.SimpleNamespace(responses=R([_mk("소파 곁에 자리를 만들었어요")])),
                    {"robots": []}, [{"placements": []}], {"activity": "x"},
                    relaxed="원안의 관계(sofa_1을(를) 마주 보는 자리)대로는 놓을 자리가 없어, 관계를 풀고 빈 공간에서 찾았습니다.")
finally:
    agent._structured = _real_struct
keys = list(cap.get("obj") or {})
print("  키 순서:", keys)
results.append(verdict("후보 생성 시 완화된 조건" in keys
                       and keys.index("후보 생성 시 완화된 조건") < keys.index("유효 후보/조합 목록(주석 포함)")))

# OO. 완화 사실이 message로 새면 재생성한다 (참가자는 원안을 본 적이 없다 §15.6·§17.5)
line("OO. message에 내부 사정('자리가 없어서') → 재생성 1회 후 깨끗한 문구")
tools.STATE["metrics"] = tools.new_metrics()
leaky = _mk("소파 앞에 놓으려 했는데 자리가 없어서 대신 창가에 티테이블을 놓았어요")
clean = _mk("창가에 둘이 함께 쓸 수 있는 티테이블을 놓았어요")
r_oo = agent.ask_place(types.SimpleNamespace(responses=R([leaky, clean])),
                       {"robots": []}, [{"placements": []}], {"activity": "x"},
                       relaxed="원안의 관계(sofa_1을(를) 마주 보는 자리)대로는 놓을 자리가 없어, 관계를 풀고 빈 공간에서 찾았습니다.")
print("  최종:", r_oo["message"], "| regen(term/leak): %d/%d"
      % (tools.STATE["metrics"]["place_regen_term"], tools.STATE["metrics"]["place_regen_leak"]))
# XX(6번). 누출은 leak 카운터만 올린다 — term과 합치면 파일럿에서 '수치가 샌 것'과
# '누출 정규식 오탐'을 가를 수 없다. 그 구분이 leak 필터(예측 기반) 수정의 정산이다.
results.append(verdict(r_oo["message"] == "창가에 둘이 함께 쓸 수 있는 티테이블을 놓았어요"
                       and tools.STATE["metrics"]["place_regen_leak"] == 1
                       and tools.STATE["metrics"]["place_regen_term"] == 0))

# OO-2. 정상 문구는 오탐하지 않는다 ('자리를 만들었어요'는 내부 사정이 아니다)
line("OO-2. 정상 message 오탐 없음")
GOOD_MSGS = ["소파 곁에 자리를 만들었어요. 이 배치로 괜찮을까요?",
             "소파 앞에 둘이 함께 쓸 수 있는 티테이블을 놓았어요.",
             "책장이 있으니 로봇은 독서대가 되었어요. 창가 자리에 두었어요.",
             "두 대를 붙여 넉넉한 상을 만들었어요. 이대로 괜찮을까요?"]
bad_hits = [m for m in GOOD_MSGS if agent._MESSAGE_LEAK.search(m) or agent._MESSAGE_BANNED.search(m)]
LEAK_MSGS = ["원안대로는 어려워서 여기에 놓았어요", "마땅한 자리를 찾지 못해 창가에 두었어요",
             "소파 앞은 공간이 없어서 대신 벽 쪽에 놓았어요"]
miss = [m for m in LEAK_MSGS if not agent._MESSAGE_LEAK.search(m)]
print("  정상 오탐:", bad_hits, "| 누출 미검출:", miss)
results.append(verdict(not bad_hits and not miss))

# XX-2. 2회차도 위반이면 실패 카운터도 종류별로 갈린다 (term/leak 대칭)
line("XX-2. 재생성 2회차도 위반 → place_regen_{term,leak}_failed가 갈려 오른다")
tools.STATE["metrics"] = tools.new_metrics()
agent.ask_place(types.SimpleNamespace(responses=R([_mk("패널을 90도로 폈어요"),
                                                   _mk("여전히 90도입니다")])),
                {"robots": []}, [{"placements": []}], {"activity": "x"})
m_term = dict(tools.STATE["metrics"])
tools.STATE["metrics"] = tools.new_metrics()
agent.ask_place(types.SimpleNamespace(responses=R([_mk("자리가 없어서 여기 놓았어요"),
                                                   _mk("마땅한 자리를 찾지 못했어요")])),
                {"robots": []}, [{"placements": []}], {"activity": "x"})
m_leak = dict(tools.STATE["metrics"])
ks = ("place_regen_term", "place_regen_term_failed",
      "place_regen_leak", "place_regen_leak_failed")
print("  term 케이스:", {k: m_term[k] for k in ks})
print("  leak 케이스:", {k: m_leak[k] for k in ks})
results.append(verdict(m_term["place_regen_term"] == 1 and m_term["place_regen_term_failed"] == 1
                       and m_term["place_regen_leak"] == 0 and m_term["place_regen_leak_failed"] == 0
                       and m_leak["place_regen_leak"] == 1 and m_leak["place_regen_leak_failed"] == 1
                       and m_leak["place_regen_term"] == 0 and m_leak["place_regen_term_failed"] == 0))

# ── ZZ: _anchor_face의 front는 앵커 폭만큼의 띠 안일 때만 (구 45° 부채꼴) ──────
line("ZZ. facing(sofa_1) 후보 전부가 앵커 폭 띠 안 — 실측 문제 좌표 (300,240) 탈락")
scn_zz = json.load(open("scenes/living_room.json", encoding="utf-8"))
(zax, zay), _, zarot = layout.placement.anchor_geometry(scn_zz, "sofa_1", DOCK)
zfv = layout.placement.front_vec(zarot)
zsv = (-zfv[1], zfv[0])
zarects = layout._anchor_rects(scn_zz, "sofa_1", DOCK)
z_side_half = max(layout._half_extent(r, zsv) for r in zarects) + layout.BODY / 2.0


def _ps_of(x, y):
    return (x - zax) * zsv[0] + (y - zay) * zsv[1]


face_pilot = layout._anchor_face(zarects, zax, zay, zfv, 300, 240)
print("  sofa_1 중심 (%g,%g) | 측면 반경+여유 = %g" % (zax, zay, z_side_half))
print("  파일럿 문제 좌표 (300,240): ps=%.0f → %s" % (_ps_of(300, 240), face_pilot))
bad_zz = []
for panels in ([90, 90], [180, 0]):
    _, cs_zz = cands(panels, "facing", "sofa_1")
    for c in cs_zz:
        if abs(_ps_of(c["x"], c["y"])) > z_side_half + 1e-6:
            bad_zz.append((panels, c["x"], c["y"], round(_ps_of(c["x"], c["y"]))))
print("  띠 밖 후보:", bad_zz)
results.append(verdict(face_pilot == "side" and not bad_zz))

# ZZ-2. 후보가 굶지 않는다 — facing/alongside 둘 다 찍는다 (띠 밖은 전부 side가 되므로
#       alongside가 넓어진다. 대칭 제약(|pf| 제한)은 지금 걸지 않는다 — 파일럿 관찰 뒤 판단)
line("ZZ-2. facing 후보 수 0 아님 + alongside 후보 수 동시 관측")
counts = {}
for panels in ([90, 90], [180, 0]):
    for mode in ("facing", "alongside"):
        _, cc = cands(panels, mode, "sofa_1")
        counts[(tuple(panels), mode)] = len(cc)
for k, v in sorted(counts.items(), key=lambda t: str(t[0])):
    print("  panels=%s %-9s → %d개" % (list(k[0]), k[1], v))
results.append(verdict(all(counts[(p, "facing")] > 0 for p in ((90, 90), (180, 0)))))

# ── YY: relation.anchor는 방의 기존 가구만 (로봇 앵커 = 도크 기준 오배치) ──────
# YY. 로봇 이름을 anchor로 쓰면 재호출 사유가 된다
line("YY. relation.anchor='BOT 2' → form_invalid + Phase A 재호출")
bad_robot_anchor = {"robots": [
    dict(GOOD_ROBOT, relation={"mode": "alongside", "anchor": "BOT 2"}),
    dict(GOOD_ROBOT, robot="BOT 2")], "connection": None}
evs_yy, n_yy, b1_yy = invalid_then_good(bad_robot_anchor)
print("  이벤트:", evs_yy)
print("  ask_form 호출:", n_yy["c"], "| 사유:", n_yy["reasons"][1] if n_yy["c"] > 1 else None)
results.append(verdict("form_invalid" in evs_yy and n_yy["c"] == 2
                       and "connection" in (n_yy["reasons"][1] or "")
                       and b1_yy["active"] == "active"))

# YY-2. 방에 없는 가구 id도 같은 경로
line("YY-2. 방에 없는 가구 id → 같은 경로로 재호출")
bad_ghost = {"robots": [dict(GOOD_ROBOT,
                             relation={"mode": "facing", "anchor": "없는가구_999"})],
             "connection": None}
evs_y2, n_y2, _ = invalid_then_good(bad_ghost)
print("  이벤트:", evs_y2, "| 사유:", n_y2["reasons"][1] if n_y2["c"] > 1 else None)
results.append(verdict("form_invalid" in evs_y2 and n_y2["c"] == 2
                       and "없는가구_999" in (n_y2["reasons"][1] or "")))

# YY-3. label로 온 앵커는 통과한다 (anchor_geometry가 label로도 조회하므로 '쓸 수 있다')
line("YY-3. anchor를 label로 줘도 통과 (동작하는 입력을 거부하지 않는다)")
_room = main._room_desc(fresh())
_label = next((f["label"] for f in _room if f.get("label")), None)
_by_label = {"robots": [dict(GOOD_ROBOT,
                             relation={"mode": "facing", "anchor": _label})],
             "connection": None}
print("  방 가구:", [(f["id"], f["label"]) for f in _room])
print("  label '%s' 검증 결과: %s" % (_label, main._validate_form(_by_label, _room)))
results.append(verdict(_label is not None and main._validate_form(_by_label, _room) is None))

# PC. pair 연결 조합이 anchor '자리' 다양성을 유지한다 (다양성 원칙 — 앞에서 자르지 않는다).
#     상한을 없앴을 때 나오는 자리 집합이 진실값. 상한을 걸어도 그 집합이 그대로여야 한다
#     (개수만 줄고 자리는 안 줄어야 한다). 임계값을 손으로 정하지 않는 게 요점이다.
line("PC. pair 연결 조합 — 상한이 anchor 자리를 지우지 않는가")
_sc_pc = fresh()
_env_pc, _st_pc = _sc_pc.environment(), _sc_pc.states()


def _pair_unit(n, p):
    return {"robot": n, "furniture": "f", "panels": p,
            "relation": {"mode": "pair", "anchor": None}, "rationale": "r"}


_units_pc = [_pair_unit("BOT 1", [0, 90]), _pair_unit("BOT 2", [0, 90])]
_con_pc = {"anchor": "BOT 1", "moving": "BOT 2", "mode": "face", "side": "both"}


def _anchor_xy(cs):
    return {(c["placements"][0]["x"], c["placements"][0]["y"]) for c in cs}


_cap_pc = layout.PRESENT_CAP
try:
    layout.PRESENT_CAP = 999          # 상한 없이 = 자리 진실값
    _truth_pc = _anchor_xy(layout.enumerate_units(_units_pc, _env_pc, _st_pc,
                                                  connection=_con_pc))
finally:
    layout.PRESENT_CAP = _cap_pc
_combos_pc = layout.enumerate_units(_units_pc, _env_pc, _st_pc, connection=_con_pc)
_used_pc = _anchor_xy(_combos_pc)
print("  상한 없을 때 자리 %d곳 / 상한(%d) 적용 후 조합 %d개·자리 %d곳"
      % (len(_truth_pc), _cap_pc, len(_combos_pc), len(_used_pc)))
results.append(verdict(len(_truth_pc) > 0 and _used_pc == _truth_pc
                       and len(_combos_pc) <= _cap_pc))

print("\n" + "=" * 50)
print("결과: %d/%d PASS" % (sum(results), len(results)))
sys.exit(0 if all(results) else 1)
