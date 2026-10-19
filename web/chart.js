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

// 시각 → 지금 봉 단위에서 그 시각이 들어가는 봉의 x 좌표(봉 사이 시각도 그 봉으로). 마지막 봉 뒤(미래)는 봉 간격으로 늘려 잡는다
const barStep = () => (state.interval === 'D' ? 86400 : state.interval * 60);
function timeX(t) {
  const bars = state.bars;
  if (!bars.length) return null;
  const last = bars.length - 1;
  if (t > bars[last].time) return chart.timeScale().logicalToCoordinate(last + Math.floor((t - bars[last].time) / barStep()));
  let lo = 0, hi = last;
  while (lo < hi) { const mid = (lo + hi + 1) >> 1; if (bars[mid].time <= t) lo = mid; else hi = mid - 1; }
  return chart.timeScale().logicalToCoordinate(lo);
}
function pointAt(p) {
  if (!p || !p.point || !state.bars.length) return null;
  const last = state.bars.length - 1, i = Math.max(0, Math.round(chart.timeScale().coordinateToLogical(p.point.x)));
  const time = i > last ? state.bars[last].time + (i - last) * barStep() : state.bars[i].time;  // 실시간 봉 오른쪽 빈 곳도 고를 수 있게
  const price = series.coordinateToPrice(p.point.y);
  return price == null ? null : { time, price: Math.round(price * 4) / 4 };  // 0.25틱에 맞춘다
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
  $('draw').innerHTML = renderCloud(chart.timeScale().width()) + renderZones() + boxes.map((d) => {
    const r = boxRect(d);
    if (!r) return '';
    const on = d === draw.selected;
    return `<rect x="${r.x}" y="${r.y}" width="${r.w}" height="${r.h}" fill="${color}" fill-opacity="${d.preview ? 0.03 : on ? 0.12 : 0.06}" stroke="${color}" stroke-width="${on ? 2 : 1}"${d.preview || on ? ' stroke-dasharray="4 3"' : ''}/>`
      + (on ? boxCorners(d).map((c) => `<rect x="${c.x - 4}" y="${c.y - 4}" width="8" height="8" fill="${token('--panel')}" stroke="${color}" stroke-width="2"/>`).join('') : '');
  }).join('') + renderMeasure() + renderOrderTags();
}
// Shift + 드래그 재기: 끈 높이(가격 차)가 지금 가격의 몇 %인가. 위로 끌면 롱, 아래로 끌면 숏으로 그만큼 움직였을 때
function renderMeasure() {
  const m = draw.measure, now = state.quote ? state.quote.price : null;
  if (!m || !now) return '';
  const x1 = timeX(m.a.time), x2 = timeX(m.b.time), y1 = series.priceToCoordinate(m.a.price), y2 = series.priceToCoordinate(m.b.price);
  if ([x1, x2, y1, y2].some((v) => v == null)) return '';
  const diff = m.b.price - m.a.price, up = diff >= 0, color = up ? UP : DOWN;
  const usd = Math.abs(diff) * (state.trading ? state.trading.multiplier : 2);
  const label = `${up ? '▲ 롱' : '▼ 숏'} ${fmtSigned(Math.abs(diff) / now, 2).replace('+', up ? '+' : '-')} · ${up ? '+' : '-'}${Math.abs(diff).toFixed(2)}pt · 1계약 $${usd.toLocaleString('en-US')}`;
  const x = Math.min(x1, x2), w = Math.max(2, Math.abs(x2 - x1)), ty = up ? Math.min(y1, y2) - 8 : Math.max(y1, y2) + 18;
  return `<rect x="${x}" y="${Math.min(y1, y2)}" width="${w}" height="${Math.max(1, Math.abs(y2 - y1))}" fill="${color}" fill-opacity="0.15" stroke="${color}"/>`
    + `<line x1="${x2}" y1="${y1}" x2="${x2}" y2="${y2}" stroke="${color}" stroke-width="2"/>`
    + `<text x="${x + w / 2}" y="${ty}" text-anchor="middle" fill="${color}" font-size="13" font-weight="700" paint-order="stroke" stroke="${token('--bg')}" stroke-width="4">${label}</text>`;
}
// 박스의 네 모서리(고른 박스의 크기 조절 손잡이): 화면 좌표와, 그 모서리가 a·b 중 어느 시각·가격인지
function boxCorners(d) {
  const x = { a: timeX(d.a.time), b: timeX(d.b.time) }, y = { a: series.priceToCoordinate(d.a.price), b: series.priceToCoordinate(d.b.price) };
  return ['a', 'b'].flatMap((tk) => ['a', 'b'].map((pk) => ({ x: x[tk], y: y[pk], tk, pk }))).filter((c) => c.x != null && c.y != null);
}

// 차트에 그릴 과거 시험 매매. 스캘핑은 하루 50번쯤 드나들어 5분봉 이상에서는 봉을 덮으므로 마지막 세션(한국 08:00 ~)만 그린다
function scalpShown(c) {
  const all = (state.replay || {})[c.key] || [];
  if (!c.scalp || state.interval <= 1 || !all.length) return all;
  const KST = 9 * 3600, start = Math.floor((all[all.length - 1].time + KST) / 86400) * 86400 - KST + 8 * 3600;
  return all.filter((t) => t.time >= start);
}

// 스캘핑 추천 구간: 진입 봉 ~ 청산 봉, 진입가 ~ 청산가(아직 들고 있으면 지금 봉·지금 가격까지 점선). 롱 초록, 숏 빨강
function renderZones() {
  const zones = state.zones || [], width = chart.timeScale().width(), last = state.bars[state.bars.length - 1];
  if (!zones.length || !last) return '';
  const step = state.interval * 60;
  return zones.map((z) => {
    const open = z.t2 == null;
    // state.bars는 실제 유닉스 시각(차트에 넘길 때만 TZ를 더한다)이라 여기서는 TZ를 더하지 않는다
    const t2 = open ? last.time : Math.floor(z.t2 / step) * step, p2 = open ? (state.quote ? state.quote.price : last.close) : z.p2;
    let x1 = timeX(Math.floor(z.t1 / step) * step), x2 = timeX(t2);
    const y1 = series.priceToCoordinate(z.p1), y2 = series.priceToCoordinate(p2);
    if ([x1, x2, y1, y2].some((v) => v == null) || x2 < 0 || x1 > width) return '';
    const bar = chart.timeScale().options().barSpacing;
    x1 -= bar / 2; x2 += bar / 2;  // 봉의 가운데가 아니라 봉 전체를 덮게
    const color = z.side > 0 ? UP : DOWN, h = Math.max(3, Math.abs(y2 - y1)), y = Math.min(y1, y2) - (h === 3 ? 1 : 0);
    return `<rect x="${x1}" y="${y}" width="${Math.max(2, x2 - x1)}" height="${h}" fill="${color}" fill-opacity="${open ? 0.25 : 0.14}" stroke="${color}" stroke-opacity="0.7"${open ? ' stroke-dasharray="3 2"' : ''}/>`;
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
// 사이트 안의 작은 확인 창: 확인을 누르면 true, 취소·Esc면 false. only가 있으면 알림(확인 버튼만)
function ask(text, title = '확인', only = false) {
  const d = $('ask');
  $('ask-title').textContent = title;
  $('ask-text').textContent = text;
  $('ask-cancel').hidden = only;
  d.returnValue = '';
  d.showModal();
  return new Promise((done) => d.addEventListener('close', () => done(d.returnValue === 'ok'), { once: true }));
}
const notice = (text) => ask(text, '알림', true);

$('tools').addEventListener('click', async (e) => {
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
    else if (draw.items.length && await ask('고른 것이 없습니다. 그린 수평선과 박스를 모두 지울까요?', '모두 지우기')) {
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
// Ctrl + 드래그: 드래그한 자리에 박스. Shift + 드래그: 재기(놓아도 남고, 다음에 누르면 사라진다). 그동안은 차트가 끌려 움직이지 않게 이벤트를 차트에 넘기지 않는다
(function () {
  const wrap = $('chart-wrap');
  let dragging = false;
  const at = (e) => {
    const r = $('chart').getBoundingClientRect();
    return pointAt({ point: { x: e.clientX - r.left, y: e.clientY - r.top } });
  };
  // 고른 박스의 모서리 손잡이(6px 안)
  const corner = (e) => {
    const d = draw.selected, r = $('chart').getBoundingClientRect();
    if (!d || d.type !== 'box') return null;
    const c = boxCorners(d).find((c) => Math.abs(c.x - (e.clientX - r.left)) <= 6 && Math.abs(c.y - (e.clientY - r.top)) <= 6);
    return c ? { d, ...c } : null;
  };
  wrap.addEventListener('pointermove', (e) => { if (!dragging) wrap.style.cursor = corner(e) ? 'nwse-resize' : wrap.style.cursor === 'nwse-resize' ? '' : wrap.style.cursor; });
  // 모서리를 끌면 크기 조절: 그 모서리의 시각과 가격만 옮긴다(맞은편 모서리는 그대로)
  wrap.addEventListener('pointerdown', (e) => {
    const c = e.button === 0 && !e.shiftKey && !e.ctrlKey && corner(e);
    if (!c) return;
    e.stopPropagation();
    e.preventDefault();
    dragging = true;
    try { wrap.setPointerCapture(e.pointerId); } catch (err) { /* 잡지 못해도 차트 안에서는 그대로 끌린다 */ }
    const move = (ev) => {
      ev.stopPropagation();
      const p = at(ev);
      if (!p) return;
      c.d[c.tk] = { ...c.d[c.tk], time: p.time };
      c.d[c.pk] = { ...c.d[c.pk], price: p.price };
      renderDrawings();
    };
    wrap.addEventListener('pointermove', move, true);
    wrap.addEventListener('pointerup', (ev) => {
      ev.stopPropagation();
      wrap.removeEventListener('pointermove', move, true);
      dragging = false;
      keep('mnqDraw', draw.items);
    }, { capture: true, once: true });
  }, true);
  wrap.addEventListener('pointerdown', (e) => {
    if (draw.measure) { draw.measure = null; renderDrawings(); }
    if (!(e.ctrlKey || e.shiftKey) || e.button !== 0) return;
    const start = at(e);
    if (!start) return;
    e.stopPropagation();
    e.preventDefault();
    dragging = true;
    try { wrap.setPointerCapture(e.pointerId); } catch (err) { /* 잡지 못해도 차트 안에서는 그대로 그려진다 */ }
    if (!e.ctrlKey) {  // Shift: 재기
      draw.measure = { a: start, b: start };
      const move = (ev) => { ev.stopPropagation(); draw.measure.b = at(ev) || draw.measure.b; renderDrawings(); };
      wrap.addEventListener('pointermove', move, true);
      wrap.addEventListener('pointerup', (ev) => { ev.stopPropagation(); wrap.removeEventListener('pointermove', move, true); dragging = false; }, { capture: true, once: true });
      return;
    }
    draw.start = start;
    draw.hover = start;
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
  // 차트 라이브러리는 마우스 이벤트로도 끌기를 하므로 Ctrl·Shift를 누른 동안은 그것도 막는다
  ['mousedown', 'mousemove', 'mouseup'].forEach((t) => wrap.addEventListener(t, (e) => { if (e.shiftKey || e.ctrlKey || dragging) e.stopPropagation(); }, true));
})();
// 미체결 지정가 주문: 차트에 점선으로. 선을 마우스로 끌어 놓으면 그 가격으로 정정한다(LS 정정 주문, 주문번호가 바뀐다)
const orderLines = new Map();  // 주문번호 → { line, order }
let orderDrag = null;          // 끄는 중인 { number, order, price }
// 이 지정가가 그 가격에 체결되면 지금 포지션의 닫는 몫이 수수료 빼고 얼마인가(진입·청산 각각 대회 $1 + 증권사 $1, 계약당). 닫는 주문이 아니면 ''
function fillPnl(o, price) {
  const p = account && account.view && account.view.find((x) => state.quote && x.symbol === state.quote.symbol);
  if (!p || Math.sign(o.qty) === Math.sign(p.qty)) return '';
  const n = Math.min(Math.abs(o.qty), Math.abs(p.qty));
  const net = n * Math.sign(p.qty) * (price - p.entry) * (state.trading ? state.trading.multiplier : 2) - n * 2 * (CONTEST_FEE + BROKER_FEE);
  return ` · 체결되면 ${net >= 0 ? '+' : ''}${fmtMoney(net)} (계좌 ${fmtSigned(net / account.capital, 3)})`;
}
const orderTitle = (o) => `지정가 ${o.qty > 0 ? '매수' : '매도'} ${Math.abs(o.qty)}${fillPnl(o, o.price)} · 끌어서 정정`;
function renderOrderLines(pending) {
  const want = new Map((pending || []).filter((o) => o.limit).map((o) => [o.number, o]));
  orderLines.forEach((v, n) => { if (!want.has(n)) { series.removePriceLine(v.line); orderLines.delete(n); } });
  want.forEach((o, n) => {
    if (orderDrag && orderDrag.number === n) return;  // 끄는 중에는 화면의 선을 그대로 둔다
    const opts = { price: o.price, color: o.qty > 0 ? ORDER_BUY : ORDER_SELL, lineWidth: 2, lineStyle: LightweightCharts.LineStyle.Dashed, axisLabelVisible: true, title: '' };
    if (orderLines.has(n)) orderLines.get(n).line.applyOptions(opts);
    else orderLines.set(n, { line: series.createPriceLine(opts), order: o });
    Object.assign(orderLines.get(n), { order: o, price: o.price, tag: orderTitle(o) });
  });
  renderDrawings();
}
// 지정가 주문 이름표(바이낸스처럼): 선 왼쪽에 흰 테두리 네모, 매수 연두 · 매도 빨강
const ORDER_BUY = '#0ecb81', ORDER_SELL = '#f6465d';
function renderOrderTags() {
  return [...orderLines.values()].map((v) => {
    const y = series.priceToCoordinate(v.price);
    if (y == null) return '';
    const w = [...v.tag].reduce((s, ch) => s + (ch.charCodeAt(0) > 0x2e00 ? 12 : 6.6), 16);  // 한글은 넓게 어림
    return `<rect x="8" y="${y - 11}" width="${w}" height="22" rx="3" fill="${v.order.qty > 0 ? ORDER_BUY : ORDER_SELL}" stroke="#fff" stroke-width="1.5"/>`
      + `<text x="16" y="${y + 4.5}" fill="#fff" font-size="12" font-weight="700">${v.tag}</text>`;
  }).join('');
}
(function () {
  const wrap = $('chart-wrap');
  const yOf = (e) => e.clientY - $('chart').getBoundingClientRect().top;
  const near = (e) => {  // 마우스가 위아래 6px 안에 있는 주문 선
    for (const [n, v] of orderLines) {
      const y = series.priceToCoordinate(v.order.price);
      if (y != null && Math.abs(y - yOf(e)) <= 6) return { number: n, order: v.order };
    }
    return null;
  };
  wrap.addEventListener('pointerdown', (e) => {
    if (e.button !== 0 || e.shiftKey || e.ctrlKey) return;
    const hit = near(e);
    if (!hit) return;
    e.stopPropagation();
    e.preventDefault();
    orderDrag = { ...hit, price: hit.order.price };
    try { wrap.setPointerCapture(e.pointerId); } catch (err) { /* 잡지 못해도 차트 안에서는 그대로 끌린다 */ }
    const move = (ev) => {
      ev.stopPropagation();
      const p = series.coordinateToPrice(yOf(ev));
      if (p == null) return;
      orderDrag.price = Math.round(p * 4) / 4;
      const v = orderLines.get(orderDrag.number);
      v.line.applyOptions({ price: orderDrag.price });
      Object.assign(v, { price: orderDrag.price, tag: `→ ${fmtPrice(orderDrag.price)}${fillPnl(orderDrag.order, orderDrag.price)}` });
      renderDrawings();
    };
    const up = async (ev) => {
      ev.stopPropagation();
      wrap.removeEventListener('pointermove', move, true);
      const { number, order, price } = orderDrag;
      const back = () => { orderDrag = null; renderOrderLines([...orderLines.values()].map((v) => v.order)); };
      if (price === order.price) return back();
      // 지금 가격을 넘겨 놓으면(매도인데 시세 아래, 매수인데 시세 위) 바로 체결되므로 한 번 묻는다
      const now = state.quote ? state.quote.price : null;
      const crosses = now != null && (order.qty < 0 ? price <= now : price >= now);
      if (crosses && !await ask(`지정가 ${fmtPrice(order.price)} → ${fmtPrice(price)}${fillPnl(order, price).replace(' · ', '\n')}\n지금 가격(${fmtPrice(now)})을 넘는 가격이라 바로 체결될 수 있습니다. 정정할까요?`, '지정가 정정')) return back();
      try {
        const r = await post('/api/live/modify', { number, price });
        Object.assign(orderLines.get(number), { price: r.price, tag: `정정 중 ${fmtPrice(r.price)}` });
        renderDrawings();
      } catch (err) {
        notice('정정하지 못했습니다: ' + err.message);
      }
      orderDrag = null;
      api('/api/live').then(renderLive).catch(() => {});
    };
    wrap.addEventListener('pointermove', move, true);
    wrap.addEventListener('pointerup', up, { capture: true, once: true });
  }, true);
  // 선 위에서는 위아래 화살표 커서. 끄는 동안 차트가 같이 끌리지 않게 마우스 이벤트도 막는다
  wrap.addEventListener('pointermove', (e) => { if (!orderDrag) wrap.style.cursor = near(e) ? 'ns-resize' : wrap.style.cursor === 'ns-resize' ? '' : wrap.style.cursor; });  // 박스 손잡이 커서는 건드리지 않는다
  ['mousedown', 'mousemove', 'mouseup'].forEach((t) => wrap.addEventListener(t, (e) => {
    if (orderDrag || (t === 'mousedown' && e.button === 0 && !e.shiftKey && !e.ctrlKey && near(e))) e.stopPropagation();
  }, true));
  // 우클릭: 그 높이의 가격으로 지정가 매수·매도·정리를 고르는 작은 메뉴. 고르면 가격 칸을 채우고 오른쪽 버튼과 같은 확인 창
  const menu = $('ctx-menu'), hide = () => { menu.hidden = true; };
  wrap.addEventListener('contextmenu', (e) => {
    const p = series.coordinateToPrice(yOf(e));
    if (p == null) return;
    e.preventDefault();
    if ($('limit-btn').disabled) return notice('대회 시작부터 주문할 수 있습니다.');
    const price = Math.round(p * 4) / 4, n = sizeContracts().n;
    const pos = account && account.view.find((x) => state.quote && x.symbol === state.quote.symbol);
    menu.innerHTML = `<button role="menuitem" data-pick="buy">지정가 매수 ${n}계약 @ ${fmtPrice(price)}</button>`
      + `<button role="menuitem" data-pick="sell">지정가 매도 ${n}계약 @ ${fmtPrice(price)}</button>`
      + (pos ? `<button role="menuitem" data-pick="close">지정가 정리 @ ${fmtPrice(price)}</button>` : '');
    menu.dataset.price = price;
    const box = wrap.getBoundingClientRect();
    menu.hidden = false;
    menu.style.left = Math.min(e.clientX - box.left, box.width - menu.offsetWidth - 4) + 'px';
    menu.style.top = Math.min(e.clientY - box.top, box.height - menu.offsetHeight - 4) + 'px';
    menu.querySelector('button').focus();
  });
  menu.addEventListener('click', (e) => {
    const b = e.target.closest('[data-pick]');
    if (!b) return;
    hide();
    $('limit-price').value = menu.dataset.price;
    ({ buy: $('limit-open').querySelector('.buy'), sell: $('limit-open').querySelector('.sell'), close: $('limit-btn') })[b.dataset.pick].click();
  });
  document.addEventListener('pointerdown', (e) => { if (!menu.contains(e.target)) hide(); });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') hide(); });
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
    // 분봉: 가상 계좌를 시작하기 전은 과거 시험(1시간봉으로 돌린 것), 그 뒤는 가상 계좌의 실제 매매.
    // 스캘핑은 과거 시험이 오늘 1분봉까지 이어지므로(서버가 1분마다 다시 계산) 그것만 그리고, 진입 ~ 청산을 추천 구간 상자로도 보인다
    const step = state.interval * 60;
    const until = c.scalp ? Infinity : c.started || Infinity;
    const past = scalpShown(c).filter((t) => t.time < until);
    markers = past.concat(c.scalp ? [] : c.trades).filter((t) => t.to !== t.from && t.time >= first)
      .map((t) => marker(Math.floor(t.time / step) * step + TZ, t.from, t.to, true, t.pnl));
  }
  state.zones = [];
  if (c.scalp && state.interval !== 'D') {
    scalpShown(c).forEach((t) => {
      if (t.from === 0 && t.to) state.zones.push({ side: t.to, t1: t.time, p1: t.price, t2: null, p2: null });
      else if (t.to === 0 && state.zones.length) Object.assign(state.zones[state.zones.length - 1], { t2: t.time, p2: t.price, why: t.why });
    });
  }
  renderDrawings();
  if (c.mtf && state.interval !== 'D') markers = markers.concat(mtfMarkers(first));  // 매매법 'MACD 동시 반전': 세 단계 신호
  series.setMarkers(markers.sort((a, b) => a.time - b.time));
  if (entryLine) series.removePriceLine(entryLine);
  entryLine = c.contracts && c.entry ? series.createPriceLine({
    price: c.entry, color: c.contracts > 0 ? UP : DOWN, lineWidth: 1, lineStyle: LightweightCharts.LineStyle.Dashed,
    axisLabelVisible: true, title: `가상 진입 ${position(c.contracts).replace(/<[^>]+>/g, '')}`,
  }) : null;
}

// MACD 동시 반전(관찰용, research/r18_macd_mtf.py 불통과): 서버가 1분봉으로 계산한 신호를 근거 세기 세 단계로.
// 약 = 같은 방향 2개 꺾임(옅고 작은 화살표, 매매 안 함) · 중 = 3개 이상(진한 보통 화살표) · 강 = 2개 이상 + 1·5·15분 RSI 과매도·과매수(금색 큰 화살표 ★)
// 화살표 방향과 자리: ▲ 봉 아래 = 매수, ▼ 봉 위 = 매도. 오른쪽 칸 범례(mtfLegend)와 같은 색·크기
const MTF_GOLD = '#f0b90b';
const MTF = {
  1: { name: '약', size: 0.6, buy: 'rgba(38, 166, 154, .5)', sell: 'rgba(239, 83, 80, .5)' },
  2: { name: '중', size: 1.2, buy: UP, sell: DOWN },
  3: { name: '강', size: 2.2, buy: MTF_GOLD, sell: MTF_GOLD },
};
async function loadMtf() {
  try { state.mtf = await api('/api/macd_mtf'); } catch (e) { state.mtf = []; }
  renderMarkers();
  if (current() && current().mtf) renderSide();  // 범례 아래 '마지막 신호'
}
// 봉이 클수록 한 봉에 신호가 몰려 차트를 덮으므로: 15분봉 이상은 2·3단계만, 글자는 1·5분봉에서만 길게
function mtfMarkers(first) {
  const step = state.interval * 60, big = state.interval >= 15;
  return (state.mtf || []).filter((s) => s.time >= first && !(big && s.level === 1)).map((s) => {
    const buy = s.side > 0, st = MTF[s.level];
    const text = (s.level === 3 ? '★ ' : '') + mtfTfs(s);  // 화살표 옆 글자: 함께 꺾인 분봉만 숫자로(예: 1 / 5)
    return {
      time: Math.floor(s.time / step) * step + TZ, position: buy ? 'belowBar' : 'aboveBar', color: buy ? st.buy : st.sell, text,
      shape: buy ? 'arrowUp' : 'arrowDown', size: st.size,
    };
  });
}
const mtfTfs = (s) => s.tfs.join(' / ');  // 함께 꺾인 분봉: 1 / 5, 5 / 15 / 60
const mtfRsi = (s) => Math.round((s.side > 0 ? Math.min : Math.max)(...Object.values(s.rsi)));  // 가장 과매도(매수)·과매수(매도)인 RSI
// 오른쪽 칸: 근거 세기 순 범례(차트와 같은 색·크기의 화살표)와 마지막 신호
function mtfLegend() {
  const arrow = (color, px, up) => `<svg width="${px}" height="${px}" viewBox="0 0 10 10" aria-hidden="true"><path d="${up ? 'M5 0 10 10 0 10z' : 'M0 0 10 0 5 10z'}" fill="${color}"/></svg>`;
  const row = (lv, what, trade) => `<li><span class="mtf-icons">${arrow(MTF[lv].buy, 6 + lv * 4, true)}${arrow(MTF[lv].sell, 6 + lv * 4, false)}</span>`
    + `<b>${lv === 3 ? '★' : ''}${MTF[lv].name}</b><span>${what}<small>${trade}</small></span></li>`;
  const s = (state.mtf || [])[state.mtf ? state.mtf.length - 1 : 0];
  return `<div class="why-block"><div class="why-title"><span>근거 세기 (강한 순)</span><span><small>▲ 봉 아래 = 매수 · ▼ 봉 위 = 매도</small></span></div>`
    + `<ul class="mtf-legend">${row(3, '2개 이상 꺾임 + RSI 과매도(매수 &lt; 30)·과매수(매도 &gt; 70)', '금색 큰 화살표 · 가상 계좌 1계약')}`
    + `${row(2, '3개 이상 같은 방향으로 꺾임', '진한 보통 화살표 · 가상 계좌 1계약')}${row(1, '2개 같은 방향으로 꺾임', '옅은 작은 화살표 · 표시만')}</ul>`
    + '<p class="why-foot">화살표 옆 숫자는 함께 꺾인 분봉입니다(예: 1 / 5 = 1분봉과 5분봉, 60 = 1시간봉).</p>'
    + (s ? `<p class="why-sum">마지막 신호 ${fmtTime(s.time + 60)} · <b class="${s.level === 3 ? 'mtf-gold' : tone(s.side)}">${s.level === 3 ? '★' : ''}${MTF[s.level].name} ${s.side > 0 ? '▲ 매수' : '▼ 매도'}</b> ${mtfTfs(s)}`
      + (s.level === 3 ? ` · RSI ${mtfRsi(s)}` : '') + '</p>' : '') + '</div>';
}
setInterval(() => { if (current() && current().mtf) loadMtf(); }, 60000);

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
    body = (c.mtf ? mtfLegend() : '') + whyBlock('조건', null, (last && (c.scalp || c.hourly) ? [['마지막 판단', `${fmtTime(last.time)} · ${last.why}`, null]] : []).concat(c.explain));
  }
  const next = c.mtf ? 'CME 장중, 1분봉 마감마다' : c.scalp ? '한국 평일 08:00 ~ 18:00, 1분봉 마감마다' : c.hourly ? '매시 정각(1시간봉 마감마다, CME 쉬는 때 제외)' : `${c.next_intraday ? `장중 ${fmtTime(c.next_intraday)} · ` : ''}일봉 ${fmtTime(d.next_decision)}`;
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
  fillLimit();
  renderSize();
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
  const orders = d.orders.slice(0, 3).map((o) => `<tr><td>${fmtTime(o.time)}</td><td>${o.ok ? `<b class="${tone(o.qty)}">${o.limit ? `지정가 ${fmtPrice(o.price)} ` : o.manual ? '직접 ' : o.filler ? '횟수 채우기 ' : ''}${o.qty > 0 ? '매수' : '매도'} ${Math.abs(o.qty)}</b> (${o.from} → ${o.to})` : `<span class="c-down">실패</span> ${o.error || ''}`}</td></tr>`).join('');
  const t = $('auto-toggle');
  t.textContent = d.enabled ? '자동 주문 켜짐' : '자동 주문 꺼짐';
  t.classList.toggle('active', d.enabled);
  t.setAttribute('aria-pressed', d.enabled);
  t.dataset.on = d.enabled;
  state.live = d;  // 비중 막대가 1계약 증거금과 최대 계약 수를 쓴다
  renderOrderLines(d.pending);
  const n = (d.pending || []).length;
  $('cancel-pending').hidden = !n;
  $('cancel-pending').textContent = `미체결 주문 취소 (${n}건)`;
  document.querySelectorAll('#trade-buttons button, #limit-btn, #limit-open button, #account-flat').forEach((b) => {
    b.disabled = !d.started;
    b.title = d.started ? '' : '대회 시작부터 누를 수 있습니다';
  });
  $('live').innerHTML = rows([
    ['목표 / 실제', d.target == null ? '-' : `${position(d.target)} / ${position(d.actual)}`],
    ['이번 주 체결', d.week == null ? '-' : `<b class="${d.week >= d.min_weekly ? 'c-up' : ''}">${d.week}</b> / ${d.min_weekly}건 <small>(모자라면 금요일 뉴욕 14시부터 채움)</small>`],
    ['상태', d.error ? `<span class="c-down">${d.error}</span>` : d.note || '-'],
  ].concat((d.pending || []).length ? [['미체결', d.pending.map((o) => `<b class="${tone(o.qty)}">${o.qty > 0 ? '매수' : '매도'} ${Math.abs(o.qty)}</b>`
    + (o.limit ? ` @ ${fmtPrice(o.price)}` : ' 시장가')).join('<br>') + '<button class="pending-cancel" data-cancel>취소</button>']] : [])) + (orders ? `<table class="trend-table"><tbody>${orders}</tbody></table>` : '<p class="empty">아직 주문이 없습니다.</p>');
}

// 직접 주문: 시장가로 바로 나가고 자동 주문은 꺼진다(켜 두면 30초 뒤 목표 계약 수로 되돌리기 때문)
$('trade-buttons').addEventListener('click', async (e) => {
  const b = e.target.closest('button');
  if (!b) return;
  const flat = b.hasAttribute('data-flatten'), qty = Number(b.dataset.qty) * sizeContracts().n;
  const est = flat ? (account ? account.view : []).map(closeEstimate) : [];
  if (b.hasAttribute('data-flip')) {  // 뒤집기: 계약 수는 서버가 주문 직전의 실제 포지션으로 정한다
    const p = account && account.view.find((x) => state.quote && x.symbol === state.quote.symbol);
    if (!p) return notice('뒤집을 포지션이 없습니다.');
    const now = position(p.qty).replace(/<[^>]+>/g, ''), next = position(-p.qty).replace(/<[^>]+>/g, '');
    if (!await ask(`${now} → ${next}: 시장가 ${p.qty > 0 ? '매도' : '매수'} ${2 * Math.abs(p.qty)}계약 한 건\n\n`
      + `지금 포지션을 닫는 몫\n${closeEstimate(p).text}\n새 ${next.split(' ')[0]} 진입에 수수료 $${(CONTEST_FEE + BROKER_FEE) * Math.abs(p.qty)} 더\n\n`
      + '자동 주문은 꺼집니다(켜 두면 30초 뒤 목표 계약 수로 되돌리기 때문).', '포지션 뒤집기')) return;
    try {
      await sending(b, () => post('/api/live/order', { flip: true }));
    } catch (err) {
      notice('주문하지 못했습니다: ' + err.message);
    }
    return afterOrder();
  }
  if (!await ask(flat ?`${est.length ? est.map((x) => x.text).join('\n\n') + '\n\n' : '들고 있는 포지션이 없습니다.\n'}모든 포지션을 시장가로 정리하고 자동 주문을 멈출까요?`
    : `MNQ ${Math.abs(qty)}계약을 시장가로 ${qty > 0 ? '매수' : '매도'}할까요?\n자동 주문은 꺼집니다(아이콘 메뉴에서 다시 켜면 목표 계약 수로 되돌립니다).`, flat ? '포지션 정리' : '직접 주문')) return;
  try {
    await sending(b, () => (flat ? post('/api/live/flatten', {}) : post('/api/live/order', { qty })));
    if (flat && est.length) closed = est.map((x) => x.html).join('<br>');
  } catch (err) {
    notice('주문하지 못했습니다: ' + err.message);
  }
  afterOrder();
});

// 주문 뒤: 주문 칸은 바로, 계좌 칸은 바로·1초·3초 뒤에 다시 읽는다(시장가는 보통 1초 안에 체결돼 5초 주기를 기다리지 않게)
function afterOrder() {
  api('/api/live').then(renderLive).catch(() => {});
  [0, 1000, 3000].forEach((ms) => setTimeout(tickAccount, ms));
}
// 주문을 보내는 동안 누른 버튼에 '주문 중…'을 띄운다(끝나면 원래 글자로)
async function sending(b, job) {
  const text = b.innerHTML;
  b.disabled = true;
  b.textContent = '주문 중…';
  try { return await job(); } finally { b.innerHTML = text; renderSize(); }
}

// 미체결 주문 취소: 주문 칸의 버튼(미체결이 있을 때만 보인다)과 상태 줄의 [취소]가 같이 쓴다
async function cancelPending() {
  if (!await ask('미체결 주문을 모두 취소할까요?', '미체결 취소')) return;
  try {
    const r = await post('/api/live/cancel', {});
    notice(`${r.canceled}건을 취소했습니다.`);
  } catch (err) {
    notice('취소하지 못했습니다: ' + err.message);
  }
  afterOrder();
}
$('cancel-pending').addEventListener('click', cancelPending);

// 비중 막대: 계약 수와 그 노출(계약 가치 ÷ 평가 자산). 매수·매도 버튼 글자도 같이 바꾼다
// 잔고의 pct%를 증거금으로 쓰면 몇 계약인가: 내림, 최소 1, 서버의 최대 계약 수(안전장치)까지
function sizeContracts() {
  const pct = Number($('size').value), eq = account ? (account.cash ?? account.deposit) : null;
  const margin = (state.live && state.live.contract_margin) || 4741;  // 서버가 아직 모르면 10-15에 본 개시증거금
  const max = (state.live && state.live.max_contracts) || 1;
  const n = eq ? Math.min(max, Math.max(1, Math.floor((pct / 100) * eq / margin))) : 1;
  return { n, pct, eq, margin, capped: eq && Math.floor((pct / 100) * eq / margin) > max };
}
function renderSize() {
  const { n, pct, eq, margin, capped } = sizeContracts(), q = state.quote;
  const expo = q && eq ? n * q.price * (state.trading ? state.trading.multiplier : 2) / eq : null;
  $('size-out').innerHTML = `잔고 <b>${pct}%</b> → <b>${n}계약</b> · 증거금 ${fmtMoney(n * margin, 0)}`
    + (expo ? ` · 레버리지 <b>${expo.toFixed(1)}배</b> (지수 1% = $${Math.round(expo * eq / 100).toLocaleString('en-US')})` : '')
    + (capped ? ' <small>(최대 계약 수에서 멈춤)</small>' : '');
  document.querySelector('#trade-buttons .buy').textContent = `매수 ${n}계약`;
  document.querySelector('#trade-buttons .sell').textContent = `매도 ${n}계약`;
  document.querySelector('#limit-open .buy').textContent = `지정가 매수 ${n}계약`;
  document.querySelector('#limit-open .sell').textContent = `지정가 매도 ${n}계약`;
}
$('size').addEventListener('input', renderSize);

$('account-flat').addEventListener('click', () => $('trade-buttons').querySelector('[data-flatten]').click());  // 같은 확인 창·같은 주문

// 지정가 정리: 지금 포지션 전부를 적은 가격의 지정가 한 건으로. 체결될 때까지는 주문 칸에 미체결로 남고 거기서 취소한다
$('limit-btn').addEventListener('click', async () => {
  const p = account && account.view.find((x) => state.quote && x.symbol === state.quote.symbol);
  if (!p) return notice('정리할 포지션이 없습니다.');
  const price = Number($('limit-price').value);
  if (!(price > 0) || Math.round(price * 4) !== price * 4) return notice('가격은 0.25 단위로 적어 주세요.');
  const side = p.qty > 0 ? '매도' : '매수', worse = p.qty > 0 ? price < p.price : price > p.price;
  if (!await ask(`${side} ${Math.abs(p.qty)}계약 지정가 ${fmtPrice(price)}`
    + (worse ? '\n※ 지금 가격보다 불리한 가격이라 바로 체결될 수 있습니다' : '\n가격이 여기에 닿아야 체결됩니다') + '\n\n'
    + `체결되면\n${closeEstimate(p, price).text}\n\n자동 주문은 꺼집니다(켜 두면 체결 뒤 목표 계약 수로 다시 들기 때문).`, '지정가 정리')) return;
  try {
    await sending($('limit-btn'), () => post('/api/live/limit', { price }));
  } catch (err) {
    notice('주문하지 못했습니다: ' + err.message);
  }
  afterOrder();
});
// 지정가 진입: 가격 칸의 가격에 비중 막대의 계약 수만큼. 반대 포지션이 있으면 체결될 때 그만큼 상계된다
$('limit-open').addEventListener('click', async (e) => {
  const b = e.target.closest('[data-side]');  // 같은 줄의 지정가 정리 버튼은 따로 받는다
  if (!b) return;
  const n = sizeContracts().n, qty = Number(b.dataset.side) * n, price = Number($('limit-price').value);
  if (!(price > 0) || Math.round(price * 4) !== price * 4) return notice('가격은 0.25 단위로 적어 주세요.');
  const now = state.quote ? state.quote.price : null, side = qty > 0 ? '매수' : '매도';
  const crosses = now != null && (qty > 0 ? price >= now : price <= now);
  const p = account && account.view.find((x) => state.quote && x.symbol === state.quote.symbol);
  if (!await ask(`지정가 ${side} ${n}계약 @ ${fmtPrice(price)}`
    + (crosses ? `\n※ 지금 가격(${fmtPrice(now)})보다 ${qty > 0 ? '높은' : '낮은'} 가격이라 바로 체결될 수 있습니다` : '\n가격이 여기에 닿아야 체결됩니다')
    + (p && Math.sign(p.qty) !== Math.sign(qty) ? `\n지금 ${position(p.qty).replace(/<[^>]+>/g, '')}과 상계됩니다${fillPnl({ qty }, price).replace(' · ', '\n')}` : '')
    + `\n수수료 진입 $${(CONTEST_FEE + BROKER_FEE) * n}\n\n자동 주문은 꺼집니다(켜 두면 체결 뒤 목표 계약 수로 되돌리기 때문).`, '지정가 진입')) return;
  try {
    await sending(b, () => post('/api/live/limit', { price, qty }));
  } catch (err) {
    notice('주문하지 못했습니다: ' + err.message);
  }
  afterOrder();
});
// 처음 시세가 오면 지정가 칸에 지금 가격을 채워 둔다(사용자가 고친 값은 건드리지 않는다)
const fillLimit = () => { if (state.quote && !$('limit-price').value) $('limit-price').value = state.quote.price; };
$('limit-now').addEventListener('click', () => { if (state.quote) $('limit-price').value = state.quote.price; });

$('live').addEventListener('click', (e) => { if (e.target.closest('[data-cancel]')) cancelPending(); });

// 대회 매매법 바꾸기: 자동 주문이 켜져 있으면 30초 안에 새 매매법의 목표 계약 수로 맞추는 주문이 나가므로 한 번 묻는다
$('live-pick').addEventListener('change', async (e) => {
  const key = e.target.value;
  if (!await ask(`대회 매매법을 "${e.target.selectedOptions[0].text}"(으)로 바꿀까요?
자동 주문이 켜져 있으면 30초 안에 그 매매법의 목표 계약 수로 맞추는 주문이 나갑니다.`, '대회 매매법 바꾸기')) {
    api('/api/live').then(renderLive).catch(() => {});
    return;
  }
  try {
    renderLive(await post('/api/live/candidate', { key }));
    state.candidate = key;
    tickTrading();
  } catch (err) {
    notice('바꾸지 못했습니다: ' + err.message);
  }
});

// 자동 주문 켜고 끄기. 켜면 30초 안에 실제 포지션을 목표 계약 수에 맞추는 주문이 나갈 수 있어 한 번 묻는다(끄기는 포지션을 건드리지 않는다)
$('auto-toggle').addEventListener('click', async () => {
  const on = $('auto-toggle').dataset.on !== 'true';
  if (on && !await ask('자동 주문을 켤까요?\n30초 안에 실제 포지션을 목표 계약 수에 맞추는 시장가 주문이 나갈 수 있습니다.', '자동 주문 켜기')) return;
  try {
    renderLive(await post('/api/live/enabled', { on }));
  } catch (err) {
    notice('바꾸지 못했습니다: ' + err.message);
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
    return { ...p, price, pnl, net: pnl - closeFee(p), roe: p.margin ? pnl / p.margin : null, of_capital: pnl / a.capital };
  });
  a.view = pos;  // 정리 버튼이 실시간 가격으로 다시 계산한 손익을 쓰게
  renderRealEntry(pos.find((p) => q && p.symbol === q.symbol));
  const pnl = pos.reduce((s, p) => s + p.pnl, 0);
  const equity = (a.cash ?? a.deposit) + pnl;  // cash = 예탁금 + 청산손익 - 증권사 수수료
  // 대회는 증권사 수수료와 별개로 체결 1계약당 $1을 더 뺀다. 증권사 수수료도 체결 1계약당 $1이라 같은 금액으로 본다
  const contest = equity - (a.fees || 0);
  $('account-note').textContent = `${fmtTime(a.at)} 조회`;
  $('account-sum').innerHTML = [
    ['평가 자산', `<b>${fmtMoney(equity)}</b>`],
    ['대회 수익률', `<b class="${tone(contest - a.capital)}" title="평가 자산에서 대회 차감(체결 1계약당 $1, 지금까지 ${fmtMoney(a.fees || 0)})을 더 뺀 값">${fmtSigned(contest / a.capital - 1, 3)}</b>`],
    ['실현 손익', `<b class="${tone(a.realized)}">${fmtMoney(a.realized || 0)}</b> <small>(수수료 ${fmtMoney(a.fees || 0)})</small>`],
    ['미실현 손익', `<b class="${tone(pnl)}">${fmtMoney(pnl)}</b>`],
    ['사용 증거금', fmtMoney(a.margin, 0)],
  ].map(([k, v]) => `<div><dt>${k}</dt><dd>${v}</dd></div>`).join('');
  $('account-pos').innerHTML = pos.length ? `<table class="trend-table account-table"><thead><tr><th>종목</th><th>포지션</th><th>진입가</th><th>현재가</th>
    <th title="평가 자산이 증거금까지 줄어드는 가격(추정). LS의 실제 반대매매 기준과 다를 수 있습니다">청산가(추정)</th><th>미실현 손익 (증거금 대비)</th><th title="지금 정리하면: 미실현 손익 - 대회 차감(체결 1계약당 매수·매도 각 $1) - 증권사 수수료">수수료 뺀 손익 (계좌 대비)</th><th>계좌 대비</th><th>증거금</th></tr></thead><tbody>${pos.map((p) =>
    `<tr><td><b>${p.symbol}</b></td><td>${position(p.qty)}</td><td>${fmtPrice(p.entry)}</td><td>${fmtPrice(p.price)}</td><td>${p.liq ? fmtPrice(p.liq) : '없음 <small>(계좌 충분)</small>'}</td>
    <td><b class="${tone(p.pnl)}">${fmtMoney(p.pnl)}</b> <span class="${tone(p.pnl)}">(${fmtSigned(p.roe)})</span></td>
    <td><b class="${tone(p.net)}">${fmtMoney(p.net)}</b> <span class="${tone(p.net)}">(${fmtSigned(p.net / a.capital, 3)})</span></td><td class="${tone(p.pnl)}">${fmtSigned(p.of_capital, 3)}</td><td>${fmtMoney(p.margin, 0)}</td></tr>`).join('')}</tbody></table>`
    : '<p class="empty">들고 있는 포지션이 없습니다.</p>';
}

// LS 모의계좌의 실제 진입가: 차트에 실선(가상 계좌의 점선과 구분), 주문 칸에 진입가와 수익률(가격 움직임 기준, 괄호 안은 손익과 계좌 대비)
let realLine = null, realKey = '', evenLine = null;
// 수수료 뺀 본전 가격: 진입·청산 각각 대회 차감 $1 + 증권사 $1(계약당)을 메우는 가격을, 이익 쪽으로 0.25틱에 올려 맞춘다(계약 수와 상관없이 2포인트)
const breakeven = (p) => {
  const raw = p.entry + Math.sign(p.qty) * closeFee(p) / (Math.abs(p.qty) * (state.trading ? state.trading.multiplier : 2));
  return (p.qty > 0 ? Math.ceil : Math.floor)(raw * 4) / 4;
};
function renderRealEntry(p) {
  const key = p ? `${p.qty}|${p.entry}` : '';
  if (key !== realKey) {  // 0.5초마다 불리므로 진입가나 계약 수가 바뀔 때만 다시 그린다
    realKey = key;
    if (realLine) series.removePriceLine(realLine);
    realLine = p ? series.createPriceLine({
      price: p.entry, color: p.qty > 0 ? UP : DOWN, lineWidth: 2, lineStyle: LightweightCharts.LineStyle.Solid,
      axisLabelVisible: true, title: `실제 진입 ${position(p.qty).replace(/<[^>]+>/g, '')}`,
    }) : null;
    if (evenLine) series.removePriceLine(evenLine);
    evenLine = p ? series.createPriceLine({
      price: breakeven(p), color: MTF_GOLD, lineWidth: 1, lineStyle: LightweightCharts.LineStyle.Dotted,
      axisLabelVisible: true, title: `본전 (수수료 $${closeFee(p)} 뺌) · 이보다 ${p.qty > 0 ? '위' : '아래'}면 이익`,
    }) : null;
  }
  const move = p ? Math.sign(p.qty) * (p.price / p.entry - 1) : 0;
  $('live-pos').innerHTML = p ? rows([
    ['실제 진입', `${position(p.qty)} @ <b>${fmtPrice(p.entry)}</b>`],
    ['본전', `<b class="mtf-gold">${fmtPrice(breakeven(p))}</b> <small>(수수료 $${closeFee(p)} 뺀 최소 익절, 진입가 ${p.qty > 0 ? '+' : '−'}${Math.abs(breakeven(p) - p.entry).toFixed(2)}pt)</small>`],
    ['수익률', `<b class="${tone(move)}">${fmtSigned(move)}</b> <small>(${fmtMoney(p.pnl)} · 계좌 ${fmtSigned(p.of_capital, 3)})</small>`],
  ]) : rows([['실제 진입', '포지션 없음']].concat(closed ? [['정리 결과', closed]] : []));
}

// 이 포지션을 수수료 빼고 얼마에 닫는가: 체결 1계약당 대회 차감 $1 + 증권사 수수료 $1(10-19 LS 모의계좌 왕복 $2 확인), 진입과 청산 두 번.
// 진입 때의 증권사 수수료는 이미 평가 자산에서 빠져 있지만, 이 매매 한 번의 순손익으로 보이게 함께 뺀다. 시장가라 실제 체결가는 조금 다를 수 있다
const CONTEST_FEE = 1, BROKER_FEE = 1;
let closed = '';
const closeFee = (p) => 2 * (CONTEST_FEE + BROKER_FEE) * Math.abs(p.qty);
function closeEstimate(p, at) {  // at: 지정가로 닫을 가격(없으면 지금 가격)
  const pnl = at == null ? p.pnl : p.qty * (state.trading ? state.trading.multiplier : 2) * (at - p.entry);
  const fee = closeFee(p), net = pnl - fee, cap = account.capital;
  const sign = (v) => (v >= 0 ? '+' : '') + fmtMoney(v);
  return {
    text: `${p.symbol} ${position(p.qty).replace(/<[^>]+>/g, '')}: 진입 ${fmtPrice(p.entry)} → ${at == null ? `지금 ${fmtPrice(p.price)}` : `지정가 ${fmtPrice(at)} (지금 ${fmtPrice(p.price)})`}\n`
      + `가격 손익 ${sign(pnl)}\n수수료 -${fmtMoney(fee)} (진입·청산 각각 대회 차감 $${CONTEST_FEE} + 증권사 $${BROKER_FEE}, ${Math.abs(p.qty)}계약)\n`
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
    else if (current() && current().scalp && Date.now() - (state.scalpAt || 0) > 55000) {  // 스캘핑 추천 구간은 서버가 1분마다 새 1분봉으로 다시 계산한다
      state.scalpAt = Date.now();
      Object.assign(state.replay, await api('/api/replay?key=' + current().key));
      renderMarkers();
    }
    if (!state.dailyCloses || Date.now() - state.dailyAt > 600000) {  // 일봉 종가는 10분마다
      state.dailyAt = Date.now();
      api('/api/chart?interval=D').then((d) => { state.dailyCloses = d.bars.map((b) => b.close); renderLevels(); }).catch(() => {});
    }
    api('/api/live').then(renderLive).catch(() => {});
    state.trading = await api('/api/trading');
    renderSide();
    if (current() && current().mtf && !state.mtf) loadMtf();  // 처음 열 때 MACD 동시 반전이 골라져 있으면
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
  if (current() && current().mtf && !state.mtf) loadMtf();
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
