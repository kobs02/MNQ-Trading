"""9차(머신러닝 2차): 실험 전에 정한 통과 기준(확인 구간 2020 ~에서 모두):
  1) 4주 수익 + MDD가 되돌림 + 추세보다 0.2%p 이상 낫다  2) 샤프가 되돌림 + 추세보다 0.1 넘게 낮지 않다
  3) 우연 점검(정답을 섞은 모델 20개)에서 상위 5% 안

실험
- A. 메타 라벨링(8차 M3)의 우연 점검: 거른 신호를 정답 섞은 메타 모델로 거르면 어떤가
- B. 변동성 예측으로 크기 조절: 다음 5일 실현 변동성을 LightGBM으로 예측(입력: 과거 변동성 1·5·20·60일, VIX·VXN, 최근 수익률, 고저 폭)
     → 되돌림 + 추세의 노출에 min(1.5, 목표 변동성 ÷ 예측 변동성)을 곱한다. 비교: 예측 없이 최근 20일 변동성으로 같은 일을 할 때(단순형),
     그리고 HAR(1·5·22일 평균 변동성의 선형 회귀, Corsi 2009)
학습: 해마다 그 전까지로 다시 학습(워크포워드, 2005 ~ 예측). 비용 0.01%. 입력은 8차와 같은 방식으로 만들고 자르기 점검을 한다
    cd C:\\dev\\MNQ-Trader && .venv\\Scripts\\python research\\r9_ml_round2.py
"""
import sys
import warnings
import numpy as np, pandas as pd
import lightgbm as lgb

warnings.filterwarnings("ignore")
sys.stdout.reconfigure(encoding="utf-8")
sys.argv = ["r8"]
src = open("research/r8_ml_daily.py", encoding="utf-8").read().split("check_no_leak()\ntech, full")[0]
g = {}
exec(src, g)  # 8차의 데이터, 입력, 평가 함수를 그대로 쓴다
m, px, ret, features, evaluate, pnl_of, line, PARAMS = (g[k] for k in ("m", "px", "ret", "features", "evaluate", "pnl_of", "line", "PARAMS"))
RNG = np.random.default_rng(9)
PERM = 20

_, full = features(m)
dip = ((px <= px.rolling(5).min()).astype(float).rolling(5).max() * (px > px.rolling(200).mean())).fillna(0.0)
base = evaluate(pnl_of(dip))
print(line("기준: 되돌림 + 추세", base))


def judge(name, e):
    b = base["확인"]
    c = {"수익+MDD 0.2%p 이상 나음": e["확인"]["score"] >= b["score"] + 0.002, "샤프 0.1 넘게 낮지 않음": e["확인"]["sharpe"] >= b["sharpe"] - 0.1}
    return c


# ---------- A. 메타 라벨링 우연 점검 ----------
entry = dip.diff() > 0
trade_ret = px.shift(-5) / px - 1


def meta(shuffle=False):
    pred = pd.Series(np.nan, index=px.index)
    first = full.index.searchsorted(pd.Timestamp("2001-06-01"))
    for year in range(2005, px.index[-1].year + 1):
        start = full.index.searchsorted(pd.Timestamp(f"{year}-01-01"))
        tr = [i for i in np.flatnonzero(entry.to_numpy()) if first <= i < start - 5 and np.isfinite(trade_ret.iloc[i])]
        te = np.flatnonzero(entry.to_numpy() & (full.index.year == year))
        if len(tr) < 50 or not len(te):
            continue
        y = trade_ret.iloc[tr].to_numpy()
        if shuffle:
            y = RNG.permutation(y)
        model = lgb.train({**PARAMS, "min_data_in_leaf": 20}, lgb.Dataset(full.iloc[tr], y), 200)
        pred.iloc[te] = model.predict(full.iloc[te])
    out = dip.copy()
    blocked = entry & (pred <= 0)
    for i in np.flatnonzero(blocked.to_numpy()):
        out.iloc[i:i + 5] = 0.0
    return out, int(blocked.sum())


expo, n_block = meta()
eA = evaluate(pnl_of(expo))
print("\n===== A. 메타 라벨링 =====")
print(line(f"메타 라벨링(거른 신호 {n_block}개)", eA))
null = []
for _ in range(PERM):
    e, k = meta(shuffle=True)
    null.append(evaluate(pnl_of(e))["확인"]["score"])
null = np.array(null)
print(f"정답 섞은 메타 모델 {PERM}개의 확인 구간 수익+MDD: 평균 {null.mean():+.2%}, 최고 {null.max():+.2%} · 진짜가 더 나은 비율 {np.mean(null < eA['확인']['score']):.0%}")
cA = judge("A", eA)
cA["우연 점검 상위 5%"] = np.mean(null < eA["확인"]["score"]) >= 0.95
for k, v in cA.items():
    print(f"  {'통과' if v else '실패'} · {k}")
print("A 최종:", "통과" if all(cA.values()) else "불통과")

# ---------- B. 변동성 예측으로 크기 조절 ----------
r = ret
rv = lambda n: r.rolling(n).std() * np.sqrt(252)
target_vol = 0.18
future = r[::-1].rolling(5).std()[::-1].shift(-1) * np.sqrt(252)  # 다음 5일(t+1 ~ t+5)의 실현 변동성
hi_lo = (m["NDX"].rolling(5).max() / m["NDX"].rolling(5).min() - 1)
V = pd.DataFrame({"rv1": r.abs() * np.sqrt(252), "rv5": rv(5), "rv20": rv(20), "rv60": rv(60), "vix": m["VIX"] / 100, "vxn": m["VXN"] / 100,
                  "ret5": px / px.shift(5) - 1, "ret20": px / px.shift(20) - 1, "hilo5": hi_lo}, index=px.index)


def vol_walk(kind, shuffle=False):
    pred = pd.Series(np.nan, index=px.index)
    first = V.index.searchsorted(pd.Timestamp("2001-06-01"))
    for year in range(2005, px.index[-1].year + 1):
        start = V.index.searchsorted(pd.Timestamp(f"{year}-01-01"))
        tr = np.arange(first, start - 6)
        te = np.flatnonzero(V.index.year == year)
        ok = tr[np.isfinite(future.iloc[tr].to_numpy()) & np.isfinite(V.iloc[tr].to_numpy()).all(1)]
        y = np.log(future.iloc[ok].to_numpy())
        if shuffle:
            y = RNG.permutation(y)
        if kind == "har":
            Xh = np.column_stack([np.ones(len(ok)), np.log(V.iloc[ok][["rv1", "rv5", "rv20"]].clip(lower=1e-4).to_numpy())])
            beta = np.linalg.lstsq(Xh, y, rcond=None)[0]
            Xt = np.column_stack([np.ones(len(te)), np.log(V.iloc[te][["rv1", "rv5", "rv20"]].clip(lower=1e-4).to_numpy())])
            pred.iloc[te] = np.exp(Xt @ beta)
        else:
            model = lgb.train({**PARAMS, "min_data_in_leaf": 100}, lgb.Dataset(V.iloc[ok], y), 300)
            pred.iloc[te] = np.exp(model.predict(V.iloc[te]))
    return pred


print("\n===== B. 변동성 예측으로 크기 조절 (목표 변동성 연 18%, 최대 1.5배) =====")
fut = future["2005":]
preds = {"단순형(최근 20일)": rv(20), "HAR": vol_walk("har"), "머신러닝": vol_walk("ml")}
for name, p in preds.items():
    a = p["2005":].reindex(fut.index)
    ok = a.notna() & fut.notna()
    err = np.mean(np.abs(np.log(a[ok]) - np.log(fut[ok])))
    print(f"{name:16s} 다음 5일 변동성 예측 오차(로그 절댓값 평균) {err:.3f} · 상관 {np.corrcoef(np.log(a[ok]), np.log(fut[ok]))[0, 1]:.2f}")
eB = {}
for name, p in preds.items():
    size = (target_vol / p).clip(upper=1.5)
    eB[name] = evaluate(pnl_of(dip * size))
    print(line(f"되돌림 + 추세 × {name}", eB[name]))
null = []
for _ in range(PERM):
    p = vol_walk("ml", shuffle=True)
    null.append(evaluate(pnl_of(dip * (target_vol / p).clip(upper=1.5)))["확인"]["score"])
null = np.array(null)
cB = judge("B", eB["머신러닝"])
cB["우연 점검 상위 5%"] = np.mean(null < eB["머신러닝"]["확인"]["score"]) >= 0.95
cB["단순형·HAR보다 나음"] = eB["머신러닝"]["확인"]["score"] > max(eB["단순형(최근 20일)"]["확인"]["score"], eB["HAR"]["확인"]["score"])
print(f"정답 섞은 변동성 모델 {PERM}개의 확인 구간 수익+MDD: 평균 {null.mean():+.2%}, 최고 {null.max():+.2%}")
for k, v in cB.items():
    print(f"  {'통과' if v else '실패'} · {k}")
print("B 최종:", "통과" if all(cB.values()) else "불통과")
