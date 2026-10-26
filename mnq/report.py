"""수익률 보고서: 켜 둔 서버에서 주문 기록·계좌·1분봉을 받아 reports/RETURNS.md와 reports/returns.svg를 새로 쓴다.
실행: python -m mnq.report  (서버가 켜져 있어야 한다. LS에 따로 묻지 않아 서버의 초당 한도와 겹치지 않는다)"""
import json
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from mnq import config

KST = timezone(timedelta(hours=9))
OUT = config.ROOT / "reports"
COST = 2.0  # 체결 1계약당 증권사 $1 + 대회 차감 $1


usd = lambda v: f"{'-' if v < 0 else '+'}${abs(v):,.0f}"


def get(path):
    with urllib.request.urlopen(f"http://{config.HOST}:{config.PORT}{path}", timeout=60) as r:
        return json.load(r)


def minute_bars(since):
    """since(첫 체결)부터의 1분봉. 서버는 켠 뒤 최근 1,000개쯤만 들고 있어서, 모자라면 이전 봉을 1,000개씩 더 받게 한다(받은 봉은 서버에 쌓인다)."""
    bars = get("/api/chart?interval=1")["bars"]
    for _ in range(60):  # 60 × 1,000봉 ≈ 6주: 대회 기간을 넘는다
        if not bars or bars[0]["time"] <= since or get("/api/chart/older?interval=1")["done"]:
            break
        bars = get("/api/chart?interval=1")["bars"]
    return bars


def fills(orders):
    """체결만 (시각, 계약 수 ±, 체결가). 주문 기록 화면(web/orders.js walk)과 같은 규칙: LS 체결 수량이 없으면 낸 수량이 다 체결됐다고 본다."""
    out = []
    for o in orders:
        q = (1 if o["qty"] > 0 else -1) * o["exec_qty"] if o.get("exec_qty") is not None else (o["qty"] if o.get("ok") else 0)
        px = o.get("exec_price") or o.get("price")
        if q and px:
            out.append((o["time"], q, px))
    return out


def curve(fl, bars, mult):
    """1분봉 끝마다 대회 기준 손익(실현 + 평가 - 비용). MDD는 봉의 불리한 쪽 끝(롱은 저가, 숏은 고가)으로 잰다."""
    cash = pos = 0.0
    i, peak, mdd, top, pts = 0, 0.0, 0.0, 0.0, []
    for b in bars:
        end = b["time"] + 60
        if end <= fl[0][0]:
            continue
        while i < len(fl) and fl[i][0] < end:
            _, q, px = fl[i]
            cash -= q * px * mult + abs(q) * COST
            pos += q
            i += 1
        for p in ((b["high"], b["low"]) if pos > 0 else (b["low"], b["high"])):  # 좋은 쪽 끝 먼저(고점 갱신), 그다음 나쁜 쪽
            eq = cash + pos * p * mult
            peak = max(peak, eq)
            if eq - peak < mdd:
                mdd, top = eq - peak, peak
        pts.append((b["time"], cash + pos * b["close"] * mult))
    return pts, mdd, top  # top: MDD가 시작된 고점의 손익(퍼센트는 그 고점 자산 대비)


def daily(fl, mult):
    """한국 날짜별 (체결 수, 계약 수, 대회 기준 실현 손익). 평균 진입가 기준으로 닫은 몫만 센다."""
    pos = avg = 0.0
    days = defaultdict(lambda: [0, 0, 0.0])
    for t, q, px in fl:
        d = days[datetime.fromtimestamp(t, KST).strftime("%m-%d")]
        if pos and (q > 0) != (pos > 0):
            closed = min(abs(q), abs(pos))
            d[2] += closed * (1 if pos > 0 else -1) * (px - avg) * mult
            left = pos + q
            avg = avg if left and (left > 0) == (pos > 0) else px
            pos = left
        else:
            avg = (avg * abs(pos) + px * abs(q)) / (abs(pos) + abs(q))
            pos += q
        d[0] += 1
        d[1] += abs(q)
        d[2] -= abs(q) * COST
    return days


def svg(pts, capital, w=760, h=300):
    """대회 기준 누적 수익률(%) 선 그래프. 흰 바탕(GitHub 어두운 화면에서도 읽히게)."""
    L, R, T, B = 56, 16, 16, 34
    xs = [t for t, _ in pts]
    ys = [v / capital * 100 for _, v in pts]
    lo, hi = min(0.0, min(ys)), max(0.0, max(ys))
    pad = (hi - lo) * 0.08 or 0.5
    lo, hi = lo - pad, hi + pad
    X = lambda t: L + (t - xs[0]) / max(1, xs[-1] - xs[0]) * (w - L - R)
    Y = lambda v: T + (hi - v) / (hi - lo) * (h - T - B)
    step = next(s for s in (0.25, 0.5, 1, 2, 5, 10, 20, 50) if (hi - lo) / s <= 6)
    grid = []
    v = step * int(lo / step)
    while v <= hi:
        grid.append(f'<line x1="{L}" x2="{w - R}" y1="{Y(v):.1f}" y2="{Y(v):.1f}" stroke="{"#9aa0aa" if v == 0 else "#e3e6eb"}"/>'
                    f'<text x="{L - 6}" y="{Y(v) + 4:.1f}" text-anchor="end">{v:+.2f}%</text>')
        v += step
    days, d = [], datetime.fromtimestamp(xs[0], KST).replace(hour=0, minute=0, second=0) + timedelta(days=1)
    while d.timestamp() < xs[-1]:
        x = X(d.timestamp())
        days.append(f'<line x1="{x:.1f}" x2="{x:.1f}" y1="{T}" y2="{h - B}" stroke="#eef0f3"/><text x="{x:.1f}" y="{h - B + 18}" text-anchor="middle">{d:%m-%d}</text>')
        d += timedelta(days=1)
    line = " ".join(f"{X(t):.1f},{Y(v):.1f}" for t, v in zip(xs, ys))
    color = "#0a9e63" if ys[-1] >= 0 else "#e0304a"
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}" font-family="Malgun Gothic, sans-serif" font-size="12" fill="#5c637e">'
            f'<rect width="{w}" height="{h}" fill="#fff"/>{"".join(grid)}{"".join(days)}'
            f'<polyline points="{line}" fill="none" stroke="{color}" stroke-width="2"/>'
            f'<circle cx="{X(xs[-1]):.1f}" cy="{Y(ys[-1]):.1f}" r="3.5" fill="{color}"/>'
            f'<text x="{X(xs[-1]) - 6:.1f}" y="{Y(ys[-1]) - 8:.1f}" text-anchor="end" fill="{color}" font-weight="700">{ys[-1]:+.2f}%</text></svg>')


def build():
    hist, acct = get("/api/live/history"), get("/api/account")
    mult, capital = hist["multiplier"], hist["capital"]
    fl = fills(hist["orders"])
    bars = minute_bars(fl[0][0])
    pts, mdd, top = curve(fl, bars, mult)
    contracts = sum(abs(q) for _, q, _ in fl)
    days = daily(fl, mult)
    contest = capital + sum(v for _, _, v in days.values()) + acct["pnl"]  # LS 평가 자산은 그날 수수료를 늦게 빼서 기록으로 계산한다
    now = datetime.fromtimestamp(acct["at"], KST)
    rows = "\n".join(f"| {k} | {n}건 | {c} | {v:+,.1f} | {v / capital:+.2%} |" for k, (n, c, v) in sorted(days.items(), reverse=True))
    md = f"""# 수익률 보고서

{now:%Y-%m-%d %H:%M} 기준 (한국 시각) · LS 모의계좌 · MNQ · 시작 자금 ${capital:,.0f}

| 대회 기준 수익률 | LS 계좌 수익률 | MDD (1분봉 평가) | 체결 |
| --- | --- | --- | --- |
| **{contest / capital - 1:+.2%}** ({usd(contest - capital)}) | {acct["equity"] / capital - 1:+.2%} | {mdd / (capital + top):.2%} ({usd(mdd)}) | {len(fl)}건 · {contracts}계약 |

![대회 기준 누적 수익률](returns.svg)

| 날짜 | 체결 | 계약 | 대회 기준 실현 손익 ($) | 시작 자금 대비 |
| --- | --- | --- | --- | --- |
{rows}

대회 기준 = 증권사 수수료와 대회 차감(체결 1계약당 각 $1)을 뺀 값. 날짜별 손익은 그날 닫은 몫만 세고, 들고 있는 포지션의 평가 손익은 닫는 날에 들어간다.
갱신: 서버를 켠 채 `python -m mnq.report`
"""
    OUT.mkdir(exist_ok=True)
    (OUT / "returns.svg").write_text(svg(pts, capital), encoding="utf-8")
    (OUT / "RETURNS.md").write_text(md, encoding="utf-8")
    return contest / capital - 1, mdd


if __name__ == "__main__":
    # 점검: 롱 2 @100 → 1 더 @103(평균 101) → 3 정리 @104 = +$18 - 비용 $12
    d = daily([(0, 2, 100.0), (60, 1, 103.0), (120, -3, 104.0)], 2.0)
    assert len(d) == 1 and abs(next(iter(d.values()))[2] - 6.0) < 1e-9, d
    pts, mdd, top = curve([(0, 1, 100.0), (60, -1, 101.0)], [{"time": 0, "open": 100, "high": 102, "low": 99, "close": 101}, {"time": 60, "open": 101, "high": 101, "low": 101, "close": 101}], 2.0)
    assert pts[-1][1] == 2.0 - 4.0 and mdd == -6.0 and top == 2.0, (pts, mdd, top)  # 고가 102에서 저가 99로: 6
    r, m = build()
    print(f"reports/RETURNS.md 갱신: 대회 기준 {r:+.2%}, MDD {usd(m)}")
