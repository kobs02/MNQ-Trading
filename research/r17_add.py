"""17차: 되돌림 + 추세(dip_trend) 롱을 든 채 더 떨어지면 1계약 더 사기(물타기 1번).

질문: 지금 규칙(1계약 ≈ 0.6배)을 든 동안 진입 종가보다 X% 더 내려가면 0.6배를 더 사서(2계약) 그 보유가 끝날 때까지 든다. 이게 나은가?
  변형 A: 진입 종가 대비 -X% (X = 1, 2, 3, 4, 5)
  변형 B: 보유 중 새 5일 최저 종가가 다시 나오면(규칙이 '5일 더' 연장하는 날)
  비교 1: 기준 = 1계약 그대로. 비교 2: 처음부터 2계약(더 많이 들기만 해도 좋아지는지 보려고)

통과 기준 (결과를 보기 전에 정함. 설계 2001 ~ 2019에서 변형 하나를 고르고, 확인 2020 ~ 으로 판정, r14와 같은 꼴)
  1. 확인 구간 4주 수익+MDD 평균이 기준(1계약)보다 0.20%p 이상 좋다
  2. 확인 구간 샤프가 기준 이상
  3. 설계 구간 4주 수익+MDD 평균도 기준보다 좋다
  4. 같은 보유에서 '무작위 날'에 추가한 것(200번)보다 확인 구간 수익+MDD가 95% 이상 좋다(떨어질 때 사는 시점 효과인지, 노출이 늘어서인지 가르기)

체결: t일 종가에 정한 노출을 t+1일 수익률에 적용, 노출 변화 1배당 0.01%. 종가 기준 MDD라 장중 MDD는 이보다 깊다(r2: 0.7 ~ 1.1%p).
    cd C:\\dev\\MNQ-Trader && .venv\\Scripts\\python research\\r17_add.py
"""
import sys
import numpy as np, pandas as pd

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, ".")
from mnq import config, strategies

C = pd.read_pickle(config.STORE / "data" / "NDX_1d.pkl").loc["1999":].Close.dropna()
cc = C.pct_change().fillna(0.0)
DESIGN, CONFIRM = ("2001", "2019"), ("2020", "2026")
UNIT = 0.6  # 1계약 ≈ 계좌의 0.6배
dip = strategies.RULES["dip_trend"](C).fillna(0.0).to_numpy()
low5 = (C <= C.rolling(5).min()).to_numpy()
px = C.to_numpy()


def episodes():
    """보유 구간 [(시작, 끝 다음)]: dip이 켜진 연속 구간."""
    out, s = [], None
    for i, v in enumerate(dip):
        if v and s is None:
            s = i
        if not v and s is not None:
            out.append((s, i)); s = None
    if s is not None:
        out.append((s, len(dip)))
    return out


EPS = episodes()


def adds(rule):
    """보유 구간마다 추가하는 날(없으면 None)."""
    out = []
    for s, e in EPS:
        k = None
        for i in range(s + 1, e):
            if (rule == "B" and low5[i]) or (rule != "B" and px[i] <= px[s] * (1 - rule)):
                k = i; break
        out.append(k)
    return out


def expo_from(add_days):
    x = dip * UNIT
    for (s, e), k in zip(EPS, add_days):
        if k is not None:
            x[k:e] += UNIT
    return pd.Series(x, index=C.index)


def pnl(expo):
    return expo.shift(1).fillna(0.0) * cc - strategies.COST * expo.diff().abs().fillna(0.0)


def stats(p, span):
    p = p.loc[span[0]:span[1]]
    w = strategies.windows(p)
    return w["score"], p.mean() / p.std() * np.sqrt(252), w["ret_p10"], w["mdd_p10"]


def row(name, p):
    d, c = stats(p, DESIGN), stats(p, CONFIRM)
    print(f"{name:<22} 설계 {d[0]:+.2%} 샤프 {d[1]:.2f} | 확인 {c[0]:+.2%} 샤프 {c[1]:.2f} 4주수익 하위10% {c[2]:+.2%} MDD 하위10% {c[3]:+.2%}")
    return d, c


base = pnl(expo_from([None] * len(EPS)))
print(f"NDX 일봉 {C.index[0]:%Y-%m-%d} ~ {C.index[-1]:%Y-%m-%d}, 보유 구간 {len(EPS)}개\n")
bd, bc = row("기준 1계약", base)
row("비교: 처음부터 2계약", pnl(pd.Series(dip * UNIT * 2, index=C.index)))
res = {}
for rule in (0.01, 0.02, 0.03, 0.04, 0.05, "B"):
    a = adds(rule)
    name = f"A -{rule:.0%} 추가" if rule != "B" else "B 새 5일 최저 추가"
    n = sum(k is not None for k in a)
    res[rule] = (a, *row(f"{name} ({n}번)", pnl(expo_from(a))))

pick = max(res, key=lambda r: res[r][1][0])
a, d, c = res[pick]
rng = np.random.default_rng(0)
rand = []
for _ in range(200):
    ra = [None if k is None else int(rng.integers(s + 1, e)) if e - s > 1 else None for (s, e), k in zip(EPS, a)]
    rand.append(stats(pnl(expo_from(ra)), CONFIRM)[0])
beat = np.mean(c[0] > np.array(rand))
print(f"\n설계에서 고른 변형: {pick}")
checks = [("1 확인 수익+MDD ≥ 기준 + 0.20%p", c[0] >= bc[0] + 0.002), ("2 확인 샤프 ≥ 기준", c[1] >= bc[1]),
          ("3 설계 수익+MDD > 기준", d[0] > bd[0]), (f"4 무작위 날 추가보다 나음 {beat:.0%} ≥ 95%", beat >= 0.95)]
for k, ok in checks:
    print(("통과 " if ok else "불통과 ") + k)
print("결론:", "통과" if all(ok for _, ok in checks) else "불통과")
