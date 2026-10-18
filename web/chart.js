// 실시간 차트 페이지: MNQ 근월물 봉을 0.5초마다 이어 그린다(서버가 LS 실시간 체결로 마지막 봉을 고친다).
// 분봉에는 고른 후보를 최근 2년 1시간봉으로 돌린 롱·숏 진입·청산(과거 시험)과 가상 매매를, 일봉(2021 ~)에는 규칙의 진입·청산을 화살표로 표시한다.
// 청산하거나 줄인 화살표에는 그 몫의 손익(비용 전)을 붙인다.
// 규칙이 보는 값도 차트에 그린다: 일봉에는 200일선·5일 최저 종가, 분봉에는 장중 갈래의 9시 시가와 노이즈 경계.
// 오른쪽 칸 맨 위는 주문 버튼과 추천 근거, 그 아래로 후보의 포지션, 다음 자동매매, 시세, 매매 기록. 차트 아래는 LS 모의계좌의 실제 포지션.

const $ = (id) => document.getElementById(id);
const token = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const fmtPrice = (v) => (v == null ? '-' : v.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 }));
const fmtMoney = (v, digits = 2) => (v < 0 ? '-$' : '$') + Math.abs(v).toLocaleString('en-US', { maximumFractionDigits: digits });
const fmtSigned = (v, digits = 2) => (v == null ? '-' : (v >= 0 ? '+' : '') + (v * 100).toFixed(digits) + '%');
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
  const key = c && [state.interval, c.key, state.bars.length, JSON.stringify(c.noise), nyHour()].join('|');
  if (key === levelsKey) return;
  levelsKey = key;
  const daily = state.interval === 'D' && c;
  const closes = state.bars.map((b) => b.close);
  const roll = (n, f) => state.bars.slice(n - 1).map((b, i) => ({ time: b.time + TZ, value: f(closes.slice(i, i + n)) }));
  smaSeries.setData(daily && c.rule !== 'hold' && c.rule !== 'vt' ? roll(200, (w) => w.reduce((a, v) => a + v, 0) / w.length) : []);
  lowSeries.setData(daily && c.rule === 'dip_trend' ? roll(5, (w) => Math.min(...w)) : []);
  levelLines.forEach((l) => series.removePriceLine(l));
  levelLines = [];
  const n = c && state.interval !== 'D' ? c.noise : null;
  if (!n || !n.today) return;
  const hour = Math.min(15, Math.max(10, nyHour() + 1));
  const band = n.bands[hour];
  const line = (price, color, title) => levelLines.push(series.createPriceLine({ price, color, title, lineWidth: 1, lineStyle: LightweightCharts.LineStyle.Dotted, axisLabelVisible: true }));
  line(n.open, token('--text-3'), '9시 시가');
  if (band) {
    line(band[0], UP, `${hour}시 판단: 위로 넘으면 롱`);
    line(band[1], DOWN, `${hour}시 판단: 아래로 넘으면 숏`);
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
    axisLabelVisible: true, title: `평균 진입 ${position(c.contracts).replace(/<[^>]+>/g, '')}`,
  }) : null;
}

// ---------- 오른쪽 칸 ----------
function rows(list) {
  return `<dl class="info">${list.map(([k, v]) => `<div><dt>${k}</dt><dd>${v}</dd></div>`).join('')}</dl>`;
}

function renderSide() {
  const d = state.trading;
  if (!d) return;
  const c = current();
  state.candidate = c.key;
  $('picks').innerHTML = d.candidates.map((x) => {
    const r = x.equity / d.capital - 1;
    const [short, ...rest] = x.name.split(' · ');
    const on = x.key === c.key;
    return `<button class="rail-item${on ? ' active' : ''}" data-key="${x.key}" aria-pressed="${on}">
      <span class="rail-top"><span class="rail-name">${short}</span><b class="${tone(r)}">${fmtSigned(r)}</b></span>
      <span class="rail-desc">${rest.join(' · ')}</span><span class="rail-facts">${x.contracts ? position(x.contracts).replace(/<[^>]+>/g, '') : '포지션 없음'}</span></button>`;
  }).join('');

  const price = state.quote ? state.quote.price : null;
  const pnl = c.contracts && c.entry && price ? c.contracts * d.multiplier * (price - c.entry) : 0;
  const notional = price ? Math.abs(c.contracts) * price * d.multiplier : 0;
  $('pos-note').textContent = c.started ? `${fmtTime(c.started)} 시작` : '';
  $('position').innerHTML = `<div class="pos-big">${position(c.contracts)}</div>` + rows([
    ...(partsText(c.parts) ? [['갈래별', partsText(c.parts)]] : []),
    ['평균 진입가', c.entry && c.contracts ? fmtPrice(c.entry) : '-'],
    ['평가손익', c.contracts ? `<b class="${tone(pnl)}">${fmtMoney(pnl)}</b>` : '-'],
    ['노출', `${(notional / c.equity).toFixed(2)}배 · ${fmtMoney(notional, 0)}`],
    ['가상 계좌', `<b>${fmtMoney(c.equity)}</b> <span class="${tone(c.equity - d.capital)}">${fmtSigned(c.equity / d.capital - 1)}</span>`],
  ]);

  const want = c.signal.contracts;
  const move = want == null ? null : want - c.contracts;
  $('next').innerHTML = rows([
    ...(c.next_intraday ? [['장중 판단', `<b>${fmtTime(c.next_intraday)}</b> <small>(평일 뉴욕 10 ~ 15시 정시, 16시 정리)</small>`]] : []),
    [c.next_intraday ? '일봉 결정' : '결정 시각', `<b>${fmtTime(d.next_decision)}</b>`],
    ['지금 신호', `${position(want || 0)} <small>(노출 ${c.signal.expo.toFixed(2)}배, ${d.close[0]} 종가 기준)</small>`],
    ['지금 결정하면', move == null ? '-' : move === 0 ? '그대로' : `<b class="${tone(move)}">${move > 0 ? '매수' : '매도'} ${Math.abs(move)}계약</b>`],
  ]) + (d.error ? `<div class="note warn">${d.error}</div>` : '');
  renderWhy(c, d);
  renderLevels();

  $('trades').innerHTML = c.trades.length ? `<table class="trend-table"><thead><tr><th>때</th><th>매매</th><th>가격</th></tr></thead><tbody>${c.trades.slice(0, 20).map((t) => {
    const move = t.to - t.from;
    return `<tr><td>${fmtTime(t.time)}${t.sleeve === 'intraday' ? ' <small>장중</small>' : ''}</td><td>${move ? `<b class="${tone(move)}">${move > 0 ? '매수' : '매도'} ${Math.abs(move)}</b> → ${position(t.to)}` : `그대로 ${position(t.to)}`}</td><td>${fmtPrice(t.price)}</td></tr>`;
  }).join('')}</tbody></table>` : `<p class="empty">아직 자동매매가 없습니다. 첫 결정은 ${fmtTime(d.next_decision)}입니다.</p>`;
  renderMarkers();
}

// 추천 포지션과 근거: 지금 신호(계약 수)와, 일봉 규칙이 마지막 종가에서 본 값, 장중 갈래의 마지막 판단과 오늘의 경계
function renderWhy(c, d) {
  $('why-note').textContent = `${c.name.split(' · ')[0]}${c.key === d.live ? ' · 자동 주문이 따라가는 후보' : ''}`;
  const list = c.explain.map(([k, v]) => [k, v]);
  if (c.noise) {
    const last = c.trades.find((t) => t.sleeve === 'intraday');
    const n = c.noise;
    const hour = Math.min(15, Math.max(10, nyHour() + 1));
    list.push(['장중 갈래', last ? `${fmtTime(last.time)} ${last.why}` : '아직 판단 없음']);
    list.push([n.today ? '오늘 경계' : `${n.date.slice(5)} 경계`, n.bands[hour]
      ? `9시 시가 ${fmtPrice(n.open)} → ${hour}시에 <span class="c-up">${fmtPrice(n.bands[hour][0])}</span> 위면 롱, <span class="c-down">${fmtPrice(n.bands[hour][1])}</span> 아래면 숏, 사이면 쉼`
      : '-']);
    if (c.parts) list.push(['합치기', `${partsText(c.parts)} → 더해서 최대 1계약`]);
  }
  $('why').innerHTML = `<div class="pos-big">${position(c.contracts)}</div>`
    + `<p class="why-sub">일봉 신호 노출 ${c.signal.expo.toFixed(2)}배 · ${d.close[0]} 나스닥100 종가 기준</p>`
    + rows(list) + '<p class="why-foot">일봉 규칙의 값은 나스닥100 지수 기준입니다. 차트의 200일선·5일 최저선은 차트의 MNQ 선물로 계산해 수준이 조금 다릅니다.</p>';
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
  $('quote-note').textContent = `${q.time.slice(4, 6)}-${q.time.slice(6, 8)} ${q.time.slice(8, 10)}:${q.time.slice(10, 12)}:${q.time.slice(12, 14)} 기준`;
  $('quote').innerHTML = rows([
    ['시가 · 고가 · 저가', `${fmtPrice(q.open)} · <span class="c-up">${fmtPrice(q.high)}</span> · <span class="c-down">${fmtPrice(q.low)}</span>`],
    ['전일 종가', fmtPrice(q.prev)],
    ['거래량', `${q.volume.toLocaleString('en-US')}계약`],
    ['1계약', `가격 × $2 = <b>${fmtMoney(q.price * 2, 0)}</b> · 1틱 ${q.tick} = ${fmtMoney(q.tick_value)}`],
    ['개시증거금', `${fmtMoney(q.margin, 0)} / 계약`],
    ['만기', `${q.expiry.slice(0, 4)}-${q.expiry.slice(4, 6)}-${q.expiry.slice(6)}`],
  ]);
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
  const name = state.trading ? (state.trading.candidates.find((c) => c.key === d.candidate) || {}).name || d.candidate : d.candidate;
  $('live-note').textContent = d.enabled ? (d.started ? '켜짐' : `${fmtTime(d.start)} 대회 시작부터`) : '꺼짐(작업 표시줄 아이콘에서 켬)';
  const orders = d.orders.slice(0, 5).map((o) => `<tr><td>${fmtTime(o.time)}</td><td>${o.ok ? `<b class="${tone(o.qty)}">${o.manual ? '직접 ' : ''}${o.qty > 0 ? '매수' : '매도'} ${Math.abs(o.qty)}</b> (${o.from} → ${o.to})` : `<span class="c-down">실패</span> ${o.error || ''}`}</td></tr>`).join('');
  $('trade-buttons').querySelectorAll('button').forEach((b) => {
    b.disabled = !d.started;
    b.title = d.started ? '' : '대회 시작부터 누를 수 있습니다';
  });
  $('live').innerHTML = rows([
    ['자동 주문', `${d.enabled ? '<b class="c-up">켜짐</b>' : '<b>꺼짐</b> <small>(작업 표시줄 아이콘에서 켬)</small>'} · ${name.split(' · ')[0]} 따라감`],
    ['목표 / 실제', d.target == null ? '-' : `${position(d.target)} / ${position(d.actual)}`],
    ['상태', d.error ? `<span class="c-down">${d.error}</span>` : d.note || '-'],
  ]) + (orders ? `<table class="trend-table"><tbody>${orders}</tbody></table>` : '<p class="empty">아직 주문이 없습니다.</p>');
}

// 직접 주문: 시장가로 바로 나가고 자동 주문은 꺼진다(켜 두면 30초 뒤 목표 계약 수로 되돌리기 때문)
$('trade-buttons').addEventListener('click', async (e) => {
  const b = e.target.closest('button');
  if (!b) return;
  const flat = b.hasAttribute('data-flatten'), qty = Number(b.dataset.qty);
  if (!confirm(flat ? '모든 포지션을 시장가로 정리하고 자동 주문을 멈출까요?'
    : `MNQ ${Math.abs(qty)}계약을 시장가로 ${qty > 0 ? '매수' : '매도'}할까요?\n자동 주문은 꺼집니다(아이콘 메뉴에서 다시 켜면 목표 계약 수로 되돌립니다).`)) return;
  b.disabled = true;
  try {
    await (flat ? post('/api/live/flatten', {}) : post('/api/live/order', { qty }));
  } catch (err) {
    alert('주문하지 못했습니다: ' + err.message);
  }
  api('/api/live').then(renderLive).catch(() => {});
  tickAccount();
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
chooseInterval(state.interval);
tickTrading();
tickAccount();
setInterval(tickBars, 500);
setInterval(tickTrading, 15000);
setInterval(tickAccount, 5000);
