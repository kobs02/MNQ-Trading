// 실시간 차트 페이지: MNQ 근월물 봉을 2초마다 이어 그리고, 고른 후보의 자동매매(가상 계좌가 계약 수를 바꾼 때)를 화살표로 표시한다.
// 오른쪽 칸에는 그 후보의 포지션, 다음 자동매매, 시세, 매매 기록을 보여 준다.

const $ = (id) => document.getElementById(id);
const token = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const fmtPrice = (v) => (v == null ? '-' : v.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 }));
const fmtMoney = (v, digits = 2) => (v < 0 ? '-$' : '$') + Math.abs(v).toLocaleString('en-US', { maximumFractionDigits: digits });
const fmtSigned = (v, digits = 2) => (v == null ? '-' : (v >= 0 ? '+' : '') + (v * 100).toFixed(digits) + '%');
const fmtTime = (sec) => new Date(sec * 1000).toLocaleString('ko-KR', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' });
const tone = (v) => (v > 0 ? 'c-up' : v < 0 ? 'c-down' : '');
const position = (n) => (n > 0 ? `<span class="c-up">롱 ${n}계약</span>` : n < 0 ? `<span class="c-down">숏 ${-n}계약</span>` : '없음');
const INTERVALS = { 1: '1분', 5: '5분', 15: '15분', 60: '1시간' };
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
  generation: 0,     // 봉 단위를 바꿀 때마다 늘린다. 늦게 온 이전 응답을 버리는 용도
};

async function api(path) {
  const res = await fetch(path);
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

// ---------- 봉 ----------
const shifted = (b) => ({ ...b, time: b.time + TZ });

async function loadBars(fresh) {
  const gen = state.generation;
  const d = await api(`/api/chart?interval=${state.interval}`);
  if (gen !== state.generation) return;
  state.quote = d.quote;
  if (fresh || state.last == null) {
    series.setData(d.bars.map(shifted));
    chart.timeScale().scrollToRealTime();
  } else {
    d.bars.filter((b) => b.time >= state.last).forEach((b) => series.update(shifted(b)));
  }
  state.bars = d.bars;
  state.last = d.bars.length ? d.bars[d.bars.length - 1].time : null;
  $('chart-msg').hidden = d.bars.length > 0;
  renderQuote();
  renderMarkers();
  if (!legendHover) renderLegend(d.bars[d.bars.length - 1]);
}

let legendHover = false;
function renderLegend(b) {
  if (!b) { $('legend').innerHTML = ''; return; }
  const change = (b.close - b.open) / b.open;
  $('legend').innerHTML = `<b>${INTERVALS[state.interval]}</b>시 ${fmtPrice(b.open)} · 고 ${fmtPrice(b.high)} · 저 ${fmtPrice(b.low)} · 종 ${fmtPrice(b.close)}
    <span class="${tone(change)}">${fmtSigned(change, 2)}</span> · 거래량 ${b.volume.toLocaleString('en-US')}`;
}
chart.subscribeCrosshairMove((p) => {
  const b = p.time ? state.bars.find((x) => x.time + TZ === p.time) : null;
  legendHover = Boolean(b);
  renderLegend(b || state.bars[state.bars.length - 1]);
});

// ---------- 자동매매 표시 ----------
const current = () => state.trading && (state.trading.candidates.find((c) => c.key === state.candidate) || state.trading.candidates[state.trading.candidates.length - 1]);

function renderMarkers() {
  const c = current();
  if (!c || !state.bars.length) return;
  const step = state.interval * 60;
  const first = state.bars[0].time;
  const markers = c.trades.filter((t) => t.to !== t.from && t.time >= first).map((t) => {
    const buy = t.to > t.from;
    return {
      time: Math.floor(t.time / step) * step + TZ, position: buy ? 'belowBar' : 'aboveBar', shape: buy ? 'arrowUp' : 'arrowDown',
      color: buy ? UP : DOWN, text: `${buy ? '매수' : '매도'} ${Math.abs(t.to - t.from)} → ${t.to > 0 ? '롱' : t.to < 0 ? '숏' : '없음'}${t.to ? ' ' + Math.abs(t.to) : ''}`,
    };
  }).sort((a, b) => a.time - b.time);
  series.setMarkers(markers);
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
    ['평균 진입가', c.entry && c.contracts ? fmtPrice(c.entry) : '-'],
    ['평가손익', c.contracts ? `<b class="${tone(pnl)}">${fmtMoney(pnl)}</b>` : '-'],
    ['노출', `${(notional / c.equity).toFixed(2)}배 · ${fmtMoney(notional, 0)}`],
    ['가상 계좌', `<b>${fmtMoney(c.equity)}</b> <span class="${tone(c.equity - d.capital)}">${fmtSigned(c.equity / d.capital - 1)}</span>`],
  ]);

  const want = c.signal.contracts;
  const move = want == null ? null : want - c.contracts;
  $('next').innerHTML = rows([
    ['결정 시각', `<b>${fmtTime(d.next_decision)}</b>`],
    ['지금 신호', `${position(want || 0)} <small>(노출 ${c.signal.expo.toFixed(2)}배, ${d.close[0]} 종가 기준)</small>`],
    ['지금 결정하면', move == null ? '-' : move === 0 ? '그대로' : `<b class="${tone(move)}">${move > 0 ? '매수' : '매도'} ${Math.abs(move)}계약</b>`],
  ]) + (d.error ? `<div class="note warn">${d.error}</div>` : '');

  $('trades').innerHTML = c.trades.length ? `<table class="trend-table"><thead><tr><th>때</th><th>매매</th><th>가격</th></tr></thead><tbody>${c.trades.slice(0, 20).map((t) => {
    const move = t.to - t.from;
    return `<tr><td>${fmtTime(t.time)}</td><td>${move ? `<b class="${tone(move)}">${move > 0 ? '매수' : '매도'} ${Math.abs(move)}</b> → ${position(t.to)}` : `그대로 ${position(t.to)}`}</td><td>${fmtPrice(t.price)}</td></tr>`;
  }).join('')}</tbody></table>` : `<p class="empty">아직 자동매매가 없습니다. 첫 결정은 ${fmtTime(d.next_decision)}입니다.</p>`;
  renderMarkers();
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
async function tickTrading() {
  try {
    state.trading = await api('/api/trading');
    renderSide();
  } catch (e) { /* 다음에 다시 */ }
}

function chooseInterval(key) {
  state.interval = key;
  state.generation += 1;
  state.last = null;
  localStorage.setItem('mnqInterval', key);
  $('intervals').innerHTML = Object.entries(INTERVALS).map(([k, name]) =>
    `<button data-key="${k}" class="${k === key ? 'active' : ''}" aria-pressed="${k === key}">${name}</button>`).join('');
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
setInterval(tickBars, 2000);
setInterval(tickTrading, 15000);
