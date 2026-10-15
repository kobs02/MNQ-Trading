"""7차: 머신러닝 스캘핑. 정규장의 모든 봉에서 "지금 롱(숏)으로 들어가면 ATR 익절·손절로 몇 포인트를 버는가"를 LightGBM으로 예측하고,
예측이 높은 봉에서만 들어간다.

- 입력(그 봉이 마감된 때까지의 정보만): 최근 1·3·6·12봉 수익률, 변동성 비율, VWAP·EMA20·EMA60과의 거리, EMA60 기울기, RSI2·RSI14,
  거래량 비율, 봉 폭, 봉 안의 종가 위치, 장 시작 뒤 시간, 시가 범위(9:30 ~ 9:45)와의 거리, 그날 시가 대비 움직임, 밤사이 갭 (가격 거리는 모두 ATR 단위)
- 정답: 다음 봉 시가에 진입해 익절 A×ATR / 손절 B×ATR / 최대 H봉으로 청산했을 때의 포인트(같은 봉에서 둘 다 닿으면 손절)
- 검증: 날짜 순 워크포워드. 앞의 날들로 학습하고 다음 STEP일을 예측, 학습과 예측 사이에 하루를 비운다(정답 누출 방지). 처음 MIN_DAYS일은 학습만
- 우연 점검: 정답을 날짜 덩어리째 섞어 학습한 모델로 같은 일을 PERM번 해서, 진짜 모델의 성적이 그 분포의 어디쯤인지 본다
- 비용: 왕복 1.5포인트. 거래 기준: 예측 순이익 > 0(비용 뒤), 그리고 예측 상위 10% / 5%
    cd C:\\dev\\MNQ-Trader && .venv\\Scripts\\python research\\r7_scalp_ml.py
"""
import glob
import sys
import warnings
import numpy as np, pandas as pd
import lightgbm as lgb

warnings.filterwarnings("ignore")
sys.stdout.reconfigure(encoding="utf-8")
COST, PERM = 1.5, 20
RNG = np.random.default_rng(11)
PARAMS = dict(objective="regression", learning_rate=0.03, num_leaves=7, min_data_in_leaf=80, feature_fraction=0.7,
              bagging_fraction=0.7, bagging_freq=1, lambda_l2=20, verbose=-1, seed=3, num_threads=8)


def features(df):
    o, hi, lo, c, v = (df[k].to_numpy(dtype=float) for k in ("open", "high", "low", "close", "volume"))
    t = df.index
    minute = (t.hour * 60 + t.minute).to_numpy()
    date = t.normalize()
    rth = (minute >= 570) & (minute < 960)
    close = pd.Series(c)
    atr = (pd.Series(hi) - pd.Series(lo)).rolling(14).mean().to_numpy()
    atr_long = (pd.Series(hi) - pd.Series(lo)).rolling(120).mean().to_numpy()
    ema20 = close.ewm(span=20, adjust=False).mean().to_numpy()
    ema60 = close.ewm(span=60, adjust=False).mean().to_numpy()
    diff = close.diff()
    rsi = lambda a: (100 - 100 / (1 + diff.clip(lower=0).ewm(alpha=a).mean() / (-diff.clip(upper=0)).ewm(alpha=a).mean())).to_numpy()
    tp = (hi + lo + c) / 3
    pv = pd.Series(np.where(rth, tp * v, 0.0)).groupby(date).cumsum().to_numpy()
    vv = pd.Series(np.where(rth, v, 0.0)).groupby(date).cumsum().to_numpy()
    vwap = np.where(vv > 0, pv / np.maximum(vv, 1e-9), np.nan)
    first = (minute >= 570) & (minute < 585)
    orh = pd.Series(np.where(first, hi, np.nan)).groupby(date).transform("max").to_numpy()
    orl = pd.Series(np.where(first, lo, np.nan)).groupby(date).transform("min").to_numpy()
    # 날짜 단위 값은 날짜별로 한 번 구해서 펼친다. (처음에는 15:55 종가를 펼친 뒤 '한 줄' 밀어서 그날의 장 마감 가격이
    # 그날 거의 모든 봉에 들어갔다: 미래 정보 누출이었다)
    day_open = pd.Series(np.where(minute == 570, o, np.nan)).groupby(date).transform("max").to_numpy()  # 정규장 봉에서만 쓴다(9:30 봉 시가는 그 봉이 마감되면 안다)
    closes = pd.Series(np.where(minute == 955, c, np.nan), index=t).groupby(date).max()  # 날짜 → 그날 15:55 봉 종가
    prev_close = pd.Series(closes.shift(1).to_numpy(), index=closes.index).reindex(date).to_numpy()  # 전날 것
    X = pd.DataFrame({
        **{f"ret{k}": (c - np.roll(c, k)) / atr for k in (1, 3, 6, 12)},
        "vol_ratio_atr": atr / atr_long,
        "d_vwap": (c - vwap) / atr, "d_ema20": (c - ema20) / atr, "d_ema60": (c - ema60) / atr,
        "slope60": (ema60 - np.roll(ema60, 5)) / atr,
        "rsi2": rsi(0.5), "rsi14": rsi(1 / 14),
        "volume_ratio": v / pd.Series(v).rolling(20).mean().to_numpy(),
        "range": (hi - lo) / atr, "close_loc": (c - lo) / np.maximum(hi - lo, 1e-9),
        "minute": minute - 570,
        "d_orh": np.where(minute >= 585, (c - orh) / atr, np.nan), "d_orl": np.where(minute >= 585, (c - orl) / atr, np.nan),
        "from_open": (c - day_open) / atr, "gap": (day_open - prev_close) / atr,
    }, index=df.index)
    return X, atr, rth


def labels(df, atr, a, b, h):
    """롱·숏 각각 다음 봉 시가에 진입했을 때의 포인트(비용 전)."""
    o, hi, lo, c = (df[k].to_numpy(dtype=float) for k in ("open", "high", "low", "close"))
    n = len(df)
    out = {}
    for side in (1, -1):
        y = np.full(n, np.nan)
        for i in range(n - h - 1):
            e, A = o[i + 1], atr[i]
            if not np.isfinite(A) or A <= 0:
                continue
            res = None
            for k in range(1, h + 1):
                fav = (hi[i + k] - e) if side > 0 else (e - lo[i + k])
                adv = (e - lo[i + k]) if side > 0 else (hi[i + k] - e)
                if adv >= b * A:
                    res = -b * A
                    break
                if fav >= a * A:
                    res = a * A
                    break
            y[i] = res if res is not None else side * (c[i + h] - e)
        out[side] = y
    return out


def walk(X, y, days, date, mask, min_days, step, shuffle=False):
    """워크포워드 예측. shuffle이면 학습용 정답을 날짜 덩어리째 섞는다(우연 점검)."""
    pred = np.full(len(y), np.nan)
    ok = mask & np.isfinite(y)
    for i in range(min_days, len(days), step):
        train = ok & (date < days[i - 1])  # 하루를 비운다
        test = ok & (date >= days[i]) & (date < days[min(i + step, len(days) - 1)] if i + step < len(days) else ok & (date >= days[i]))
        if train.sum() < 300 or test.sum() == 0:
            continue
        yt = y[train]
        if shuffle:
            blocks = date[train]
            uniq = blocks.unique()
            perm = dict(zip(uniq, RNG.permutation(uniq)))
            by_day = {d: yt[blocks == d] for d in uniq}
            yt = np.concatenate([np.resize(by_day[perm[d]], (blocks == d).sum()) for d in uniq])
        model = lgb.train(PARAMS, lgb.Dataset(X[train], yt), 150)
        pred[test] = model.predict(X[test])
    return pred


def score(pred, y, q=None):
    """예측이 기준을 넘는 봉에서 들어갔을 때: (거래 수, 비용 뒤 평균, t)."""
    ok = np.isfinite(pred) & np.isfinite(y)
    p, r = pred[ok], y[ok]
    take = p > (np.quantile(p, 1 - q) if q else COST)
    net = r[take] - COST
    if len(net) < 5:
        return len(net), np.nan, np.nan
    return len(net), net.mean(), net.mean() / net.std() * np.sqrt(len(net))


def study(df, label, setups, min_days, step):
    X, atr, rth = features(df)
    date = df.index.normalize()
    days = date.unique()
    print(f"\n######## {label}: {len(days)}일, 처음 {min_days}일은 학습만, {step}일씩 예측 ########")
    for a, b, h in setups:
        ys = labels(df, atr, a, b, h)
        for side, name in ((1, "롱"), (-1, "숏")):
            y = ys[side]
            base = y[rth & np.isfinite(y) & (date >= days[min_days])] - COST
            pred = walk(X.to_numpy(), y, days, date, rth, min_days, step)
            real = {q: score(pred, y, q) for q in (None, 0.10, 0.05)}
            null = {q: [score(walk(X.to_numpy(), y, days, date, rth, min_days, step, shuffle=True), y, q)[1] for _ in range(PERM)] for q in (0.10,)}
            nm = np.array([v for v in null[0.10] if np.isfinite(v)])
            rank = (nm < real[0.10][1]).mean() if len(nm) else np.nan
            print(f"익절 {a}ATR 손절 {b}ATR 최대 {h}봉 {name}: 모든 봉에서 들어가면 {base.mean():+.2f}pt({len(base)}번)"
                  f" | 예측 > 비용: {real[None][0]}번 {real[None][1]:+.2f}pt (t {real[None][2]:+.1f})"
                  f" | 상위 10%: {real[0.10][0]}번 {real[0.10][1]:+.2f}pt (t {real[0.10][2]:+.1f})"
                  f" | 상위 5%: {real[0.05][0]}번 {real[0.05][1]:+.2f}pt (t {real[0.05][2]:+.1f})"
                  f" | 정답 섞은 모델 상위 10% 평균 {np.mean(nm):+.2f}pt, 진짜 모델이 그보다 나은 비율 {rank:.0%}")


d5 = pd.read_pickle("store/data/NQF_5m.pkl").rename(columns=str.lower)
d5.index = d5.index.tz_convert("America/New_York")
study(d5[["open", "high", "low", "close", "volume"]], "야후 NQ=F 5분봉", [(1.0, 1.0, 6), (2.0, 1.0, 12), (3.0, 2.0, 3)], 25, 5)

d1 = pd.read_pickle(sorted(glob.glob("store/data/ls_*_1m.pkl"))[-1])
vol = d1["volume"].groupby(d1.index.normalize()).sum()
d1 = d1[d1.index.normalize().isin(vol[vol > vol.max() * 0.2].index)]
study(d1, "LS MNQZ26 1분봉", [(1.0, 1.0, 15), (2.0, 1.0, 30)], 10, 3)
