// three.js 뷰어: buildRoom(scene JSON) + 로봇 GLB 스왑 로드 + 상태 보간.
//
// 좌표 규약 (collision.py와 일치시킬 것):
//   방 (x, y) cm, y는 '위쪽' → three: X = x - w/2, Z = d/2 - y (바닥 = XZ 평면)
//   rot(도, CCW) → rotation.y = rad(rot)  (이 매핑에서 부호가 정확히 일치)
//   로봇 GLB: 단위 mm(×0.1), 파일명 robot_<L>x<R>.glb, 모델 -z = 왼쪽 패널.
//   본체 중심 오프셋 (전 파일 공통): (255.922, 342.419(바닥), 87.751) mm
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';

const USE_GLB = true;                       // false면 조립식 로봇만 사용
const ROBOT_COLORS = { 'BOT 1': 0xf0ffff, 'BOT 2': 0xe6fbff };
const ROBOT_ACCENT_COLORS = { 'BOT 1': 0x7fb9c9, 'BOT 2': 0x96d5e3 };
const FURN_COLOR = 0xd6d0c6;
const FURNITURE_MODEL_YAW = {
  'sofa.glb': 0,
  'table.glb': 0,
  'tv.glb': 0,
  'bed.glb': 0,
  'desk.glb': 0,
  'dining_table.glb': 0,
  'kitchen_counter.glb': 0,
  'bathroom_sink.glb': 0,
  'toilet.glb': 0
};

// ---------- 기본 세팅 ----------
const canvas = document.getElementById('canvas');
const renderer = new THREE.WebGLRenderer({ canvas, antialias: true });
renderer.setPixelRatio(window.devicePixelRatio);
renderer.toneMapping = THREE.ACESFilmicToneMapping;
renderer.toneMappingExposure = 0.92;
renderer.outputColorSpace = THREE.SRGBColorSpace;
renderer.shadowMap.enabled = true;
renderer.shadowMap.type = THREE.PCFSoftShadowMap;
const scene3 = new THREE.Scene();
scene3.background = new THREE.Color(0xf4efe6);
const camera = new THREE.PerspectiveCamera(50, 1, 1, 5000);
const controls = new OrbitControls(camera, canvas);
scene3.add(new THREE.HemisphereLight(0xfffbef, 0xbfd1de, 1.05));
const fill = new THREE.AmbientLight(0xffffff, .18);
scene3.add(fill);
const sun = new THREE.DirectionalLight(0xfff0cf, 2.25);
sun.position.set(-260, 460, 230);
sun.castShadow = true;
sun.shadow.mapSize.set(2048, 2048);
sun.shadow.camera.near = 10;
sun.shadow.camera.far = 900;
sun.shadow.camera.left = -360;
sun.shadow.camera.right = 360;
sun.shadow.camera.top = 360;
sun.shadow.camera.bottom = -360;
sun.shadow.bias = -0.0002;
sun.shadow.normalBias = 0.025;
scene3.add(sun);

const viewEl = document.getElementById('view');
function resize() {
  const w = viewEl.clientWidth, h = viewEl.clientHeight;
  renderer.setSize(w, h);
  camera.aspect = w / h;
  camera.updateProjectionMatrix();
}
addEventListener('resize', resize); resize();

const loader = new GLTFLoader();
let room = { w: 400, d: 300 };
let roomGroup = null;
const robots = new Map();      // name -> RobotView
const glbCache = new Map();    // "LxR" -> Promise<scene template>

const X = x => x - room.w / 2;
const Z = y => room.d / 2 - y;

function fixMeshGeometry(root) {
  root.traverse(o => {
    if (!o.isMesh || !o.geometry) return;
    const g = o.geometry;
    if (!g.attributes.normal) g.computeVertexNormals();
    if (g.attributes.normal) g.normalizeNormals();
    o.castShadow = true;
    o.receiveShadow = true;
  });
}

function createFloorMaterial() {
  const c = document.createElement('canvas');
  c.width = 512;
  c.height = 512;
  const ctx = c.getContext('2d');
  ctx.fillStyle = '#e6d8bf';
  ctx.fillRect(0, 0, c.width, c.height);
  for (let y = 0; y < c.height; y += 64) {
    ctx.fillStyle = (y / 64) % 2 ? '#deceb2' : '#eadcc4';
    ctx.fillRect(0, y, c.width, 62);
    ctx.strokeStyle = 'rgba(122, 91, 55, .22)';
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(0, y + 63);
    ctx.lineTo(c.width, y + 63);
    ctx.stroke();
    for (let x = (y / 64) % 2 ? 128 : 0; x < c.width; x += 170) {
      ctx.strokeStyle = 'rgba(122, 91, 55, .16)';
      ctx.beginPath();
      ctx.moveTo(x, y + 6);
      ctx.lineTo(x, y + 58);
      ctx.stroke();
    }
  }
  const tex = new THREE.CanvasTexture(c);
  tex.colorSpace = THREE.SRGBColorSpace;
  tex.wrapS = THREE.RepeatWrapping;
  tex.wrapT = THREE.RepeatWrapping;
  tex.repeat.set(3, 3);
  return new THREE.MeshStandardMaterial({ map: tex, color: 0xfff2df, roughness: .72 });
}

// ---------- 방 ----------
function buildRoom(sceneJson) {
  if (roomGroup) scene3.remove(roomGroup);
  roomGroup = new THREE.Group();
  room = { w: sceneJson.width, d: sceneJson.depth };

  const floor = new THREE.Mesh(
    new THREE.BoxGeometry(room.w, 2, room.d),
    createFloorMaterial());
  floor.position.y = -1;
  floor.receiveShadow = true;
  roomGroup.add(floor);
  const grid = new THREE.GridHelper(Math.max(room.w, room.d), Math.max(room.w, room.d) / 20,
                                    0xc9bca6, 0xe5d8c1);
  grid.position.y = 0.1;
  grid.material.transparent = true;
  grid.material.opacity = .28;
  roomGroup.add(grid);

  const wallMat = new THREE.MeshStandardMaterial({ color: 0xf0eadf, roughness: .88, transparent: true, opacity: .62 });
  const accentWallMat = new THREE.MeshStandardMaterial({ color: 0xb5b8c6, roughness: .82, transparent: true, opacity: .68 });
  const WH = 90, WT = 4;   // 벽 높이/두께
  [[room.w + WT * 2, WH, WT, 0, -room.d / 2 - WT / 2, accentWallMat],
   [room.w + WT * 2, WH, WT, 0,  room.d / 2 + WT / 2, wallMat],
   [WT, WH, room.d, -room.w / 2 - WT / 2, 0, wallMat],
   [WT, WH, room.d,  room.w / 2 + WT / 2, 0, wallMat]].forEach(([w, h, dd, px, pz, mat]) => {
    const m = new THREE.Mesh(new THREE.BoxGeometry(w, h, dd), mat);
    m.position.set(px, h / 2, pz);
    m.receiveShadow = true;
    roomGroup.add(m);
  });

  for (const f of sceneJson.pre_existing_furniture || []) roomGroup.add(furnitureMesh(f));
  scene3.add(roomGroup);

  const r = Math.max(room.w, room.d);
  camera.position.set(0, r * 1.05, r * .85);
  controls.target.set(0, 0, 0);
  controls.update();
}

function furnitureMesh(f) {
  const g = new THREE.Group();
  g.position.set(X(f.x), 0, Z(f.y));
  g.rotation.y = THREE.MathUtils.degToRad(f.rot || 0);
  const h = f.h || 60;
  const box = new THREE.Mesh(new THREE.BoxGeometry(f.w, h, f.d),
    new THREE.MeshStandardMaterial({ color: FURN_COLOR, roughness: .62 }));
  box.position.y = h / 2;
  box.castShadow = true;
  box.receiveShadow = true;
  g.add(box);
  if (f.model && USE_GLB) {   // GLB가 있으면 로드해서 교체 (실패 시 박스 유지)
    loader.load('/models/' + f.model, gltf => {
      const obj = gltf.scene;
      fixMeshGeometry(obj);
      const bb = new THREE.Box3().setFromObject(obj);
      const size = bb.getSize(new THREE.Vector3());
      const s = Math.min(f.w / size.x, h / size.y, f.d / size.z);
      obj.scale.setScalar(s);
      obj.rotation.y = THREE.MathUtils.degToRad(FURNITURE_MODEL_YAW[f.model] || 0);
      const bb2 = new THREE.Box3().setFromObject(obj);
      const c = bb2.getCenter(new THREE.Vector3());
      obj.position.sub(c).setY(obj.position.y - bb2.min.y);
      g.remove(box); g.add(obj);
    }, undefined, () => {});
  }
  return g;
}

// ---------- 로봇 ----------
// 조립식 fallback: 본체 + 힌지 패널 2개 → 패널이 '스르륵' 펼쳐지는 애니메이션 제공
function buildFallbackRobot(color) {
  const g = new THREE.Group();
  const mat = new THREE.MeshStandardMaterial({ color, roughness: .46, metalness: .03 });
  const pmat = new THREE.MeshStandardMaterial({ color, roughness: .36, metalness: .02, transparent: true, opacity: .96 });
  const body = new THREE.Mesh(new THREE.BoxGeometry(40, 50, 40), mat);
  body.position.y = 25;
  body.castShadow = true;
  body.receiveShadow = true;
  g.add(body);
  const hinges = {};
  for (const [side, sx] of [['left', -1], ['right', 1]]) {
    const hinge = new THREE.Group();
    hinge.position.set(sx * 20, 50, 0);
    const panel = new THREE.Mesh(new THREE.BoxGeometry(3, 30, 40), pmat);
    panel.position.y = -15;
    panel.castShadow = true;
    panel.receiveShadow = true;
    hinge.add(panel);
    hinges[side] = hinge;
    g.add(hinge);
  }
  g.userData.hinges = hinges;   // rotation.z = ∓angle
  return g;
}

function glbTemplate(key) {   // key = "LxR"
  if (!glbCache.has(key)) {
    glbCache.set(key, new Promise(res => {
      loader.load(`/models/robot_${key}.glb`, gltf => {
        const inner = gltf.scene;
        fixMeshGeometry(inner);
        inner.position.set(-255.922, 342.419, -87.751);   // 본체 중심 보정 (mm)
        const wrap = new THREE.Group();
        wrap.add(inner);
        wrap.scale.setScalar(0.1);                        // mm → cm
        wrap.rotation.y = Math.PI / 2;                    // 모델 +z = 오른쪽 패널 → 방 +x
        res(wrap);
      }, undefined, () => res(null));                     // 실패 → fallback 유지
    }));
  }
  return glbCache.get(key);
}

class RobotView {
  constructor(name) {
    this.name = name;
    this.rig = new THREE.Group();
    this.fallback = buildFallbackRobot(ROBOT_COLORS[name] || 0x888888);
    this.rig.add(this.fallback);
    this.glbNode = null;
    this.glbKey = null;      // 현재 화면에 붙은 패널 상태 키
    this.wantKey = null;     // 가장 최근에 요청된 키 (경합 시 최신만 반영)
    this.cur = { x: 0, y: 0, rot: 0, pl: 0, pr: 0 };
    this.tgt = { ...this.cur };
    this.speed = 1;
    this.dim = 1;            // inactive 흐림 계수 — 비동기 GLB 스왑 후에도 재적용
    scene3.add(this.rig);
  }
  setTarget(st, duration) {
    this.tgt = { x: st.x, y: st.y, rot: st.rot || 0,
                 pl: st.panel_left || 0, pr: st.panel_right || 0 };
    this.speed = duration > 0 ? 1 / duration : 1e6;
    if (duration <= 0) this.cur = { ...this.tgt };
    this.dim = st.active === 'inactive' ? .55 : 1;
    if (USE_GLB) this.swapGlb(`${this.tgt.pl}x${this.tgt.pr}`);
    this.applyDim();
  }
  applyDim() {
    this.rig.traverse(o => {
      if (o.material) { o.material.transparent = true; o.material.opacity = .95 * this.dim; }
    });
  }
  async swapGlb(key) {
    if (key === this.glbKey && this.glbNode) return;   // 이미 그 상태면 스왑 불필요
    this.wantKey = key;                                // 최신 요청 기록
    const tpl = await glbTemplate(key);
    if (key !== this.wantKey) return;                  // 그새 더 최신 요청이 왔으면 이 결과는 폐기 (순서 꼬임 방지)
    if (tpl === null) {                                // GLB 없음/로드 실패 → 조립식 fallback으로 표시
      if (this.glbNode) { this.rig.remove(this.glbNode); this.glbNode = null; }
      this.fallback.visible = true;                    // 패널 변화가 최소한 조립식으로라도 보이게
      this.glbKey = key;
      return;
    }
    if (this.glbNode) this.rig.remove(this.glbNode);
    this.glbKey = key;
    this.glbNode = tpl.clone(true);
    this.glbNode.traverse(o => {
      if (o.isMesh) {
        if (o.geometry && !o.geometry.attributes.normal) o.geometry.computeVertexNormals();
        o.castShadow = true;
        o.receiveShadow = true;
        o.material = new THREE.MeshStandardMaterial({
          color: ROBOT_COLORS[this.name] || 0x888888,
          emissive: ROBOT_ACCENT_COLORS[this.name] || 0x7fb9c9,
          emissiveIntensity: .045,
          roughness: .42,
          metalness: .02,
          side: THREE.DoubleSide
        });
      }
    });
    this.rig.add(this.glbNode);
    this.fallback.visible = false;
    this.applyDim();   // 새 재질은 불투명 기본값 — 스왑 완료 시 dim 재적용 (inactive 흐림 유지)
  }
  tick(dt) {
    const k = Math.min(1, dt * this.speed * 1.6);
    this.cur.x += (this.tgt.x - this.cur.x) * k;
    this.cur.y += (this.tgt.y - this.cur.y) * k;
    let dr = ((this.tgt.rot - this.cur.rot + 540) % 360) - 180;   // 최단 경로
    this.cur.rot += dr * k;
    this.cur.pl += (this.tgt.pl - this.cur.pl) * k;
    this.cur.pr += (this.tgt.pr - this.cur.pr) * k;
    this.rig.position.set(X(this.cur.x), 0, Z(this.cur.y));
    this.rig.rotation.y = THREE.MathUtils.degToRad(this.cur.rot);
    const h = this.fallback.userData.hinges;
    h.left.rotation.z = -THREE.MathUtils.degToRad(this.cur.pl);
    h.right.rotation.z = THREE.MathUtils.degToRad(this.cur.pr);
  }
}

function applyStates(states, duration) {
  for (const st of states || []) {
    if (!robots.has(st.robot)) robots.set(st.robot, new RobotView(st.robot));
    robots.get(st.robot).setTarget(st, duration);
    lastStates.set(st.robot, st);   // 수동 패널 하이라이트용 최신 상태
  }
  refreshManualUI();
}

// ---------- 렌더 루프 ----------
let last = performance.now();
function animate(now) {
  const dt = Math.min(.05, (now - last) / 1000);
  last = now;
  for (const r of robots.values()) r.tick(dt);
  if (baseline && robots.has(selRobot)) {          // 선택된 로봇 발밑 링
    const rig = robots.get(selRobot).rig;
    selRing.visible = true;
    selRing.position.set(rig.position.x, 0.6, rig.position.z);
  } else selRing.visible = false;
  controls.update();
  renderer.render(scene3, camera);
  requestAnimationFrame(animate);
}
requestAnimationFrame(animate);

// ---------- UI: 채팅 ----------
const $ = id => document.getElementById(id);
function subtitle(text, ms = 5000) {
  const el = $('subtitle');
  el.textContent = text; el.style.display = 'block';
  clearTimeout(el._t);
  el._t = setTimeout(() => el.style.display = 'none', ms);
}

let pending = null;   // null | 'approval' | 'clarify' — 입력창의 다음 메시지 용도
function addBubble(who, text) {
  const log = $('chatLog');
  const b = document.createElement('div');
  b.className = 'bubble ' + who;
  b.textContent = text;
  log.appendChild(b);
  log.scrollTop = log.scrollHeight;
  return b;
}

function addApproval(message) {
  const b = addBubble('agent', message);
  const btns = document.createElement('div');
  btns.className = 'btns';
  const ok = document.createElement('button');
  ok.className = 'btnOk'; ok.textContent = '승인';
  const no = document.createElement('button');
  no.className = 'btnNo'; no.textContent = '수정 요청 (아래에 입력)';
  ok.onclick = () => {
    ws.send(JSON.stringify({ type: 'user_feedback', approved: true, feedback: '' }));
    addBubble('user', '(승인)');
    btns.remove(); pending = null;
  };
  no.onclick = () => { $('msgInput').focus(); };
  btns.append(ok, no);
  b.appendChild(btns);
  pending = 'approval';
  b._btns = btns;
}

function sendInput() {
  const inp = $('msgInput');
  const t = inp.value.trim();
  if (!t || !ws || ws.readyState !== 1) return;
  inp.value = '';
  addBubble('user', t);
  if (pending === 'clarify') {
    ws.send(JSON.stringify({ type: 'clarify_answer', answer: t }));
    pending = null;
  } else if (pending === 'approval') {
    ws.send(JSON.stringify({ type: 'user_feedback', approved: false, feedback: t }));
    document.querySelectorAll('.btns').forEach(e => e.remove());
    pending = null;
  } else {
    ws.send(JSON.stringify({ type: 'user_utterance', text: t, input: 'typed' }));
  }
}
$('sendBtn').onclick = sendInput;
$('msgInput').addEventListener('keydown', e => { if (e.key === 'Enter') sendInput(); });

// ---------- baseline 수동 모드 (--baseline) ----------
// scene_change 메시지의 baseline 플래그로 켜진다. 버튼 1회 = manual_command 1건.
// (조작 수 카운트는 파이썬 baseline_loop 몫). 좌표·회전의 진실은 파이썬이므로
// 브라우저는 델타·목표값만 보낸다.
let baseline = false;
let selRobot = 'BOT 1';
const lastStates = new Map();   // name -> 마지막 state (패널 버튼 하이라이트용)

const selRing = new THREE.Mesh(
  new THREE.RingGeometry(31, 35, 40),
  new THREE.MeshBasicMaterial({ color: 0x2e7d32, side: THREE.DoubleSide,
                                transparent: true, opacity: .65 }));
selRing.rotation.x = -Math.PI / 2;
selRing.visible = false;
scene3.add(selRing);

function sendManual(payload) {
  if (ws && ws.readyState === 1)
    ws.send(JSON.stringify({ type: 'manual_command', ...payload }));
}
function refreshManualUI() {
  if (!baseline) return;
  document.querySelectorAll('.rBtn').forEach(b =>
    b.classList.toggle('sel', b.dataset.robot === selRobot));
  const st = lastStates.get(selRobot);
  if (!st) return;
  document.querySelectorAll('.pBtn').forEach(b => {
    const cur = b.dataset.side === 'left' ? (st.panel_left || 0) : (st.panel_right || 0);
    b.classList.toggle('sel', Number(b.dataset.angle) === cur);
  });
}
document.querySelectorAll('.rBtn').forEach(b =>
  b.onclick = () => { selRobot = b.dataset.robot; refreshManualUI(); });
document.querySelectorAll('.mvBtn').forEach(b =>
  b.onclick = () => sendManual({ action: 'move_delta', robot: selRobot,
                                 dx: +b.dataset.dx, dy: +b.dataset.dy, drot: 0 }));
document.querySelectorAll('.rtBtn').forEach(b =>
  b.onclick = () => sendManual({ action: 'move_delta', robot: selRobot,
                                 dx: 0, dy: 0, drot: +b.dataset.drot }));
document.querySelectorAll('.pBtn').forEach(b =>
  b.onclick = () => sendManual({ action: 'panel', robot: selRobot,
                                 side: b.dataset.side, angle: +b.dataset.angle }));
$('storeBtn').onclick = () => sendManual({ action: 'store', robot: selRobot });
$('doneBtn').onclick = () => sendManual({ action: 'commit' });
$('roomSel').onchange = e => sendManual({ action: 'scene', space: e.target.value });

// ---------- WebSocket ----------
let ws;
let lastSpace = null;   // 마지막으로 라벨을 그린 방 (재연결 시 중복 라벨 방지)
let lastReqId = null;   // 마지막으로 그린 HITL 요청 id (같은 소켓 세션 내 중복 방지)
function connect() {
  ws = new WebSocket(`ws://${location.host}/ws`);
  ws.onopen = () => $('status').textContent = '연결됨';
  ws.onclose = () => { $('status').textContent = '재연결 중...'; setTimeout(connect, 1500); };
  ws.onmessage = ev => {
    const m = JSON.parse(ev.data);
    if (m.type === 'scene_change') {
      if (m.baseline && !baseline) {   // 수동 모드 켜기 (패널 표시 + 채팅 입력 숨김)
        baseline = true;
        document.body.classList.add('baseline');
      }
      if (m.scene) {
        buildRoom(m.scene);
        // 방 라벨은 실제로 방이 바뀔 때만 (재연결 스냅샷마다 중복 추가 방지)
        if (m.scene.space !== lastSpace) {
          addBubble('system', '― ' + (m.scene.space || '방') + ' ―');
          lastSpace = m.scene.space;
        }
        if (m.scene.space) $('roomSel').value = m.scene.space;   // 드롭다운 동기화
      }
      applyStates(m.states, 0);
    } else if (m.type === 'state_update') {
      applyStates(m.states, m.duration ?? 1.2);
    } else if (m.type === 'message') {
      subtitle(m.text);
    } else if (m.type === 'chat') {
      addBubble(m.who || 'agent', m.text);
    } else if (m.type === 'approval_request') {
      if (m.req_id == null || m.req_id !== lastReqId) {   // 재접속 재전송 시 중복 방지
        if (m.req_id != null) lastReqId = m.req_id;
        addApproval(m.message);
      }
    } else if (m.type === 'clarify_request') {
      if (m.req_id == null || m.req_id !== lastReqId) {
        if (m.req_id != null) lastReqId = m.req_id;
        addBubble('agent', m.question +
          (m.candidates?.length ? '\n(후보: ' + m.candidates.join(', ') + ')' : ''));
        pending = 'clarify';
      }
    }
  };
}
connect();
