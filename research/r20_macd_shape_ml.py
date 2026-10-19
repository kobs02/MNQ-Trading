"""20차: MACD 동시 반전 신호 + 캔들 파동 모양 학습(메타 라벨링) (10-19 사용자 요청).

MACD 동시 반전(18차, 1·5·15·60분봉, 약·중·강 모든 신호)이 후보를 내면, 그 직전 캔들 모양을 학습한 모델이 '30분 뒤 비용 빼고 이익일까'를
확률로 내고, 0.5 넘는 신호만 든다. 진입은 다음 1분봉 시가, 청산은 30분 뒤 시가, 1계약. 비용 왕복 2.5포인트(18차와 같음).

모양 입력(신호 방향으로 뒤집어서: 숏이면 오르내림과 위아래 꼬리를 바꿔 롱과 같은 모양으로 본다):
  1분봉 마지막 30개 종가 경로(지금 종가 대비, 최근 60분 변동성으로 나눔), 마지막 10개 봉의 몸통·윗꼬리·아랫꼬리(평균 봉 길이로 나눔),
  5분봉 마지막 12개 종가 경로, 그리고 신호 정보(약·중·강, 꺾인 단위 수, 방향 맞춘 RSI 1·5·15분, 뉴욕 시각).
학습: 날마다 다시 학습(워크포워드). 그날 전에 청산까지 끝난 신호만 학습에 쓴다(정답이 미래에 걸치지 않게).
모델: LightGBM(7차와 같은 작은 설정, 고르기 없음). 관찰용으로 k-최근접(파동 모양이 가장 닮은 과거 25개의 승률)도 본다.

통과 기준 (결과를 보기 전에 정함. 앞 절반 = 설계, 뒤 절반 = 확인. 둘 다 워크포워드이고 설계 구간은 처음 5일을 학습만 한다)
  1. 확인 구간에서 고른 신호의 비용 뒤 합계 > 0
  2. 확인 구간에서 고른 신호의 건당 손익이 거르지 않은 전체 신호의 건당보다 좋다
  3. 확인 구간에서 같은 비율을 무작위로 고른 것(1,000번)보다 합계가 95% 이상 좋다
  4. 설계 구간의 고른 신호 합계도 > 0

    cd C:\\dev\\MNQ-Trader && .venv\\Scripts\\python research\\r20_macd_shape_ml.py
"""
import sys
import numpy as np, pandas as pd
import lightgbm as lgb

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, ".")
from mnq import strategies

COST, HOLD = 2.5, 30
PARAMS = dict(objective="binary", learning_rate=0.03, num_leaves=7, min_data_in_leaf=40, feature_fraction=0.7,
              bagging_fraction=0.7, bagging_freq=1, lambda_l2=20, verbose=-1, seed=3, num_threads=8)
M = strategies.minute_history()
O, Hh, L, C = (M[k].to_numpy() for k in ("open", "high", "low", "close"))
T = M.index


def shape(i, side):
    """i번째 1분봉(마감)까지만 보고 만든 모양 입력. 숏이면 롱 모양으로 뒤집는다."""
    lo = i - 59
    c = C[lo:i + 1]
    vol = np.std(np.diff(np.log(c))) or 1e-6
    path = (c[-30:] / c[-1] - 1) / vol * side
    rng = np.mean(Hh[i - 29:i + 1] - L[i - 29:i + 1]) or 0.25
    o, h, l, cc = O[i - 9:i + 1], Hh[i - 9:i + 1], L[i - 9:i + 1], C[i - 9:i + 1]
    body = (cc - o) / rng * side
    up, dn = (h - np.maximum(o, cc)) / rng, (np.minimum(o, cc) - l) / rng
    if side < 0:
        up, dn = dn, up
    five = M["close"].iloc[i - 59:i + 1].resample("5min", label="left", closed="left").last().dropna().to_numpy()[-12:]
    five = (five / five[-1] - 1) / (vol * np.sqrt(5)) * side
    return np.concatenate([path, body, up, dn, np.pad(five, (12 - len(five), 0))])


# ---------- 신호와 정답 ----------
pos = {int(np.searchsorted(T, pd.Timestamp(e["time"], unit="s", tz="UTC").tz_convert("America/New_York").tz_localize(None))): e
       for e in strategies.macd_mtf_events(M)}
rows = []
for i, e in sorted(pos.items()):
    if i < 60 or i + 1 >= len(T):
        continue
    a = i + 1
    b = int(np.searchsorted(T, T[a] + pd.Timedelta(minutes=HOLD)))
    if b >= len(T) or T[b] - T[a] > pd.Timedelta(minutes=HOLD + 60):  # 장 쉬는 시간을 넘긴 매매는 뺀다
        continue
    side = e["side"]
    r = e["rsi"]
    meta = [e["level"], len(e["tfs"])] + [(r.get(k, 50) if side > 0 else 100 - r.get(k, 50)) for k in (1, 5, 15)] + [T[i].hour + T[i].minute / 60]
    rows.append({"i": i, "t": T[i], "exit_t": T[b], "side": side, "pnl": side * (O[b] - O[a]) - COST, "x": np.concatenate([shape(i, side), meta])})
D = pd.DataFrame(rows)
X = np.stack(D["x"]); y = (D["pnl"] > 0).to_numpy().astype(int)
print(f"신호 {len(D)}개 (1분봉 {T[0]:%Y-%m-%d} ~ {T[-1]:%Y-%m-%d}), 입력 {X.shape[1]}개, 비용 뒤 이익인 비율 {y.mean():.0%}, 건당 {D.pnl.mean():+.2f}pt")

# 자르기 점검: 신호 봉에서 자료를 잘라 다시 만든 모양 입력 = 전체 자료로 만든 것
full = [shape(D.i[k], D.side[k]) for k in range(0, len(D), 97)]
M_all, arrays = M, (O, Hh, L, C)
cut_ok = True
for n, k in enumerate(range(0, len(D), 97)):
    M = M_all.iloc[:D.i[k] + 1]
    O, Hh, L, C = (M[c].to_numpy() for c in ("open", "high", "low", "close"))
    cut_ok &= np.allclose(shape(D.i[k], D.side[k]), full[n])
M, (O, Hh, L, C) = M_all, arrays
assert cut_ok, "미래 정보 누출(모양 입력)"
print("자르기 점검 통과(모양 입력)")


def walk(days, first_test, model):
    """first_test 날부터 날마다: 그날 전에 끝난 신호로 학습해 그날 신호의 확률을 낸다."""
    prob = np.full(len(D), np.nan)
    day = D["t"].dt.normalize()
    for d in days[days >= first_test]:
        test = (day == d).to_numpy()
        train = (D["exit_t"] < d).to_numpy()
        if test.sum() == 0 or train.sum() < 100:
            continue
        prob[test] = model(X[train], y[train], X[test])
    return prob


def lgbm(xt, yt, xs):
    return lgb.train(PARAMS, lgb.Dataset(xt, yt), 150).predict(xs)


def knn(xt, yt, xs, k=25):
    """파동 모양(1분봉 30개 종가 경로)이 가장 닮은 과거 k개의 승률."""
    a, b = xt[:, :30], xs[:, :30]
    dist = ((b[:, None, :] - a[None, :, :]) ** 2).sum(-1)
    idx = np.argsort(dist, axis=1)[:, :k]
    return yt[idx].mean(1)


days = D["t"].dt.normalize().unique()
days = pd.DatetimeIndex(sorted(days))
mid = days[len(days) // 2]
rng = np.random.default_rng(0)
for name, model in (("LightGBM(판정)", lgbm), ("k-최근접 25(관찰)", knn)):
    prob = walk(days, days[5], model)
    print(f"\n{name}")
    res = {}
    for seg, mask in (("설계", (D["t"] < mid) & ~np.isnan(prob)), ("확인", (D["t"] >= mid) & ~np.isnan(prob))):
        mask = mask.to_numpy()
        allp = D.pnl[mask].to_numpy()
        keep = mask & (prob > 0.5)
        kp = D.pnl[keep].to_numpy()
        rate = keep.sum() / max(mask.sum(), 1)
        rand = np.array([rng.choice(allp, keep.sum(), replace=False).sum() for _ in range(1000)]) if keep.sum() else np.zeros(1)
        beat = float((kp.sum() > rand).mean()) if keep.sum() else 0.0
        res[seg] = (kp.sum(), kp.mean() if len(kp) else np.nan, allp.mean(), beat)
        print(f"  {seg}: 전체 {mask.sum()}개 건당 {allp.mean():+.2f}pt (합계 {allp.sum():+.0f}) → 고른 {keep.sum()}개({rate:.0%}) 건당 "
              f"{(kp.mean() if len(kp) else float('nan')):+.2f}pt, 합계 {kp.sum():+.0f}pt (1계약 ${kp.sum() * 2:+,.0f}), "
              f"승률 {((kp > 0).mean() if len(kp) else float('nan')):.0%}, 같은 비율 무작위보다 나은 비율 {beat:.0%}")
    c, d = res["확인"], res["설계"]
    ok = [c[0] > 0, c[1] > c[2], c[3] >= 0.95, d[0] > 0]
    print(f"  기준 1 확인 합계 > 0: {ok[0]} · 기준 2 건당 개선: {ok[1]} · 기준 3 무작위 95%: {ok[2]} · 기준 4 설계 > 0: {ok[3]} → "
          f"{'통과' if all(ok) else '불통과'}{' (관찰용이라 판정 아님)' if '관찰' in name else ''}")
