// 타임랩스: 주문 기록의 체결을 포지션 0 → 0 한 번(끝난 매매)씩 묶어, 진입 ~ 청산의 1분봉을 하나씩 다시 그린다
const $ = (id) => document.getElementById(id);
const fmtPrice = (v) => (v == null ? '-' : v.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 }));
const fmtMoney = (v) => (v < 0 ? '-$' : '$') + Math.abs(v).toLocaleString('en-US', { maximumFractionDigits: 2 });
const tone = (v) => (v > 0 ? 'c-up' : v < 0 ? 'c-down' : '');
const side = (n) => (n > 0 ? `롱 ${n}` : n < 0 ? `숏 ${-n}` : '0');
const fmtTime = (sec) => new Date(sec * 1000).toLocaleString('ko-KR', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' });
const fmtSpan = (sec) => (sec >= 3600 ? `${Math.floor(sec / 3600)}시간 ` : '') + `${Math.round((sec % 3600) / 60)}분`;
const token = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const TZ = -new Date().getTimezoneOffset() * 60;  // 주문 기록 차트와 같게 브라우저 시각으로 그린다
const STEP = 60, BEFORE = 30 * STEP, AFTER = 10 * STEP;
const barOf = (sec) => Math.floor(sec / STEP) * STEP;

// 끝난 매매: 포지션이 0으로 돌아오거나 반대로 뒤집히면 끝. 뒤집은 체결은 닫은 몫의 수수료만 앞 매매에, 나머지는 새 매매에 넣는다
function roundTrips(rows) {
  const out = [];
  let cur = null;
  for (const r of rows) {
    if (!r.filled || !r.px) continue;
    const before = r.pos - r.filled, n = Math.abs(r.filled);
    if (!cur) cur = { fills: [], gross: 0, fee: 0, max: 0, dir: Math.sign(r.pos) };
    cur.fills.push(r);
    cur.gross += r.realized || 0;
    const flip = before && r.pos && Math.sign(r.pos) !== Math.sign(before);
    cur.max = Math.max(cur.max, Math.abs(before), flip ? 0 : Math.abs(r.pos));
    cur.fee += flip ? r.fee * r.closed / n : r.fee;
    if (flip || !r.pos) {
      out.push(cur);
      cur = flip ? { fills: [r], gross: 0, fee: r.fee * (n - r.closed) / n, max: Math.abs(r.pos), dir: Math.sign(r.pos) } : null;
    }
  }
  return out;  // 아직 열려 있는 cur는 끝나지 않았으므로 뺀다
}

let trips = [], mult = 2, picked = null;
async function loadTrips() {
  try {
    const r = await fetch('/api/live/history');
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const d = await r.json();
    mult = d.multiplier;
    trips = roundTrips(walk(d.orders, d.multiplier, d.fee)).reverse();
    $('updated').textContent = `${new Date().toLocaleTimeString('ko-KR')} 조회`;
  } catch (e) {
    $('updated').textContent = '불러오지 못했습니다: ' + e.message;
    return;
  }
  $('trips').innerHTML = trips.length ? trips.map((t, i) => {
    const a = t.fills[0], z = t.fills.at(-1), net = t.gross - t.fee;
    return `<tr data-i="${i}" tabindex="0" class="${picked === t.fills[0].time ? 'picked' : ''}"><td>${fmtTime(a.time)}</td><td>${fmtTime(z.time)}</td>`
      + `<td><b class="${t.dir > 0 ? 'c-up' : 'c-down'}">${t.dir > 0 ? '롱' : '숏'}</b></td><td>${t.max}</td><td>${fmtPrice(a.px)}</td><td>${fmtPrice(z.px)}</td>`
      + `<td>${fmtSpan(z.time - a.time)}</td><td>${t.fills.length}건</td><td><b class="${tone(net)}">${fmtMoney(net)}</b></td></tr>`;
  }).join('') : '<tr><td colspan="9" class="empty">아직 끝난 매매가 없습니다.</td></tr>';
}
const choose = (e) => {
  const tr = e.target.closest('tr[data-i]');
  if (tr) open(trips[Number(tr.dataset.i)]);
};
$('trips').addEventListener('click', choose);
$('trips').addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); choose(e); } });

// 차트(실시간 차트와 같은 모양)
const chart = LightweightCharts.createChart($('tl-chart'), {
  autoSize: true, layout: { background: { color: 'transparent' }, fontFamily: 'inherit', fontSize: 13 },
  timeScale: { timeVisible: true, secondsVisible: false }, crosshair: { mode: LightweightCharts.CrosshairMode.Normal }, localization: { locale: 'ko-KR' },
});
const series = chart.addCandlestickSeries({ borderVisible: false, priceFormat: { type: 'custom', minMove: 0.25, formatter: fmtPrice } });
function theme() {
  chart.applyOptions({
    layout: { textColor: token('--text-2') }, grid: { vertLines: { color: token('--grid') }, horzLines: { color: token('--grid') } },
    rightPriceScale: { borderColor: token('--border') }, timeScale: { borderColor: token('--border') },
  });
  series.applyOptions({ upColor: token('--long'), downColor: token('--short'), wickUpColor: token('--long'), wickDownColor: token('--short') });
}
theme();
document.addEventListener('themechange', () => { theme(); if (bars.length) draw(); });

// 서버는 최근 1분봉만 들고 있으므로 진입 30분 전까지 모자라면 이전 봉을 더 받게 한다(주문 기록 차트와 같은 방식)
async function barsFor(from, to) {
  const load = async (path) => { const r = await fetch(path); if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); };
  let all = (await load('/api/chart?interval=1')).bars;
  for (let k = 0; k < 60 && all.length && all[0].time > from; k++) {
    if ((await load('/api/chart/older?interval=1')).done) break;
    all = (await load('/api/chart?interval=1')).bars;
  }
  return all.filter((b) => b.time >= from && b.time <= to);
}

let bars = [], trip = null, k = 0, playing = false, avgLine = null;
async function open(t) {
  stop();
  picked = t.fills[0].time;
  document.querySelectorAll('#trips tr').forEach((tr) => tr.classList.toggle('picked', trips[tr.dataset.i] === t));
  const a = t.fills[0], z = t.fills.at(-1);
  $('tl-title').textContent = `${t.dir > 0 ? '롱' : '숏'} · ${fmtTime(a.time)} ~ ${fmtTime(z.time)} 불러오는 중…`;
  try {
    bars = await barsFor(barOf(a.time) - BEFORE, barOf(z.time) + AFTER);
  } catch (e) {
    $('tl-title').textContent = '1분봉을 불러오지 못했습니다: ' + e.message;
    return;
  }
  if (!bars.length) { $('tl-title').textContent = '그 구간의 1분봉이 없습니다.'; return; }
  trip = t;
  const net = t.gross - t.fee;
  $('tl-title').innerHTML = `${t.dir > 0 ? '롱' : '숏'} · ${fmtTime(a.time)} ~ ${fmtTime(z.time)} <small>${fmtSpan(z.time - a.time)} · 대회 기준 <b class="${tone(net)}">${fmtMoney(net)}</b></small>`;
  $('seek').max = bars.length - 1;
  $('seek').disabled = $('play').disabled = false;
  k = Math.max(0, bars.findIndex((b) => b.time >= barOf(a.time) - 5 * STEP));  // 진입 5분 전부터 재생(그 앞 25분은 바로 보여 준다)
  draw();
  chart.timeScale().fitContent();
  play();
}

// k번째 봉까지 그리고, 뒤는 빈 자리로 둬서 시간 축이 매매 구간 전체로 고정되게 한다
function draw() {
  const t = bars[k].time;
  series.setData(bars.map((b, i) => (i <= k ? { ...b, time: b.time + TZ } : { time: b.time + TZ })));
  const have = new Set(bars.map((b) => b.time));
  const done = trip.fills.filter((f) => barOf(f.time) <= t);
  series.setMarkers(done.filter((f) => have.has(barOf(f.time))).map((f) => {
    const buy = f.filled > 0;
    return {
      time: barOf(f.time) + TZ, position: buy ? 'belowBar' : 'aboveBar', shape: buy ? 'arrowUp' : 'arrowDown',
      color: token(f.realized != null ? (f.realized >= 0 ? '--accent' : '--loss') : buy ? '--long' : '--short'),
      text: f.realized != null ? `${f.realized >= 0 ? '+' : ''}${fmtMoney(f.realized)}` : `${buy ? '매수' : '매도'} ${Math.abs(f.filled)}`,
    };
  }));
  const last = done.at(-1), pos = last ? last.pos : 0, close = bars[k].close;
  const realized = done.reduce((s, f) => s + (f.realized || 0), 0), open_ = pos ? pos * (close - last.avg) * mult : 0;
  if (avgLine) { series.removePriceLine(avgLine); avgLine = null; }
  if (pos) avgLine = series.createPriceLine({ price: last.avg, color: token(pos > 0 ? '--long' : '--short'), lineStyle: LightweightCharts.LineStyle.Dashed, lineWidth: 1, title: '평균 진입가' });
  const stat = (label, value, extra = '') => `<div><span>${label}</span><b>${value}</b>${extra ? `<em>${extra}</em>` : ''}</div>`;
  $('hud').innerHTML = [
    stat('시각', new Date(t * 1000).toLocaleTimeString('ko-KR', { hour: '2-digit', minute: '2-digit' }), `${k + 1} / ${bars.length}봉`),
    stat('포지션', side(pos), pos ? `평균 ${fmtPrice(last.avg)}` : ''),
    stat('평가 손익', pos ? `<span class="${tone(open_)}">${fmtMoney(open_)}</span>` : '-', `종가 ${fmtPrice(close)}`),
    stat('실현 손익', `<span class="${tone(realized)}">${fmtMoney(realized)}</span>`, '수수료 전'),
  ].join('');
  $('seek').value = k;
}

// 재생: 정해진 속도(초당 봉 수)로 k를 옮긴다. 화면 갱신마다 지난 시간만큼 나아가므로 탭이 느려도 속도는 같다
let raf = 0, carry = 0, prev = 0;
function frame(now) {
  carry += ((now - prev) / 1000) * Number($('speed').value);
  prev = now;
  const n = Math.floor(carry);
  if (n) {
    carry -= n;
    k = Math.min(bars.length - 1, k + n);
    draw();
  }
  if (k >= bars.length - 1) return stop();
  raf = requestAnimationFrame(frame);
}
function play() {
  if (k >= bars.length - 1) { k = 0; draw(); }  // 끝까지 봤으면 처음부터
  playing = true;
  carry = 0;
  prev = performance.now();
  $('play').textContent = '⏸ 멈춤';
  raf = requestAnimationFrame(frame);
}
function stop() {
  playing = false;
  cancelAnimationFrame(raf);
  $('play').textContent = '▶ 재생';
}
$('play').addEventListener('click', () => (playing ? stop() : play()));
$('seek').addEventListener('input', () => { stop(); k = Number($('seek').value); draw(); });

loadTrips();
setInterval(loadTrips, 60000);  // 새로 끝난 매매가 목록에 붙게
