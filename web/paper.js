// 모의 운용 페이지: 후보 전략마다 $100,000 가상 계좌로 MNQ 근월물을 실제 가격으로 따라간 기록과, 같은 규칙의 과거 시험
// 왼쪽 레일에서 후보를 고르고, 오른쪽에 계좌 → 그래프 → 대회 기준 성적 → 탭(매매 기록, 과거 시험, 후보 비교, 방식과 한계) 순으로 보여 준다.

const $ = (id) => document.getElementById(id);
const fmtMoney = (v, digits = 2) => (v < 0 ? '-$' : '$') + Math.abs(v).toLocaleString('en-US', { maximumFractionDigits: digits });
const fmtPct = (v, digits = 1) => (v == null ? '-' : (v * 100).toFixed(digits) + '%');
const fmtSigned = (v, digits = 1) => (v == null ? '-' : (v >= 0 ? '+' : '') + (v * 100).toFixed(digits) + '%');
const fmtPrice = (v) => (v == null ? '-' : v.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 }));
const tone = (v) => (v > 0 ? 'c-up' : v < 0 ? 'c-down' : '');
const fmtDay = (sec) => new Date(sec * 1000).toLocaleDateString('ko-KR', { year: 'numeric', month: 'numeric', day: 'numeric' });
const fmtTime = (sec) => new Date(sec * 1000).toLocaleString('ko-KR', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' });
const fmtAge = (days) => (days < 1 ? (days * 24).toFixed(1) + '시간' : days.toFixed(1) + '일');
const token = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const TABS = { trades: '매매 기록', backtest: '과거 시험', compare: '후보 비교', method: '방식과 한계' };
const SLEEVES = { daily: '일봉', intraday: '장중' };
// 갈래가 둘인 후보(섞기): "일봉 롱 1 · 장중 숏 1"
const partsText = (parts) => (parts && 'intraday' in parts ? Object.entries(parts).map(([k, n]) => `${SLEEVES[k]} ${n > 0 ? '롱' : n < 0 ? '숏' : ''}${n ? ' ' + Math.abs(n) : '없음'}`).join(' · ') : '');
const lastPeriod = (c) => Object.entries(c.backtest.periods).slice(-1)[0];
const position = (n) => (n > 0 ? `<span class="c-up">롱 ${n}계약</span>` : n < 0 ? `<span class="c-down">숏 ${-n}계약</span>` : '없음');

const state = {
  data: null,
  candidate: localStorage.getItem('mnqCandidate'),
  view: localStorage.getItem('mnqView') === 'backtest' ? 'backtest' : 'live',
  tab: [new URLSearchParams(location.search).get('tab'), localStorage.getItem('mnqTab')].find((key) => TABS[key]) || 'trades',
};

async function api(path) {
  const res = await fetch(path);
  const body = await res.json();
  if (!res.ok) throw new Error(body.error || res.statusText);
  return body;
}

const chart = LightweightCharts.createChart($('paper-chart'), {
  autoSize: true,
  layout: { background: { color: 'transparent' }, textColor: token('--text-2'), fontFamily: 'inherit', fontSize: 13 },
  grid: { vertLines: { color: token('--grid') }, horzLines: { color: token('--grid') } },
  rightPriceScale: { borderColor: token('--border') },
  timeScale: { borderColor: token('--border'), rightOffset: 0, minBarSpacing: 0.001, fixLeftEdge: true, fixRightEdge: true, timeVisible: true },
  localization: { locale: 'ko-KR', priceFormatter: (v) => fmtMoney(v, 0) },
});
let lines = [];

const ret = (c, cap) => (c.equity == null ? null : c.equity / cap - 1);

// ---------- 왼쪽 레일: 후보마다 이름, 지금까지의 수익률, 포지션·기간 ----------
function renderRail(d) {
  $('rail').innerHTML = `<div class="rail-group" role="group" aria-label="후보"><div class="rail-label">MNQ 후보</div>${d.candidates.map((c) => {
    const r = ret(c, d.capital);
    const [short, ...rest] = c.name.split(' · ');
    const on = c.key === state.candidate;
    const facts = c.started ? `${c.contracts ? `${c.contracts > 0 ? '롱' : '숏'} ${Math.abs(c.contracts)}계약` : '포지션 없음'} · ${fmtAge((Date.now() / 1000 - c.started) / 86400)}` : '곧 시작';
    return `<button class="rail-item${on ? ' active' : ''}" data-key="${c.key}" aria-pressed="${on}">
      <span class="rail-top"><span class="rail-name">${short}</span><b class="${tone(r || 0)}">${r == null ? '-' : fmtSigned(r, 2)}</b></span>
      <span class="rail-desc">${rest.join(' · ')}</span><span class="rail-facts">${facts}</span></button>`;
  }).join('')}</div>`;
}

// ---------- 대회 기준 성적: 4주 구간의 수익률·MDD 분포 ----------
function windowsHtml(c) {
  const row = (name, p) => {
    const w = p.windows, s = p.stats;
    return `<tr><td>${name}</td><td>${w.count}개</td><td class="${tone(w.ret_median)}"><b>${fmtSigned(w.ret_median, 2)}</b></td>
      <td>${fmtPct(w.up, 0)}</td><td class="c-down">${fmtSigned(w.ret_p10, 1)}</td><td class="c-up">${fmtSigned(w.ret_p90, 1)}</td>
      <td class="c-down">${fmtPct(w.mdd_median, 1)}</td><td class="c-down">${fmtPct(w.mdd_p10, 1)}</td>
      <td class="${tone(w.score)}"><b>${fmtSigned(w.score, 2)}</b></td><td>${s.sharpe == null ? '-' : s.sharpe.toFixed(2)}</td></tr>`;
  };
  return `<div class="journal"><table class="trend-table"><thead><tr><th>구간</th><th>4주 구간</th><th>수익률 중앙값</th><th>플러스</th><th>수익률 하위 10%</th><th>상위 10%</th>
    <th>MDD 중앙값</th><th>MDD 하위 10%</th><th>수익 + MDD 평균</th><th>샤프(연)</th></tr></thead>
    <tbody>${Object.entries(c.backtest.periods).map(([name, p]) => row(name, p)).join('')}</tbody></table></div>
    <p class="hint">4주 구간은 5거래일씩 밀면서 잘랐습니다. MDD는 구간 시작부터 잰 일별 종가 기준입니다(장중 낙폭은 더 큽니다). "수익 + MDD"는 대회의 종합 점수를 대신한 간단한 값입니다.
      ${Object.keys(c.backtest.periods).length > 1 ? '규칙은 2019년까지만 보고 골랐고, 2020년 이후는 확인용입니다.'
        : '<b>이 후보는 1시간봉 2년치 전체를 보고 고른 것이라 확인용 구간이 따로 없습니다.</b> 실제보다 좋게 나왔을 수 있습니다.'}
      계좌 대비 노출을 소수로 계산했으므로 정수 계약과는 조금 다릅니다.</p>`;
}

// ---------- 탭 ----------
function tradesHtml(d, c) {
  if (!c.trades || !c.trades.length) return `<p class="empty">아직 결정한 적이 없습니다. 평일 뉴욕 16:10(${fmtTime(d.next_decision)})에 그날 종가로 첫 결정을 합니다.</p>`;
  return `<div class="journal"><table class="trend-table"><thead><tr><th>결정한 때</th><th>갈래</th><th>근거</th><th>목표 노출</th><th>포지션</th><th>MNQ 가격</th><th>비용</th></tr></thead>
    <tbody>${c.trades.map((t) => `<tr><td>${fmtTime(t.time)}</td><td>${SLEEVES[t.sleeve || 'daily']}</td><td style="text-align: left">${t.why || `${t.date} 종가 ${fmtPrice(t.close)}`}</td><td>${t.expo.toFixed(2)}배</td>
      <td>${t.from === t.to ? `${position(t.to)} <small>그대로</small>` : `${position(t.from)} → <b>${position(t.to)}</b>`}</td><td>${fmtPrice(t.price)}</td>
      <td class="c-down">${t.fee ? fmtMoney(-t.fee) : '-'}</td></tr>`).join('')}</tbody></table></div>
    <p class="hint">목표 노출(계좌 대비 배수)을 그때의 계좌 금액과 MNQ 가격으로 정수 계약으로 바꿨습니다. 1계약 = MNQ 가격 × $${d.multiplier}. 비용은 계약당 ${fmtMoney(d.fee)}로 잡았습니다.</p>`;
}

function backtestHtml(d, c) {
  const bt = c.backtest;
  const years = Object.keys(bt.years);
  const periods = Object.entries(bt.periods).map(([name, p]) => {
    const s = p.stats;
    return `<tr><td>${name}</td><td class="${tone(s.yearly)}"><b>${fmtSigned(s.yearly, 1)}</b></td><td>${s.sharpe == null ? '-' : s.sharpe.toFixed(2)}</td>
      <td class="c-down">${fmtPct(s.drawdown, 0)}</td><td class="c-down">${fmtSigned(s.worst_month, 1)}</td><td class="c-down">${fmtSigned(s.worst_day, 1)}</td></tr>`;
  }).join('');
  return `<h3>구간별</h3>
    <table class="trend-table"><thead><tr><th>구간</th><th>연 복리</th><th>샤프(연)</th><th>최대 낙폭</th><th>최악의 달</th><th>최악의 날</th></tr></thead><tbody>${periods}</tbody></table>
    <h3>연도별</h3>
    <div class="journal"><table class="trend-table"><thead><tr><th>연도</th>${years.map((y) => `<th>${y}</th>`).join('')}</tr></thead><tbody>
    <tr><td>수익률</td>${years.map((y) => `<td class="${tone(bt.years[y].total)}">${fmtSigned(bt.years[y].total, 0)}</td>`).join('')}</tr>
    <tr><td>최대 낙폭</td>${years.map((y) => `<td class="c-down">${fmtPct(bt.years[y].drawdown, 0)}</td>`).join('')}</tr></tbody></table></div>
    <p class="hint">${bt.note}. 정한 노출을 다음 봉부터 적용하고 노출이 바뀐 만큼 비용을 뗐습니다. ${fmtDay(bt.until)}까지.</p>`;
}

function compareHtml(d) {
  const row = (c) => {
    const [period, p] = lastPeriod(c);
    const w = p.windows, s = p.stats;
    const r = ret(c, d.capital);
    const on = c.key === state.candidate;
    return `<tr tabindex="0" data-key="${c.key}" class="${on ? 'fresh' : ''}"${on ? ' aria-current="true"' : ''}>
      <td><b>${c.name}</b><br><small>${period}</small></td><td>${position(c.signal.contracts || 0)}</td>
      <td class="${tone(r || 0)}">${r == null ? '-' : `<b>${fmtSigned(r, 2)}</b>`}</td><td class="c-down">${c.drawdown == null ? '-' : fmtPct(c.drawdown, 1)}</td>
      <td class="${tone(w.ret_median)}">${fmtSigned(w.ret_median, 2)}</td><td class="c-down">${fmtPct(w.mdd_median, 1)}</td>
      <td class="${tone(w.score)}"><b>${fmtSigned(w.score, 2)}</b></td><td>${s.sharpe.toFixed(2)}</td></tr>`;
  };
  return `<div class="journal"><table class="trend-table compare">
      <thead><tr><th rowspan="2">후보</th><th rowspan="2">지금 신호</th><th colspan="2" class="span">모의 운용</th><th colspan="4" class="span">과거 시험 4주 구간 (후보마다 마지막 구간)</th></tr>
      <tr><th>수익률</th><th>최대 낙폭</th><th>수익률 중앙값</th><th>MDD 중앙값</th><th>수익 + MDD</th><th>샤프(연)</th></tr></thead>
      <tbody>${d.candidates.map(row).join('')}</tbody></table></div>
    <p class="hint">줄을 누르면 그 후보로 바뀝니다. "지금 신호"는 마지막 종가(${d.close[0]})로 정한 노출을 지금 계좌와 가격으로 바꾼 계약 수입니다.</p>`;
}

function methodHtml(d, c) {
  return `<div class="doc">
    <p>${c.note}</p>
    <ul>
      <li>하루 한 번, 평일 <b>뉴욕 16:10</b>(나스닥100 종가가 나온 뒤)에 그날 종가로 다음 날의 노출을 정하고, 그 순간의 MNQ 가격에 <b>정수 계약</b>으로 맞춥니다.
        MNQ는 뉴욕 17:00까지 거래되므로 그 사이에 주문할 수 있습니다.</li>
      <li>노출은 계좌 대비 배수입니다. 1계약 = MNQ 가격 × $${d.multiplier}이라 지금 가격(${fmtPrice(d.price)})에서 1계약은 계좌 ${fmtMoney(d.capital, 0)}의 약
        <b>${d.price ? (d.price * d.multiplier / d.capital).toFixed(2) : '-'}배</b>입니다. 노출은 이 단위로 반올림됩니다.</li>
      <li>가상 계좌는 1분마다 LS증권 시세로 MNQ 가격을 받아 다시 계산하고, 그래프에는 한 시간에 한 점씩 남깁니다.</li>
    </ul>
    <h3>한계</h3>
    <ul>
      <li><b>대회는 4주뿐입니다.</b> 4주 성적은 운의 영향이 큽니다. 과거 시험에서도 같은 규칙의 4주 수익률이 구간마다 크게 달랐습니다(대회 기준 성적의 상위·하위 10% 참고).</li>
      <li>과거 시험은 지수 종가로 계산했고 정해진 시각의 가격에 전량 체결된다고 봅니다. 실제로는 호가 차이와 체결 지연이 더 듭니다.</li>
      <li>후보들은 모두 나스닥 롱만 듭니다(쉬거나 크기를 바꿀 뿐 숏은 없습니다). 그래서 나스닥이 크게 오르는 달에는 보유가 가장 앞섭니다.</li>
      <li>프로그램이 꺼져 있던 동안의 결정은 되짚지 않습니다. 그동안은 들고 있던 계약 그대로입니다.</li>
    </ul></div>`;
}

// ---------- 그래프 ----------
function renderChart(d, c) {
  lines.forEach((l) => chart.removeSeries(l));
  lines = [];
  const live = state.view === 'live';
  const data = live ? (c.curve || []).map(([time, value]) => ({ time, value }))
    : c.backtest.curve.map(([time, value]) => ({ time, value: value * d.capital }));
  chart.applyOptions({ rightPriceScale: { mode: live ? LightweightCharts.PriceScaleMode.Normal : LightweightCharts.PriceScaleMode.Logarithmic } });
  [9, 5].forEach((lineWidth) => {
    const glow = chart.addLineSeries({ color: token('--online-glow'), lineWidth, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false, autoscaleInfoProvider: () => null });
    glow.setData(data);
    lines.push(glow);
  });
  const line = chart.addLineSeries({ color: token('--online'), lineWidth: 3, priceLineVisible: false, pointMarkersVisible: data.length === 1 });
  line.setData(data);
  lines.push(line);
  $('paper-chart').hidden = false;
  chart.timeScale().fitContent();
  $('chart-note').innerHTML = live
    ? `<span>한 시간마다 그때의 MNQ 가격으로 계산한 가상 계좌(${fmtMoney(d.capital, 0)} 시작)</span>`
    : `<span>같은 규칙을 과거 데이터에 돌린 결과(${fmtMoney(d.capital, 0)} 시작, 세로축은 로그): ${c.backtest.note}</span>`;
}

function render() {
  const d = state.data;
  if (!d) return;
  if (!d.candidates.some((c) => c.key === state.candidate)) state.candidate = d.candidates[d.candidates.length - 1].key;
  const c = d.candidates.find((x) => x.key === state.candidate);
  const [short, ...rest] = c.name.split(' · ');
  renderRail(d);
  $('title').textContent = short;
  $('subtitle').textContent = rest.join(' · ');
  $('views').innerHTML = [['live', '모의 운용'], ['backtest', '과거 시험']].map(([key, name]) =>
    `<button data-view="${key}" class="${state.view === key ? 'active' : ''}" aria-pressed="${state.view === key}">${name}</button>`).join('');
  $('banner').innerHTML = d.error ? `<div class="note warn">${d.error}</div>` : '';

  const ready = c.started != null;
  $('message').hidden = ready;
  $('kpis').hidden = !ready;
  $('summary-card').hidden = false;
  $('message').textContent = '모의 운용을 준비하는 중입니다. 서버를 켜면 1분 안에 시작합니다.';
  $('period').textContent = ready ? `${fmtTime(c.started)} 시작 · ${d.symbol || 'MNQ'} · LS증권 시세 · 실제 주문 없음 · 계약당 비용 ${fmtMoney(d.fee)}` : '';
  if (ready) {
    const r = ret(c, d.capital);
    const notional = d.price ? Math.abs(c.contracts) * d.price * d.multiplier : 0;
    $('kpis').innerHTML = `
      <div class="kpi main"><span>${fmtMoney(d.capital, 0)}로 시작한 가상 계좌</span><b>${fmtMoney(c.equity)}</b><em class="${tone(r)}">${fmtSigned(r, 2)}</em></div>
      <div class="kpi"><span>지금 포지션</span><b>${position(c.contracts)}</b><small>${partsText(c.parts) ? partsText(c.parts) + ' · ' : ''}노출 ${(notional / c.equity).toFixed(2)}배 · ${fmtMoney(notional, 0)}</small></div>
      <div class="kpi"><span>최대 낙폭</span><b class="c-down">${fmtPct(c.drawdown, 2)}</b><small>시간별 기록 기준</small></div>
      <div class="kpi"><span>${d.symbol || 'MNQ'}</span><b>${fmtPrice(d.price)}</b><small>${d.price_at ? fmtTime(d.price_at) : ''}</small></div>
      <div class="kpi"><span>다음 결정</span><b>${new Date(d.next_decision * 1000).toLocaleTimeString('ko-KR', { hour: 'numeric', minute: '2-digit' })}</b>
        <small>${new Date(d.next_decision * 1000).toLocaleDateString('ko-KR', { month: 'long', day: 'numeric' })} · 일봉 신호 ${position(c.signal.contracts || 0)}${c.intraday ? ' · 장중은 평일 뉴욕 10 ~ 15시 정시' : ''}</small></div>`;
  }
  $('summary').innerHTML = windowsHtml(c);
  renderChart(d, c);

  $('tabs').innerHTML = Object.entries(TABS).map(([key, name]) => {
    const count = key === 'trades' ? (c.trades || []).length : key === 'compare' ? d.candidates.length : '';
    return `<button role="tab" data-tab="${key}" aria-selected="${state.tab === key}" class="${state.tab === key ? 'active' : ''}">${name}${count === '' ? '' : ` <small>${count}</small>`}</button>`;
  }).join('');
  $('panel').innerHTML = state.tab === 'trades' ? tradesHtml(d, c) : state.tab === 'backtest' ? backtestHtml(d, c)
    : state.tab === 'compare' ? compareHtml(d) : methodHtml(d, c);
}

async function refresh() {
  try {
    state.data = await api('/api/paper');
    render();
    $('updated').textContent = `갱신 ${new Date().toLocaleTimeString('ko-KR', { hour: '2-digit', minute: '2-digit' })}`;
  } catch (e) {
    $('message').hidden = false;
    $('message').textContent = '불러오지 못했습니다: ' + e.message;
  }
}

function choose(key) {
  state.candidate = key;
  localStorage.setItem('mnqCandidate', key);
  render();
}
$('rail').addEventListener('click', (ev) => {
  const item = ev.target.closest('[data-key]');
  if (item) choose(item.dataset.key);
});
$('panel').addEventListener('click', (ev) => {
  const row = ev.target.closest('tr[data-key]');
  if (row) choose(row.dataset.key);
});
$('panel').addEventListener('keydown', (ev) => {
  const row = ev.target.closest('tr[data-key]');
  if (row && (ev.key === 'Enter' || ev.key === ' ')) { ev.preventDefault(); choose(row.dataset.key); }
});
$('views').addEventListener('click', (ev) => {
  const { view } = ev.target.dataset;
  if (!view) return;
  state.view = view;
  localStorage.setItem('mnqView', view);
  render();
});
$('tabs').addEventListener('click', (ev) => {
  const button = ev.target.closest('[data-tab]');
  if (!button) return;
  state.tab = button.dataset.tab;
  localStorage.setItem('mnqTab', state.tab);
  render();
  $('tabs').querySelector('.active').focus();
});
document.addEventListener('themechange', render);
refresh();
setInterval(refresh, 60 * 1000);
