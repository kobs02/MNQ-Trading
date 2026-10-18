"""12차: 장중 1시간봉 머신러닝. 섞기 후보의 장중 갈래(노이즈 영역 돌파)와 같은 자리(평일 뉴욕 10 ~ 15시 정시)에서
다음 한 시간 수익을 LightGBM으로 예측해 롱/숏/쉼을 정한다. 같은 시각·같은 비용으로 노이즈 영역 돌파와 비교한다.

실험 전에 정한 통과 기준(확인 구간 = 예측이 나온 날의 뒤쪽 절반에서 모두):
  1) 샤프가 노이즈 영역 돌파보다 0.3 이상 높다   2) 4주 수익 + MDD가 노이즈 영역 돌파보다 낫다
  3) 정답을 날짜째 섞은 모델 20개보다 낫다(상위 5%)  4) 확인 구간 달의 60% 이상 플러스
변형(롱/숏 vs 문턱 넘을 때만)은 앞쪽 절반(설계)에서 고른다. 학습: 앞의 120일 이상으로 매달 다시 학습, 하루 비움.
입력은 자르기 점검(그 봉까지 잘라 계산 = 전체로 계산)으로 확인한다. 데이터: NQ=F 1시간봉(야후, 약 2년)
    cd C:\\dev\\MNQ-Trader && .venv\\Scripts\\python research\\r12_ml_hourly.py
"""
import sys
import warnings
import numpy as np, pandas as pd
import lightgbm as lgb

warnings.filterwarnings("ignore")
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, ".")
from mnq import strategies

COST, WIN, PERM = 0.00005, 20, 20
RNG = np.random.default_rng(12)
PARAMS = dict(objective="regression", learning_rate=0.03, num_leaves=5, min_data_in_leaf=80, feature_fraction=0.7,
              bagging_fraction=0.7, bagging_freq=1, lambda_l2=20, verbose=-1, seed=1, num_threads=8)
H = strategies.hourly()
H = H[H.index.dayofweek < 5]


def features(h):
    """(날짜, 판단 시각 10 ~ 15) 마다의 입력. 쓰는 봉은 판단 시각 전에 끝난 것뿐."""
    mv = strategies.moves(h)                                  # 9시 시가 대비 hour 봉 종가
    c16 = h[h.index.hour == 15]["close"]; c16.index = c16.index.normalize()   # 정규장 마감(15시 봉 종가)
    o9 = h[h.index.hour == 9]["open"]; o9.index = o9.index.normalize()
    r1 = h["close"].pct_change()
    rv = r1.groupby(h.index.normalize()).apply(lambda s: s.abs().sum())  # 하루 전체 움직임(전날 것만 쓴다)
    rows = []
    for d in mv.index:
        prev = c16[c16.index < d]
        if not len(prev) or d not in o9.index:
            continue
        past = mv[mv.index < d].tail(strategies.NOISE_DAYS)
        for hr in strategies.INTRADAY_HOURS:
            bar = hr - 1
            m = mv.at[d, bar] if bar in mv.columns else np.nan
            if np.isnan(m) or past[bar].notna().sum() < strategies.NOISE_DAYS:
                continue
            last = h[(h.index.normalize() == d) & (h.index.hour == bar)]
            pr = h[(h.index.normalize() == d) & (h.index.hour == bar - 1)]
            rows.append({"date": d, "hour": hr, "m": m, "z": m / past[bar].abs().mean(), "gap": o9[d] / prev.iloc[-1] - 1,
                         "prevday": prev.iloc[-1] / prev.iloc[-2] - 1 if len(prev) > 1 else np.nan,
                         "trend20": prev.iloc[-1] / prev.tail(20).mean() - 1,
                         "last": last["close"].iloc[0] / pr["close"].iloc[0] - 1 if len(pr) else np.nan,
                         "rvprev": rv[rv.index < d].tail(5).mean(), "range": (h[(h.index.normalize() == d) & (h.index.hour <= bar) & (h.index.hour >= 9)]["high"].max()
                                                                            - h[(h.index.normalize() == d) & (h.index.hour <= bar) & (h.index.hour >= 9)]["low"].min()) / o9[d]})
    return pd.DataFrame(rows).set_index(["date", "hour"])


def target(h):
    """판단 시각 hour에 들어 hour+1에 나올 때의 수익(hour 봉의 시가 → 종가)."""
    t = h[(h.index.hour >= 10) & (h.index.hour <= 15)]
    y = t["close"] / t["open"] - 1
    y.index = pd.MultiIndex.from_arrays([t.index.normalize(), t.index.hour], names=["date", "hour"])
    return y


def check_no_leak(X):
    keys = X.index[RNG.choice(np.arange(200, len(X)), 15, replace=False)]
    for d, hr in keys:
        cut = features(H[H.index < d + pd.Timedelta(hours=hr)])  # 판단 시각 전 봉까지
        a, b = X.loc[(d, hr)], cut.loc[(d, hr)]
        bad = [c for c in X.columns if not np.isclose(a[c], b[c], equal_nan=True)]
        assert not bad, ((d, hr), bad)
    print("자르기 점검 통과: 미래 정보가 섞인 입력 없음")


def walk(X, y, shuffle=False):
    days = X.index.get_level_values(0)
    uniq = np.unique(days)
    pred = pd.Series(np.nan, index=X.index)
    months = pd.Series(uniq).groupby(pd.Series(uniq).dt.to_period("M")).first()
    for start in months:
        if (uniq < start).sum() < 120:
            continue
        tr = days < start - pd.Timedelta(days=1)
        te = (days >= start) & (days < start + pd.offsets.MonthBegin(1))
        yt = y[tr].to_numpy()
        if shuffle:  # 날짜째 섞는다(하루 안의 시각 순서는 둔다)
            d = days[tr]
            u = np.unique(d)
            mp = dict(zip(u, RNG.permutation(u)))
            yt = np.concatenate([np.resize(y[tr][d == mp[k]].to_numpy(), (d == k).sum()) for k in u])
        model = lgb.train(PARAMS, lgb.Dataset(X[tr], yt), 150)
        pred[te] = model.predict(X[te])
    return pred


def daily_pnl(side, y):
    """시간마다 방향 × 수익 − 방향이 바뀐 만큼 비용(16시엔 0으로 정리) → 날마다 합."""
    s = side.fillna(0.0).unstack().reindex(columns=list(strategies.INTRADAY_HOURS)).fillna(0.0)
    r = y.unstack().reindex(columns=s.columns).fillna(0.0)
    turn = s.diff(axis=1).abs().fillna(s.abs()).sum(axis=1) + s[15].abs()
    return (s * r).sum(axis=1) - turn * COST


def evaluate(p):
    arr, rows = p.to_numpy(), []
    for i in range(0, len(arr) - WIN + 1, 5):
        eq = np.cumprod(np.concatenate([[1.0], 1 + arr[i:i + WIN]]))
        rows.append((eq[-1] - 1, (eq / np.maximum.accumulate(eq) - 1).min()))
    r, d = np.array(rows).T
    mo = (1 + p).groupby(p.index.to_period("M")).prod() - 1
    return {"sharpe": p.mean() / p.std() * np.sqrt(252) if p.std() > 0 else 0.0, "score": float(np.mean(r + d)),
            "months": float((mo > 0).mean()), "total": float((1 + p).prod() - 1)}


def show(name, e):
    return (f"{name:26s} " + " | ".join(f"{k} 샤프 {v['sharpe']:5.2f} 수익+MDD {v['score']:+6.2%} 플러스 달 {v['months']:4.0%} 누적 {v['total']:+6.1%}"
                                         for k, v in e.items()))


if __name__ == "__main__":
    X = features(H)
    y = target(H).reindex(X.index)
    ok = y.notna()
    X, y = X[ok], y[ok]
    check_no_leak(X)
    pred = walk(X, y)
    have = pred.notna()
    days = pd.DatetimeIndex(np.unique(X.index.get_level_values(0)[have]))
    cut = days[len(days) // 2]
    print(f"표본 {len(X)}개, 예측 {have.sum()}개, 설계 {days[0]:%Y-%m-%d} ~ / 확인 {cut:%Y-%m-%d} ~ {days[-1]:%Y-%m-%d}\n")

    def split(side):
        p = daily_pnl(side[have], y[have])
        return {"설계": evaluate(p[p.index < cut]), "확인": evaluate(p[p.index >= cut])}

    noise = strategies.noise_sides(strategies.moves(H), 1.0).stack().reindex(X.index)
    base = split(noise)
    print(show("노이즈 영역 1배 돌파", base))
    variants = {"롱/숏(부호)": lambda p: np.sign(p),
                "상위 50%만": lambda p: np.sign(p).where(p.abs() > p.abs().expanding().median().shift(1), 0.0),
                "롱만": lambda p: (p > 0).astype(float),
                "노이즈 돌파 + 머신러닝 동의": lambda p: noise.where(np.sign(p) == noise, 0.0)}
    res = {k: split(f(pred)) for k, f in variants.items()}
    for k, v in res.items():
        print(show(k, v))
    chosen = max(res, key=lambda k: res[k]["설계"]["score"])
    e = res[chosen]["확인"]
    print(f"\n===== 설계 구간에서 고른 변형: {chosen} =====")
    null = []
    for _ in range(PERM):
        pn = walk(X, y, shuffle=True)
        null.append(split(variants[chosen](pn))["확인"]["sharpe"])
    b = base["확인"]
    crit = {"샤프가 노이즈 돌파 + 0.3 이상": e["sharpe"] >= b["sharpe"] + 0.3, "4주 수익 + MDD가 노이즈 돌파보다 나음": e["score"] > b["score"],
            "정답 섞은 모델 20개보다 나음(상위 5%)": np.mean(np.array(null) < e["sharpe"]) >= 0.95, "확인 구간 달의 60% 이상 플러스": e["months"] >= 0.6}
    print(f"정답 섞은 모델의 확인 구간 샤프: 평균 {np.mean(null):.2f}, 최고 {np.max(null):.2f}")
    for k, v in crit.items():
        print(f"  {'통과' if v else '실패'} · {k}")
    print("최종:", "통과" if all(crit.values()) else "불통과")
