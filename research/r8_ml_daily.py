"""8차: 일봉 머신러닝. 실험 전에 정한 통과 기준(확인 구간 2020 ~에서 모두):
  1) 샤프가 되돌림 + 추세보다 0.3 이상 높다  2) 4주 수익 + MDD가 되돌림 + 추세보다 낫다
  3) 7개 연도 중 5개 이상 플러스            4) 정답을 섞은 모델 20개보다 낫다(상위 5% 안)
설계 구간 2005 ~ 2019에서 변형 가운데 하나를 고르고, 그것만 확인 구간으로 판정한다(다른 변형의 확인 구간 값은 참고로만 보여 준다).

실험
- M1 기술 지표만 / M2 기술 + 다른 시장(VIX·VXN·금리·달러·S&P·러셀·반도체) → 다음 날(y1) 또는 다음 5일(y5) 수익 예측
  노출: 롱/쉼(예측 > 0이면 1) 또는 롱/숏(부호대로 ±1)
- M3 메타 라벨링: 되돌림 + 추세 신호(진입하는 날) 가운데 머신러닝이 '5일 수익 > 0'으로 본 것만 들어간다
학습: 2001 ~ 그 해 전까지로 해마다 다시 학습(워크포워드), 정답 기간만큼 비운다. 비용: 노출이 바뀐 만큼 0.01%
    cd C:\\dev\\MNQ-Trader && .venv\\Scripts\\python research\\r8_ml_daily.py
"""
import sys
import warnings
import numpy as np, pandas as pd
import lightgbm as lgb

warnings.filterwarnings("ignore")
sys.stdout.reconfigure(encoding="utf-8")
COST, WIN, PERM = 0.0001, 20, 20
RNG = np.random.default_rng(5)
PARAMS = dict(objective="regression", learning_rate=0.03, num_leaves=7, min_data_in_leaf=200, feature_fraction=0.7,
              bagging_fraction=0.7, bagging_freq=1, lambda_l2=20, verbose=-1, seed=1, num_threads=8)

m = pd.read_pickle("store/data/macro_1d.pkl")
m = m[m["NDX"].notna()].ffill()["2000":]
px = m["NDX"]
ret = px.pct_change()


def rsi(s, n):
    d = s.diff()
    return 100 - 100 / (1 + d.clip(lower=0).ewm(alpha=1 / n).mean() / (-d.clip(upper=0)).ewm(alpha=1 / n).mean())


def features(m):
    px = m["NDX"]
    r = px.pct_change()
    tech = pd.DataFrame({
        **{f"ret{k}": px / px.shift(k) - 1 for k in (1, 5, 20, 60, 120, 250)},
        "vol20": r.rolling(20).std(), "vol60": r.rolling(60).std(), "volratio": r.rolling(20).std() / r.rolling(60).std(),
        **{f"sma{k}": px / px.rolling(k).mean() - 1 for k in (20, 50, 200)},
        "rsi2": rsi(px, 2), "rsi14": rsi(px, 14), "dd250": px / px.rolling(250).max() - 1,
        "dip5": (px <= px.rolling(5).min()).astype(float), "dow": px.index.dayofweek.astype(float), "month": px.index.month.astype(float),
    }, index=px.index)
    cross = pd.DataFrame({
        "vix": m["VIX"], "vix5": m["VIX"] / m["VIX"].shift(5) - 1, "vixrel": m["VIX"] / m["VIX"].rolling(60).mean(),
        "vxn": m["VXN"], "vxn_vix": m["VXN"] / m["VIX"], "tnx20": m["TNX"] - m["TNX"].shift(20), "dxy20": m["DXY"] / m["DXY"].shift(20) - 1,
        "ndx_spx20": (px / px.shift(20)) / (m["SPX"] / m["SPX"].shift(20)) - 1,
        "rut_spx20": (m["RUT"] / m["RUT"].shift(20)) / (m["SPX"] / m["SPX"].shift(20)) - 1,
        "sox_ndx20": (m["SOX"] / m["SOX"].shift(20)) / (px / px.shift(20)) - 1,
    }, index=px.index)
    return tech, pd.concat([tech, cross], axis=1)


def check_no_leak():
    """자르기 점검: 그 날까지만 잘라 계산한 입력 = 전체로 계산한 입력."""
    _, full = features(m)
    for i in RNG.choice(np.arange(400, len(m)), 25, replace=False):
        _, cut = features(m.iloc[: i + 1])
        a, b = full.iloc[i], cut.iloc[-1]
        bad = [c for c in full.columns if not np.isclose(a[c], b[c], equal_nan=True)]
        assert not bad, (m.index[i], bad)
    print("자르기 점검 통과: 미래 정보가 섞인 입력 없음\n")


def walk(X, y, horizon, shuffle=False):
    """해마다 다시 학습해 그 해를 예측한다(2005 ~). 학습은 2001 ~ 그 해 시작 - horizon일."""
    pred = pd.Series(np.nan, index=X.index)
    for year in range(2005, X.index[-1].year + 1):
        start = X.index.searchsorted(pd.Timestamp(f"{year}-01-01"))
        tr = np.arange(X.index.searchsorted(pd.Timestamp("2001-06-01")), max(start - horizon, 0))
        te = np.flatnonzero(X.index.year == year)
        ok = tr[np.isfinite(y.iloc[tr].to_numpy())]
        yt = y.iloc[ok].to_numpy()
        if shuffle:  # 해 단위 덩어리로 섞는다(같은 해 안의 순서는 둔다)
            years = X.index[ok].year
            uniq = np.unique(years)
            mapping = dict(zip(uniq, RNG.permutation(uniq)))
            yt = np.concatenate([np.resize(y.iloc[ok][years == mapping[u]].to_numpy(), (years == u).sum()) for u in uniq])
        model = lgb.train(PARAMS, lgb.Dataset(X.iloc[ok], yt), 200)
        pred.iloc[te] = model.predict(X.iloc[te])
    return pred


def pnl_of(expo):
    expo = expo.fillna(0.0)
    return (expo.shift(1).fillna(0.0) * ret - COST * expo.diff().abs().fillna(0.0)).dropna()


def evaluate(p):
    out = {}
    for name, a, b in (("설계", "2005", "2019"), ("확인", "2020", None)):
        x = p[a:b]
        arr = x.to_numpy()
        rows = []
        for i in range(0, len(arr) - WIN + 1, 5):
            eq = np.cumprod(np.concatenate([[1.0], 1 + arr[i:i + WIN]]))
            rows.append((eq[-1] - 1, (eq / np.maximum.accumulate(eq) - 1).min()))
        r, d = np.array(rows).T
        years = x.groupby(x.index.year).sum()
        out[name] = {"sharpe": x.mean() / x.std() * np.sqrt(252) if x.std() > 0 else 0.0, "score": float(np.mean(r + d)),
                     "years": f"{(years > 0).sum()}/{len(years)}", "up": int((years > 0).sum()), "n": len(years)}
    return out


def line(name, e):
    d, h = e["설계"], e["확인"]
    return (f"{name:34s} 설계 샤프 {d['sharpe']:5.2f} 4주 수익+MDD {d['score']:+6.2%} 플러스 해 {d['years']:>5s} | "
            f"확인 샤프 {h['sharpe']:5.2f} 수익+MDD {h['score']:+6.2%} 플러스 해 {h['years']:>5s}")


check_no_leak()
tech, full = features(m)
y1 = ret.shift(-1)
y5 = px.shift(-5) / px - 1

# 기준: 보유, 되돌림 + 추세(dip_trend)
dip = ((px <= px.rolling(5).min()).astype(float).rolling(5).max() * (px > px.rolling(200).mean())).fillna(0.0)
base = {"보유": evaluate(pnl_of(pd.Series(1.0, index=px.index))), "되돌림 + 추세": evaluate(pnl_of(dip))}
print("===== 기준 =====")
for k, v in base.items():
    print(line(k, v))

print("\n===== M1 · M2: 다음 날 / 다음 5일 수익 예측 =====")
results, preds = {}, {}
for xname, X in (("M1 기술", tech), ("M2 기술 + 다른 시장", full)):
    for yname, y, hz in (("y1", y1, 1), ("y5", y5, 5)):
        pred = walk(X, y, hz)
        preds[(xname, yname)] = (X, y, hz, pred)
        for mode in ("롱/쉼", "롱/숏"):
            expo = (pred > 0).astype(float) if mode == "롱/쉼" else np.sign(pred)
            results[(xname, yname, mode)] = evaluate(pnl_of(expo.where(pred.notna())))
            print(line(f"{xname} {yname} {mode}", results[(xname, yname, mode)]))

print("\n===== M3: 되돌림 + 추세 신호를 머신러닝으로 거르기(메타 라벨링) =====")
entry = (dip.diff() > 0)  # 새로 들어가는 날
trade_ret = px.shift(-5) / px - 1
meta_pred = pd.Series(np.nan, index=px.index)
X = full
for year in range(2005, px.index[-1].year + 1):
    start = X.index.searchsorted(pd.Timestamp(f"{year}-01-01"))
    tr = [i for i in np.flatnonzero(entry.to_numpy()) if X.index.searchsorted(pd.Timestamp("2001-06-01")) <= i < start - 5 and np.isfinite(trade_ret.iloc[i])]
    te = np.flatnonzero(entry.to_numpy() & (X.index.year == year))
    if len(tr) < 50 or not len(te):
        continue
    model = lgb.train({**PARAMS, "min_data_in_leaf": 20}, lgb.Dataset(X.iloc[tr], trade_ret.iloc[tr].to_numpy()), 200)
    meta_pred.iloc[te] = model.predict(X.iloc[te])
keep = meta_pred > 0
meta_dip = dip.copy()
blocked = entry & ~keep.fillna(False) & meta_pred.notna()
for i in np.flatnonzero(blocked.to_numpy()):  # 걸러 낸 신호의 5일 보유를 지운다
    meta_dip.iloc[i:i + 5] = 0.0
results["M3 메타 라벨링"] = evaluate(pnl_of(meta_dip))
print(line("M3 메타 라벨링", results["M3 메타 라벨링"]))
print(f"(신호 {int(entry['2005':].sum())}개 중 머신러닝이 거른 것 {int(blocked.sum())}개)")

# 설계 구간에서 하나를 고른다
chosen = max(results, key=lambda k: results[k]["설계"]["sharpe"])
e = results[chosen]
print(f"\n===== 설계 구간에서 고른 변형: {chosen} =====")
dipb = base["되돌림 + 추세"]["확인"]
crit = {
    "샤프가 되돌림 + 추세 + 0.3 이상": e["확인"]["sharpe"] >= dipb["sharpe"] + 0.3,
    "4주 수익 + MDD가 되돌림 + 추세보다 나음": e["확인"]["score"] > dipb["score"],
    "7개 연도 중 5개 이상 플러스": e["확인"]["up"] >= 5,
}
if chosen in [k for k in results if k != "M3 메타 라벨링"]:
    X, y, hz, _ = preds[chosen[:2]]
    mode = chosen[2]
    null = []
    for _ in range(PERM):
        p = walk(X, y, hz, shuffle=True)
        ex = (p > 0).astype(float) if mode == "롱/쉼" else np.sign(p)
        null.append(evaluate(pnl_of(ex.where(p.notna())))["확인"]["sharpe"])
    crit["정답 섞은 모델 20개보다 나음(상위 5%)"] = np.mean(np.array(null) < e["확인"]["sharpe"]) >= 0.95
    print(f"정답 섞은 모델의 확인 구간 샤프: 평균 {np.mean(null):.2f}, 최고 {np.max(null):.2f}")
for k, v in crit.items():
    print(f"  {'통과' if v else '실패'} · {k}")
print("최종:", "통과" if all(crit.values()) else "불통과")
