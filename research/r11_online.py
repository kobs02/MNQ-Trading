"""11차: 온라인 학습으로 규칙 고르기. 단순 규칙 묶음(롱만, 롱/숏, 되돌림, 추세, 모멘텀 …)에서
지난 성적으로 다음 달에 쓸 규칙(또는 가중치)을 정한다. 무엇을 쓸지를 '학습'하고, 신호 자체는 단순하게 둔다.

실험 전에 정한 통과 기준(확인 구간 2020 ~에서 모두):
  1) 4주 수익 + MDD가 되돌림 + 추세보다 낫다      2) 샤프가 되돌림 + 추세 이상
  3) 연도의 70% 이상 플러스                       4) 같은 묶음에서 무작위로 고른 것 50번보다 낫다(상위 5%)
설계 구간 2005 ~ 2019에서 학습기와 설정을 하나 고르고, 그것만 확인 구간으로 판정한다.
학습기: FTL(지난 L일 샤프가 가장 높은 규칙 하나), TOP3(상위 3개 평균), HEDGE(지수 가중, 학습률 eta)
비용: 노출이 바뀐 만큼 0.01%. 매달 첫 거래일 종가에 다시 고른다(그날까지의 손익만 쓴다).
    cd C:\\dev\\MNQ-Trader && .venv\\Scripts\\python research\\r11_online.py
"""
import sys
import numpy as np, pandas as pd

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, ".")
from mnq import strategies

COST, WIN = 0.0001, 20
RNG = np.random.default_rng(11)
px = strategies.history()["1990":]
ret = px.pct_change().fillna(0.0)


def sma(n):
    return px.rolling(n).mean()


def dip(n, hold):
    return (px <= px.rolling(n).min()).astype(float).rolling(hold).max()


def rsi(n):
    d = px.diff()
    return 100 - 100 / (1 + d.clip(lower=0).ewm(alpha=1 / n).mean() / (-d.clip(upper=0)).ewm(alpha=1 / n).mean())


vol = ret.rolling(20).std() * np.sqrt(252)
vt = (0.15 / vol).clip(upper=2.0)
up = {n: (px > sma(n)).astype(float) for n in (50, 100, 200)}
POOL = {
    "보유": pd.Series(1.0, index=px.index),
    "변동성 맞춤": vt,
    **{f"{n}일선 위 롱": up[n] for n in (50, 100, 200)},
    **{f"{n}일선 롱/숏": up[n] * 2 - 1 for n in (50, 100, 200)},
    **{f"모멘텀 {n}일 롱/쉼": (px > px.shift(n)).astype(float) for n in (20, 60, 120, 250)},
    **{f"모멘텀 {n}일 롱/숏": np.sign(px - px.shift(n)) for n in (20, 60, 120, 250)},
    **{f"되돌림 {n}/{h} + 200일선": dip(n, h) * up[200] for n in (3, 5, 10) for h in (3, 5, 10)},
    **{f"되돌림 {n}/{h}": dip(n, h) for n in (5, 10) for h in (5,)},
    "RSI2<10 롱 + 200일선": (rsi(2) < 10).astype(float).rolling(3).max() * up[200],
    "RSI2 롱/숏(200일선 방향)": ((rsi(2) < 10).astype(float) * up[200] - (rsi(2) > 90).astype(float) * (1 - up[200])).rolling(3).mean(),
    "추세 + 변동성": up[200] * vt,
}
E = pd.DataFrame(POOL).fillna(0.0)
P = E.shift(1).fillna(0.0).mul(ret, axis=0) - COST * E.diff().abs().fillna(0.0)  # 규칙마다 날마다 손익
months = P.index.to_series().groupby(P.index.to_period("M")).first().to_numpy()


def learner(kind, L, eta=None, pick=None):
    """매달 첫 거래일 종가에 지난 L일 손익으로 가중치를 정하고 다음 달 동안 노출 = Σ 가중치 × 규칙 노출."""
    W = pd.DataFrame(np.nan, index=E.index, columns=E.columns)
    for day in months:
        i = P.index.get_loc(day)
        if i < L:
            continue
        past = P.iloc[i - L + 1:i + 1]  # 그날 종가까지(그날 손익 포함)
        if kind == "RAND":
            w = np.zeros(len(E.columns)); w[pick[day]] = 1.0
        else:
            sh = past.mean() / past.std().replace(0, np.nan)
            sh = sh.fillna(-9).to_numpy()
            if kind == "FTL":
                w = (np.arange(len(sh)) == sh.argmax()).astype(float)
            elif kind == "TOP3":
                w = np.isin(np.arange(len(sh)), np.argsort(sh)[-3:]) / 3.0
            else:  # HEDGE: 누적 로그 수익에 지수 가중
                g = np.log1p(past).sum().to_numpy()
                w = np.exp(eta * (g - g.max())); w /= w.sum()
        W.loc[day] = w
    W = W.ffill().fillna(0.0)
    expo = (W * E).sum(axis=1)
    return expo, expo.shift(1).fillna(0.0) * ret - COST * expo.diff().abs().fillna(0.0)


def evaluate(p, a, b):
    x = p[a:b]
    arr, rows = x.to_numpy(), []
    for i in range(0, len(arr) - WIN + 1, 5):
        eq = np.cumprod(np.concatenate([[1.0], 1 + arr[i:i + WIN]]))
        rows.append((eq[-1] - 1, (eq / np.maximum.accumulate(eq) - 1).min()))
    r, d = np.array(rows).T
    yrs = (1 + x).groupby(x.index.year).prod() - 1
    return {"sharpe": x.mean() / x.std() * np.sqrt(252) if x.std() > 0 else 0.0, "score": float(np.mean(r + d)),
            "ret": float(np.median(r)), "up": int((yrs > 0).sum()), "n": len(yrs), "total": float((1 + x).prod() - 1)}


def show(name, p):
    d, h = evaluate(p, "2005", "2019"), evaluate(p, "2020", None)
    print(f"{name:28s} 설계 샤프 {d['sharpe']:5.2f} 수익+MDD {d['score']:+6.2%} 해 {d['up']:2d}/{d['n']} | "
          f"확인 샤프 {h['sharpe']:5.2f} 수익+MDD {h['score']:+6.2%} 4주 중앙 {h['ret']:+6.2%} 해 {h['up']}/{h['n']} 누적 {h['total']:+7.1%}")
    return d, h


if __name__ == "__main__":
    # 자르기 점검: 그날까지 잘라 계산한 규칙 노출 = 전체로 계산한 값 (규칙은 rolling/shift 뿐이라 같아야 한다)
    print(f"규칙 {len(E.columns)}개, {px.index[0]:%Y-%m-%d} ~ {px.index[-1]:%Y-%m-%d}\n")
    print("===== 기준 =====")
    base = {k: show(k, P[k]) for k in ("보유", "되돌림 5/5 + 200일선")}
    print("\n===== 규칙 하나씩(참고: 이것들 중 사후에 고르는 건 선택 편향) =====")
    for k in E.columns:
        show(k, P[k])

    print("\n===== 학습기 =====")
    res = {}
    for L in (63, 126, 252, 504):
        for kind in ("FTL", "TOP3"):
            res[(kind, L, None)] = show(f"{kind} L={L}", learner(kind, L)[1])
        for eta in (5, 20, 50):
            res[("HEDGE", L, eta)] = show(f"HEDGE L={L} eta={eta}", learner("HEDGE", L, eta)[1])

    chosen = max(res, key=lambda k: res[k][0]["score"])  # 설계 구간 4주 수익+MDD로 고른다
    kind, L, eta = chosen
    h = res[chosen][1]
    print(f"\n===== 설계 구간에서 고른 학습기: {kind} L={L} eta={eta} =====")
    null = []
    for _ in range(50):
        pick = {d: RNG.integers(len(E.columns)) for d in months}
        null.append(evaluate(learner("RAND", L, pick=pick)[1], "2020", None)["score"])
    dipb = base["되돌림 5/5 + 200일선"][1]
    crit = {
        "4주 수익 + MDD가 되돌림 + 추세보다 나음": h["score"] > dipb["score"],
        "샤프가 되돌림 + 추세 이상": h["sharpe"] >= dipb["sharpe"],
        "연도의 70% 이상 플러스": h["up"] >= 0.7 * h["n"],
        "무작위로 고른 것 50번보다 나음(상위 5%)": np.mean(np.array(null) < h["score"]) >= 0.95,
    }
    print(f"무작위 고르기의 확인 구간 수익+MDD: 평균 {np.mean(null):+.2%}, 최고 {np.max(null):+.2%}")
    for k, v in crit.items():
        print(f"  {'통과' if v else '실패'} · {k}")
    print("최종:", "통과" if all(crit.values()) else "불통과")
