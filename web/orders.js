// 주문 기록: LS 모의계좌에 낸 주문을 시간 순서로 따라가며 평균 진입가와 실현 손익을 계산한다
const $ = (id) => document.getElementById(id);
const fmtPrice = (v) => (v == null ? '-' : v.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 }));
const fmtMoney = (v) => (v < 0 ? '-$' : '$') + Math.abs(v).toLocaleString('en-US', { maximumFractionDigits: 2 });
const fmtSigned = (v, digits = 2) => (v >= 0 ? '+' : '') + (v * 100).toFixed(digits) + '%';
const tone = (v) => (v > 0 ? 'c-up' : v < 0 ? 'c-down' : '');
const side = (n) => (n > 0 ? `롱 ${n}` : n < 0 ? `숏 ${-n}` : '0');
const fmtTime = (sec) => new Date(sec * 1000).toLocaleString('ko-KR', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit' });

// 체결마다: 같은 방향이면 평균 진입가를 옮기고, 반대 방향이면 닫은 몫의 손익을 낸다(넘치면 반대로 새로 연다)
function walk(orders, mult, fee) {
  let pos = 0, avg = 0;
  return orders.map((o) => {
    const qty = o.exec_qty != null ? Math.sign(o.qty) * o.exec_qty : o.ok ? o.qty : 0;  // LS 체결 수량이 없으면 낸 수량이 다 체결됐다고 본다
    const px = o.exec_price || o.price;
    let realized = null;
    if (qty && px) {
      if (pos && Math.sign(qty) !== Math.sign(pos)) {
        const closed = Math.min(Math.abs(qty), Math.abs(pos));
        realized = closed * Math.sign(pos) * (px - avg) * mult;
        const left = pos + qty;
        avg = Math.sign(left) === Math.sign(pos) || !left ? avg : px;  // 뒤집혔으면 넘친 몫은 체결가에 새로 연다
        pos = left;
      } else {
        avg = (avg * Math.abs(pos) + px * Math.abs(qty)) / (Math.abs(pos) + Math.abs(qty));
        pos += qty;
      }
    }
    return { ...o, filled: qty, px, realized, fee: Math.abs(qty) * 2 * fee, pos };
  });
}

// 체결 표: 최근 것부터 PAGE건씩 쪽으로 나눈다. 15초마다 새로 받아도 보던 쪽은 그대로
const PAGE = 10;
let table = [], page = 0;
function renderRows() {
  const pages = Math.max(1, Math.ceil(table.length / PAGE));
  page = Math.min(page, pages - 1);
  $('rows').innerHTML = table.length ? table.slice(page * PAGE, page * PAGE + PAGE).map((r) => {
    const slip = r.exec_price && r.price && !r.limit ? Math.sign(r.qty) * (r.exec_price - r.price) / 0.25 : null;  // + = 불리
    const state = !r.ok ? `<span class="c-down">실패</span> ${r.error || ''}` : r.unfilled ? `미체결 ${r.unfilled}` : r.canceled ? `취소 ${r.canceled}` : r.state ? '체결' : '체결(추정)';
    return `<tr><td${r.order_time ? ` title="주문 낸 시각 ${fmtTime(r.order_time)}"` : ''}>${fmtTime(r.time)}</td><td>${r.kind}</td><td><b class="${tone(r.qty)}">${r.qty > 0 ? '매수' : '매도'} ${Math.abs(r.qty)}</b></td>`
      + `<td>${r.from != null ? `${side(r.from)} → ${side(r.to)}` : `→ ${side(r.pos)}`}</td><td>${fmtPrice(r.price)}</td><td>${r.exec_price ? fmtPrice(r.exec_price) : '-'}</td>`
      + `<td class="${slip > 0 ? 'c-down' : slip < 0 ? 'c-up' : ''}">${slip == null ? '-' : `${slip > 0 ? '+' : ''}${slip.toFixed(1)}틱`}</td>`
      + `<td>${r.realized == null ? '-' : `<b class="${tone(r.realized)}">${fmtMoney(r.realized)}</b>`}</td><td>${r.filled ? fmtMoney(r.fee) : '-'}</td><td>${state}</td><td>${r.number || '-'}</td></tr>`;
  }).join('') : '<tr><td colspan="11" class="empty">아직 주문이 없습니다.</td></tr>';
  $('pager').hidden = pages < 2;
  $('pager').innerHTML = `<button type="button" class="plain-button" data-go="-1"${page ? '' : ' disabled'}>← 최근</button>`
    + `<span>${page + 1} / ${pages} 쪽 · ${table.length}건</span>`
    + `<button type="button" class="plain-button" data-go="1"${page < pages - 1 ? '' : ' disabled'}>이전 →</button>`;
}
$('pager').addEventListener('click', (e) => {
  const go = Number(e.target.dataset.go);
  if (!go) return;
  page += go;
  renderRows();
});

function render(d) {
  const rows = walk(d.orders, d.multiplier, d.fee);
  const fills = rows.filter((r) => r.filled);
  const closes = rows.filter((r) => r.realized != null);
  const gross = closes.reduce((s, r) => s + r.realized, 0), fees = fills.reduce((s, r) => s + r.fee, 0);
  const wins = closes.filter((r) => r.realized > 0).length;
  const stat = (label, value, extra = '') => `<div><span>${label}</span><b>${value}</b>${extra ? `<em>${extra}</em>` : ''}</div>`;
  const broker = fees / 2;  // fees = 체결 1계약당 증권사 $1 + 대회 차감 $1
  $('summary').innerHTML = [
    stat('대회 기준 손익', `<span class="${tone(gross - fees)}">${fmtMoney(gross - fees)}</span>`, `${fmtSigned((gross - fees) / d.capital, 2)} · 대회 차감 ${fmtMoney(broker)}까지 뺌`),
    stat('실현 손익 (증권사 수수료 뺌)', `<span class="${tone(gross - broker)}">${fmtMoney(gross - broker)}</span>`, `LS 계좌와 같은 값 · 수수료 전 ${fmtMoney(gross)}, 수수료 ${fmtMoney(broker)}`),
    stat('체결', `${fills.length}건`, `${fills.reduce((s, r) => s + Math.abs(r.filled), 0)}계약`),
    stat('청산 승률', closes.length ? `${Math.round((wins / closes.length) * 100)}%` : '-', `${wins} / ${closes.length}`),
  ].join('');
  table = rows.slice().reverse();
  renderRows();
  renderCalendar(rows, d);
  $('updated').textContent = `${new Date().toLocaleTimeString('ko-KR')} 조회`;
  return renderChart(rows);
}

// 대회 달력: 한국 날짜마다 닫은 몫의 손익 - 그날 체결의 수수료(대회 기준). 대회 첫 주 월요일부터 마지막 날이 든 주까지, 주마다 한 줄
const kstDay = (sec) => new Date((sec + 9 * 3600) * 1000).toISOString().slice(0, 10);
function renderCalendar(rows, d) {
  const days = new Map();
  rows.filter((r) => r.filled).forEach((r) => {
    const k = kstDay(r.time), v = days.get(k) || { net: 0, fills: 0 };
    v.net += (r.realized || 0) - r.fee;
    v.fills += 1;
    days.set(k, v);
  });
  const first = kstDay(d.start), last = kstDay(d.end), today = kstDay(Date.now() / 1000);
  const day = new Date(first + 'T00:00:00Z');
  day.setUTCDate(day.getUTCDate() - ((day.getUTCDay() + 6) % 7));  // 그 주 월요일
  const cell = (net, extra) => `<b class="${tone(net)}">${net > 0 ? '+' : ''}${fmtMoney(net)}</b><small class="${tone(net)}">${fmtSigned(net / d.capital, 2)}</small>${extra}`;
  const weeks = [];
  while (day.toISOString().slice(0, 10) <= last) {
    let week = 0, cells = '', begun = false;
    for (let i = 0; i < 7; i++, day.setUTCDate(day.getUTCDate() + 1)) {
      const k = day.toISOString().slice(0, 10), v = days.get(k), out = k < first || k > last;
      week += v ? v.net : 0;
      begun = begun || (k >= first && k <= today);
      const cls = [out ? 'out' : '', k === today ? 'today' : '', v ? (v.net > 0 ? 'win' : v.net < 0 ? 'loss' : '') : ''].join(' ').trim();
      cells += `<td class="${cls}"><span class="cal-date">${day.getUTCMonth() + 1}.${day.getUTCDate()}</span>`
        + (v ? cell(v.net, `<em>체결 ${v.fills}건</em>`) : out || k > today ? '' : '<small>거래 없음</small>') + '</td>';
    }
    weeks.push(`<tr>${cells}<td class="cal-week">${begun ? cell(week, '') : ''}</td></tr>`);  // 아직 안 온 주는 비운다
  }
  $('cal').innerHTML = weeks.join('');
}

// 매매 차트: 첫 체결 ~ 마지막 체결 구간의 1분봉에 체결을 화살표로
const token = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const TZ = -new Date().getTimezoneOffset() * 60;  // lightweight-charts는 UTC로 그리므로 브라우저 시각으로 옮긴다(실시간 차트와 같게)
let tradeChart = null, tradeSeries = null, shown = [], labels = [], barAt = new Map();
function chartTheme() {
  tradeChart.applyOptions({
    layout: { textColor: token('--text-2') }, grid: { vertLines: { color: token('--grid') }, horzLines: { color: token('--grid') } },
    rightPriceScale: { borderColor: token('--border') }, timeScale: { borderColor: token('--border') },
  });
  tradeSeries.applyOptions({ upColor: token('--long'), downColor: token('--short'), wickUpColor: token('--long'), wickDownColor: token('--short') });
}
async function renderChart(rows) {
  shown = rows;
  const fills = rows.filter((r) => r.filled && r.px);
  if (!fills.length) {
    $('trade-chart').innerHTML = '<p class="empty">아직 체결이 없습니다.</p>';
    return;
  }
  const first = fills[0].time, last = fills[fills.length - 1].time;
  const minutes = 1;  // 1분봉만(사용자 요청, 10-20)
  const step = minutes * 60, pad = 6 * step;
  let bars;
  try {
    const load = async (path) => { const r = await fetch(path); if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); };
    bars = (await load(`/api/chart?interval=${minutes}`)).bars;
    // 서버는 켠 뒤 최근 1분봉 1,000개쯤만 들고 있다: 첫 체결까지 모자라면 이전 봉을 1,000개씩 더 받게 한다(서버에 쌓여 다음부터는 바로 온다)
    for (let k = 0; k < 60 && bars.length && bars[0].time > first - pad; k++) {
      if ((await load(`/api/chart/older?interval=${minutes}`)).done) break;
      bars = (await load(`/api/chart?interval=${minutes}`)).bars;
    }
    bars = bars.filter((b) => b.time >= first - pad && b.time <= last + pad);
  } catch (e) {
    $('trade-chart-note').textContent = '차트를 불러오지 못했습니다: ' + e.message;
    return;
  }
  if (!tradeChart) {
    tradeChart = LightweightCharts.createChart($('trade-chart'), {
      autoSize: true, layout: { background: { color: 'transparent' }, fontFamily: 'inherit', fontSize: 13 },
      timeScale: { timeVisible: true, secondsVisible: false }, crosshair: { mode: LightweightCharts.CrosshairMode.Normal }, localization: { locale: 'ko-KR' },
    });
    tradeSeries = tradeChart.addCandlestickSeries({ borderVisible: false, priceFormat: { type: 'custom', minMove: 0.25, formatter: fmtPrice } });
    tradeSeries.priceScale().applyOptions({ scaleMargins: { top: 0.18, bottom: 0.18 } });  // 위아래로 띄운 글자가 들어갈 자리
    chartTheme();
    // 끌기·확대 때 글자 자리를 다시 잡는다. 가격 축은 범위가 바뀐 뒤 다음 그리기에서 다시 맞춰지므로 두 프레임 뒤에 잰다
    const later = () => requestAnimationFrame(() => requestAnimationFrame(renderLabels));
    tradeChart.timeScale().subscribeVisibleLogicalRangeChange(later);
    new ResizeObserver(later).observe($('trade-chart'));
    document.addEventListener('themechange', () => { chartTheme(); renderChart(shown); });  // 화살표 색도 새 테마로
  }
  const fresh = !tradeSeries.data().length;
  const data = bars.map((b) => ({ ...b, time: b.time + TZ }));
  tradeSeries.setData(data);
  barAt = new Map(data.map((b) => [b.time, b]));
  labels = fills.map((r) => {
    const buy = r.filled > 0;
    return {
      time: Math.floor(r.time / step) * step + TZ, buy,
      color: token(r.realized != null ? (r.realized >= 0 ? '--accent' : '--loss') : buy ? '--long' : '--short'),
      text: r.realized != null ? `정리 ${r.realized >= 0 ? '+' : ''}${fmtMoney(r.realized)}` : side(r.pos),
    };
  });
  // 화살표만 차트에 두고 글자는 아래 renderLabels가 그린다(차트 글자는 크기를 못 바꾸고 서로 겹친다)
  tradeSeries.setMarkers(labels.map((l) => ({ time: l.time, position: l.buy ? 'belowBar' : 'aboveBar', shape: l.buy ? 'arrowUp' : 'arrowDown', color: l.color })));
  if (fresh) tradeChart.timeScale().fitContent();
  requestAnimationFrame(() => requestAnimationFrame(renderLabels));  // 새 봉으로 가격 축이 다시 맞춰진 뒤에
  $('trade-chart-note').textContent = `${fmtTime(first)} ~ ${fmtTime(last)} · ${minutes}분봉 · ▲ 매수 · ▼ 매도 · 한국 시각`;
}

// 체결 글자: 봉 끝에서 떨어뜨려(매수는 아래, 매도는 위) 그리고, 이미 놓인 글자 칸과 겹치면 한 층씩 더 띄운다. 띄운 글자는 화살표까지 가는 가는 선으로 잇는다
const LABEL_GAP = 30, LABEL_STEP = 23, LABEL_H = 20, LABEL_PAD = 3;
const measure = document.createElement('canvas').getContext('2d');
function renderLabels() {
  const svg = $('trade-labels');
  if (!tradeChart || !labels.length) { svg.innerHTML = ''; return; }
  const ts = tradeChart.timeScale(), width = ts.width(), placed = [];
  measure.font = `700 13px ${getComputedStyle(document.body).fontFamily}`;
  const hits = (r) => placed.some((p) => r.x1 < p.x2 + LABEL_PAD && r.x2 > p.x1 - LABEL_PAD && r.y1 < p.y2 + LABEL_PAD && r.y2 > p.y1 - LABEL_PAD);
  const out = labels.map((l) => {
    const x = ts.timeToCoordinate(l.time), b = barAt.get(l.time);
    if (x == null || !b || x < 0 || x > width) return '';
    const edge = tradeSeries.priceToCoordinate(l.buy ? b.low : b.high);
    if (edge == null) return '';
    const w = Math.ceil(measure.measureText(l.text).width) + 14;
    const lx = Math.min(Math.max(x, w / 2 + 2), width - w / 2 - 2);  // 차트 양 끝에서 잘리지 않게 안으로
    const dir = l.buy ? 1 : -1;
    let cy, r;
    for (let k = 0; k < 12; k++) {
      cy = edge + dir * (LABEL_GAP + k * LABEL_STEP);
      r = { x1: lx - w / 2, x2: lx + w / 2, y1: cy - LABEL_H / 2, y2: cy + LABEL_H / 2 };
      if (!hits(r)) break;
    }
    placed.push(r);
    const tip = edge + dir * 16;  // 화살표 끝 근처
    return `<line x1="${x}" y1="${tip}" x2="${x}" y2="${cy - dir * LABEL_H / 2}" stroke="${l.color}" stroke-opacity=".5"/>`
      + `<rect x="${r.x1}" y="${r.y1}" width="${w}" height="${LABEL_H}" rx="4" fill="${token('--panel')}" stroke="${l.color}"/>`
      + `<text x="${lx}" y="${cy + 4.5}" text-anchor="middle" fill="${l.color}" font-size="13" font-weight="700">${l.text}</text>`;
  });
  // 선을 먼저, 글자 칸을 나중에 그려 다른 글자의 연결선이 칸을 가로지르지 않게
  const lines = out.map((o) => o.slice(0, o.indexOf('/>') + 2)).join(''), boxes = out.map((o) => o.slice(o.indexOf('/>') + 2)).join('');
  svg.innerHTML = `<svg x="0" y="0" width="${width}" overflow="hidden">${lines}${boxes}</svg>`;
}

async function tick() {
  try {
    const r = await fetch('/api/live/history');
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    await render(await r.json());
  } catch (e) {
    $('updated').textContent = '불러오지 못했습니다: ' + e.message;
  }
}
$('refresh').addEventListener('click', async () => {
  $('refresh').disabled = true;
  await tick();
  $('refresh').disabled = false;
});
tick();
setInterval(tick, 15000);
