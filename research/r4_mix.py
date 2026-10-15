"""4차: 일봉 갈래(되돌림 + 추세, 밤사이를 든다)와 장중 갈래(노이즈 영역 돌파, 마감에 정리)를 한 계좌에 섞으면.

둘 다 NQ=F 1시간봉(2024-05 ~)으로 시간마다의 손익을 만들어 더한다. 노출은 계좌 대비 배수(소수, 정수 계약 전)
MDD는 시간마다의 계좌로 잰다(장중 MDD). 비용 한쪽 0.005%.
    cd C:\\dev\\MNQ-Trader && .venv\\Scripts\\python research\\r4_mix.py
"""
import sys
import numpy as np, pandas as pd

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, ".")
from mnq import strategies

COST, WIN = 0.00005, 20

h = pd.read_pickle("store/data/NQF_1h.pkl")
h = h[h.index.minute == 0]
h.index = h.index.tz_localize(None)  # 뉴욕 시각 그대로
ret = h["Close"].pct_change()
gap = h.index.to_series().diff() > pd.Timedelta(hours=1)
ret[(gap & (h.index.hour != 18)) | (ret.abs() > 0.05)] = np.nan
ret = ret.fillna(0.0)


def daily_sleeve(key):
    """일봉 규칙의 시간별 손익: t일 16:00에 정한 노출을 t일 16:00 ~ t+1일 16:00에 든다."""
    ndx = strategies.history()
    expo = strategies.exposure(key, ndx)
    day = pd.DatetimeIndex(ret.index.normalize())
    held = expo.shift(1).reindex(day, method="ffill").to_numpy()
    after = ret.index.hour >= 16
    held[after] = expo.reindex(day[after], method="ffill").to_numpy()
    held = pd.Series(np.nan_to_num(held), index=ret.index)
    trade = held.diff().abs().fillna(0.0) * COST
    return held * ret - trade


def intraday_sleeve(k=1.0):
    """노이즈 영역 돌파: 9시 봉 시가를 기준으로, 10 ~ 15시 정시마다 시가 대비 움직임이 지난 14일 같은 시각 평균의 k배 밖이면 그 방향으로,
    안이면 쉼, 16시에 정리. 시간별 손익(그 시간 봉 동안의 손익)."""
    pnl = pd.Series(0.0, index=ret.index)
    rth = h[(h.index.hour >= 9) & (h.index.hour <= 15) & (h.index.dayofweek < 5)]
    date = rth.index.normalize()
    opens = rth[rth.index.hour == 9]["Open"]
    opens.index = opens.index.normalize()
    move = (rth["Close"] / opens.reindex(date).to_numpy() - 1).to_frame("m")
    move["date"], move["hour"] = date, rth.index.hour
    mv = move.pivot_table(index="date", columns="hour", values="m")
    noise = mv.abs().rolling(14).mean().shift(1)
    for d in mv.index[15:]:
        pos, last = 0, None
        for hr in range(9, 15):
            m, n = mv.at[d, hr], noise.at[d, hr]
            ts = d + pd.Timedelta(hours=hr + 1)  # 다음 봉(hr+1시 봉)
            if np.isnan(m) or np.isnan(n) or ts not in pnl.index:
                continue
            want = 1 if m > k * n else -1 if m < -k * n else 0
            pnl[ts] += want * ret[ts] - abs(want - pos) * COST
            pos, last = want, ts
        if last is not None:
            pnl[last] -= abs(pos) * COST  # 마감에 정리
    return pnl


def windows(hp):
    days = hp.groupby(hp.index.normalize()).sum()
    days = days[days.index.dayofweek < 5]
    out = []
    idx = days.index
    for i in range(0, len(idx) - WIN + 1, 5):
        a, b = idx[i], idx[i + WIN - 1] + pd.Timedelta(days=1)
        part = hp[(hp.index >= a) & (hp.index < b)]
        eq = np.cumprod(np.concatenate([[1.0], 1 + part.to_numpy()]))
        out.append((eq[-1] - 1, (eq / np.maximum.accumulate(eq) - 1).min()))
    r, m = np.array(out).T
    sharpe = days.mean() / days.std() * np.sqrt(252)
    return sharpe, r, m


d_s, i_s = daily_sleeve("dip_trend"), intraday_sleeve(1.0)
dd, ii = d_s.groupby(d_s.index.normalize()).sum(), i_s.groupby(i_s.index.normalize()).sum()
print(f"일별 손익 상관(일봉 갈래 vs 장중 갈래): {np.corrcoef(dd, ii)[0, 1]:+.2f}   (2024-05 ~, {len(dd)}일)")
print(f"장중 갈래가 포지션을 든 시간 중 일봉 갈래도 든 비율: {((i_s != 0) & (d_s != 0)).sum() / max((i_s != 0).sum(), 1):.0%}\n")
print("===== 섞기 (노출 = 일봉 × a + 장중 × b) =====")
for a, b in ((1, 0), (0, 1), (0.5, 0.5), (1, 0.5), (1, 1), (0.5, 1), (1.5, 1)):
    s, r, m = windows(a * d_s + b * i_s)
    print(f"일봉 {a:3.1f} + 장중 {b:3.1f}   샤프 {s:5.2f} | 4주 수익 중앙 {np.median(r):+6.2%} 평균 {r.mean():+6.2%} 플러스 {np.mean(r > 0):4.0%} | "
          f"MDD 중앙 {np.median(m):6.2%} 하위10% {np.percentile(m, 10):6.2%} | 수익+MDD {np.mean(r + m):+6.2%} | 수익/|MDD| {np.median(r / np.maximum(-m, 0.002)):+5.2f}")
print("\n비교: 보유 1배")
s, r, m = windows(daily_sleeve("hold"))
print(f"보유               샤프 {s:5.2f} | 4주 수익 중앙 {np.median(r):+6.2%} 평균 {r.mean():+6.2%} 플러스 {np.mean(r > 0):4.0%} | MDD 중앙 {np.median(m):6.2%} 하위10% {np.percentile(m, 10):6.2%} | 수익+MDD {np.mean(r + m):+6.2%}")
