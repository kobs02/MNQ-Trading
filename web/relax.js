// 스트레스 발산!!: 4버튼 리듬게임(S·D·L·;). 노트를 맞히면 판정·콤보만큼 점수가 오르고, 점수가 문턱을 넘으면 다음 단계로 자란다
const $ = (id) => document.getElementById(id);
const KEYS = ['KeyS', 'KeyD', 'KeyL', 'Semicolon'], LABELS = ['S', 'D', 'L', ';'];
// 딜레이 패턴: 동시치기 없이 24비트(한 박의 1/6) 간격으로 노트가 하나씩, 무작위 모양의 계단이 끊임없이 이어진다. 난이도 = BPM
const DIFFS = {
  easy: { name: 'EASY', bpm: 60 },
  normal: { name: 'NORMAL', bpm: 90 },
  hard: { name: 'HARD', bpm: 120 },
  super: { name: 'SUPER HARD', bpm: 150 },
};
const STEP = (bpm) => 60000 / bpm / 6;  // 24비트 간격(ms)
const F = 1000 / 24;  // 판정 한 칸 41.666…ms(1/24초)
const JUDGE = [[F, 'PERFECT', 3], [F * 2, 'GREAT', 2], [F * 3, 'GOOD', 1]];  // [±ms 안, 이름, 점수]
const MISS_MS = F * 3;
const PX_PER_MS = 0.12;  // 속도 1.0일 때 노트가 1ms에 내려오는 거리(px)
const COMBO = [[100, 5], [50, 3], [20, 2]];  // [콤보 이상, 배수]
// [문턱, 그림, 이름]
const STAGES = [
  [0, '🌰', '씨앗'], [30, '🌱', '새싹'], [100, '🌿', '풀잎'], [250, '🪴', '화분'], [600, '🌳', '나무'],
  [1200, '🌸', '꽃나무'], [2500, '🍎', '열매나무'], [5000, '🌲🌳🌲', '작은 숲'], [10000, '🏝️', '평화로운 섬'],
  [20000, '🌈', '무지개'], [40000, '🌕', '보름달'], [80000, '🪐', '토성'], [160000, '🌌', '은하수'], [320000, '🧘', '마음의 평화'],
];
const SOUNDS = ['s', 'd', 'l', 'k'];  // ; 는 녹음이 없어 옆의 k 소리

const load = (k, d) => { try { return localStorage.getItem(k) ?? d; } catch (e) { return d; } };
const save = (k, v) => { try { localStorage.setItem(k, v); } catch (e) { /* 저장 못 해도 게임은 계속 */ } };
let score = Number(load('relax-score', 0)) || 0;
let diff = DIFFS[load('relax-diff', 'normal')] ? load('relax-diff', 'normal') : 'normal';
let speed = Math.min(10, Math.max(1, Number(load('relax-speed', 3)) || 3));

const audio = new AudioContext(), buffers = {};
SOUNDS.forEach((k) => fetch(`sounds/nk-cream/${k}.wav`).then((r) => r.arrayBuffer()).then((b) => audio.decodeAudioData(b)).then((d) => { buffers[k] = d; }).catch(() => {}));
function play(lane) {
  if (audio.state === 'suspended') audio.resume();
  const buf = buffers[SOUNDS[lane]];
  if (!buf) return;
  const s = audio.createBufferSource();
  s.buffer = buf;
  s.playbackRate.value = 0.97 + Math.random() * 0.06;  // 연타해도 기계음처럼 똑같지 않게
  s.connect(audio.destination);
  s.start();
}

// 게임 상태. 노트 t = 판정선에 닿는 시각(performance.now 기준)
let running = false, notes = [], nextAt = 0, combo = 0, maxCombo = 0, counts = { PERFECT: 0, GREAT: 0, GOOD: 0, MISS: 0 };
let flash = null;  // 마지막 판정 { text, at, gain }
const pressed = [0, 0, 0, 0];  // 레인 누름 표시가 끝나는 시각

const canvas = $('lane'), g = canvas.getContext('2d');
let W = 0, H = 0, JY = 0;
function fit() {
  const r = canvas.getBoundingClientRect(), dpr = window.devicePixelRatio || 1;
  canvas.width = r.width * dpr; canvas.height = r.height * dpr;
  g.setTransform(dpr, 0, 0, dpr, 0, 0);
  W = r.width; H = r.height; JY = H - 70;
}
window.addEventListener('resize', fit);
const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
const multiplier = () => (COMBO.find(([c]) => combo >= c) || [0, 1])[1];

// 섞인 계단: 오르내림이 섞인 모양. 3 ~ 4개 토막으로 잘라 쓴다
const MIXED = [[1, 2, 0, 3], [2, 1, 3, 0], [0, 3, 1, 2], [3, 0, 2, 1], [0, 2, 1, 3], [3, 1, 2, 0], [1, 0, 2, 3], [2, 3, 1, 0], [0, 2, 3, 1], [3, 1, 0, 2]];
const rand = (n) => Math.floor(Math.random() * n);
// 다음 토막 종류: 같은 방향 계단을 주로 잇고, 가끔 꺾이거나(변곡점) 섞인 계단이 나온다
const NEXT = {
  up: [[0.64, 'up'], [0.76, 'turn'], [0.88, 'zig'], [1, 'mixed']],
  down: [[0.64, 'down'], [0.76, 'turn'], [0.88, 'zig'], [1, 'mixed']],
  mixed: [[0.35, 'up'], [0.7, 'down'], [0.85, 'zig'], [1, 'mixed']],
};
let hist = [], mode = 'up', prev = null;  // hist: 최근 레인 몇 개(이음매 점검용), prev: 바로 앞 토막 { kind, len }
function run(dir, from, k) {  // from에서 dir(+1 오르기, -1 내리기)으로 k칸
  return Array.from({ length: k }, (_, i) => from + dir * i);
}
function segment(kind) {
  let k = [2, 2, 3, 3, 3, 4][rand(6)];  // 123·234·12·23·34가 섞이고 1234는 가끔
  if (k === 4 && prev && prev.kind === kind && prev.len === 4 && Math.random() < 0.9) k = [2, 3, 3][rand(3)];  // 1234 바로 뒤 1234는 드물게
  if (kind === 'up') return run(1, rand(5 - k), k);
  if (kind === 'down') return run(-1, 3 - rand(5 - k), k);
  if (kind === 'zig') return mode === 'down' || (mode === 'mixed' && Math.random() < 0.5) ? [3, 1, 2, 0] : [0, 2, 1, 3];  // 엇갈린 계단 4231 · 1324
  if (kind === 'turn') {  // 꼭대기(바닥)에서 바로 반대로: 1 2 3 4 → 3 2 1
    const last = hist[hist.length - 1], dir = mode === 'up' ? -1 : 1, room = dir < 0 ? last : 3 - last;
    if (room >= 2) return run(dir, last + dir, Math.min(room, k));
    return segment(mode === 'up' ? 'down' : 'up');
  }
  const kk = 3 + rand(2), shape = MIXED[rand(MIXED.length)], start = rand(5 - kk);
  return shape.slice(start, start + kk);
}
// 연타(1 1), 트릴(1 2 1 2, 1 4 2 1 4)은 막고, 1 2 1처럼 되돌아오는 꼴도 변곡점에서만 허용한다
function bad(lanes, turn) {
  const seq = hist.concat(lanes);
  for (let i = hist.length; i < seq.length; i++) {
    if (seq[i] === seq[i - 1]) return true;
    if (seq[i] === seq[i - 2] && (!turn || seq[i - 1] === seq[i - 3])) return true;
    if (seq[i] === seq[i - 3] && seq[i - 1] === seq[i - 4] && Math.abs(seq[i] - seq[i - 1]) >= 2) return true;  // 1 4 x 1 4처럼 사이에 하나 낀 넓은 트릴
  }
  return false;
}
function spawn(now) {
  const lead = JY / (PX_PER_MS * speed) + 200;  // 레인 맨 위에서 나타나도록 그만큼 앞서 만든다
  const step = STEP(DIFFS[diff].bpm);
  while (nextAt < now + lead) {
    let kind, lanes, tries = 0;
    do {
      const r = Math.random();
      kind = hist.length ? NEXT[mode].find(([p]) => r < p)[1] : 'up';
      // 꺾기는 끝 레인(1·4)에서만, 꺾은 바로 뒤에는 다시 꺾지 않는다: 1 2 3 2 1 2 3처럼 좁게 오가는 꼴을 피한다
      const turnOk = (hist[hist.length - 1] === 0 || hist[hist.length - 1] === 3) && prev.kind !== 'turn';
      lanes = kind === 'turn' && !turnOk ? null : segment(kind);
    } while ((!lanes || bad(lanes, kind === 'turn')) && ++tries < 200);
    if (!lanes) { kind = 'mixed'; lanes = segment(kind); }
    if (kind === 'turn') mode = mode === 'up' ? 'down' : 'up';
    else if (kind !== 'zig') mode = kind;
    else if (mode === 'mixed') mode = lanes[0] === 0 ? 'up' : 'down';  // 엇갈린 계단도 방향을 이어 간다
    for (const lane of lanes) { notes.push({ t: nextAt, lane }); nextAt += step; }
    hist = hist.concat(lanes).slice(-4);
    prev = { kind, len: lanes.length };
  }
}
function judge(text, gain) {
  counts[text]++;
  flash = { text, at: performance.now(), gain };
  if (text === 'MISS') { combo = 0; return; }
  combo++;
  maxCombo = Math.max(maxCombo, combo);
  addScore(gain * multiplier());
}
function hit(lane) {
  play(lane);
  pressed[lane] = performance.now() + 90;
  if (!running) return;
  const now = performance.now();
  const n = notes.filter((x) => x.lane === lane && !x.done && Math.abs(x.t - now) <= MISS_MS).sort((a, b) => a.t - b.t)[0];
  if (!n) return;  // 노트가 없는 곳을 친 것은 벌점 없음
  n.done = true;
  const [, text, gain] = JUDGE.find(([ms]) => Math.abs(n.t - now) <= ms);
  judge(text, gain);
}

function draw(now) {
  const lw = W / 4, bg = css('--sunken'), line = css('--border-strong'), grad = g.createLinearGradient(0, 0, W, 0);
  grad.addColorStop(0, '#7b6cff'); grad.addColorStop(0.55, '#4f8bff'); grad.addColorStop(1, '#22d3ee');
  g.fillStyle = bg; g.fillRect(0, 0, W, H);
  for (let i = 0; i < 4; i++) {
    if (pressed[i] > now) {  // 누른 레인은 아래에서 빛이 올라온다
      const lg = g.createLinearGradient(0, JY, 0, JY - 260);
      lg.addColorStop(0, 'rgba(79,139,255,.35)'); lg.addColorStop(1, 'rgba(79,139,255,0)');
      g.fillStyle = lg; g.fillRect(i * lw, JY - 260, lw, 260);
    }
    if (i) { g.fillStyle = line; g.fillRect(i * lw, 0, 1, H); }
  }
  for (const n of notes) {
    if (n.done) continue;
    const y = JY - (n.t - now) * PX_PER_MS * speed;
    if (y < -20) continue;
    g.fillStyle = n.lane === 1 || n.lane === 2 ? '#22d3ee' : '#f4f6fb';  // 디제이맥스처럼 바깥 흰색 · 안쪽 하늘색
    g.fillRect(n.lane * lw + 4, y - 12, lw - 8, 24);
  }
  g.fillStyle = grad; g.fillRect(0, JY - 2, W, 4);  // 판정선
  g.font = '700 18px "Wanted Sans Variable", sans-serif'; g.textAlign = 'center'; g.textBaseline = 'middle';
  for (let i = 0; i < 4; i++) {
    g.fillStyle = pressed[i] > now ? '#22d3ee' : css('--text-3');
    g.fillText(LABELS[i], i * lw + lw / 2, JY + 35);
  }
  if (flash && now - flash.at < 500) {
    const a = 1 - (now - flash.at) / 500, color = { PERFECT: '#22d3ee', GREAT: '#2ee69f', GOOD: '#f0b90b', MISS: '#ff6f83' }[flash.text];
    g.globalAlpha = a; g.fillStyle = color; g.font = '800 30px "Wanted Sans Variable", sans-serif';
    g.fillText(flash.text, W / 2, H * 0.42);
    if (combo > 1) { g.font = '800 22px "Wanted Sans Variable", sans-serif'; g.fillStyle = css('--text'); g.fillText(`${combo} COMBO${multiplier() > 1 ? ` ×${multiplier()}` : ''}`, W / 2, H * 0.42 + 34); }
    g.globalAlpha = 1;
  }
}
function frame(now) {
  if (running) {
    spawn(now);
    for (const n of notes) if (!n.done && now - n.t > MISS_MS) { n.done = true; judge('MISS', 0); }
    notes = notes.filter((n) => !n.done || now - n.t < 0);
    renderStats();
  }
  draw(now);
  requestAnimationFrame(frame);
}

function start() {
  if (audio.state === 'suspended') audio.resume();
  notes = []; combo = 0; maxCombo = 0; counts = { PERFECT: 0, GREAT: 0, GOOD: 0, MISS: 0 }; flash = null;
  nextAt = performance.now() + 1200; hist = []; mode = 'up'; prev = null;
  running = true;
  $('over').hidden = true;
  $('start').blur();
}
function stop(msg) {
  if (!running) return;
  running = false;
  notes = [];
  const total = counts.PERFECT + counts.GREAT + counts.GOOD + counts.MISS;
  $('over-msg').textContent = msg || `멈춤 · 맞힌 노트 ${total - counts.MISS}/${total} · 최대 콤보 ${maxCombo}`;
  $('start').textContent = '다시 시작';
  $('over').hidden = false;
}

const stageOf = (n) => STAGES.reduce((i, s, j) => (n >= s[0] ? j : i), 0);
function addScore(gain) {
  const before = stageOf(score);
  score += gain;
  save('relax-score', score);
  const pic = $('pic');
  if (stageOf(score) > before) {
    pic.classList.remove('evolve'); void pic.offsetWidth; pic.classList.add('evolve');  // 애니메이션을 처음부터 다시
    const t = document.createElement('div');
    t.className = 'toast';
    t.textContent = `🎉 ${STAGES[stageOf(score)][2]}(으)로 자랐어요!`;
    $('grow').append(t);
    setTimeout(() => t.remove(), 2200);
  } else {
    pic.classList.add('bump');
    setTimeout(() => pic.classList.remove('bump'), 70);
  }
  renderGrow();
}
function renderGrow() {
  const i = stageOf(score), [from, pic, name] = STAGES[i], next = STAGES[i + 1];
  $('stage-name').innerHTML = `${i + 1}단계 <b>${name}</b>`;
  $('pic').textContent = pic;
  $('score').textContent = score.toLocaleString();
  $('bar').style.width = next ? `${(score - from) / (next[0] - from) * 100}%` : '100%';
  $('next').textContent = next ? `다음 단계 "${next[2]}"까지 ${(next[0] - score).toLocaleString()}` : '끝까지 키웠습니다. 이제 차트로 돌아가도 됩니다 🙏';
}
let statsKey = '';
function renderStats() {
  const key = JSON.stringify(counts) + maxCombo;
  if (key === statsKey) return;
  statsKey = key;
  $('stats').innerHTML = Object.entries(counts).map(([k, v]) => `<span>${k} <b>${v}</b></span>`).join('') + `<span>최대 콤보 <b>${maxCombo}</b></span>`;
}
function renderOpts() {
  $('diff').innerHTML = Object.entries(DIFFS).map(([k, d]) => `<button class="plain-button" data-diff="${k}" aria-pressed="${k === diff}">${d.name} <small>${d.bpm}</small></button>`).join('');
  $('speed').value = speed;
  $('speed-out').textContent = `×${speed.toFixed(1)}`;
}

document.addEventListener('keydown', (e) => {
  if ($('ask').open) return;  // 확인 창에서는 Space·Enter가 창의 버튼을 누르게
  if (e.code === 'Space') { e.preventDefault(); return stop(); }  // 스크롤·버튼 누름 막기
  const lane = KEYS.indexOf(e.code);
  if (lane < 0 || e.ctrlKey || e.metaKey || e.altKey) return;
  e.preventDefault();
  if (!e.repeat) hit(lane);
});
document.addEventListener('visibilitychange', () => { if (document.hidden) stop(); });  // 다른 탭에 가 있는 동안 노트가 전부 MISS 되지 않게
$('start').addEventListener('click', start);
$('diff').addEventListener('click', (e) => {
  const b = e.target.closest('[data-diff]');
  if (!b) return;
  diff = b.dataset.diff;
  save('relax-diff', diff);
  renderOpts();
  b.blur();
});
$('speed').addEventListener('input', (e) => {
  speed = Number(e.target.value);
  save('relax-speed', speed);
  renderOpts();
});
$('speed').addEventListener('change', (e) => e.target.blur());  // 놓은 뒤 S·D 키가 슬라이더를 움직이지 않게
$('reset').addEventListener('click', () => {
  stop();
  $('ask').returnValue = '';
  $('ask').showModal();
});
$('ask').addEventListener('close', () => {
  if ($('ask').returnValue !== 'ok') return;
  score = 0;
  save('relax-score', 0);
  renderGrow();
});

fit();
renderOpts();
renderGrow();
renderStats();
requestAnimationFrame(frame);
