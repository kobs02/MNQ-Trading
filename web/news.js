// 뉴스·심리: /api/news(mnq/news.py)를 5분마다 읽어 그린다. 그리는 방식은 Recommender의 news.js를 옮겼다
const $ = (id) => document.getElementById(id);
const tone = (v) => (v > 0 ? 'c-up' : v < 0 ? 'c-down' : 'c-flat');
const signed = (v) => (v > 0 ? '+' : '') + v.toFixed(2);
const escapeHtml = (text) => text.replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const fmtAgo = (sec) => {
  const m = Math.floor(sec / 60);
  if (m < 1) return '방금';
  if (m < 60) return `${m}분 전`;
  if (m < 1440) return `${Math.floor(m / 60)}시간 전`;
  return `${Math.floor(m / 1440)}일 전`;
};

const SENTIMENT = { 1: ['긍정', 'c-up'], '-1': ['부정', 'c-down'], 0: ['중립', 'c-flat'] };
const OUTLOOK = {
  1: ['▲ 긍정적', 'long', '뉴스와 심리가 좋은 쪽으로 기울어 있습니다.'],
  '-1': ['▼ 부정적', 'short', '뉴스와 심리가 나쁜 쪽으로 기울어 있습니다.'],
  0: ['― 중립', 'wait', '긍정과 부정이 비슷하거나 서로 엇갈려 뚜렷한 방향이 없습니다.'],
};
const FILTERS = [['all', '전체'], ['foreign', '해외'], ['domestic', '국내'], ['community', '커뮤니티'], ['positive', '긍정'], ['negative', '부정']];
const PAGE = 5;  // 최신 글은 5개씩 쪽으로 나눈다
const state = { data: null, filter: 'all', page: 0 };

async function api(path) {
  const res = await fetch(path);
  const body = await res.json();
  if (!res.ok) throw new Error(body.error || res.statusText);
  return body;
}

// -1 ~ +1 점수를 가운데(0)에서 좌우로 뻗는 막대로
function scoreBar(score) {
  const width = Math.min(Math.abs(score), 1) * 50;
  return `<div class="score-bar"><i class="${score >= 0 ? 'up' : 'down'}" style="${score >= 0 ? 'left' : 'right'}: 50%; width: ${width}%"></i></div>`;
}

function renderOutlook() {
  const o = state.data.outlook;
  const [label, cls, text] = OUTLOOK[o.direction];
  const rows = o.parts.map((p) => `<tr><td>${p.name}</td><td class="${tone(p.score)}"><b>${signed(p.score)}</b></td><td>${scoreBar(p.score)}</td><td>${Math.round(p.weight * 100)}%</td></tr>`).join('');
  $('outlook').innerHTML = `
    <div class="signal"><span class="badge ${cls}">${label}</span>
      <span class="signal-sub">${text}<small>종합 점수 <b class="${tone(o.direction)}">${signed(o.score)}</b> (-1 ~ +1, ±${o.threshold} 안이면 중립)</small></span></div>
    <table class="parts"><thead><tr><th>구성 요소</th><th>점수</th><th>부정 ← 0 → 긍정</th><th>비중</th></tr></thead><tbody>${rows}</tbody></table>
    <div class="note">참고용입니다. 그 뒤 가격 방향을 맞힌다는 검증은 없고, 자동 주문·추천에도 쓰지 않습니다.</div>
    ${o.notes.map((n) => `<div class="note warn">⚠ ${n}</div>`).join('')}`;
}

// 최근 값 선 그래프와 축: 왼쪽 y축 눈금(lo·hi가 있으면 그 범위, 공포·탐욕 0 ~ 100), 아래 x축 날짜(times가 있을 때). 선은 칸 너비에 맞춰 늘어난다
function sparkline(values, label, lo, hi, times, digits = 0) {
  const w = 220, h = 60, a = lo ?? Math.floor(Math.min(...values)), b = hi ?? Math.ceil(Math.max(...values)), span = b - a || 1;
  const xs = values.map((_, i) => (i / (values.length - 1)) * w), ys = values.map((v) => h - ((v - a) / span) * h);
  const yTicks = [b, (a + b) / 2, a], xTicks = [0, 1, 2, 3].map((k) => Math.round((k / 3) * (values.length - 1)));
  const grid = yTicks.map((v) => `<line x1="0" x2="${w}" y1="${h - ((v - a) / span) * h}" y2="${h - ((v - a) / span) * h}" class="grid"/>`).join('')
    + xTicks.map((i) => `<line x1="${xs[i]}" x2="${xs[i]}" y1="0" y2="${h}" class="grid"/>`).join('');
  const day = (t) => { const d = new Date(t * 1000); return `${d.getMonth() + 1}/${d.getDate()}`; };
  return `<div class="spark-box"><div class="spark-y">${yTicks.map((v) => `<span>${v.toFixed(digits)}</span>`).join('')}</div>
    <svg viewBox="0 0 ${w} ${h}" class="spark" preserveAspectRatio="none" role="img" aria-label="${label}">${grid}
    <polyline points="${xs.map((x, i) => `${x.toFixed(1)},${ys[i].toFixed(1)}`).join(' ')}"/></svg>
    ${times ? `<div class="spark-x">${xTicks.map((i) => `<span>${day(times[i])}</span>`).join('')}</div>` : ''}</div>`;
}

function renderTiles() {
  const d = state.data, tiles = [];
  const fg = d.fear_greed;
  if (fg) {
    tiles.push(`<div class="tile"><span>CNN 공포·탐욕 지수 <small>0 공포 ~ 100 탐욕</small></span>
      <b class="${tone(fg.value - 50)}">${fg.value} <em>${fg.rating}</em></b>${sparkline(fg.history.map((p) => p.value), '최근 30일 공포·탐욕 지수', 0, 100, fg.history.map((p) => p.time))}
      <small>일주일 전 ${fg.week_ago} · 한 달 전 ${fg.month_ago} → 지금 ${fg.value}</small></div>`);
  } else {
    tiles.push('<div class="tile"><span>CNN 공포·탐욕 지수</span><b>-</b><small>CNN에서 받지 못했습니다.</small></div>');
  }
  // VIX·VXN: 옵션 가격에 담긴 앞으로 30일 변동성(연율 %). 높을수록 시장이 불안해한다. 색은 오르면(불안 커짐) 빨강
  const vol = (key, title, sub) => {
    const v = d.vol[key];
    if (!v) return `<div class="tile"><span>${title}</span><b>-</b><small>야후에서 받지 못했습니다.</small></div>`;
    const chg = v.value - v.prev, level = v.value >= 30 ? '높음(불안)' : v.value >= 20 ? '보통 이상' : '낮음(안정)';
    return `<div class="tile"><span>${title} <small>${sub}</small></span>
      <b>${v.value.toFixed(2)} <em><span class="${tone(-chg)}">${chg >= 0 ? '+' : ''}${chg.toFixed(2)}</span></em></b>${sparkline(v.history, `${title} 최근 30거래일`, null, null, v.times, 1)}
      <small>${level} · 최근 석 달 평균 ${v.avg.toFixed(1)} · 하루 변동폭 ≈ 지수 ${(v.value / Math.sqrt(252)).toFixed(1)}%</small></div>`;
  };
  tiles.push(vol('vxn', 'VXN', '나스닥 100 공포지수'), vol('vix', 'VIX', 'S&P 500 공포지수'));
  $('tiles').innerHTML = tiles.join('');
  $('fg-parts').innerHTML = fg && fg.parts.length ? `<table class="parts"><tbody>${fg.parts.map((p) => `<tr><td>${p.name}</td>
    <td class="${tone(p.value - 50)}"><b>${p.value}</b></td><td>${scoreBar((p.value - 50) / 50)}</td><td>${p.rating}</td></tr>`).join('')}</tbody></table>` : '자료가 없습니다.';
}

function renderGroups() {
  const d = state.data;
  $('window').textContent = `최근 ${d.window_hours}시간 · 최신 글일수록 크게 반영`;
  const rows = d.groups.map((g) => {
    const share = (n) => (g.total ? (n / g.total) * 100 : 0);
    return `<tr><td>${g.name}</td><td class="${tone(g.score)}"><b>${g.total ? signed(g.score) : '-'}</b></td>
      <td><div class="split tall"><i class="up" style="width: ${share(g.positive)}%"></i><i class="flat" style="width: ${share(g.neutral)}%"></i><i class="down" style="width: ${share(g.negative)}%"></i></div></td>
      <td><span class="c-up">긍정 ${g.positive}</span> · 중립 ${g.neutral} · <span class="c-down">부정 ${g.negative}</span></td></tr>`;
  }).join('');
  const sources = d.sources.map((s) => `<span class="source ${s.ok ? '' : 'failed'}">${s.name} <small>${s.ok ? `${s.count}건` : s.stale ? `${s.count}건 (이전 수신분)` : '수신 실패'}</small></span>`).join('');
  $('groups').innerHTML = `<table class="parts"><tbody>${rows}</tbody></table><div class="sources">${sources}</div>`;
}

function renderItems() {
  const d = state.data;
  $('filters').innerHTML = FILTERS.map(([key, label]) => `<button data-key="${key}" class="${key === state.filter ? 'active' : ''}" aria-pressed="${key === state.filter}">${label}</button>`).join('');
  const match = { all: () => true, positive: (i) => i.sentiment > 0, negative: (i) => i.sentiment < 0 }[state.filter] || ((i) => i.group === state.filter);
  const items = d.items.filter(match), now = Date.now() / 1000, pages = Math.max(1, Math.ceil(items.length / PAGE));
  state.page = Math.min(state.page, pages - 1);
  $('items').innerHTML = items.length ? items.slice(state.page * PAGE, state.page * PAGE + PAGE).map((i) => {
    const [label, cls] = SENTIMENT[i.sentiment];
    const words = [...i.words.positive.map((w) => `<span class="c-up">${escapeHtml(w)}</span>`), ...i.words.negative.map((w) => `<span class="c-down">${escapeHtml(w)}</span>`)].join(' ');
    const link = /^https?:\/\//.test(i.link) ? escapeHtml(i.link) : '#';
    return `<a class="news-item" href="${link}" target="_blank" rel="noopener noreferrer"><b class="chip ${cls}">${label}</b>
      <span class="news-title">${escapeHtml(i.title)}</span><small>${escapeHtml(i.source)} · ${fmtAgo(now - i.time)}${words ? ' · ' + words : ''}</small></a>`;
  }).join('') : '해당하는 글이 없습니다.';
  $('pager').hidden = pages < 2;
  $('pager').innerHTML = `<button type="button" class="plain-button" data-go="-1"${state.page ? '' : ' disabled'}>← 최신</button>`
    + `<span>${state.page + 1} / ${pages} 쪽 · ${items.length}건</span>`
    + `<button type="button" class="plain-button" data-go="1"${state.page < pages - 1 ? '' : ' disabled'}>이전 →</button>`;
}

async function load() {
  $('refresh').disabled = true;
  try {
    state.data = await api('/api/news');
    $('updated').textContent = `${fmtAgo(Date.now() / 1000 - state.data.updated_at)} 수집 · 5분마다 갱신`;
    renderOutlook();
    renderTiles();
    renderGroups();
    renderItems();
  } catch (e) {
    $('outlook').textContent = '뉴스를 불러오지 못했습니다: ' + e.message;
  }
  $('refresh').disabled = false;
}

$('filters').addEventListener('click', (ev) => {
  if (!ev.target.dataset.key) return;
  state.filter = ev.target.dataset.key;
  state.page = 0;
  renderItems();
});
$('pager').addEventListener('click', (ev) => {
  const go = Number(ev.target.dataset.go);
  if (!go) return;
  state.page += go;
  renderItems();
});
$('refresh').addEventListener('click', load);
load();
setInterval(load, 5 * 60 * 1000);
