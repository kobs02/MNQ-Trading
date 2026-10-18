// 실시간 차트 페이지: MNQ 근월물 봉을 0.5초마다 이어 그린다(서버가 LS 실시간 체결로 마지막 봉을 고친다).
// 분봉에는 고른 후보를 최근 2년 1시간봉으로 돌린 롱·숏 진입·청산(과거 시험)과 가상 매매를, 일봉(2021 ~)에는 규칙의 진입·청산을 화살표로 표시한다.
// 청산하거나 줄인 화살표에는 그 몫의 손익(비용 전)을 붙인다.
// 규칙이 보는 값도 차트에 그린다: 일봉에는 200일선·5일 최저 종가, 분봉에는 장중 갈래의 9시 시가와 노이즈 경계.
// 오른쪽 칸은 두 개: 주문(대회 매매법 고르기, 자동 주문 켜고 끄기, 직접 주문)과 매매법(차트에 표시할 후보의 포지션과 근거). 차트 아래는 LS 모의계좌의 실제 포지션.

const $ = (id) => document.getElementById(id);
const token = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const fmtPrice = (v) => (v == null ? '-' : v.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 }));
const fmtMoney = (v, digits = 2) => (v < 0 ? '-$' : '$') + Math.abs(v).toLocaleString('en-US', { maximumFractionDigits: digits });
const fmtSigned = (v, digits = 2) => (v == null ? '-' : (v >= 0 ? '+' : '') + (v * 100).toFixed(digits) + '%');
const fmtLeft = (sec) => {  // 남은 시간: 3일 07:12:05
  const s = Math.max(0, Math.round(sec - Date.now() / 1000)), d = Math.floor(s / 86400);
  return (d ? `${d}일 ` : '') + [Math.floor(s / 3600) % 24, Math.floor(s / 60) % 60, s % 60].map((v) => String(v).padStart(2, '0')).join(':');
};
const fmtTime = (sec) => new Date(sec * 1000).toLocaleString('ko-KR', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' });
const tone = (v) => (v > 0 ? 'c-up' : v < 0 ? 'c-down' : '');
const SLEEVES = { daily: '일봉', intraday: '장중' };
const partsText = (parts) => (parts && 'intraday' in parts ? Object.entries(parts).map(([k, n]) => `${SLEEVES[k]} ${n > 0 ? '롱' : n < 0 ? '숏' : ''}${n ? ' ' + Math.abs(n) : '없음'}`).join(' · ') : '');
const position = (n) => (n > 0 ? `<span class="c-up">롱 ${n}계약</span>` : n < 0 ? `<span class="c-down">숏 ${-n}계약</span>` : '없음');
const INTERVALS = { 1: '1분', 5: '5분', 15: '15분', 60: '1시간', D: '일봉' };
const UP = '#26a69a', DOWN = '#ef5350';
// 차트 라이브러리는 UTC로 표시하므로 시각을 이 컴퓨터의 시간대만큼 밀어서 넣는다
const TZ = -new Date().getTimezoneOffset() * 60;

const state = {
  interval: INTERVALS[localStorage.getItem('mnqInterval')] ? localStorage.getItem('mnqInterval') : '5',
  candidate: localStorage.getItem('mnqCandidate'),
  last: null,        // 차트에 그린 마지막 봉의 시각(밀기 전)
  bars: [],
  quote: null,
  trading: null,
  signals: null,     // 후보마다 2021년부터 규칙대로 계약 수를 바꾼 날들(일봉에 표시)
  replay: null,      // 후보마다 최근 2년 1시간봉으로 돌린 계약 수 변화(분봉에 표시)
  dailyCloses: null, // 일봉 종가(분봉에도 200일선·5일 최저 종가를 가로선으로 그리는 용도)
  generation: 0,     // 봉 단위를 바꿀 때마다 늘린다. 늦게 온 이전 응답을 버리는 용도
  loadingOlder: false,
  noOlder: false,    // 더 과거의 봉이 서버(LS)에 없다
};

async function api(path) {
  const res = await fetch(path);
  const body = await res.json();
  if (!res.ok) throw new Error(body.error || res.statusText);
  return body;
}

async function post(path, data) {
  const res = await fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) });
  const body = await res.json();
  if (!res.ok) throw new Error(body.error || res.statusText);
  return body;
}

const chart = LightweightCharts.createChart($('chart'), {
  autoSize: true,
  layout: { background: { color: 'transparent' }, textColor: token('--text-2'), fontFamily: 'inherit', fontSize: 13 },
  grid: { vertLines: { color: token('--grid') }, horzLines: { color: token('--grid') } },
  rightPriceScale: { borderColor: token('--border') },
  timeScale: { borderColor: token('--border'), timeVisible: true, secondsVisible: false, rightOffset: 8 },
  crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
  localization: { locale: 'ko-KR' },
});
const series = chart.addCandlestickSeries({
  upColor: UP, downColor: DOWN, wickUpColor: UP, wickDownColor: DOWN, borderVisible: false,
  priceFormat: { type: 'custom', minMove: 0.25, formatter: fmtPrice },
});
let entryLine = null;
// 규칙이 보는 값(지표): 일봉의 선 두 개와 분봉의 가로선들
const lineOpts = (color, title) => ({ color, title, lineWidth: 1, priceLineVisible: false, crosshairMarkerVisible: false, lastValueVisible: true,
  priceFormat: { type: 'custom', minMove: 0.25, formatter: fmtPrice } });
const smaSeries = chart.addLineSeries(lineOpts(token('--accent'), '200일선'));
const lowSeries = chart.addLineSeries({ ...lineOpts(token('--text-3'), '5일 최저 종가'), lineStyle: LightweightCharts.LineStyle.Dotted,
  lineType: LightweightCharts.LineType.WithSteps });
let levelLines = [];

// ---------- 봉 ----------
const shifted = (b) => ({ ...b, time: b.time + TZ });

async function loadBars(fresh) {
  const gen = state.generation;
  const whole = fresh || state.last == null;
  // 처음에는 봉 전체를, 그다음부터는 마지막 봉부터만 받는다(0.5초마다 물으므로 가볍게)
  const d = await api(`/api/chart?interval=${state.interval}${whole ? '' : `&since=${state.last}`}`);
  if (gen !== state.generation) return;
  state.quote = d.quote;
  if (whole) {
    series.setData(d.bars.map(shifted));
    state.bars = d.bars;
    chart.timeScale().scrollToRealTime();
  } else {
    d.bars.forEach((b) => {
      series.update(shifted(b));
      const i = state.bars.length - 1;
      if (i >= 0 && state.bars[i].time === b.time) state.bars[i] = b;
      else if (i < 0 || b.time > state.bars[i].time) state.bars.push(b);
    });
  }
  state.last = state.bars.length ? state.bars[state.bars.length - 1].time : null;
  $('chart-msg').hidden = d.bars.length > 0;
  renderQuote();
  renderMarkers();
  renderStudies();
  renderDrawings();
  if (!legendHover) renderLegend(d.bars[d.bars.length - 1]);
}

// 화면을 왼쪽 끝 가까이까지 끌면 그 이전 봉을 받아 앞에 붙인다. 보고 있던 자리는 그대로 둔다
async function loadOlder() {
  if (state.loadingOlder || state.noOlder || state.interval === 'D' || !state.bars.length) return;
  state.loadingOlder = true;
  const gen = state.generation;
  try {
    const d = await api(`/api/chart/older?interval=${state.interval}`);
    if (gen !== state.generation) return;
    state.noOlder = d.done;
    if (d.bars.length) {
      const range = chart.timeScale().getVisibleLogicalRange();
      state.bars = d.bars.concat(state.bars);
      series.setData(state.bars.map(shifted));
      if (range) chart.timeScale().setVisibleLogicalRange({ from: range.from + d.bars.length, to: range.to + d.bars.length });
      renderMarkers();
      renderStudies();
    }
  } catch (e) { /* 다음에 끌 때 다시 */ } finally {
    state.loadingOlder = false;
  }
}
chart.timeScale().subscribeVisibleLogicalRangeChange((range) => {
  if (range && range.from < 30) loadOlder();
});

let legendHover = false;
function renderLegend(b) {
  if (!b) { $('legend').innerHTML = ''; return; }
  const change = (b.close - b.open) / b.open;
  const day = state.interval === 'D' ? `${new Date(b.time * 1000).toISOString().slice(0, 10)} · ` : '';
  $('legend').innerHTML = `<b>${INTERVALS[state.interval]}</b>${day}시 ${fmtPrice(b.open)} · 고 ${fmtPrice(b.high)} · 저 ${fmtPrice(b.low)} · 종 ${fmtPrice(b.close)}
    <span class="${tone(change)}">${fmtSigned(change, 2)}</span> · 거래량 ${b.volume.toLocaleString('en-US')}`;
}
chart.subscribeCrosshairMove((p) => {
  const b = p.time ? state.bars.find((x) => x.time + TZ === p.time) : null;
  legendHover = Boolean(b);
  renderLegend(b || state.bars[state.bars.length - 1]);
});

// ---------- 자동매매 표시 ----------
const current = () => state.trading && (state.trading.candidates.find((c) => c.key === state.candidate)
  || state.trading.candidates.find((c) => c.key === state.trading.live));  // 처음에는 자동 주문이 따라가는 후보

// 뉴욕 시각의 '시'(0 ~ 23)
const nyHour = () => Number(new Date().toLocaleString('en-US', { timeZone: 'America/New_York', hour: 'numeric', hourCycle: 'h23' }));

// 규칙이 보는 값을 차트에: 일봉은 차트의 봉(NQ 선물)으로 계산한 200일선·5일 최저 종가(규칙은 나스닥100 지수로 보므로 수준이 조금 다르다),
// 분봉은 장중 갈래가 오늘 보는 9시 시가와 다음 판단 시각의 노이즈 경계(LS MNQ 1시간봉으로 계산한 그대로)
let levelsKey = null;
function renderLevels() {
  const c = current();
  // 0.5초마다 불리므로 후보·봉 단위·봉 개수·경계·시가 바뀔 때만 다시 그린다
  const key = c && [state.interval, c.key, state.bars.length, JSON.stringify(c.noise), nyHour(), (state.dailyCloses || []).length].join('|');
  if (key === levelsKey) return;
  levelsKey = key;
  const daily = state.interval === 'D' && c;
  const closes = state.bars.map((b) => b.close);
  const roll = (n, f) => state.bars.slice(n - 1).map((b, i) => ({ time: b.time + TZ, value: f(closes.slice(i, i + n)) }));
  smaSeries.setData(daily && c.rule !== 'flat' ? roll(200, (w) => w.reduce((a, v) => a + v, 0) / w.length) : []);
  lowSeries.setData(daily && c.rule === 'dip_trend' ? roll(5, (w) => Math.min(...w)) : []);
  levelLines.forEach((l) => series.removePriceLine(l));
  levelLines = [];
  if (!c || state.interval === 'D') return;
  const line = (price, color, title) => levelLines.push(series.createPriceLine({ price, color, title, lineWidth: 1, lineStyle: LightweightCharts.LineStyle.Dotted, axisLabelVisible: true }));
  // 분봉: 일봉 지표의 지금 값(일봉 화면의 두 선의 마지막 값)을 가로선으로. 화면의 가격 범위 밖이면 보이지 않는다
  const dc = state.dailyCloses;
  if (dc && dc.length >= 200 && c.rule !== 'flat') {
    line(dc.slice(-200).reduce((a, v) => a + v, 0) / 200, token('--accent'), '200일선');
    if (c.rule === 'dip_trend') line(Math.min(...dc.slice(-5)), token('--text-3'), '5일 최저 종가');
  }
  const n = c.noise;
  if (!n || !n.today) return;
  const hour = Math.min(15, Math.max(10, nyHour() + 1));
  const band = n.bands[hour];
  line(n.open, token('--text-3'), '9시 시가');
  if (band) {
    line(band[0], UP, `${hour}시 판단: 위로 넘으면 롱`);
    line(band[1], DOWN, `${hour}시 판단: 아래로 넘으면 숏`);
  }
}

// ---------- 차트 도구: 수평선·박스 그리기, MACD·RSI ----------
// 그린 것은 시각(봉 시작, 밀기 전)·가격으로 저장해 봉 단위를 바꿔도 같은 자리에 그린다. 저장은 이 브라우저(localStorage)에만
const load = (k, v) => { try { return JSON.parse(localStorage.getItem(k)) || v; } catch (e) { return v; } };
const keep = (k, v) => { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) { /* 저장 못 해도 화면은 그대로 */ } };
const draw = { tool: null, start: null, hover: null, mouse: null, ichi: null, selected: null, items: load('mnqDraw', []), studies: load('mnqStudies', { macd: false, rsi: false, ichimoku: false }) };
const hlines = new Map();  // 수평선 항목 → 가격선 객체

// 시각 → 지금 봉 단위에서 그 시각이 들어가는 봉의 x 좌표(봉 사이 시각도 그 봉으로)
function timeX(t) {
  const bars = state.bars;
  if (!bars.length) return null;
  let lo = 0, hi = bars.length - 1;
  while (lo < hi) { const mid = (lo + hi + 1) >> 1; if (bars[mid].time <= t) lo = mid; else hi = mid - 1; }
  return chart.timeScale().logicalToCoordinate(lo);
}
function pointAt(p) {
  if (!p || !p.point || !state.bars.length) return null;
  const i = Math.max(0, Math.min(state.bars.length - 1, Math.round(chart.timeScale().coordinateToLogical(p.point.x))));
  const price = series.coordinateToPrice(p.point.y);
  return price == null ? null : { time: state.bars[i].time, price: Math.round(price * 4) / 4 };  // 0.25틱에 맞춘다
}

function renderDrawings() {
  // 수평선: 가격선으로(가격 축에 값이 보인다)
  const want = new Set(draw.items.filter((d) => d.type === 'hline'));
  hlines.forEach((line, d) => { if (!want.has(d)) { series.removePriceLine(line); hlines.delete(d); } });
  want.forEach((d) => {
    if (!hlines.has(d)) hlines.set(d, series.createPriceLine({ price: d.price, color: token('--accent'), lineWidth: 1, lineStyle: LightweightCharts.LineStyle.Solid, axisLabelVisible: true, title: '' }));
    const on = d === draw.selected;  // 고른 선은 굵은 점선
    hlines.get(d).applyOptions({ lineWidth: on ? 2 : 1, lineStyle: on ? LightweightCharts.LineStyle.Dashed : LightweightCharts.LineStyle.Solid });
  });
  // 박스: 그리기 층(SVG)에. 그리는 중이면 커서까지 미리 보여 준다
  const boxes = draw.items.filter((d) => d.type === 'box');
  if (draw.start && draw.hover) boxes.push({ a: draw.start, b: draw.hover, preview: true });
  const color = token('--accent');
  $('draw').innerHTML = renderCloud(chart.timeScale().width()) + boxes.map((d) => {
    const r = boxRect(d);
    if (!r) return '';
    const on = d === draw.selected;
    return `<rect x="${r.x}" y="${r.y}" width="${r.w}" height="${r.h}" fill="${color}" fill-opacity="${d.preview ? 0.03 : on ? 0.12 : 0.06}" stroke="${color}" stroke-width="${on ? 2 : 1}"${d.preview || on ? ' stroke-dasharray="4 3"' : ''}/>`;
  }).join('');
}

// 박스의 화면 사각형(차트 칸 안으로 자른 것). 화면 밖이면 null
function boxRect(d) {
  const x1 = timeX(d.a.time), x2 = timeX(d.b.time), y1 = series.priceToCoordinate(d.a.price), y2 = series.priceToCoordinate(d.b.price);
  if ([x1, x2, y1, y2].some((v) => v == null)) return null;
  const x = Math.max(0, Math.min(x1, x2)), w = Math.min(chart.timeScale().width(), Math.max(x1, x2)) - x;
  return w > 0 ? { x, y: Math.min(y1, y2), w, h: Math.abs(y2 - y1) } : null;
}

// 누른 자리에 있는 그린 것(위에 그린 것부터): 수평선은 위아래 5px, 박스는 안쪽이나 테두리 4px 안
function hitTest(pt) {
  if (!pt) return null;
  for (let i = draw.items.length - 1; i >= 0; i--) {
    const d = draw.items[i];
    if (d.type === 'hline') {
      const y = series.priceToCoordinate(d.price);
      if (y != null && Math.abs(y - pt.y) <= 5) return d;
    } else {
      const r = boxRect(d);
      if (r && pt.x >= r.x - 4 && pt.x <= r.x + r.w + 4 && pt.y >= r.y - 4 && pt.y <= r.y + r.h + 4) return d;
    }
  }
  return null;
}
function select(d) {
  draw.selected = d;
  renderDrawings();
}
function removeSelected() {
  draw.items = draw.items.filter((d) => d !== draw.selected);
  keep('mnqDraw', draw.items);
  select(null);
}

// 일목 구름: 선행스팬 A·B는 26봉 앞(미래)까지 나가므로 차트 선 대신 그리기 층에. A가 위면 초록, 아래면 빨강 구름
function renderCloud(width) {
  const k = draw.ichi;
  if (!k) return '';
  const ts = chart.timeScale(), range = ts.getVisibleLogicalRange();
  if (!range) return '';
  const pts = [];
  for (let i = Math.max(0, Math.floor(range.from) - 1); i <= Math.min(k.a.length - 1, Math.ceil(range.to) + 1); i++) {
    if (k.a[i] == null || k.b[i] == null) continue;
    const x = ts.logicalToCoordinate(i), ya = series.priceToCoordinate(k.a[i]), yb = series.priceToCoordinate(k.b[i]);
    if (x != null && ya != null && yb != null) pts.push({ x, ya, yb, up: k.a[i] >= k.b[i] });
  }
  let out = '';
  for (let i = 1; i < pts.length; i++) {
    const p = pts[i - 1], q = pts[i];
    out += `<polygon points="${p.x},${p.ya} ${q.x},${q.ya} ${q.x},${q.yb} ${p.x},${p.yb}" fill="${q.up ? UP : DOWN}" fill-opacity="0.12"/>`;
  }
  const line = (key, color) => `<polyline points="${pts.map((p) => `${p.x},${p[key]}`).join(' ')}" fill="none" stroke="${color}" stroke-width="1" stroke-opacity="0.8"/>`;
  return pts.length > 1 ? `<svg x="0" y="0" width="${width}" overflow="hidden">${out}${line('ya', UP)}${line('yb', DOWN)}</svg>` : '';
}

function renderTools() {
  $('tools').querySelectorAll('button').forEach((b) => {
    if (b.dataset.tool === 'clear') return;
    const on = b.dataset.tool ? draw.tool === b.dataset.tool : Boolean(draw.studies[b.dataset.study]);
    b.classList.toggle('active', on);
    b.setAttribute('aria-pressed', on);
  });
}
function setTool(tool) {
  draw.tool = draw.tool === tool ? null : tool;
  draw.start = draw.hover = null;
  $('chart-wrap').classList.toggle('drawing', Boolean(draw.tool));
  renderTools();
  renderDrawings();
}
$('tools').addEventListener('click', (e) => {
  const b = e.target.closest('button');
  if (!b) return;
  if (b.dataset.study) {
    draw.studies[b.dataset.study] = !draw.studies[b.dataset.study];
    keep('mnqStudies', draw.studies);
    renderTools();
    renderStudies();
    renderDrawings();
  } else if (b.dataset.tool === 'clear') {
    // 고른 것이 있으면 그것만, 없으면 한 번 묻고 모두
    if (draw.selected) removeSelected();
    else if (draw.items.length && confirm('고른 것이 없습니다. 그린 수평선과 박스를 모두 지울까요?')) {
      draw.items = [];
      keep('mnqDraw', draw.items);
      setTool(null);
    }
  } else setTool(b.dataset.tool);
});
const addItem = (item) => { draw.items.push(item); keep('mnqDraw', draw.items); renderDrawings(); };
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape' && draw.tool) setTool(null);
  // Delete(또는 Backspace): 고른 수평선·박스를 지운다. 글을 고르는 칸에 있을 때는 그대로 둔다
  if ((e.key === 'Delete' || e.key === 'Backspace') && draw.selected && !/^(INPUT|SELECT|TEXTAREA)$/.test(document.activeElement.tagName)) {
    e.preventDefault();
    removeSelected();
  }
  // Alt + H: 마우스가 있는 가격에 수평선
  if (e.altKey && e.code === 'KeyH' && draw.mouse) {
    e.preventDefault();
    addItem({ type: 'hline', price: draw.mouse.price });
  }
});
// Shift + 드래그: 드래그한 자리에 박스. 그동안은 차트가 끌려 움직이지 않게 이벤트를 차트에 넘기지 않는다
(function () {
  const wrap = $('chart-wrap');
  let dragging = false;
  const at = (e) => {
    const r = $('chart').getBoundingClientRect();
    return pointAt({ point: { x: e.clientX - r.left, y: e.clientY - r.top } });
  };
  wrap.addEventListener('pointerdown', (e) => {
    if (!e.shiftKey || e.button !== 0) return;
    const start = at(e);
    if (!start) return;
    e.stopPropagation();
    e.preventDefault();
    dragging = true;
    draw.start = start;
    draw.hover = start;
    try { wrap.setPointerCapture(e.pointerId); } catch (err) { /* 잡지 못해도 차트 안에서는 그대로 그려진다 */ }
    const move = (ev) => { ev.stopPropagation(); draw.hover = at(ev) || draw.hover; renderDrawings(); };
    const up = (ev) => {
      ev.stopPropagation();
      wrap.removeEventListener('pointermove', move, true);
      const end = at(ev) || draw.hover;
      dragging = false;
      draw.start = draw.hover = null;
      if (end && (end.time !== start.time || end.price !== start.price)) addItem({ type: 'box', a: start, b: end });
      else renderDrawings();
    };
    wrap.addEventListener('pointermove', move, true);
    wrap.addEventListener('pointerup', up, { capture: true, once: true });
  }, true);
  // 차트 라이브러리는 마우스 이벤트로도 끌기를 하므로 Shift를 누른 동안은 그것도 막는다
  ['mousedown', 'mousemove', 'mouseup'].forEach((t) => wrap.addEventListener(t, (e) => { if (e.shiftKey || dragging) e.stopPropagation(); }, true));
})();
chart.subscribeClick((p) => {
  if (!draw.tool) {
    select(hitTest(p.point));  // 도구가 꺼져 있으면 누른 것을 고른다(빈 곳이면 고르기 풀기)
    return;
  }
  const at = pointAt(p);
  if (!at) return;
  if (draw.tool === 'hline') {
    draw.items.push({ type: 'hline', price: at.price });
  } else if (!draw.start) {
    draw.start = at;
    return;
  } else {
    draw.items.push({ type: 'box', a: draw.start, b: at });
  }
  keep('mnqDraw', draw.items);
  setTool(null);
});
chart.subscribeCrosshairMove((p) => {
  draw.mouse = pointAt(p);  // Alt + H가 쓸 마우스 자리
  if (!draw.start) return;
  draw.hover = draw.mouse;
  renderDrawings();
});
chart.timeScale().subscribeVisibleLogicalRangeChange(() => renderDrawings());
new ResizeObserver(() => renderDrawings()).observe($('chart-wrap'));

// 보조 지표: 같은 차트 아래쪽에 따로 띄운 가격 축(칸)에. 지금 보는 봉 단위의 종가로 계산한다
const ema = (xs, n) => { const k = 2 / (n + 1); let e = xs[0]; return xs.map((x) => (e = x * k + e * (1 - k))); };
const studyLine = (scale, color, title) => chart.addLineSeries({ priceScaleId: scale, color, title, lineWidth: 1, priceLineVisible: false, lastValueVisible: true, crosshairMarkerVisible: false });
const rsiSeries = studyLine('rsi', '#a78bfa', 'RSI 14');
const macdHist = chart.addHistogramSeries({ priceScaleId: 'macd', priceLineVisible: false, lastValueVisible: false });
const macdSeries = studyLine('macd', '#60a5fa', 'MACD');
const signalSeries = studyLine('macd', '#f59e0b', 'Signal');
// 일목균형표: 전환선·기준선·후행스팬은 본 차트의 선으로, 선행스팬 A·B와 구름은 그리기 층에(renderCloud)
const ichiLine = (color, title) => chart.addLineSeries({ color, title, lineWidth: 1, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false });
const tenkanSeries = ichiLine('#38bdf8', '전환선');
const kijunSeries = ichiLine('#f472b6', '기준선');
const chikouSeries = ichiLine('#a3a3a3', '후행스팬');
[70, 30].forEach((v) => rsiSeries.createPriceLine({ price: v, color: token('--text-3'), lineWidth: 1, lineStyle: LightweightCharts.LineStyle.Dotted, axisLabelVisible: false, title: String(v) }));

function renderStudies() {
  const { macd, rsi } = draw.studies;
  const n = (macd ? 1 : 0) + (rsi ? 1 : 0);
  // 칸 나누기: 지표 하나에 아래 20%씩, 본 차트는 그 위
  series.priceScale().applyOptions({ scaleMargins: { top: 0.08, bottom: 0.05 + 0.2 * n } });
  if (rsi) chart.priceScale('rsi').applyOptions({ scaleMargins: { top: macd ? 0.6 : 0.8, bottom: macd ? 0.22 : 0.02 } });
  if (macd) chart.priceScale('macd').applyOptions({ scaleMargins: { top: 0.8, bottom: 0.02 } });
  const bars = state.bars, closes = bars.map((b) => b.close), at = (i) => bars[i].time + TZ;
  if (rsi && bars.length > 15) {
    // RSI 14(와일더 평균)
    let up = 0, down = 0;
    const out = [];
    for (let i = 1; i < bars.length; i++) {
      const ch = closes[i] - closes[i - 1];
      up = (up * 13 + Math.max(ch, 0)) / 14;
      down = (down * 13 + Math.max(-ch, 0)) / 14;
      if (i >= 14) out.push({ time: at(i), value: down ? 100 - 100 / (1 + up / down) : 100 });
    }
    rsiSeries.setData(out);
  } else rsiSeries.setData([]);
  if (macd && bars.length > 35) {
    const fast = ema(closes, 12), slow = ema(closes, 26);
    const line = closes.map((_, i) => fast[i] - slow[i]);
    const sig = ema(line, 9);
    const from = 26;  // 앞쪽은 평균이 덜 쌓여 그리지 않는다
    macdSeries.setData(line.slice(from).map((v, i) => ({ time: at(i + from), value: v })));
    signalSeries.setData(sig.slice(from).map((v, i) => ({ time: at(i + from), value: v })));
    macdHist.setData(line.slice(from).map((v, i) => ({ time: at(i + from), value: v - sig[i + from], color: (v - sig[i + from] >= 0 ? UP : DOWN) + '99' })));
  } else [macdSeries, signalSeries, macdHist].forEach((x) => x.setData([]));
  if (draw.studies.ichimoku && bars.length > 52) {
    // 전환선 9, 기준선 26: 그 기간 고가·저가의 가운데. 선행스팬 A = (전환 + 기준) / 2, B = 52기간 가운데, 둘 다 26봉 앞에. 후행스팬 = 종가를 26봉 뒤에
    const mid = (i, n) => {
      if (i < n - 1) return null;
      let hi = -Infinity, lo = Infinity;
      for (let j = i - n + 1; j <= i; j++) { hi = Math.max(hi, bars[j].high); lo = Math.min(lo, bars[j].low); }
      return (hi + lo) / 2;
    };
    const tenkan = bars.map((_, i) => mid(i, 9)), kijun = bars.map((_, i) => mid(i, 26));
    const a = new Array(bars.length + 26).fill(null), b = new Array(bars.length + 26).fill(null);
    bars.forEach((_, i) => {
      if (tenkan[i] != null && kijun[i] != null) a[i + 26] = (tenkan[i] + kijun[i]) / 2;
      b[i + 26] = mid(i, 52);
    });
    draw.ichi = { a, b };
    const line = (xs) => xs.map((v, i) => (v == null ? null : { time: at(i), value: v })).filter(Boolean);
    tenkanSeries.setData(line(tenkan));
    kijunSeries.setData(line(kijun));
    chikouSeries.setData(closes.slice(26).map((v, i) => ({ time: at(i), value: v })));
  } else {
    draw.ichi = null;
    [tenkanSeries, kijunSeries, chikouSeries].forEach((x) => x.setData([]));
  }
}

// 계약 수 변화 하나를 화살표 하나로: 새로 들면 "롱 진입 4", 다 정리하면 "롱 청산 +$120", 그 밖에는 늘리거나 줄인 계약 수
function marker(time, from, to, long, pnl) {
  const buy = to > from;
  const side = (n) => (n > 0 ? '롱' : '숏');
  const text = (from === 0 ? `${side(to)} 진입 ${Math.abs(to)}` : to === 0 ? `${side(from)} 청산`
    : (from > 0) !== (to > 0) ? `${side(to)} 전환 ${Math.abs(to)}` : `${buy ? '+' : '−'}${Math.abs(to - from)}${long ? ` → ${Math.abs(to)}` : ''}`)
    + (pnl == null ? '' : ` ${pnl < 0 ? '−' : '+'}$${Math.abs(Math.round(pnl)).toLocaleString('en-US')}`);
  return { time, position: buy ? 'belowBar' : 'aboveBar', shape: buy ? 'arrowUp' : 'arrowDown', color: buy ? UP : DOWN, text };
}

function renderMarkers() {
  const c = current();
  if (!c || !state.bars.length) return;
  const first = state.bars[0].time;
  let markers;
  if (state.interval === 'D') {
    // 일봉: 규칙을 2021년부터 돌렸을 때의 자리(과거 시험) + 가상 계좌의 실제 매매(그날 종가로 정한 날짜에)
    const days = new Map(((state.signals || {})[c.key] || []).filter((s) => s.time >= first).map((s) => [s.time, s]));
    c.trades.filter((t) => t.to !== t.from).forEach((t) => {
      const day = Date.parse(t.date) / 1000;  // 그날 0시(UTC): 일봉의 시각과 같다
      days.set(day, { time: day, from: t.from, to: t.to });
    });
    markers = [...days.values()].map((s) => marker(s.time + TZ, s.from, s.to, false, s.pnl));
  } else {
    // 분봉: 가상 계좌를 시작하기 전은 과거 시험(1시간봉으로 돌린 것), 그 뒤는 가상 계좌의 실제 매매
    const step = state.interval * 60;
    const until = c.started || Infinity;
    const past = ((state.replay || {})[c.key] || []).filter((t) => t.time < until);
    markers = past.concat(c.trades).filter((t) => t.to !== t.from && t.time >= first)
      .map((t) => marker(Math.floor(t.time / step) * step + TZ, t.from, t.to, true, t.pnl));
  }
  series.setMarkers(markers.sort((a, b) => a.time - b.time));
  if (entryLine) series.removePriceLine(entryLine);
  entryLine = c.contracts && c.entry ? series.createPriceLine({
    price: c.entry, color: c.contracts > 0 ? UP : DOWN, lineWidth: 1, lineStyle: LightweightCharts.LineStyle.Dashed,
    axisLabelVisible: true, title: `가상 진입 ${position(c.contracts).replace(/<[^>]+>/g, '')}`,
  }) : null;
}

// ---------- 오른쪽 칸 ----------
function rows(list) {
  return `<dl class="info">${list.map(([k, v]) => `<div><dt>${k}</dt><dd>${v}</dd></div>`).join('')}</dl>`;
}

// 매매법 칸: 차트에 표시할 후보 탭(★ = 대회 매매법), 그 후보의 가상 포지션과 근거, 다음 판단 시각
function renderSide() {
  const d = state.trading;
  if (!d) return;
  const c = current();
  state.candidate = c.key;
  $('picks').innerHTML = d.candidates.map((x) => {
    const on = x.key === c.key;
    return `<button data-key="${x.key}" class="${on ? 'active' : ''}" aria-pressed="${on}">${x.name.split(' · ')[0]}${x.key === d.live ? ' ★' : ''}</button>`;
  }).join('');
  renderWhy(c, d);
  renderLevels();
  renderMarkers();
}

// 지금 포지션(가상 계좌)과 근거: 갈래마다 조건 체크리스트(✓ 충족, ✗ 불충족, • 참고)와 그 갈래의 결론, 합친 결과, 다음 판단 시각
const sideText = (n) => (n > 0 ? `<span class="c-up">롱 ${n}</span>` : n < 0 ? `<span class="c-down">숏 ${-n}</span>` : '없음');
const whyItem = ([k, v, ok]) => `<li class="${ok === true ? 'ok' : ok === false ? 'no' : ''}"><i aria-hidden="true">${ok === true ? '✓' : ok === false ? '✗' : '•'}</i>`
  + `<b>${k}</b><span>${v}</span></li>`;
const whyBlock = (title, result, items) => `<div class="why-block"><div class="why-title"><span>${title}</span>${result == null ? '' : `<span>${result}</span>`}</div>`
  + `<ul class="why-list">${items.map(whyItem).join('')}</ul></div>`;

function renderWhy(c, d) {
  const price = state.quote ? state.quote.price : null;
  const pnl = c.contracts && c.entry && price ? c.contracts * d.multiplier * (price - c.entry) : 0;
  const r = c.equity / d.capital - 1;
  $('why-note').textContent = c.key === d.live ? '★ 대회 매매법' : '가상 계좌로만 지켜봄';
  let body;
  if (c.noise) {
    const parts = c.parts || {};
    const last = c.trades.find((t) => t.sleeve === 'intraday');
    const n = c.noise;
    const hour = Math.min(15, Math.max(10, nyHour() + 1));
    const band = n.bands[hour];
    body = whyBlock('일봉 갈래 · 되돌림 + 추세', sideText(parts.daily || 0), c.explain)
      + whyBlock('장중 갈래 · 노이즈 돌파', sideText(parts.intraday || 0), [
        ['마지막 판단', last ? `${fmtTime(last.time)} · ${last.why}` : '아직 없음', null],
        [`${hour}시 경계`, band ? `<span class="c-up">${fmtPrice(band[0])}</span> 위 → 롱 · <span class="c-down">${fmtPrice(band[1])}</span> 아래 → 숏`
          + (n.today ? '' : ` <small>(${n.date.slice(5)} 기준)</small>`) : '-', null],
        ['기준', `그날 뉴욕 9시 시가 ${fmtPrice(n.open)} · 16시 정리`, null],
      ])
      + `<p class="why-sum">일봉 ${sideText(parts.daily || 0)} + 장중 ${sideText(parts.intraday || 0)} → <b>${position(c.contracts)}</b> <small>(최대 1계약)</small></p>`;
  } else {
    // 마지막 판단(가상 계좌의 마지막 매매 까닭)이 있으면 조건 위에
    const last = c.trades.find((t) => t.why);
    body = whyBlock('조건', null, (last && (c.scalp || c.hourly) ? [['마지막 판단', `${fmtTime(last.time)} · ${last.why}`, null]] : []).concat(c.explain));
  }
  const next = c.scalp ? '평일 뉴욕 9:30 ~ 16시, 1분봉 마감마다' : c.hourly ? '매시 정각(1시간봉 마감마다, CME 쉬는 때 제외)' : `${c.next_intraday ? `장중 ${fmtTime(c.next_intraday)} · ` : ''}일봉 ${fmtTime(d.next_decision)}`;
  $('why').innerHTML = `<div class="why-head"><span class="pos-big">${position(c.contracts)}</span><span class="why-acct">`
    + (c.contracts && c.entry ? `진입 ${fmtPrice(c.entry)} · <b class="${tone(pnl)}">${fmtMoney(pnl)}</b><br>` : '')
    + `가상 계좌 <b class="${tone(r)}">${fmtSigned(r)}</b>`
    + Object.entries(c.exit || {}).map(([k, t]) => `<br>${c.noise ? (k === 'daily' ? '일봉 ' : '장중 ') : ''}정리까지 <b data-until="${t}">${fmtLeft(t)}</b>`
      + ` <small title="${k === 'daily' ? '새 5일 최저 종가가 나오면 5거래일 더 늘어난다. 휴장일은 따지지 않는다' : '장중 갈래는 뉴욕 16시에 정리'}">(${fmtTime(t)})</small>`).join('')
    + `</span></div>`
    + body + `<p class="why-next">다음 판단 <b>${next}</b></p>` + (d.error ? `<div class="note warn">${d.error}</div>` : '')
    + '<p class="why-foot">일봉 조건의 값은 나스닥100 지수 기준이고, 차트의 선들은 MNQ 선물로 계산해 수준이 조금 다릅니다.</p>';
}

function renderQuote() {
  const q = state.quote;
  if (!q) return;
  $('symbol').textContent = q.symbol;
  $('symbol-name').textContent = q.name;
  $('price').textContent = fmtPrice(q.price);
  const change = q.diff / q.prev;
  $('change').className = 'change ' + tone(q.diff);
  $('change').textContent = `${q.diff >= 0 ? '+' : ''}${fmtPrice(q.diff)} (${fmtSigned(change)})`;
  document.title = `${fmtPrice(q.price)} ${q.symbol} | MNQ Trader`;
  renderSide();
  renderAccount();
}

// ---------- 갱신 ----------
async function tickBars() {
  try {
    await loadBars(false);
    $('conn').textContent = `실시간 · ${new Date().toLocaleTimeString('ko-KR', { hour: '2-digit', minute: '2-digit', second: '2-digit' })}`;
    $('conn').className = 'conn live';
  } catch (e) {
    $('conn').textContent = '연결 끊김: ' + e.message;
    $('conn').className = 'conn down';
  }
}
// LS 모의계좌: 따라가는 후보, 목표와 실제 계약 수, 지금 주문하지 않는 까닭, 최근 주문
function renderLive(d) {
  const pick = $('live-pick');
  if (document.activeElement !== pick) {  // 고르는 중에는 다시 그리지 않는다
    pick.innerHTML = Object.entries(d.choices).map(([k, n]) => `<option value="${k}"${k === d.candidate ? ' selected' : ''}>${n.split(' · ')[0]}</option>`).join('');
  }
  $('live-note').textContent = d.started ? '' : `${fmtTime(d.start)} 대회 시작부터`;
  const orders = d.orders.slice(0, 3).map((o) => `<tr><td>${fmtTime(o.time)}</td><td>${o.ok ? `<b class="${tone(o.qty)}">${o.manual ? '직접 ' : o.filler ? '횟수 채우기 ' : ''}${o.qty > 0 ? '매수' : '매도'} ${Math.abs(o.qty)}</b> (${o.from} → ${o.to})` : `<span class="c-down">실패</span> ${o.error || ''}`}</td></tr>`).join('');
  const t = $('auto-toggle');
  t.textContent = d.enabled ? '자동 주문 켜짐' : '자동 주문 꺼짐';
  t.classList.toggle('active', d.enabled);
  t.setAttribute('aria-pressed', d.enabled);
  t.dataset.on = d.enabled;
  $('trade-buttons').querySelectorAll('button').forEach((b) => {
    b.disabled = !d.started;
    b.title = d.started ? '' : '대회 시작부터 누를 수 있습니다';
  });
  $('live').innerHTML = rows([
    ['목표 / 실제', d.target == null ? '-' : `${position(d.target)} / ${position(d.actual)}`],
    ['이번 주 체결', d.week == null ? '-' : `<b class="${d.week >= d.min_weekly ? 'c-up' : ''}">${d.week}</b> / ${d.min_weekly}건 <small>(모자라면 금요일 뉴욕 14시부터 채움)</small>`],
    ['상태', d.error ? `<span class="c-down">${d.error}</span>` : d.note || '-'],
  ]) + (orders ? `<table class="trend-table"><tbody>${orders}</tbody></table>` : '<p class="empty">아직 주문이 없습니다.</p>');
}

// 직접 주문: 시장가로 바로 나가고 자동 주문은 꺼진다(켜 두면 30초 뒤 목표 계약 수로 되돌리기 때문)
$('trade-buttons').addEventListener('click', async (e) => {
  const b = e.target.closest('button');
  if (!b) return;
  const flat = b.hasAttribute('data-flatten'), qty = Number(b.dataset.qty);
  const est = flat ? (account ? account.view : []).map(closeEstimate) : [];
  if (!confirm(flat ? `${est.length ? est.map((x) => x.text).join('\n\n') + '\n\n' : '들고 있는 포지션이 없습니다.\n'}모든 포지션을 시장가로 정리하고 자동 주문을 멈출까요?`
    : `MNQ ${Math.abs(qty)}계약을 시장가로 ${qty > 0 ? '매수' : '매도'}할까요?\n자동 주문은 꺼집니다(아이콘 메뉴에서 다시 켜면 목표 계약 수로 되돌립니다).`)) return;
  b.disabled = true;
  try {
    await (flat ? post('/api/live/flatten', {}) : post('/api/live/order', { qty }));
    if (flat && est.length) closed = est.map((x) => x.html).join('<br>');
  } catch (err) {
    alert('주문하지 못했습니다: ' + err.message);
  }
  api('/api/live').then(renderLive).catch(() => {});
  tickAccount();
});

// 대회 매매법 바꾸기: 자동 주문이 켜져 있으면 30초 안에 새 매매법의 목표 계약 수로 맞추는 주문이 나가므로 한 번 묻는다
$('live-pick').addEventListener('change', async (e) => {
  const key = e.target.value;
  if (!confirm(`대회 매매법을 "${e.target.selectedOptions[0].text}"(으)로 바꿀까요?
자동 주문이 켜져 있으면 30초 안에 그 매매법의 목표 계약 수로 맞추는 주문이 나갑니다.`)) {
    api('/api/live').then(renderLive).catch(() => {});
    return;
  }
  try {
    renderLive(await post('/api/live/candidate', { key }));
    state.candidate = key;
    tickTrading();
  } catch (err) {
    alert('바꾸지 못했습니다: ' + err.message);
  }
});

// 자동 주문 켜고 끄기. 켜면 30초 안에 실제 포지션을 목표 계약 수에 맞추는 주문이 나갈 수 있어 한 번 묻는다(끄기는 포지션을 건드리지 않는다)
$('auto-toggle').addEventListener('click', async () => {
  const on = $('auto-toggle').dataset.on !== 'true';
  if (on && !confirm('자동 주문을 켤까요?\n30초 안에 실제 포지션을 목표 계약 수에 맞추는 시장가 주문이 나갈 수 있습니다.')) return;
  try {
    renderLive(await post('/api/live/enabled', { on }));
  } catch (err) {
    alert('바꾸지 못했습니다: ' + err.message);
  }
});

// 차트 아래 칸: LS 모의계좌의 실제 자산과 포지션. MNQ 손익은 실시간 시세로 다시 계산해 0.5초마다 움직이게 한다
let account = null;
function renderAccount() {
  const a = account;
  if (!a) return;
  const q = state.quote;
  const mult = state.trading ? state.trading.multiplier : 2;
  const pos = a.positions.map((p) => {
    const price = q && q.symbol === p.symbol ? q.price : p.price;
    const pnl = q && q.symbol === p.symbol ? p.qty * mult * (price - p.entry) : p.pnl;
    return { ...p, price, pnl, roe: p.margin ? pnl / p.margin : null, of_capital: pnl / a.capital };
  });
  a.view = pos;  // 정리 버튼이 실시간 가격으로 다시 계산한 손익을 쓰게
  renderRealEntry(pos.find((p) => q && p.symbol === q.symbol));
  const pnl = pos.reduce((s, p) => s + p.pnl, 0);
  const equity = a.deposit + pnl;
  $('account-note').textContent = `${fmtTime(a.at)} 조회`;
  $('account-sum').innerHTML = [
    ['평가 자산', `<b>${fmtMoney(equity)}</b>`],
    ['대회 수익률', `<b class="${tone(equity - a.capital)}">${fmtSigned(equity / a.capital - 1)}</b>`],
    ['미실현 손익', `<b class="${tone(pnl)}">${fmtMoney(pnl)}</b>`],
    ['사용 증거금', fmtMoney(a.margin, 0)],
  ].map(([k, v]) => `<div><dt>${k}</dt><dd>${v}</dd></div>`).join('');
  $('account-pos').innerHTML = pos.length ? `<table class="trend-table account-table"><thead><tr><th>종목</th><th>포지션</th><th>진입가</th><th>현재가</th>
    <th title="평가 자산이 증거금까지 줄어드는 가격(추정). LS의 실제 반대매매 기준과 다를 수 있습니다">청산가(추정)</th><th>미실현 손익 (증거금 대비)</th><th>계좌 대비</th><th>증거금</th></tr></thead><tbody>${pos.map((p) =>
    `<tr><td><b>${p.symbol}</b></td><td>${position(p.qty)}</td><td>${fmtPrice(p.entry)}</td><td>${fmtPrice(p.price)}</td><td>${p.liq ? fmtPrice(p.liq) : '없음 <small>(계좌 충분)</small>'}</td>
    <td><b class="${tone(p.pnl)}">${fmtMoney(p.pnl)}</b> <span class="${tone(p.pnl)}">(${fmtSigned(p.roe)})</span></td><td class="${tone(p.pnl)}">${fmtSigned(p.of_capital, 3)}</td><td>${fmtMoney(p.margin, 0)}</td></tr>`).join('')}</tbody></table>`
    : '<p class="empty">들고 있는 포지션이 없습니다.</p>';
}

// LS 모의계좌의 실제 진입가: 차트에 실선(가상 계좌의 점선과 구분), 주문 칸에 진입가와 수익률(가격 움직임 기준, 괄호 안은 손익과 계좌 대비)
let realLine = null, realKey = '';
function renderRealEntry(p) {
  const key = p ? `${p.qty}|${p.entry}` : '';
  if (key !== realKey) {  // 0.5초마다 불리므로 진입가나 계약 수가 바뀔 때만 다시 그린다
    realKey = key;
    if (realLine) series.removePriceLine(realLine);
    realLine = p ? series.createPriceLine({
      price: p.entry, color: p.qty > 0 ? UP : DOWN, lineWidth: 2, lineStyle: LightweightCharts.LineStyle.Solid,
      axisLabelVisible: true, title: `실제 진입 ${position(p.qty).replace(/<[^>]+>/g, '')}`,
    }) : null;
  }
  const move = p ? Math.sign(p.qty) * (p.price / p.entry - 1) : 0;
  $('live-pos').innerHTML = p ? rows([
    ['실제 진입', `${position(p.qty)} @ <b>${fmtPrice(p.entry)}</b>`],
    ['수익률', `<b class="${tone(move)}">${fmtSigned(move)}</b> <small>(${fmtMoney(p.pnl)} · 계좌 ${fmtSigned(p.of_capital, 3)})</small>`],
  ]) : rows([['실제 진입', '포지션 없음']].concat(closed ? [['정리 결과', closed]] : []));
}

// 지금 정리하면 수수료 빼고 얼마인가. 대회는 체결 1계약당 매수·매도 각 $1을 수익률에서 빼고(진입 + 청산 = $2),
// 증권사 수수료는 LS가 포지션에 적어 준 값(CsgnCmsn)만 넣는다. 시장가라 실제 체결가는 조금 다를 수 있다
const CONTEST_FEE = 1;
let closed = '';
function closeEstimate(p) {
  const fee = 2 * CONTEST_FEE * Math.abs(p.qty) + (p.fee || 0), net = p.pnl - fee, cap = account.capital;
  const sign = (v) => (v >= 0 ? '+' : '') + fmtMoney(v);
  return {
    text: `${p.symbol} ${position(p.qty).replace(/<[^>]+>/g, '')}: 진입 ${fmtPrice(p.entry)} → 지금 ${fmtPrice(p.price)}\n`
      + `가격 손익 ${sign(p.pnl)}\n수수료 -${fmtMoney(fee)} (대회 차감 $${2 * CONTEST_FEE}×${Math.abs(p.qty)}계약${p.fee ? ` + 증권사 ${fmtMoney(p.fee)}` : ''})\n`
      + `수수료 뺀 손익 ${sign(net)} · 계좌 대비 ${fmtSigned(net / cap, 3)} · 증거금 대비 ${fmtSigned(p.margin ? net / p.margin : null)}`,
    html: `<b class="${tone(net)}">${sign(net)}</b> <small>(수수료 ${fmtMoney(fee)} 뺌 · 계좌 ${fmtSigned(net / cap, 3)}, 누른 때 가격 기준 예상)</small>`,
  };
}

async function tickAccount() {
  try {
    account = await api('/api/account');
    renderAccount();
  } catch (e) {
    $('account-note').textContent = '조회하지 못했습니다: ' + e.message;
  }
}

async function tickTrading() {
  try {
    if (!state.signals) state.signals = await api('/api/signals');
    if (!state.replay) state.replay = await api('/api/replay');
    if (!state.dailyCloses || Date.now() - state.dailyAt > 600000) {  // 일봉 종가는 10분마다
      state.dailyAt = Date.now();
      api('/api/chart?interval=D').then((d) => { state.dailyCloses = d.bars.map((b) => b.close); renderLevels(); }).catch(() => {});
    }
    api('/api/live').then(renderLive).catch(() => {});
    state.trading = await api('/api/trading');
    renderSide();
  } catch (e) { /* 다음에 다시 */ }
}

function chooseInterval(key) {
  state.interval = key;
  state.generation += 1;
  state.last = null;
  state.noOlder = key === 'D';
  localStorage.setItem('mnqInterval', key);
  $('intervals').innerHTML = Object.entries(INTERVALS).map(([k, name]) =>
    `<button data-key="${k}" class="${k === key ? 'active' : ''}" aria-pressed="${k === key}">${name}</button>`).join('');
  chart.applyOptions({ timeScale: { timeVisible: key !== 'D' } });
  $('chart-msg').hidden = false;
  loadBars(true).catch((e) => { $('chart-msg').textContent = '차트를 불러오지 못했습니다: ' + e.message; });
}

$('intervals').addEventListener('click', (ev) => {
  const key = ev.target.dataset.key;
  if (key && key !== state.interval) chooseInterval(key);
});
$('picks').addEventListener('click', (ev) => {
  const item = ev.target.closest('[data-key]');
  if (!item) return;
  state.candidate = item.dataset.key;
  localStorage.setItem('mnqCandidate', state.candidate);
  renderSide();
});

// 차트와 오른쪽 칸 사이 손잡이: 끌거나 좌우 화살표로 오른쪽 칸 너비를 바꾼다(더블클릭하면 원래대로)
(function () {
  const side = $('side'), grip = $('resizer');
  const set = (w) => { w = Math.max(320, Math.min(window.innerWidth - 400, w)); side.style.width = w + 'px'; localStorage.setItem('mnqSide', w); };
  const saved = Number(localStorage.getItem('mnqSide'));
  if (saved) set(saved);
  grip.addEventListener('pointerdown', (e) => {
    const move = (ev) => set(window.innerWidth - ev.clientX);
    document.body.classList.add('resizing');
    grip.setPointerCapture(e.pointerId);
    grip.addEventListener('pointermove', move);
    grip.addEventListener('pointerup', () => { grip.removeEventListener('pointermove', move); document.body.classList.remove('resizing'); }, { once: true });
  });
  grip.addEventListener('keydown', (e) => {
    if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') { set(side.offsetWidth + (e.key === 'ArrowLeft' ? 40 : -40)); e.preventDefault(); }
  });
  grip.addEventListener('dblclick', () => { side.style.width = ''; localStorage.removeItem('mnqSide'); });
})();

document.addEventListener('themechange', () => renderMarkers());
renderTools();
chooseInterval(state.interval);
tickTrading();
tickAccount();
// 정리까지 남은 시간: 시세가 멈춰도(주말) 1초마다 흐르게 칸을 다시 그리지 않고 글자만 바꾼다
setInterval(() => document.querySelectorAll('[data-until]').forEach((el) => { el.textContent = fmtLeft(Number(el.dataset.until)); }), 1000);
setInterval(tickBars, 500);
setInterval(tickTrading, 15000);
setInterval(tickAccount, 5000);
