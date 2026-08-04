// three.js 뷰어: buildRoom(scene JSON) + 로봇 GLB 로드 + 상태 보간.
//
// 좌표 규약 (collision.py와 일치시킬 것):
//   방 (x, y) cm, y는 '위쪽' → three: X = x - w/2, Z = d/2 - y (바닥 = XZ 평면)
//   rot(도, CCW) → rotation.y = rad(rot)  (이 매핑에서 부호가 정확히 일치)
//   로봇 GLB: robot_animated.glb 1개, 단위 mm(×0.1), 모델 -z = 방의 왼쪽 패널.
//   본체 중심 오프셋: (255.922, 342.419(바닥), 87.751) mm
//   패널은 피벗 노드를 직접 회전시켜 만든다 (각도별 파일 스왑 없음).
//   주의: 모델 노드 명명이 방 규약과 반대 — right_wing_pivot_anim이 -z(방 왼쪽)에 있다.
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { DRACOLoader } from 'three/addons/loaders/DRACOLoader.js';

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
// robot_animated.glb는 DRACO로 압축돼 있고(extensionsRequired), three가 디코더를 번들하지
// 않으므로 경로 지정이 반드시 필요하다. index.html의 three와 같은 CDN·같은 버전을 쓴다.
const draco = new DRACOLoader();
draco.setDecoderPath('https://cdn.jsdelivr.net/npm/three@0.160.0/examples/jsm/libs/draco/');
loader.setDRACOLoader(draco);
let room = { w: 400, d: 300 };
let roomGroup = null;
const robots = new Map();      // name -> RobotView

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
  if (roomGroup) {
    scene3.remove(roomGroup);
    roomGroup.traverse(o => {   // GPU 리소스 해제 (scene_change마다 누적 방지)
      o.geometry?.dispose();
      for (const m of [].concat(o.material || [])) { m.map?.dispose(); m.dispose(); }
    });
  }
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
  const mat = new THREE.MeshStandardMaterial({ color, roughness: .46, metalness: .03, transparent: true, opacity: .95 });
  const pmat = new THREE.MeshStandardMaterial({ color, roughness: .36, metalness: .02, transparent: true, opacity: .95 });
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
  g.userData.mats = [mat, pmat];   // dim 적용용 (매 갱신마다 traverse 하지 않도록 직접 참조)
  return g;
}

let robotTemplate = null;   // Promise<wrap|null> — 로봇 GLB는 1개, 전 로봇이 공유한다
function robotGlbTemplate() {
  if (!robotTemplate) {
    robotTemplate = new Promise(res => {
      loader.load('/models/robot_animated.glb', gltf => {
        const inner = gltf.scene;
        fixMeshGeometry(inner);
        inner.position.set(-255.922, 342.419, -87.751);   // 본체 중심 보정 (mm)
        const wrap = new THREE.Group();
        wrap.add(inner);
        wrap.scale.setScalar(0.1);                        // mm → cm
        wrap.rotation.y = Math.PI / 2;                    // 모델 +z = 오른쪽 패널 → 방 +x
        res(wrap);
      }, undefined, () => res(null));                     // 실패 → fallback 유지
    });
  }
  return robotTemplate;
}

class RobotView {
  constructor(name) {
    this.name = name;
    this.rig = new THREE.Group();
    this.fallback = buildFallbackRobot(ROBOT_COLORS[name] || 0x888888);
    this.rig.add(this.fallback);
    // GLB용 재질은 로봇당 1개만 만들어 모든 메쉬가 공유한다 (갱신마다 new → GPU 누수 방지)
    this.mat = new THREE.MeshStandardMaterial({
      color: ROBOT_COLORS[name] || 0x888888,
      emissive: ROBOT_ACCENT_COLORS[name] || 0x7fb9c9,
      emissiveIntensity: .045,
      roughness: .42,
      metalness: .02,
      side: THREE.DoubleSide,
      transparent: true,
      opacity: .95
    });
    this.pivots = null;      // { left, right } — GLB 패널 피벗 노드 (로드 완료 후 세팅)
    this.cur = { x: 0, y: 0, rot: 0, pl: 0, pr: 0 };
    this.tgt = { ...this.cur };
    this.speed = 1;
    this.dim = 1;            // inactive 흐림 계수 — 비동기 GLB 로드 후에도 재적용
    scene3.add(this.rig);
    if (USE_GLB) this.attachGlb();
  }
  setTarget(st, duration) {
    this.tgt = { x: st.x, y: st.y, rot: st.rot || 0,
                 pl: st.panel_left || 0, pr: st.panel_right || 0 };
    this.speed = duration > 0 ? 1 / duration : 1e6;
    if (duration <= 0) this.cur = { ...this.tgt };
    this.dim = st.active === 'inactive' ? .55 : 1;
    this.applyDim();
  }
  applyDim() {
    const op = .95 * this.dim;
    this.mat.opacity = op;
    for (const m of this.fallback.userData.mats) m.opacity = op;
  }
  async attachGlb() {
    const tpl = await robotGlbTemplate();
    if (tpl === null) return;   // GLB 없음/로드 실패 → 조립식 fallback 유지
    const node = tpl.clone(true);
    node.traverse(o => {
      if (o.isMesh) {
        o.castShadow = true;
        o.receiveShadow = true;
        o.material = this.mat;   // 로봇당 재질 1개 공유 (메쉬마다 new 하지 않는다)
      }
    });
    // 모델 노드 명명이 방 규약과 반대다: 방 왼쪽(-z) 패널의 피벗이 right_wing_pivot_anim.
    const pl = node.getObjectByName('right_wing_pivot_anim');
    const pr = node.getObjectByName('left_wing_pivot_anim');
    if (pl && pr) this.pivots = { left: pl, right: pr };
    else console.warn('[robot] 패널 피벗 노드를 찾지 못했습니다 — 본체만 표시됩니다');
    this.rig.add(node);
    this.fallback.visible = false;
    this.applyDim();   // 로드 완료 시 dim 재적용 (inactive 흐림 유지)
  }
  tick(dt) {
    const k = Math.min(1, dt * this.speed * 1.6);
    this.cur.x += (this.tgt.x - this.cur.x) * k;
    this.cur.y += (this.tgt.y - this.cur.y) * k;
    let dr = ((this.tgt.rot - this.cur.rot + 540) % 360) - 180;   // 최단 경로
    this.cur.rot += dr * k;
    // 패널은 이동·회전이 끝난 뒤에만 움직인다 (주행 중 패널이 먼저 펴지는 것 방지).
    // 지수 보간은 꼬리가 길어서, 임계값에 닿으면 스냅하고 패널 단계로 넘어간다.
    if (Math.abs(this.tgt.x - this.cur.x) < 2 &&
        Math.abs(this.tgt.y - this.cur.y) < 2 && Math.abs(dr) < 3) {
      this.cur.x = this.tgt.x; this.cur.y = this.tgt.y; this.cur.rot = this.tgt.rot;
      this.cur.pl += (this.tgt.pl - this.cur.pl) * k;
      this.cur.pr += (this.tgt.pr - this.cur.pr) * k;
    }
    this.rig.position.set(X(this.cur.x), 0, Z(this.cur.y));
    this.rig.rotation.y = THREE.MathUtils.degToRad(this.cur.rot);
    const h = this.fallback.userData.hinges;
    h.left.rotation.z = -THREE.MathUtils.degToRad(this.cur.pl);
    h.right.rotation.z = THREE.MathUtils.degToRad(this.cur.pr);
    if (this.pivots) {   // GLB 패널: bake된 애니메이션과 같은 축·부호로 피벗을 직접 회전
      this.pivots.left.rotation.x = THREE.MathUtils.degToRad(this.cur.pl);
      this.pivots.right.rotation.x = -THREE.MathUtils.degToRad(this.cur.pr);
    }
  }
}

function applyStates(states, duration) {
  for (const st of states || []) {
    if (!robots.has(st.robot)) robots.set(st.robot, new RobotView(st.robot));
    robots.get(st.robot).setTarget(st, duration);
  }
}

// ---------- 렌더 루프 ----------
let last = performance.now();
function animate(now) {
  const dt = Math.min(.05, (now - last) / 1000);
  last = now;
  for (const r of robots.values()) r.tick(dt);
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

// ---------- push-to-talk 음성 입력 ----------
let mediaRecorder = null, chunks = [], recording = false;
async function startRec() {
  if (recording) return;
  try {
    if (!mediaRecorder) {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      mediaRecorder = new MediaRecorder(stream);
      mediaRecorder.ondataavailable = e => chunks.push(e.data);
      mediaRecorder.onstop = onRecStop;
    }
    chunks = [];
    mediaRecorder.start();
    recording = true;
    $('micBtn').classList.add('rec');
    $('status').textContent = '녹음 중... (놓으면 전송)';
  } catch (e) {
    addBubble('system', '마이크를 사용할 수 없습니다: ' + e.message);
  }
}
function stopRec() {
  if (!recording) return;
  recording = false;
  mediaRecorder.stop();
  $('micBtn').classList.remove('rec');
  $('status').textContent = '전사 중...';
}
async function onRecStop() {
  const blob = new Blob(chunks, { type: mediaRecorder.mimeType || 'audio/webm' });
  if (blob.size < 2000) { $('status').textContent = '연결됨'; return; }   // 너무 짧음
  try {
    const r = await fetch('/stt', { method: 'POST', body: blob,
                                    headers: { 'Content-Type': blob.type } });
    const data = await r.json();
    $('status').textContent = '연결됨';
    const text = (data.text || '').trim();
    if (!text) { addBubble('system', '(음성을 인식하지 못했어요)'); return; }
    addBubble('user', text);
    if (pending === 'clarify') {
      ws.send(JSON.stringify({ type: 'clarify_answer', answer: text }));
      pending = null;
    } else if (pending === 'approval') {
      ws.send(JSON.stringify({ type: 'user_feedback', approved: false, feedback: text }));
      document.querySelectorAll('.btns').forEach(e => e.remove());
      pending = null;
    } else {
      ws.send(JSON.stringify({ type: 'user_utterance', text, input: 'voice' }));
    }
  } catch (e) {
    $('status').textContent = '연결됨';
    addBubble('system', 'STT 오류: ' + e.message);
  }
}
// 버튼 홀드
$('micBtn').addEventListener('mousedown', startRec);
$('micBtn').addEventListener('touchstart', e => { e.preventDefault(); startRec(); });
addEventListener('mouseup', stopRec);
addEventListener('touchend', stopRec);
// 입력창 밖에서 스페이스바 홀드 = 기존 STT UX 그대로
addEventListener('keydown', e => {
  if (e.code === 'Space' && !e.repeat && document.activeElement !== $('msgInput')) {
    e.preventDefault(); startRec();
  }
});
addEventListener('keyup', e => {
  if (e.code === 'Space' && document.activeElement !== $('msgInput')) {
    e.preventDefault(); stopRec();
  }
});

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
      if (m.scene) {
        buildRoom(m.scene);
        // 방 라벨은 실제로 방이 바뀔 때만 (재연결 스냅샷마다 중복 추가 방지)
        if (m.scene.space !== lastSpace) {
          addBubble('system', '― ' + (m.scene.space || '방') + ' ―');
          lastSpace = m.scene.space;
        }
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
