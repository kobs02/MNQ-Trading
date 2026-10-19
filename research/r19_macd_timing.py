"""19차: MACD 동시 반전을 (A) 섞기 일봉 갈래의 진입 시점 고르기로, (B) 느린 단위 + 위 단위 추세 방향의 독립 매매법으로 (10-19 사용자 요청).

18차(1·5·15·60분봉, 1분봉 5주)의 약점: 1분봉 잡음, 겹치는 단위의 이중 계산, 추세 거스르기, 비용, 작은 표본.
그래서 이번에는 NQ=F 1시간봉 2년치를 쓰고, 꺾임은 1·2·4시간봉 MACD 선(12, 26) 기울기로 본다(18차와 같은 정의:
그 단위의 마지막 마감 봉이 꺾인 봉이면 꺾인 상태, 같은 방향 2개 이상 + 이번 시간에 새로 꺾인 단위가 있으면 신호).
앞 절반 = 설계, 뒤 절반 = 확인. 변형은 미리 하나씩만 정했다(고르기 없음).

(A) 섞기의 일봉 갈래(되돌림 + 추세)가 롱을 새로 열 때, 16시에 바로 사지 않고 그 뒤 처음 나오는 위로 꺾임 신호의 봉 마감에 산다.
    다음 날 16시(다음 결정)까지 신호가 없으면 그때 산다. 청산·장중 갈래·상한(1계약)은 섞기 그대로.
    통과 기준: 1. 확인 구간 4주 수익+MDD 평균이 섞기보다 좋다  2. 설계 구간도 섞기보다 좋다
              3. 확인 구간 합계 손익이 '같은 기다림 시간들을 진입마다 무작위로 섞은 것'(1,000번)보다 95% 이상 좋다
(B) 1·2·4시간봉 중 2개 이상 위로 꺾임 + 일봉 MACD 선 > 0이면 1계약 롱, 2개 이상 아래로 꺾임 + 일봉 MACD 선 < 0이면 1계약 숏.
    반대 방향 2개 이상 꺾임(추세 무관)이나 72시간이면 정리.
    통과 기준: 1. 확인 구간 비용 뒤 합계 > 0  2. 설계 구간 > 0  3. 확인 구간이 같은 수·보유·방향 비율의 무작위 진입(1,000번)보다 95% 이상 좋다
    (참고로 4주 수익+MDD도 섞기와 비교한다)
비용: 섞기 과거 시험과 같게 노출이 바뀐 만큼 한쪽 0.005%(1계약 ≈ 계좌 0.6배 → 한쪽 약 $3).

    cd C:\\dev\\MNQ-Trader && .venv\\Scripts\\python research\\r19_macd_timing.py
"""
import sys
import numpy as np, pandas as pd

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, ".")
from mnq import config, strategies

H = strategies.hourly()
PX = strategies.history()
CAND = config.CANDIDATES["mix"]
UNIT = None  # 1계약의 계좌 대비 노출(가격마다): 아래에서 계산
TFS = (1, 2, 4)


def returns(h):
    ret = h["close"].pct_change()
    gap = h.index.to_series().diff() > pd.Timedelta(hours=1)
    ret[(gap & (h.index.hour != 18)) | (ret.abs() > 0.05)] = np.nan  # 롤오버 틈(mix_pnl과 같게)
    return ret.fillna(0.0)


def turn_counts(h):
    """시간봉마다(그 봉이 마감된 때): 위·아래로 꺾인 상태인 단위 수, 이번 시간에 새로 꺾인 단위가 있는가. 마감된 봉만 쓴다."""
    close_t = h.index + pd.Timedelta(hours=1)
    up = np.zeros(len(h), int); dn = np.zeros(len(h), int); fresh = np.zeros(len(h), bool)
    for tf in TFS:
        c = h["close"] if tf == 1 else h["close"].resample(f"{tf}h", label="left", closed="left").last().dropna()
        s = (c.ewm(span=12, adjust=False).mean() - c.ewm(span=26, adjust=False).mean()).diff()
        d = pd.Series(np.where((s > 0) & (s.shift() <= 0), 1, np.where((s < 0) & (s.shift() >= 0), -1, 0)), index=c.index + pd.Timedelta(hours=tf))
        cur = d.reindex(close_t, method="ffill").fillna(0).to_numpy()
        up += cur > 0; dn += cur < 0
        fresh |= pd.Series(close_t).isin(d.index[d.to_numpy() != 0]).to_numpy()
    return up, dn, fresh


def daily_trend(h):
    """일봉 MACD 선(뉴욕 16시 종가로 만든 일봉, 그날 16시 뒤부터 안다)의 부호를 시간봉마다."""
    day = h["close"][h.index.hour == 15]  # 15 ~ 16시 봉의 종가 = 16시 종가
    m = day.ewm(span=12, adjust=False).mean() - day.ewm(span=26, adjust=False).mean()
    m.index = m.index + pd.Timedelta(hours=1)
    return np.sign(m.reindex(h.index + pd.Timedelta(hours=1), method="ffill").fillna(0).to_numpy())


def mix_parts(h):
    """섞기의 시간별 일봉 갈래 노출(배율 a 적용 전, 0 또는 1)과 장중 갈래 노출(b 적용). strategies.mix_pnl과 같은 계산."""
    a, b = CAND["lever"], CAND["intraday"]["lever"]
    expo = strategies.exposure("mix", PX)
    day = pd.DatetimeIndex(h.index.normalize())
    held = expo.shift(1).reindex(day, method="ffill").to_numpy()
    after = h.index.hour >= 16
    held[after] = expo.reindex(day[after], method="ffill").to_numpy()
    sides = strategies.noise_sides(strategies.moves(h), CAND["intraday"]["k"]).stack()
    stamps = pd.DatetimeIndex([d + pd.Timedelta(hours=hr) for d, hr in sides.index])
    intra = pd.Series(sides.to_numpy() * b, index=stamps).reindex(h.index).fillna(0.0).to_numpy()
    return np.nan_to_num(held), intra, a


def mix_with(h, held, intra, a):
    total = np.clip(held * a + intra, -a, a)  # 상한 1계약(한 갈래 몫)
    t = pd.Series(total, index=h.index)
    return t * returns(h) - t.diff().abs().fillna(0.0) * strategies.COST * 0.5


def episodes(held):
    """일봉 갈래 보유 구간 [(시작 위치, 끝 위치)]: held가 0 → 양수로 바뀐 봉부터 다시 0이 되는 봉 전까지."""
    out, s = [], None
    for i, v in enumerate(held):
        if v > 0 and s is None:
            s = i
        if v <= 0 and s is not None:
            out.append((s, i)); s = None
    if s is not None:
        out.append((s, len(held)))
    return out


def delayed(held, eps, delays):
    """보유 구간마다 진입을 delays[k]봉 늦춘 held(구간 끝을 넘으면 그 구간은 안 든다)."""
    out = held.copy()
    for (s, e), d in zip(eps, delays):
        out[s:min(s + d, e)] = 0.0
    return out


def daily(p):
    d = p.groupby(p.index.normalize()).sum()
    return d[d.index.dayofweek < 5]


def score(p):
    w = strategies.windows(daily(p))
    return w["score"], w["ret_mean"], w["mdd_median"]


def split(h):
    mid = h.index[len(h) // 2].normalize()
    return h[h.index < mid], h[h.index >= mid], mid


# ---------- 자르기 점검: 앞쪽만 잘라 계산한 꺾임 = 전체로 계산한 꺾임의 앞쪽 ----------
cut = len(H) // 2
full, part = turn_counts(H), turn_counts(H.iloc[:cut])
assert all((f[:cut - 30] == p[:cut - 30]).all() for f, p in zip(full, part)), "미래 정보 누출(꺾임)"
assert (daily_trend(H)[:cut - 30] == daily_trend(H.iloc[:cut])[:cut - 30]).all(), "미래 정보 누출(일봉 추세)"
print("자르기 점검 통과")
DES, CON, MID = split(H)
print(f"NQ=F 1시간봉 {H.index[0]:%Y-%m-%d} ~ {H.index[-1]:%Y-%m-%d} · 설계 ~ {MID:%Y-%m-%d} 전 · 확인 {MID:%Y-%m-%d} ~\n")

# ---------- (A) 섞기 일봉 갈래의 진입 시점 ----------
print("(A) 섞기 일봉 갈래: 16시 바로 진입 대신 처음 나오는 위로 꺾임(1·2·4시간 중 2개 이상)에 진입, 다음 16시까지 없으면 그때")
held, intra, a = mix_parts(H)
up, dn, fresh = turn_counts(H)
sig_up = fresh & (up >= 2) & (dn == 0)
eps = episodes(held)
delays = []
for s, e in eps:
    limit = int(np.searchsorted(H.index, H.index[s] + pd.Timedelta(hours=24)))  # 다음 결정(다음 날 16시)
    k = next((i for i in range(s - 1, min(limit, e)) if sig_up[i]), None)  # i봉 마감에 신호 → i+1봉부터 든다(s-1: 16시 봉 마감 신호도 인정)
    delays.append(max(0, (k + 1 - s) if k is not None else limit - s))
base = mix_with(H, held, intra, a)
var = mix_with(H, delayed(held, eps, delays), intra, a)
print(f"  일봉 갈래 진입 {len(eps)}번, 기다린 시간 중앙 {np.median(delays):.0f}시간 · 신호 없이 다음 16시에 산 것 {sum(1 for (s, e), d in zip(eps, delays) if d >= 23)}번")
rows, ok_a = [], {}
for name, (lo, hi) in (("설계", (H.index[0], MID)), ("확인", (MID, H.index[-1] + pd.Timedelta(hours=1)))):
    b_, v_ = base[(base.index >= lo) & (base.index < hi)], var[(var.index >= lo) & (var.index < hi)]
    sb, sv = score(b_), score(v_)
    rows.append({"구간": name, "섞기 4주 수익+MDD": f"{sb[0]:+.2%}", "늦춘 진입": f"{sv[0]:+.2%}", "섞기 합계": f"{b_.sum():+.2%}", "늦춘 합계": f"{v_.sum():+.2%}",
                 "섞기 4주 수익": f"{sb[1]:+.2%}", "늦춘 4주 수익": f"{sv[1]:+.2%}", "섞기 MDD 중앙": f"{sb[2]:+.2%}", "늦춘 MDD 중앙": f"{sv[2]:+.2%}"})
    ok_a[name] = sv[0] > sb[0]
print(pd.DataFrame(rows).to_string(index=False))
rng = np.random.default_rng(0)
con = lambda p: p[p.index >= MID].sum()
rand = np.array([con(mix_with(H, delayed(held, eps, rng.permutation(delays)), intra, a)) for _ in range(1000)])
beat_a = float((con(var) > rand).mean())
print(f"  확인 합계 {con(var):+.2%} vs 기다림을 무작위로 섞은 것 중앙 {np.median(rand):+.2%} · 나은 비율 {beat_a:.0%}")
print(f"  기준 1 확인 > 섞기: {ok_a['확인']} · 기준 2 설계 > 섞기: {ok_a['설계']} · 기준 3 무작위 95%: {beat_a >= 0.95} → "
      f"{'통과' if ok_a['확인'] and ok_a['설계'] and beat_a >= 0.95 else '불통과'}\n")

# ---------- (B) 느린 단위 동시 꺾임 + 일봉 추세 방향 ----------
print("(B) 1·2·4시간봉 2개 이상 꺾임 + 일봉 MACD 방향으로 1계약, 반대 꺾임이나 72시간이면 정리")
trend = daily_trend(H)
o = H["open"].to_numpy(); t = H.index
unit = H["close"].to_numpy() * config.MULTIPLIER / config.CAPITAL  # 1계약의 계좌 대비 노출
enter = np.where(fresh & (up >= 2) & (dn == 0) & (trend > 0), 1, np.where(fresh & (dn >= 2) & (up == 0) & (trend < 0), -1, 0))
leave_up, leave_dn = fresh & (up >= 2) & (dn == 0), fresh & (dn >= 2) & (up == 0)


def b_trades(lo, hi):
    out, i = [], lo
    while i < hi - 1:
        if not enter[i]:
            i += 1; continue
        side, s = enter[i], i + 1
        j = s
        while j < hi - 1 and t[j] < t[s] + pd.Timedelta(hours=72) and not (leave_dn[j] if side > 0 else leave_up[j]):
            j += 1
        e = j + 1 if j < hi - 1 and t[j] < t[s] + pd.Timedelta(hours=72) else j
        if e >= hi:
            break
        out.append((s, e, side)); i = e
    return out


def b_pnl(trs, sides=None):
    pos = np.zeros(len(H))
    for k, (s, e, side) in enumerate(trs):
        pos[s:e] = (side if sides is None else sides[k]) * unit[s]
    p = pd.Series(pos, index=H.index)
    return p * returns(H) - p.diff().abs().fillna(0.0) * strategies.COST * 0.5


mid_i = int(np.searchsorted(t, MID))
ok_b = {}
for name, (lo, hi) in (("설계", (0, mid_i)), ("확인", (mid_i, len(H)))):
    trs = b_trades(lo, hi)
    p = b_pnl(trs)
    seg = p.iloc[lo:hi]
    pts = [side * (o[e] - o[s]) for s, e, side in trs]
    sc = score(seg)
    weeks = (t[hi - 1] - t[lo]).days / 7
    print(f"  {name}: {len(trs)}번(주당 {len(trs) / weeks:.1f}), 롱 {sum(1 for x in trs if x[2] > 0)} · 숏 {sum(1 for x in trs if x[2] < 0)}, "
          f"합계 {seg.sum():+.2%} (비용 전 건당 {np.mean(pts):+.1f}pt, 승률 {np.mean(np.array(pts) > 0):.0%}), "
          f"4주 수익+MDD {sc[0]:+.2%} · 4주 수익 {sc[1]:+.2%} · MDD 중앙 {sc[2]:+.2%}")
    ok_b[name] = seg.sum() > 0
    if name == "확인":
        holds = np.array([e - s for s, e, _ in trs]); sides = np.array([x[2] for x in trs])
        r = []
        for _ in range(1000):
            starts = np.sort(rng.integers(lo, hi - holds.max() - 1, len(trs)))
            r.append(b_pnl([(s, s + h, 1) for s, h in zip(starts, holds)], rng.permutation(sides)).iloc[lo:hi].sum())
        beat_b = float((seg.sum() > np.array(r)).mean())
        print(f"  확인 무작위 진입(같은 수·보유·방향 비율) 중앙 {np.median(r):+.2%} · 나은 비율 {beat_b:.0%}")
mix_con = score(base[base.index >= MID])[0]
print(f"  참고: 같은 확인 구간 섞기 4주 수익+MDD {mix_con:+.2%}")
print(f"  기준 1 확인 > 0: {ok_b['확인']} · 기준 2 설계 > 0: {ok_b['설계']} · 기준 3 무작위 95%: {beat_b >= 0.95} → "
      f"{'통과' if ok_b['확인'] and ok_b['설계'] and beat_b >= 0.95 else '불통과'}")
