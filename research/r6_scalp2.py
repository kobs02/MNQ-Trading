"""6차: 스캘핑 보완. 5차(r5_scalp.py)는 고정 포인트 익절·손절, 필터 없는 단순 규칙이라 살아남은 설정이 없었다. 여기서는

- 추세 필터: 큰 흐름(EMA 60봉)이 위면 롱만, 아래면 숏만
- 진입: 추세 속 눌림(RSI2), VWAP 되돌림(추세 방향), 시가 범위 돌파(ORB 15분), 큰 봉 따라가기
- 익절·손절을 ATR(14봉) 배수로, 최대 보유 봉 수, 시간대(장 초반 90분 / 장 막판 1시간 / 정규장 전체)
- 무작위 진입과 비교: 같은 시간대·같은 방향 비율·같은 청산 규칙으로 진입만 무작위(200번 반복 평균). 규칙의 우위 = 규칙 - 무작위
- 앞쪽 60%(날짜)에서 고르고 뒤쪽 40%에서 확인. 비용: 왕복 1.5포인트(시장가) / 1.0포인트(지정가 진입)

데이터: 야후 NQ=F 5분봉 60일(2026-08 ~), LS MNQZ26 1분봉 23일(근월물이 된 뒤). 1포인트 = $2/계약.
    cd C:\\dev\\MNQ-Trader && .venv\\Scripts\\python research\\r6_scalp2.py
"""
import glob
import itertools
import sys
import numpy as np, pandas as pd

sys.stdout.reconfigure(encoding="utf-8")
RNG = np.random.default_rng(7)


def load_5m():
    d = pd.read_pickle("store/data/NQF_5m.pkl").rename(columns=str.lower)
    d.index = d.index.tz_convert("America/New_York")
    return d[["open", "high", "low", "close", "volume"]]


def load_1m():
    d = pd.read_pickle(sorted(glob.glob("store/data/ls_*_1m.pkl"))[-1])
    vol = d["volume"].groupby(d.index.normalize()).sum()
    return d[d.index.normalize().isin(vol[vol > vol.max() * 0.2].index)]


def study(df, label, bar_min, holds):
    hmax = max(holds)
    o, hi, lo, c, v = (df[k].to_numpy(dtype=float) for k in ("open", "high", "low", "close", "volume"))
    t = df.index
    n = len(df)
    minute = (t.hour * 60 + t.minute).to_numpy()
    date = t.normalize()
    rth = (minute >= 570) & (minute < 960)
    windows = {"초반 90분": (minute >= 570) & (minute < 660), "막판 1시간": (minute >= 900) & (minute < 960), "정규장 전체": rth}

    close = pd.Series(c)
    atr = (pd.Series(hi) - pd.Series(lo)).rolling(14).mean().to_numpy()
    ema60 = close.ewm(span=60, adjust=False).mean().to_numpy()
    trend = np.sign(c - ema60) * (np.abs(np.diff(ema60, prepend=ema60[0])) > 0)  # 위 +1 / 아래 -1
    diff = close.diff()
    rsi2 = (100 - 100 / (1 + diff.clip(lower=0).ewm(alpha=0.5).mean() / (-diff.clip(upper=0)).ewm(alpha=0.5).mean())).to_numpy()
    # 정규장 VWAP(그날 9:30부터)
    tp = (hi + lo + c) / 3
    sess = pd.Series(np.where(rth, 1, 0), index=t)
    pv = pd.Series(np.where(rth, tp * v, 0.0)).groupby(date).cumsum().to_numpy()
    vv = pd.Series(np.where(rth, v, 0.0)).groupby(date).cumsum().to_numpy()
    vwap = np.where(vv > 0, pv / np.maximum(vv, 1e-9), np.nan)
    # 시가 범위(9:30 ~ 9:45)
    first = (minute >= 570) & (minute < 585)
    orh = pd.Series(np.where(first, hi, np.nan)).groupby(date).transform("max").to_numpy()
    orl = pd.Series(np.where(first, lo, np.nan)).groupby(date).transform("min").to_numpy()
    after_or = minute >= 585
    big = (hi - lo) > 2.0 * atr

    sig = {}
    for k in (10, 5):
        sig[f"추세 눌림 RSI2<{k}"] = np.where((trend > 0) & (rsi2 < k), 1, np.where((trend < 0) & (rsi2 > 100 - k), -1, 0))
    for k in (1.0, 2.0):
        sig[f"VWAP 되돌림 {k}ATR(추세 방향)"] = np.where((trend > 0) & (c < vwap - k * atr), 1, np.where((trend < 0) & (c > vwap + k * atr), -1, 0))
    brk_up = after_or & (c > orh) & (np.roll(c, 1) <= np.roll(orh, 1))
    brk_dn = after_or & (c < orl) & (np.roll(c, 1) >= np.roll(orl, 1))
    sig["ORB 15분 돌파"] = np.where(brk_up, 1, np.where(brk_dn, -1, 0))
    sig["ORB 15분 돌파(추세 방향)"] = np.where(brk_up & (trend > 0), 1, np.where(brk_dn & (trend < 0), -1, 0))
    sig["큰 봉 따라가기(2ATR)"] = np.where(big & (c > o), 1, np.where(big & (c < o), -1, 0))

    idx = np.arange(n - hmax - 1)
    entry = o[idx + 1]
    up = np.stack([hi[idx + 1 + k] - entry for k in range(hmax)], axis=1)
    dn = np.stack([lo[idx + 1 + k] - entry for k in range(hmax)], axis=1)
    last = np.stack([c[idx + 1 + k] - entry for k in range(hmax)], axis=1)
    span = (t.asi8[idx + hmax] - t.asi8[idx + 1]) / 60e9
    ok = (span <= hmax * bar_min + 5) & np.isfinite(atr[idx]) & (atr[idx] > 0)
    days = date.unique()
    cut = days[int(len(days) * 0.6)]
    part = np.where(date[idx] < cut, 0, 1)

    def trade(rows, side, a, b, h):
        """rows: 진입 후보 위치, side: ±1, 익절 a×ATR, 손절 b×ATR, 최대 h봉 → 포인트(비용 전)."""
        A = atr[idx][rows][:, None]
        u = np.where(side[:, None] > 0, up[rows, :h], -dn[rows, :h])
        d = np.where(side[:, None] > 0, -dn[rows, :h], up[rows, :h])
        ft = np.where((u >= a * A).any(1), (u >= a * A).argmax(1), h)
        fs = np.where((d >= b * A).any(1), (d >= b * A).argmax(1), h)
        end = side * last[rows, h - 1]
        return np.where(fs <= ft, np.where(fs < h, -b * A[:, 0], end), np.where(ft < h, a * A[:, 0], end))

    out = []
    for (name, s), (wname, wmask) in itertools.product(sig.items(), windows.items()):
        s_i = s[idx]
        base = ok & wmask[idx]
        for p in (0, 1):
            pool = np.flatnonzero(base & (part == p))
            rows = pool[s_i[pool] != 0]
            if len(rows) < 20:
                continue
            side = s_i[rows]
            for a, b, h in itertools.product((0.5, 1.0, 2.0, 3.0), (0.5, 1.0, 2.0), holds):
                pts = trade(rows, side, a, b, h)
                rand = np.mean([trade(r, RNG.permutation(side), a, b, h).mean()
                                for r in (np.sort(RNG.choice(pool, len(rows), replace=False)) for _ in range(30))])
                out.append((name, wname, a, b, h, p, len(pts), (pts > 1.5).mean(), pts.mean(), pts.std(), rand))
    res = pd.DataFrame(out, columns=["rule", "win", "tp", "sl", "h", "part", "n", "winrate", "gross", "sd", "rand"])
    key = ["rule", "win", "tp", "sl", "h"]
    w = res.pivot_table(index=key, columns="part", values=["n", "winrate", "gross", "sd", "rand"]).dropna()
    w.columns = [f"{x}{y}" for x, y in w.columns]
    w["edge0"], w["edge1"] = w.gross0 - w.rand0, w.gross1 - w.rand1
    w["t0"] = w.gross0 / (w.sd0 / np.sqrt(w.n0))
    print(f"\n######## {label}: {len(days)}일 ({days[0]:%m-%d} ~ {days[-1]:%m-%d}), 앞쪽 ~ {cut:%m-%d} / 뒤쪽 · 설정 {len(w):,}개 ########")
    print(f"규칙 우위(규칙 - 무작위 진입, 포인트)의 평균: 앞쪽 {w.edge0.mean():+.2f} · 뒤쪽 {w.edge1.mean():+.2f} · 앞뒤 상관 {np.corrcoef(w.edge0, w.edge1)[0, 1]:+.2f}")
    for rule, g in w.groupby(level="rule"):
        print(f"  {rule:26s} 우위 앞 {g.edge0.mean():+5.2f} 뒤 {g.edge1.mean():+5.2f} | 비용 전 기대 앞 {g.gross0.mean():+5.2f} 뒤 {g.gross1.mean():+5.2f}")
    for cost in (1.5, 1.0):
        net0, net1 = w.gross0 - cost, w.gross1 - cost
        sel = w[(w.n0 >= 40) & (net0 > 0)]
        print(f"\n비용 {cost}포인트: 앞쪽 플러스 {(net0 > 0).mean():.1%} · 그중(40번 이상) 뒤쪽도 플러스 {((sel.gross1 - cost) > 0).mean() if len(sel) else 0:.0%} ({len(sel)}개)")
        best = w[w.n0 >= 40].assign(net0=net0, net1=net1).sort_values("net0", ascending=False).head(6)
        for k, r in best.iterrows():
            print(f"  {k[0]:26s} {k[1]:6s} 익절 {k[2]}ATR 손절 {k[3]}ATR 최대 {k[4]}봉 | 앞 {int(r.n0)}번 승률 {r.winrate0:.0%} 순 {r.net0:+.2f}pt(t {r.t0:+.1f}) 우위 {r.edge0:+.2f}"
                  f" | 뒤 {int(r.n1)}번 승률 {r.winrate1:.0%} 순 {r.net1:+.2f}pt 우위 {r.edge1:+.2f}")
    return w


w5 = study(load_5m(), "야후 NQ=F 5분봉", 5, (3, 6, 12))
w1 = study(load_1m(), "LS MNQZ26 1분봉", 1, (5, 15, 30))
