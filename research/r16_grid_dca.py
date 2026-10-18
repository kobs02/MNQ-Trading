"""16차: 코인 거래소 봇의 대표 규칙 두 가지(그리드, DCA)를 MNQ에 대회처럼 4주 구간마다 새로 시작해 본다.

- 데이터: 야후 NQ=F 일봉 2021 ~ (1시간봉은 2024-05부터라 2021부터는 일봉). 하루 안의 경로는 양봉이면 시가 → 저가 → 고가 → 종가,
  음봉이면 시가 → 고가 → 저가 → 종가로 가정하고, 그 경로에서 주문 가격에 닿으면 그 가격에 체결한다
- 구간: 거래일 20일, 5일 간격(겹침). 구간 첫날 시가에 봇을 새로 세우고, 날마다 종가로 평가해 수익률과 MDD(종가 기준)를 잰다
- 노출: 계좌 대비 배수(1계약 ≈ 0.6배). 비용: 노출이 바뀐 만큼 0.01%(r1과 같다)
- 파라미터는 설계 구간(2021 ~ 2023에 시작한 4주)에서 고르고, 확인 구간(2024 ~)에 그대로 적용한다
- 통과 기준(미리 정함): 확인 구간 4주 수익+MDD 평균이 되돌림 + 추세보다 0.2%p 이상 좋고, 설계 구간에서도 더 좋을 것

그리드: 시작가 c 아래로 간격 s마다 L칸. 칸 k(가격 c(1 - sk))에 닿으면 X/L배 사고, 한 칸 위(c(1 - s(k-1)))에서 판다.
  중립형은 위쪽도 대칭으로 숏(c(1 + sk)에서 팔고 한 칸 아래에서 되산다). 범위를 벗어나도 다시 세우지 않는다(보통의 봇과 같게).
DCA: 시작가에 b배 사고, 마지막 매수가보다 d 내려갈 때마다 b·m^n배를 더 산다(최대 N번). 평균 단가보다 tp 오르면 모두 팔고 그 가격에 b배로 다시 시작.
    cd C:\\dev\\MNQ-Trader && .venv\\Scripts\\python research\\r16_grid_dca.py
"""
import itertools
import sys
import numpy as np, pandas as pd

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, ".")
from mnq import config, strategies

COST = strategies.COST
WIN, STEP = 20, 5
df = pd.read_pickle(config.STORE / "data" / "NQF_1d.pkl")[["Open", "High", "Low", "Close"]].dropna()
df.columns = ["open", "high", "low", "close"]
full = df  # 200일선 계산용
df = df.loc["2021":]


def path(bar):
    o, h, l, c = bar
    return (o, l, h, c) if c >= o else (o, h, l, c)


class Book:
    """노출 단위 계좌: 시작 금액 1. 사고팔 때 노출(배수)만큼 지수 단위 수량으로 바꿔 든다."""

    def __init__(self, p0):
        self.cash, self.q, self.p0, self.fills = 0.0, 0.0, p0, 0

    def trade(self, expo, price):  # expo > 0 사기, < 0 팔기(시작 금액 대비 배수)
        self.fills += 1
        dq = expo / self.p0
        self.q += dq
        self.cash -= dq * price + COST * abs(expo)

    def equity(self, price):
        return 1 + self.cash + self.q * price


def run_grid(bars, s, L, X, neutral):
    c = bars[0][0]
    book, e = Book(c), X / L
    long_lv = [c * (1 - s * k) for k in range(1, L + 1)]
    short_lv = [c * (1 + s * k) for k in range(1, L + 1)] if neutral else []
    held_l, held_s = [False] * L, [False] * len(short_lv)
    eq = []
    for bar in bars:
        pts = path(bar)
        for a, b in zip(pts, pts[1:]):
            if b < a:  # 내려가는 길: 아래 칸 사기(가까운 것부터), 위 칸 숏 되사기
                for k in range(len(short_lv)):
                    back = c * (1 + s * k)
                    if held_s[k] and b <= back <= a:
                        book.trade(e, back); held_s[k] = False
                for k, lv in enumerate(long_lv):
                    if not held_l[k] and b <= lv <= a:
                        book.trade(e, lv); held_l[k] = True
            else:  # 올라가는 길: 산 칸은 한 칸 위에서 팔기, 위 칸 숏
                for k in range(L):
                    up = c * (1 - s * k)
                    if held_l[k] and a <= up <= b:
                        book.trade(-e, up); held_l[k] = False
                for k, lv in enumerate(short_lv):
                    if not held_s[k] and a <= lv <= b:
                        book.trade(-e, lv); held_s[k] = True
        eq.append(book.equity(bar[3]))
    return eq, book.fills


def run_dca(bars, b, d, m, N, tp):
    book = Book(bars[0][0])
    state = {}

    def start(price):
        book.trade(b, price)
        state.update(n=0, last=price, cost=b * price, size=b)

    start(bars[0][0])
    eq = []
    for bar in bars:
        pts = path(bar)
        for a, z in zip(pts, pts[1:]):
            while True:
                avg = state["cost"] / state["size"]
                buy, sell = state["last"] * (1 - d), avg * (1 + tp)
                if z < a and state["n"] < N and z <= buy <= a:  # 추가 매수
                    size = b * m ** (state["n"] + 1)
                    book.trade(size, buy)
                    state.update(n=state["n"] + 1, last=buy, cost=state["cost"] + size * buy, size=state["size"] + size)
                    a = buy
                elif z > a and a <= sell <= z:  # 익절 뒤 다시 시작
                    book.trade(-state["size"], sell)
                    start(sell)
                    a = sell
                else:
                    break
        eq.append(book.equity(bar[3]))
    return eq, book.fills


def score(eq):
    eq = np.concatenate([[1.0], eq])
    return eq[-1] - 1, (eq / np.maximum.accumulate(eq) - 1).min()


starts = range(0, len(df) - WIN + 1, STEP)
bars = list(df.itertuples(index=False, name=None))
design = [i for i in starts if df.index[i].year <= 2023]
confirm = [i for i in starts if df.index[i].year >= 2024]


def evaluate(fn, params, idx):
    out = [fn(bars[i:i + WIN], *params) for i in idx]
    r, m = np.array([score(eq) for eq, _ in out]).T
    return {"score": float(np.mean(r + m)), "ret": float(np.median(r)), "mdd": float(np.median(m)), "mdd10": float(np.percentile(m, 10)),
            "up": float((r > 0).mean()), "fills": float(np.median([f for _, f in out]))}


def expo_run(expo):
    """노출 규칙(종가로 정해 다음 날 적용)을 같은 4주 구간으로."""
    ret = full["close"].pct_change().fillna(0.0)
    pnl = (expo.shift(1).fillna(0.0) * ret - COST * expo.diff().abs().fillna(0.0)).loc["2021":]

    def run(idx):
        rows = [score(np.cumprod(1 + pnl.iloc[i:i + WIN].to_numpy())) for i in idx]
        r, m = np.array(rows).T
        return {"score": float(np.mean(r + m)), "ret": float(np.median(r)), "mdd": float(np.median(m)), "mdd10": float(np.percentile(m, 10)),
                "up": float((r > 0).mean())}
    return run


base = {"늘 롱 0.6배(≈1계약)": expo_run(pd.Series(0.6, index=full.index)),
        "되돌림 + 추세 0.6배": expo_run(strategies.RULES["dip_trend"](full["close"]).fillna(0.0) * 0.6)}
fmt = lambda x: (f"수익+MDD {x['score']:+6.2%} | 4주 수익 중앙 {x['ret']:+6.2%} 플러스 {x['up']:4.0%} | MDD 중앙 {x['mdd']:6.2%} 하위10% {x['mdd10']:6.2%}"
                 + (f" | 4주 체결 중앙 {x['fills']:.0f}" if "fills" in x else ""))
print(f"NQ=F 일봉 {df.index[0]:%Y-%m-%d} ~ {df.index[-1]:%Y-%m-%d} · 4주 구간 설계 {len(design)}개(2021 ~ 2023 시작), 확인 {len(confirm)}개(2024 ~)\n")
print("===== 기준 =====")
ref = {}
for name, run in base.items():
    ref[name] = (run(design), run(confirm))
    print(f"  {name:22s} 설계 {fmt(ref[name][0])}\n  {'':22s} 확인 {fmt(ref[name][1])}")
dip_d, dip_c = ref["되돌림 + 추세 0.6배"][0]["score"], ref["되돌림 + 추세 0.6배"][1]["score"]

families = {
    "그리드": (run_grid, [(s, L, X, n) for s, L, X, n in itertools.product((0.005, 0.01, 0.015, 0.02, 0.03), (3, 5, 8), (0.6, 1.2), (False, True))],
             lambda p: f"간격 {p[0]:.1%} {p[1]}칸 최대 {p[2]}배 {'중립' if p[3] else '롱'}"),
    "DCA": (run_dca, [p for p in itertools.product((0.1, 0.2), (0.01, 0.02, 0.03), (1.0, 1.5, 2.0), (3, 5), (0.005, 0.01, 0.02))
                      if p[0] * sum(p[2] ** k for k in range(p[3] + 1)) <= 2.0],
            lambda p: f"첫 {p[0]}배 간격 {p[1]:.0%} 배수 {p[2]} 최대 {p[3]}번 익절 {p[4]:.1%}"),
}
# 대회 규정(주당 2회 이상)을 맞추는 설정만: 설계 구간 4주 체결 중앙 8건 이상. 이 조건이 없으면 거의 거래하지 않는 설정(점수 0 근처)이 뽑힌다
# (처음 돌렸을 때 3% 간격 그리드가 확인 구간의 53%에서 체결 0건인 채로 '통과'해 넣은 조건이다. 확인 구간을 한 번 본 뒤라 그만큼 근거가 약하다)
MIN_FILLS = 8
print("\n(아무것도 하지 않으면 수익+MDD 0%라 이 점수에서는 거래를 안 하는 쪽이 늘 유리하다. 그래서 대회 규정 '주당 2회'를 맞추는 설정만 고른다)")
for fam, (fn, grid, label) in families.items():
    res = [(p, evaluate(fn, p, design)) for p in grid]
    res = [r for r in res if r[1]["fills"] >= MIN_FILLS]
    res.sort(key=lambda x: x[1]["score"], reverse=True)
    print(f"\n===== {fam}: 설정 {len(grid)}개 중 주당 2회를 맞추는 {len(res)}개, 설계 구간 상위 5개 =====")
    for p, x in res[:5]:
        print(f"  {label(p):34s} {fmt(x)}")
    best = res[0][0]
    c = evaluate(fn, best, confirm)
    allc = [evaluate(fn, p, confirm)["score"] for p, _ in res]
    ok = [c["score"] >= dip_c + 0.002, res[0][1]["score"] > dip_d]
    print(f"  → 고른 설정을 확인 구간에: {fmt(c)}")
    print(f"     확인 구간 설정 전체의 수익+MDD 중앙 {np.median(allc):+.2%}, 최고 {max(allc):+.2%}(뒤늦게 고른 값이라 쓰면 안 됨)")
    print(f"     판정: 확인 ≥ 되돌림 + 0.2%p {'O' if ok[0] else '.'} · 설계 > 되돌림 {'O' if ok[1] else '.'} → {'통과' if all(ok) else '불통과'}")
