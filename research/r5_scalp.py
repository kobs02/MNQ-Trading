"""5차: 스캘핑(1분봉, 몇 분 ~ 1시간 보유). 승률이 가장 높은 규칙과, 비용 뒤 기대 손익이 가장 높은 규칙을 찾고 앞쪽 기간에서 고른 것을 뒤쪽 기간에서 확인한다.

- 데이터: LS증권 MNQ 근월물 1분봉(research/fetch_1m.py로 받은 것). 거래가 적던 시기(분당 거래량이 아주 작음)는 뺀다
- 진입: 봉이 마감될 때 신호 → 다음 봉 시가에 진입. 청산: 익절 TP, 손절 SL(포인트), 최대 H분. 같은 봉에서 둘 다 닿으면 손절로 본다(보수적)
- 비용: 한 번 사고팔 때 COST 포인트(기본 1.5 = $3/계약: 수수료 왕복 약 $2 + 미끄러짐 0.25포인트 × 2). 1포인트 = $2
- 고르는 방법: 앞쪽 60%(날짜 기준)에서 거래 50번 이상인 것 가운데 고르고, 뒤쪽 40%에서 그대로 확인. 여러 개를 시험했으므로 앞쪽의 최고값은 운이 섞인 값이다
    cd C:\\dev\\MNQ-Trader && .venv\\Scripts\\python research\\r5_scalp.py
"""
import glob
import itertools
import sys
import numpy as np, pandas as pd

sys.stdout.reconfigure(encoding="utf-8")
COST = 1.5
HMAX = 60

path = sorted(glob.glob("store/data/ls_*_1m.pkl"))[-1]
df = pd.read_pickle(path)
vol_day = df["volume"].groupby(df.index.normalize()).sum()
liquid = vol_day[vol_day > vol_day.max() * 0.2].index  # 근월물이 되기 전(거래가 적던 날)은 뺀다
df = df[df.index.normalize().isin(liquid)]
print(f"데이터: {path} · {len(df):,}개 · {df.index[0]:%Y-%m-%d} ~ {df.index[-1]:%Y-%m-%d} · {len(liquid)}일\n")

o, hi, lo, c = (df[k].to_numpy() for k in ("open", "high", "low", "close"))
n = len(df)
t = df.index
minute = t.hour * 60 + t.minute
rth = (minute >= 9 * 60 + 30) & (minute < 16 * 60)
# 진입 뒤 1 ~ HMAX분 동안 진입가 대비 고가·저가(포인트). 진입가 = 다음 봉 시가. 하루를 넘는 시간 공백(주말 등)이 끼면 그 진입은 뺀다
idx = np.arange(n - HMAX - 1)
entry = o[idx + 1]
up = np.stack([hi[idx + 1 + k] - entry for k in range(HMAX)], axis=1)
dn = np.stack([lo[idx + 1 + k] - entry for k in range(HMAX)], axis=1)
last = np.stack([c[idx + 1 + k] - entry for k in range(HMAX)], axis=1)
span = (t.asi8[idx + HMAX] - t.asi8[idx + 1]) / 60e9  # 분
valid = span <= HMAX + 5
day = t[idx].normalize()
cut = liquid[int(len(liquid) * 0.6)]
train = valid & (day < cut)
test = valid & (day >= cut)

# 지표
close = pd.Series(c)
atr = (pd.Series(hi) - pd.Series(lo)).rolling(14).mean().to_numpy()
ema = {k: close.ewm(span=k, adjust=False).mean().to_numpy() for k in (20, 50)}
diff = close.diff()
rsi2 = (100 - 100 / (1 + diff.clip(lower=0).ewm(alpha=1 / 2).mean() / (-diff.clip(upper=0)).ewm(alpha=1 / 2).mean())).to_numpy()
hh = {k: pd.Series(hi).rolling(k).max().shift(1).to_numpy() for k in (15, 30, 60)}
ll = {k: pd.Series(lo).rolling(k).min().shift(1).to_numpy() for k in (15, 30, 60)}

SIGNALS = {}
for k_ema, k_atr in itertools.product((20, 50), (1.5, 2.5, 3.5)):
    z = (c - ema[k_ema]) / atr
    SIGNALS[f"되돌림 EMA{k_ema} {k_atr}ATR"] = np.where(z < -k_atr, 1, np.where(z > k_atr, -1, 0))
for k in (15, 30, 60):
    SIGNALS[f"돌파 {k}분 고저"] = np.where(c > hh[k], 1, np.where(c < ll[k], -1, 0))
for lo_, hi_ in ((5, 95), (2, 98)):
    SIGNALS[f"RSI2 {lo_}/{hi_}"] = np.where(rsi2 < lo_, 1, np.where(rsi2 > hi_, -1, 0))
TPS, SLS, HS = (2, 4, 6, 10, 15, 25), (2, 4, 6, 10, 15, 25), (5, 15, 60)


rows = []
for (name, sig), session in itertools.product(SIGNALS.items(), ("장중", "전체")):
    s = sig[idx]
    pick = (s != 0) & (rth[idx] if session == "장중" else True)
    for part, mask in (("train", train), ("test", test)):
        m = pick & mask
        if m.sum() < 50:
            continue
        side = s[m]
        U, D, L = up[m], dn[m], last[m]
        for tp, sl, h in itertools.product(TPS, SLS, HS):
            u = np.where(side[:, None] > 0, U[:, :h], -D[:, :h])
            d = np.where(side[:, None] > 0, -D[:, :h], U[:, :h])
            ft = np.where((u >= tp).any(1), (u >= tp).argmax(1), h)
            fs = np.where((d >= sl).any(1), (d >= sl).argmax(1), h)
            pts = np.where(fs <= ft, np.where(fs < h, -sl, side * L[:, h - 1]), np.where(ft < h, tp, side * L[:, h - 1]))
            net = pts - COST
            rows.append((name, session, tp, sl, h, part, len(net), (net > 0).mean(), net.mean(), net.std()))
res = pd.DataFrame(rows, columns=["rule", "session", "tp", "sl", "h", "part", "n", "win", "exp", "sd"])
key = ["rule", "session", "tp", "sl", "h"]
wide = res.pivot_table(index=key, columns="part", values=["n", "win", "exp", "sd"]).dropna()
wide.columns = [f"{a}_{b}" for a, b in wide.columns]
wide["t_train"] = wide.exp_train / (wide.sd_train / np.sqrt(wide.n_train))
print(f"시험한 설정: {len(wide):,}개 (규칙 {len(SIGNALS)} × 시간대 2 × 익절 {len(TPS)} × 손절 {len(SLS)} × 보유 {len(HS)}). 비용 {COST}포인트(${COST * 2:.0f}) 뺀 값\n")


def show(title, top):
    print(title)
    for k, r in top.iterrows():
        print(f"  {k[0]:20s} {k[1]} 익절 {k[2]:>2} 손절 {k[3]:>2} 최대 {k[4]:>2}분 | 앞쪽 {int(r.n_train):5d}번 승률 {r.win_train:5.1%} 기대 {r.exp_train:+5.2f}pt (t {r.t_train:+4.1f})"
              f" | 뒤쪽 {int(r.n_test):5d}번 승률 {r.win_test:5.1%} 기대 {r.exp_test:+5.2f}pt (${r.exp_test * 2:+.2f}/계약)")


show("===== 앞쪽에서 승률이 가장 높은 10개 =====", wide.sort_values("win_train", ascending=False).head(10))
show("\n===== 앞쪽에서 비용 뒤 기대 손익이 가장 높은 10개 (거래 100번 이상) =====", wide[wide.n_train >= 100].sort_values("exp_train", ascending=False).head(10))
print(f"\n앞쪽에서 기대 손익이 플러스였던 설정: {(wide.exp_train > 0).mean():.1%} · 그 가운데 뒤쪽에서도 플러스: {(wide[wide.exp_train > 0].exp_test > 0).mean():.1%}")
print(f"앞쪽·뒤쪽 기대 손익의 순위 상관: {wide.exp_train.rank().corr(wide.exp_test.rank()):+.2f}")
print("\n승률과 기대 손익의 관계(앞쪽, 설정 전체): 승률 구간별 평균 기대 손익")
bins = pd.cut(wide.win_train, [0, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 1.0])
print(wide.groupby(bins, observed=True).exp_train.agg(["count", "mean"]).round(2).to_string())
for cost in (0.75, 2.5):
    adj = wide.exp_train + COST - cost
    print(f"\n비용을 {cost}포인트로 잡으면: 앞쪽 기대 손익 플러스 {(adj > 0).mean():.1%}, 최고 {adj.max():+.2f}pt")
