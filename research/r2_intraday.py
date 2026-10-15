"""2차: 장중 전략. 나스닥100의 수익이 하루 중 언제 나오는지, 장중 신호가 비용을 넘는지, 일봉 후보들의 장중 MDD는 어떤지.

데이터(무료로 구할 수 있는 만큼):
- ^NDX 일봉 시가·종가 2001 ~ (그 전에는 시가가 전날 종가로 채워져 있어 쓰지 않는다)
- NQ=F 1시간봉 2024-05 ~ (야후, 뉴욕 시각, 정시 시작). 연결 시세라 롤오버 날에 틈이 있다 → 그 시간의 수익은 뺀다
- LS 분봉은 지금 월물(MNQZ26)만 있어 연구에는 짧다

비용: 사고팔 때마다 한쪽 0.005%(1틱 0.25 ≈ 0.0008% + 수수료 약 $1.5/계약 ≈ 0.0025%, 넉넉히)
    cd C:\\dev\\MNQ-Trader && .venv\\Scripts\\python research\\r2_intraday.py
"""
import sys
import numpy as np, pandas as pd

sys.stdout.reconfigure(encoding="utf-8")
COST = 0.00005  # 한쪽
WIN = 20


def tstat(x):
    x = pd.Series(x).dropna()
    return x.mean() / x.std() * np.sqrt(len(x)) if len(x) > 2 and x.std() > 0 else np.nan


def sharpe(daily):
    daily = pd.Series(daily).dropna()
    return daily.mean() / daily.std() * np.sqrt(252) if daily.std() > 0 else np.nan


def windows(daily, marks=None):
    """4주(20거래일) 구간, 5일 간격: (수익 중앙, 플러스 비율, MDD 중앙, 수익+MDD 평균). marks가 있으면 그 시각들(장중)로 MDD를 잰다."""
    daily = daily.dropna()
    days = daily.index
    rows = []
    for i in range(0, len(days) - WIN + 1, 5):
        part = daily.iloc[i:i + WIN]
        eq = np.cumprod(np.concatenate([[1.0], 1 + part.to_numpy()]))
        if marks is not None:
            m = marks[(marks.index.normalize() >= part.index[0]) & (marks.index.normalize() <= part.index[-1])]
            path = np.cumprod(np.concatenate([[1.0], 1 + m.to_numpy()]))
        else:
            path = eq
        rows.append((eq[-1] - 1, (path / np.maximum.accumulate(path) - 1).min()))
    r, m = np.array(rows).T
    return np.median(r), (r > 0).mean(), np.median(m), np.mean(r + m)


def line(name, daily, marks=None):
    r, up, m, s = windows(daily, marks)
    return f"{name:34s} 샤프 {sharpe(daily):5.2f} | 4주 수익 중앙 {r:+6.2%} 플러스 {up:4.0%} | MDD 중앙 {m:6.2%} | 수익+MDD {s:+6.2%}"


# ---------- 1. 밤사이 vs 장중 (^NDX 일봉, 2001 ~) ----------
d = pd.read_pickle("store/data/NDX_1d.pkl")["2001":]
night = d["Open"] / d["Close"].shift(1) - 1   # 전날 종가 → 오늘 시가
day = d["Close"] / d["Open"] - 1               # 오늘 시가 → 종가
print("===== 1. 나스닥100 수익이 언제 나오나 (2001 ~, 하루 평균, bp = 0.01%) =====")
for label, a, b in (("2001 ~ 2009", "2001", "2009"), ("2010 ~ 2019", "2010", "2019"), ("2020 ~", "2020", None)):
    n, t = night[a:b], day[a:b]
    print(f"{label:12s} 밤사이 {n.mean()*1e4:+5.1f}bp (t {tstat(n):+4.1f}) | 장중 {t.mean()*1e4:+5.1f}bp (t {tstat(t):+4.1f}) | "
          f"샤프 밤사이만 {sharpe(n - 2*COST):5.2f} · 장중만 {sharpe(t - 2*COST):5.2f} · 보유 {sharpe(night + day):5.2f}")
print("(밤사이만 / 장중만은 매일 한 번씩 사고팔므로 비용 0.01%를 뗐다)")
print("\n4주 구간 (2020 ~, 일별 종가 기준 MDD):")
for name, x in (("보유", (night + day)["2020":]), ("밤사이만 (16:00 매수 → 9:30 매도)", (night - 2*COST)["2020":]), ("장중만 (9:30 매수 → 16:00 매도)", (day - 2*COST)["2020":])):
    print(line(name, x))

# ---------- 2 ~ 4. NQ=F 1시간봉 ----------
h = pd.read_pickle("store/data/NQF_1h.pkl")
h = h[h.index.minute == 0]  # 몇 개 섞인 9:30 시작 봉은 뺀다
ret = h["Close"].pct_change()
gap = h.index.to_series().diff() > pd.Timedelta(hours=1)
ret[gap & (h.index.hour != 18)] = np.nan  # 주말·휴장 뒤 첫 봉 말고 시간이 빈 곳(롤오버 등)은 버린다
ret[ret.abs() > 0.05] = np.nan            # 롤오버 틈
hours = ret.groupby(ret.index.hour)
print("\n===== 2. 시간대별 평균 수익 (NQ=F 1시간봉, 뉴욕 시각, 2024-05 ~) =====")
print("  ".join(f"{hr:02d}시 {g.mean()*1e4:+4.1f}({tstat(g):+.1f})" for hr, g in hours))
print("(괄호는 t값. 24개를 한꺼번에 보므로 |t| < 3이면 우연일 수 있다)")

# 날짜(뉴욕)마다 주요 시각의 가격
px = h["Close"]
def at(hour):
    s = px[px.index.hour == hour]
    s.index = s.index.normalize()
    return s
c16_prev = at(15).shift(1)   # 15시 봉의 종가 = 16:00
o10, c15, c16, o9 = at(9), at(14), at(15), at(8)  # 9시 봉 종가 = 10:00, 14시 봉 종가 = 15:00, 8시 봉 종가 = 9:00
df = pd.DataFrame({"prev": c16_prev, "p9": o9, "p10": o10, "p15": c15, "p16": c16}).dropna()
df = df[df.index.dayofweek < 5]
first = df.p10 / df.prev - 1    # 전날 16:00 → 10:00 (밤사이 + 첫 30분)
last = df.p16 / df.p15 - 1      # 15:00 → 16:00
print("\n===== 3. 장중 모멘텀 (Gao·Han·Li·Zhou 2018): 전날 16:00 → 10:00 수익이 15:00 → 16:00 방향을 맞히나 =====")
beta = np.polyfit(first, last, 1)[0]
corr = np.corrcoef(first, last)[0, 1]
sig = np.sign(first) * last - 2 * COST
print(f"{len(df)}일 · 상관 {corr:+.3f} · 기울기 {beta:+.3f} · 맞힌 비율 {(np.sign(first) == np.sign(last)).mean():.1%} · "
      f"따라 했을 때 하루 평균 {sig.mean()*1e4:+.2f}bp (t {tstat(sig):+.1f}) · 샤프 {sharpe(sig):.2f}")

# ---------- 4. 노이즈 영역 돌파 (Zarattini·Aziz·Barbon 2024를 1시간봉으로 줄인 것) ----------
# 날마다 9:30 시가(9시 봉 시가로 대신) 기준으로, 지난 14일 같은 시각의 |시가 대비 움직임| 평균을 '노이즈'로 본다.
# 10:00 ~ 15:00 매 정시에 가격이 시가 × (1 ± 노이즈) 밖이면 그 방향으로 들고, 안으로 돌아오면 정리, 16:00에 모두 정리.
print("\n===== 4. 노이즈 영역 돌파 (1시간봉 근사, 장 마감에 모두 정리 = 밤사이 위험 없음) =====")
rth = h[(h.index.hour >= 9) & (h.index.hour <= 15) & (h.index.dayofweek < 5)].copy()
rth["date"] = rth.index.normalize()
opens = rth[rth.index.hour == 9].set_index("date")["Open"]
rows = []
for date, g in rth.groupby("date"):
    if date not in opens.index:
        continue
    o = opens[date]
    for ts, r in g.iterrows():
        rows.append((date, ts.hour, r["Close"] / o - 1))
mv = pd.DataFrame(rows, columns=["date", "hour", "move"]).pivot(index="date", columns="hour", values="move")
noise = mv.abs().rolling(14).mean().shift(1)  # 그날 전까지의 14일
results = {}
for k in (1.0, 1.5):
    daily_pnl, marks = [], []
    for date in mv.index[15:]:
        pos, pnl = 0, 0.0
        for hr in range(9, 15):  # hr 시 봉의 종가(hr+1 시)에 판단, 다음 봉 동안 들고 있는다
            m, n = mv.at[date, hr], noise.at[date, hr]
            nxt = mv.at[date, hr + 1] if hr + 1 in mv.columns else np.nan
            if np.isnan(m) or np.isnan(n) or np.isnan(nxt):
                continue
            want = 1 if m > k * n else -1 if m < -k * n else 0
            cost = abs(want - pos) * COST
            pos = want
            r = pos * ((1 + nxt) / (1 + m) - 1) - cost
            pnl += r
            marks.append((pd.Timestamp(date) + pd.Timedelta(hours=hr + 1), r))
        pnl -= abs(pos) * COST  # 마감에 정리
        daily_pnl.append((date, pnl))
    dp = pd.Series(dict(daily_pnl))
    mk = pd.Series(dict(marks)).sort_index()
    results[k] = dp
    traded = (dp != 0).mean()
    print(line(f"노이즈 {k}배 밖이면 따라가기 (거래한 날 {traded:.0%})", dp, mk))

# ---------- 5. 일봉 후보들의 장중 MDD (2024-05 ~, 1시간봉으로 계좌를 다시 계산) ----------
print("\n===== 5. 일봉 후보: 종가 기준 MDD와 장중(1시간) 기준 MDD (2024-05 ~) =====")
sys.path.insert(0, ".")
from mnq import strategies
ndx = pd.read_pickle("store/data/NDX_1d.pkl")["Close"].dropna()
for key in ("hold", "vt", "trend_vt", "dip_trend"):
    from mnq import config
    expo = strategies.exposure(key, ndx) * config.CANDIDATES[key]["lever"]
    held = expo.shift(1).reindex(pd.DatetimeIndex(ret.index.tz_localize(None).normalize()), method="ffill")
    # t일 16:00에 정한 노출은 t일 16:00 ~ t+1일 16:00에 들고 있다: 16시 이후 봉은 다음 날 장의 노출을 쓴다
    hourly = pd.Series(held.to_numpy(), index=ret.index)
    after = ret.index.hour >= 16
    hourly[after] = expo.reindex(pd.DatetimeIndex(ret.index[after].tz_localize(None).normalize()), method="ffill").to_numpy()
    hp = (hourly * ret).dropna()
    hp.index = hp.index.tz_localize(None)
    dp = hp.groupby(hp.index.normalize()).sum()
    dp = dp[dp.index.dayofweek < 5]
    r1, u1, m1, s1 = windows(dp)
    r2, u2, m2, s2 = windows(dp, hp)
    print(f"{config.CANDIDATES[key]['name'].split(' · ')[0]:12s} 4주 MDD 중앙: 종가 기준 {m1:6.2%} → 장중 기준 {m2:6.2%} | 수익+MDD {s1:+6.2%} → {s2:+6.2%}")
