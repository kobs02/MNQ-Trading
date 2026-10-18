"""후보 전략: 나스닥100 일봉 종가로 다음 날의 노출(계좌 대비 배수)을 정하는 규칙들과, 그 과거 시험.

과거 시험은 research/r1_daily.py와 같다: t일 종가로 정한 노출을 t+1일 수익률에 적용, 노출이 바뀐 만큼 0.01% 비용.
시세는 나스닥100 지수(^NDX, 야후). 선물 수익률과는 배당·금리만큼(4주에 0.1 ~ 0.3%) 다르다.
"""
import time

import numpy as np
import pandas as pd

from . import config

COST = 0.0001
WIN, STEP = 20, 5  # 대회와 같은 4주(거래일 20일) 구간, 5거래일 간격


def _dip(px):
    return (px <= px.rolling(5).min()).astype(float).rolling(5).max()  # 5일 중 최저로 마감하면 5일 동안 롱


def _ibs(px, x=0.15):
    """종가가 그날 범위의 아래 x 안에서 끝나면 다음 하루 롱(research/r14_ideas.py). 고가·저가는 history()가 받아 둔 같은 파일에서."""
    d = pd.read_pickle(config.STORE / "data" / "NDX_1d.pkl").reindex(px.index)
    return ((px - d["Low"]) / (d["High"] - d["Low"]).replace(0, np.nan) < x).astype(float)


RULES = {
    "flat": lambda px: pd.Series(0.0, index=px.index),  # 일봉 갈래가 없는 후보(스캘핑)
    "dip_trend": lambda px: _dip(px) * (px > px.rolling(200).mean()),
    "ibs_trend": lambda px: _ibs(px) * (px > px.rolling(200).mean()),
}


def explain(key, px):
    """화면의 '추천 근거': 그 후보의 일봉 규칙이 마지막 종가에서 본 조건들 [(항목, 설명, 충족 True / 불충족 False / 참고 None)].
    값은 나스닥100 지수(^NDX) 기준."""
    rule, c = config.CANDIDATES[key]["rule"], float(px.iloc[-1])
    sma = float(px.rolling(200).mean().iloc[-1])
    trend = ("상승장", f"종가 {c:,.0f} {'>' if c > sma else '≤'} 200일선 {sma:,.0f} ({c / sma - 1:+.1%})", bool(c > sma))
    if config.CANDIDATES[key].get("ichimoku"):
        return [("롱", "구름이 초록으로 바뀜: 선행스팬 A(전환 9·기준 26 평균) > B(52봉 가운데)", None),
                ("숏", "구름이 빨강으로 바뀜: A < B. 늘 1계약, 반전 때 롱·숏을 뒤집는다", None),
                ("연구 결과", "2년 1시간봉: 샤프 0.04 ~ 0.21, 4주 수익+MDD가 섞기보다 나쁨 → 관찰용", False)]
    if rule == "flat":
        sc = config.CANDIDATES[key]["scalp"]
        return [("진입", f"1분봉 종가가 직전 {sc['look']}분 고가 위 → 롱, 저가 아래 → 숏 (뉴욕 9:30 ~ 16시)", None),
                ("청산", f"+{sc['tp']:g}포인트 익절 · -{sc['sl']:g}포인트 손절 · {sc['hold']}분 경과", None),
                ("연구 결과", "승률 93%지만 1회 평균 -1.4 ~ -2.7포인트(비용 뒤) → 관찰용", False)]
    if rule == "ibs_trend":
        d = pd.read_pickle(config.STORE / "data" / "NDX_1d.pkl").reindex(px.index).iloc[-1]
        v = (c - d["Low"]) / (d["High"] - d["Low"])
        return [trend, ("바닥 마감", f"IBS {v:.3f} " + ("< 0.15: 그날 범위의 아래 15%에서 마감" if v < 0.15 else "≥ 0.15: 범위 바닥 마감이 아님"), bool(v < 0.15)),
                ("보유", "신호 다음 하루만", None)]
    # dip_trend: 5일 중 최저로 마감한 날부터 5일 동안 롱(그 사이 200일선 위일 때만)
    hits = px[px <= px.rolling(5).min()]
    last = hits.index[-1]
    ago = len(px.loc[last:]) - 1  # 마지막 신호가 몇 거래일 전인가
    again = float(px.iloc[-4:].min())
    out = [trend, ("눌림목", f"{last:%m-%d} 종가({hits.iloc[-1]:,.0f})가 5일 최저" + (" (마지막 종가)" if ago == 0 else f" ({ago}거래일 전)"), bool(ago < 5))]
    if ago < 5:
        out.append(("유지", f"새 5일 최저가 없으면 {5 - ago}거래일 뒤 정리", None))
    out.append(("연장", f"다음 종가 ≤ {again:,.0f}이면 5일 더", None))
    return out


def days_left(key, px):
    """일봉 규칙으로 들고 있으면 마지막 종가로부터 몇 거래일 뒤 결정에서 정리하는가(새 신호로 연장되기 전 기준), 안 들고 있으면 None."""
    rule = config.CANDIDATES[key]["rule"]
    if rule == "flat" or not exposure(key, px).iloc[-1]:
        return None
    if rule == "ibs_trend":
        return 1
    ago = len(px.loc[px[px <= px.rolling(5).min()].index[-1]:]) - 1
    return 5 - ago


def exposure(key, px):
    """종가 표 px로 정한 날마다의 노출(배율 적용 전). 섞기 후보는 일봉 갈래의 것."""
    return RULES[config.CANDIDATES[key]["rule"]](px).fillna(0.0)


# ---------- 장중 갈래: 노이즈 영역 돌파 (Zarattini·Aziz·Barbon 2024를 1시간봉으로 줄인 것) ----------
# 날마다 9시(뉴욕) 봉의 시가를 기준으로, 시가 대비 움직임이 지난 NOISE_DAYS일 같은 시각 |움직임| 평균의 k배 밖이면 그 방향으로 든다.
# 판단은 10 ~ 15시 정시(9 ~ 14시 봉이 마감된 때), 다음 한 시간을 들고, 16시에 모두 정리한다. 연구: research/r4_mix.py
NOISE_DAYS = 14
INTRADAY_HOURS = range(10, 16)  # 판단 시각(뉴욕). 16시에 정리


def moves(frame):
    """1시간봉 표(뉴욕 시각, 봉 시작 시각이 색인, open·close) → 날짜 × 봉 시작 시(9 ~ 15)의 '9시 시가 대비 종가 움직임' 표."""
    rth = frame[(frame.index.hour >= 9) & (frame.index.hour <= 15) & (frame.index.dayofweek < 5)]
    date = rth.index.normalize()
    opens = rth[rth.index.hour == 9]["open"]
    opens.index = opens.index.normalize()
    table = pd.DataFrame({"date": date, "hour": rth.index.hour, "m": rth["close"].to_numpy() / opens.reindex(date).to_numpy() - 1})
    return table.pivot_table(index="date", columns="hour", values="m")


def noise_side(table, date, hour, k):
    """date의 hour시(판단 시각)에 들 방향: +1 롱, -1 숏, 0 쉼. 그날 9시 봉이나 hour-1시 봉이 없으면 None."""
    bar = hour - 1
    if date not in table.index or bar not in table.columns or np.isnan(table.at[date, bar]):
        return None
    past = table.loc[table.index < date, bar].dropna().tail(NOISE_DAYS)
    if len(past) < NOISE_DAYS:
        return None
    m, n = table.at[date, bar], past.abs().mean()
    return 1 if m > k * n else -1 if m < -k * n else 0


def noise_sides(table, k):
    """noise_side를 표 전체에 한 번에: 날짜 × 판단 시각(10 ~ 15)의 방향(+1, -1, 0, 계산할 수 없으면 NaN). 과거 시험용."""
    out = pd.DataFrame(np.nan, index=table.index, columns=list(INTRADAY_HOURS))
    for hour in INTRADAY_HOURS:
        if hour - 1 not in table.columns:
            continue
        col = table[hour - 1]
        valid = col.dropna()
        noise = valid.abs().rolling(NOISE_DAYS).mean().shift(1).reindex(col.index)  # 그날 전까지 값이 있는 날 NOISE_DAYS개의 평균
        side = np.where(col > k * noise, 1.0, np.where(col < -k * noise, -1.0, 0.0))
        out[hour] = np.where(col.notna() & noise.notna(), side, np.nan)
    return out


def hourly():
    """나스닥100 선물 1시간봉(야후 NQ=F, 최근 2년, 뉴욕 시각, 정시 시작). 6시간마다 새로. 섞기 후보의 과거 시험용."""
    path = config.STORE / "data" / "NQF_1h.pkl"
    if not path.exists() or time.time() - path.stat().st_mtime > 6 * 3600:
        import yfinance as yf
        df = yf.download("NQ=F", period="730d", interval="1h", progress=False, auto_adjust=False, multi_level_index=False)
        if len(df) > 1000:
            df.to_pickle(path)
    h = pd.read_pickle(path)
    h = h[h.index.minute == 0].rename(columns=str.lower)
    h.index = h.index.tz_localize(None)
    return h


def mix_pnl(key, px, levers=None, cap=None):
    """섞기 후보의 시간별 손익(1시간봉). 두 갈래의 노출을 시간마다 더하고, 그 합을 ±cap(계좌 대비)으로 자른 뒤 손익과 비용을 계산한다.
    비용은 합친 노출이 바뀐 만큼(한 계좌라 갈래끼리 반대면 상쇄된다). levers=(일봉 몫, 장중 몫), cap은 연구에서 바꿔 볼 때만 준다."""
    cand = config.CANDIDATES[key]
    a, b = levers or (cand["lever"], cand["intraday"]["lever"])
    if cap is None and cand.get("max_contracts"):
        cap = cand["max_contracts"] * max(a, b)  # 한 갈래 몫(1계약)이 상한
    h = hourly()
    ret = h["close"].pct_change()
    gap = h.index.to_series().diff() > pd.Timedelta(hours=1)
    ret[(gap & (h.index.hour != 18)) | (ret.abs() > 0.05)] = np.nan  # 롤오버 틈
    ret = ret.fillna(0.0)
    # 일봉 갈래: t일 16:00에 정한 노출을 t일 16:00 ~ t+1일 16:00에 든다
    expo = exposure(key, px) * a
    day = pd.DatetimeIndex(ret.index.normalize())
    held = expo.shift(1).reindex(day, method="ffill").to_numpy()
    after = ret.index.hour >= 16
    held[after] = expo.reindex(day[after], method="ffill").to_numpy()
    total = pd.Series(np.nan_to_num(held), index=ret.index)
    # 장중 갈래: hour시(10 ~ 15)에 정한 방향을 그 시간 봉(hour ~ hour+1시) 동안 든다. 16시 봉부터는 0
    sides = noise_sides(moves(h), cand["intraday"]["k"]).stack()  # (날짜, 시각) → 방향
    stamps = pd.DatetimeIndex([d + pd.Timedelta(hours=hr) for d, hr in sides.index])
    total = total.add(pd.Series(sides.to_numpy() * b, index=stamps).reindex(total.index).fillna(0.0))
    if cap:
        total = total.clip(-cap, cap)
    return total * ret - total.diff().abs().fillna(0.0) * COST * 0.5  # 비용: 노출이 바뀐 만큼 한쪽 0.005%


def history():
    """나스닥100 일봉 종가. 하루에 한 번만 새로 받는다."""
    path = config.STORE / "data" / "NDX_1d.pkl"
    if not path.exists() or time.time() - path.stat().st_mtime > 6 * 3600:
        import yfinance as yf
        df = yf.download("^NDX", period="max", interval="1d", progress=False, auto_adjust=False, multi_level_index=False)
        if len(df) > 1000:
            path.parent.mkdir(parents=True, exist_ok=True)
            df.to_pickle(path)
    return pd.read_pickle(path)["Close"].dropna()


def stats(pnl):
    """일별 손익의 성적표."""
    if len(pnl) < 2:
        return None
    eq = (1 + pnl).cumprod()
    months = (1 + pnl).groupby([pnl.index.year, pnl.index.month]).prod() - 1
    years = len(pnl) / 252
    return {"days": len(pnl), "total": float(eq.iloc[-1] - 1), "daily": float(pnl.mean()),
            "yearly": float(eq.iloc[-1] ** (1 / years) - 1) if years >= 0.5 else None,
            "sharpe": float(pnl.mean() / pnl.std() * np.sqrt(252)) if pnl.std() > 0 else None,
            "up_days": float((pnl[pnl != 0] > 0).mean()) if (pnl != 0).any() else None,
            "worst_day": float(pnl.min()), "worst_month": float(months.min()),
            "drawdown": float((eq / eq.cummax() - 1).min())}


def windows(pnl):
    """대회와 같은 4주 구간마다의 수익률과 MDD(구간 시작 금액부터 잰 일별 종가 기준)의 분포."""
    p = pnl.to_numpy()
    rows = []
    for i in range(0, len(p) - WIN + 1, STEP):
        eq = np.cumprod(np.concatenate([[1.0], 1 + p[i:i + WIN]]))
        rows.append((eq[-1] - 1, (eq / np.maximum.accumulate(eq) - 1).min()))
    if not rows:
        return None
    r, m = np.array(rows).T
    return {"count": len(rows), "ret_median": float(np.median(r)), "ret_mean": float(r.mean()), "ret_p10": float(np.percentile(r, 10)),
            "ret_p90": float(np.percentile(r, 90)), "up": float((r > 0).mean()),
            "mdd_median": float(np.median(m)), "mdd_p10": float(np.percentile(m, 10)), "score": float(np.mean(r + m))}


def signals(key, px, since="2021-01-01"):
    """그 후보의 규칙을 since부터 돌렸을 때 계약 수가 바뀐 날들: [{time, from, to, price}].

    계좌는 늘 시작 금액(config.CAPITAL)이고 1계약 = 그날 지수 × 2달러로 본다(차트에 진입·청산 자리를 보여 주는 용도).
    날짜는 그날 종가로 정한 것이고, 실제로는 그 직후(뉴욕 16:10)에 맞춘다.
    """
    expo = exposure(key, px) * config.CANDIDATES[key]["lever"]
    want = (expo * config.CAPITAL / (px * config.MULTIPLIER)).round().astype(int)
    out, held = [], 0
    for day, n in want[since:].items():
        if n != held:
            out.append({"time": int(day.timestamp()), "from": int(held), "to": int(n), "price": float(px[day])})
            held = int(n)
    return _with_pnl(out)


def _with_pnl(events):
    """계약 수 변화 목록에 평균 진입가를 따라가며, 줄이거나 정리할 때의 손익(달러, 비용 전)을 pnl로 붙인다."""
    entry = None
    for e in events:
        held, want, price = e["from"], e["to"], e["price"]
        closed = abs(held) if want == 0 or (held > 0) != (want > 0) else max(abs(held) - abs(want), 0)
        if held and closed:
            e["pnl"] = round((price - entry) * (1 if held > 0 else -1) * closed * config.MULTIPLIER, 2)
        if want == 0:
            entry = None
        elif held == 0 or (held > 0) != (want > 0):
            entry = price
        elif abs(want) > abs(held):
            entry = (entry * abs(held) + price * (abs(want) - abs(held))) / abs(want)
    return events


def replay(key, px):
    """분봉 차트용: 그 후보를 NQ=F 1시간봉(최근 2년) 위에서 가상 계좌처럼 돌린 계약 수 변화 [{time, from, to, price, sleeve, why, pnl?}].

    일봉 갈래는 그날 뉴욕 16:10에, 장중 갈래는 10 ~ 15시 정시에 정하고 16시에 정리한다(mnq/paper.py와 같은 순서·같은 상한).
    계좌는 늘 시작 금액으로 보고, 가격은 그 시각 직전 1시간봉의 종가다."""
    cand = config.CANDIDATES[key]
    if "scalp" in cand:
        return _scalp_events(key)
    if cand.get("ichimoku"):
        return _ichimoku_events()
    h = hourly()
    at = h["close"].copy()
    at.index = at.index + pd.Timedelta(hours=1)  # 그 정시의 가격 = 직전 봉 종가
    size = lambda lever, price: int(round(lever * config.CAPITAL / (price * config.MULTIPLIER)))
    plan = []  # (뉴욕 시각, 갈래, 그 갈래의 계약 수 또는 방향, 까닭)
    expo = exposure(key, px)[h.index[0].normalize():]
    for day, e in expo.items():
        plan.append((day + pd.Timedelta(hours=16, minutes=10), "daily", e * cand["lever"], f"종가 {px[day]:,.2f} → 노출 {e * cand['lever']:.2f}배"))
    if "intraday" in cand:
        sides = noise_sides(moves(h), cand["intraday"]["k"])
        for day, row in sides.iterrows():
            for hour, side in row.dropna().items():
                why = "노이즈 위로 → 롱" if side > 0 else "노이즈 아래로 → 숏" if side < 0 else "노이즈 안 → 쉼"
                plan.append((day + pd.Timedelta(hours=hour), "intraday", side, f"{hour}시 판단: {why}"))
            plan.append((day + pd.Timedelta(hours=16), "intraday", 0.0, "16시 정리"))
    plan.sort(key=lambda p: (p[0], p[1] == "daily"))  # 16:00 정리가 16:10 일봉 결정보다 먼저
    parts, held, out = {}, 0, []
    cap = cand.get("max_contracts")
    for t, sleeve, v, why in plan:
        i = at.index.searchsorted(t, side="right") - 1
        if i < 0 or t - at.index[i] > pd.Timedelta(hours=2):
            continue  # 그 시각 시세가 없다(쉬는 날)
        price = float(at.iloc[i])
        parts[sleeve] = size(v, price) if sleeve == "daily" else int(v) * size(cand["intraday"]["lever"], price)
        want = sum(parts.values())
        if cap:
            want = max(-cap, min(cap, want))
        if want != held:
            out.append({"time": int(t.tz_localize("America/New_York").timestamp()), "from": held, "to": want, "price": price, "sleeve": sleeve, "why": why})
            held = want
    return _with_pnl(out)


# ---------- 스캘핑(관찰용): research/r5_scalp.py에서 승률이 가장 높던 설정 ----------
# 1분봉 종가가 직전 look분 고가 위면 롱, 저가 아래면 숏(뉴욕 9:30 ~ 16시에 시작한 봉만). 다음 봉 시가에 들고 익절 tp·손절 sl(포인트)·최대 hold분.
# 같은 봉에서 익절·손절이 함께 닿으면 손절로 본다(r5와 같은 보수적 가정). r5는 신호마다 겹쳐 셌지만 여기서는 한 번에 하나만 든다.
def scalp_trades(frame, look=30, tp=2.0, sl=25.0, hold=60, since=None):
    """frame: 1분봉(뉴욕 시각, 봉 시작 색인, open·high·low·close, 마감된 봉만). since 뒤에 신호가 난 매매들
    [{side, t_in, entry, t_out, exit, why}]. 아직 끝나지 않은 매매는 t_out이 None이다."""
    o, h, l, c = (frame[k].to_numpy() for k in ("open", "high", "low", "close"))
    top = frame["high"].shift(1).rolling(look).max().to_numpy()
    bottom = frame["low"].shift(1).rolling(look).min().to_numpy()
    t = frame.index
    minute = t.hour * 60 + t.minute
    ok = (minute >= 9 * 60 + 30) & (minute < 16 * 60) & (t.dayofweek < 5)
    if since is not None:
        ok &= t >= since
    out, i, n = [], look, len(frame)
    while i < n - 1:
        side = 1 if c[i] > top[i] else -1 if c[i] < bottom[i] else 0
        if not (ok[i] and side):
            i += 1
            continue
        trade = {"side": side, "t_in": t[i + 1], "entry": float(o[i + 1]), "t_out": None, "exit": None, "why": None}
        out.append(trade)
        end = t[i + 1] + pd.Timedelta(minutes=hold)
        j = i + 1
        while j < n and t[j] < end:
            gain, loss = (h[j] - o[i + 1], o[i + 1] - l[j]) if side > 0 else (o[i + 1] - l[j], h[j] - o[i + 1])
            if loss >= sl or gain >= tp:
                hit = loss >= sl  # 둘 다 닿으면 손절
                trade.update(t_out=t[j] + pd.Timedelta(minutes=1), exit=trade["entry"] + side * (-sl if hit else tp), why="손절" if hit else "익절")
                break
            j += 1
        else:
            if j == n:
                return out  # 아직 들고 있다
            trade.update(t_out=t[j], exit=float(c[j - 1]), why=f"{hold}분 청산")
        i = j
    return out


# ---------- 일목 구름 반전(관찰용): research/r15_ichimoku.py ----------
def ichimoku(frame):
    """1시간봉(high·low) → 봉마다 선행스팬 A·B(26봉 앞으로 밀기 전, 오른쪽 끝에 그려지는 미래 구름의 값)와 그 봉 마감 뒤 들 방향 side.
    A가 위면 +1(롱), 아래면 -1(숏), 계산 전(52봉 미만)은 0. 구름 색이 바뀌는 봉에서 롱·숏이 뒤집힌다."""
    mid = lambda n: (frame["high"].rolling(n).max() + frame["low"].rolling(n).min()) / 2
    a, b = (mid(9) + mid(26)) / 2, mid(52)
    return pd.DataFrame({"a": a, "b": b, "side": np.sign(a - b).fillna(0.0)})


def _ichimoku_events():
    """일목 구름 반전 과거 시험(NQ=F 1시간봉)의 계약 수 변화(1계약, 차트 화살표용). 시각은 봉이 마감된 때."""
    h = hourly()
    k = ichimoku(h)
    out, held = [], 0
    for t, row in k.iterrows():
        if row.side and row.side != held:
            out.append({"time": int((t + pd.Timedelta(hours=1)).tz_localize("America/New_York").timestamp()), "from": held, "to": int(row.side),
                        "price": float(h.at[t, "close"]), "sleeve": "ichimoku", "why": "구름 반전 → " + ("롱" if row.side > 0 else "숏")})
            held = int(row.side)
    return _with_pnl(out)


def minute_history():
    """스캘핑 과거 시험용 LS MNQ 1분봉(research/fetch_1m.py로 받은 것, 뉴욕 시각). 근월물이 되기 전 거래가 적던 날은 뺀다(r5와 같게)."""
    import glob
    df = pd.read_pickle(sorted(glob.glob(str(config.STORE / "data" / "ls_*_1m.pkl")))[-1])
    vol = df["volume"].groupby(df.index.normalize()).sum()
    df = df[df.index.normalize().isin(vol[vol > vol.max() * 0.2].index)]
    df.index = df.index.tz_localize(None)
    return df


def _scalp_events(key):
    """스캘핑 과거 시험의 계약 수 변화(1계약, 차트 화살표용)."""
    out = []
    for x in scalp_trades(minute_history(), **config.CANDIDATES[key]["scalp"]):
        side = "롱" if x["side"] > 0 else "숏"
        out.append({"time": int(x["t_in"].tz_localize("America/New_York").timestamp()), "from": 0, "to": x["side"], "price": x["entry"],
                    "sleeve": "scalp", "why": f"돌파 → {side}"})
        if x["t_out"] is not None:
            out.append({"time": int(x["t_out"].tz_localize("America/New_York").timestamp()), "from": x["side"], "to": 0, "price": x["exit"],
                        "sleeve": "scalp", "why": x["why"]})
    return _with_pnl(out)


def backtest(key, px):
    """과거 시험: 그래프용 곡선(배율 적용), 구간별·연도별 성적, 4주 구간 분포.

    일봉 후보는 나스닥100 지수 일봉(2000 ~), 섞기 후보는 장중 갈래 때문에 NQ=F 1시간봉(최근 2년)으로 한다. 마지막 구간이 확인용이다."""
    cand = config.CANDIDATES[key]
    if "scalp" in cand:
        closed = [x for x in scalp_trades(minute_history(), **cand["scalp"]) if x["t_out"] is not None]
        pts = pd.Series([(x["exit"] - x["entry"]) * x["side"] for x in closed], index=[x["t_out"] for x in closed])
        cash = (pts * config.MULTIPLIER - 2 * config.FEE_PER_CONTRACT) / config.CAPITAL  # 1계약, 가상 계좌와 같은 비용(사고팔 때마다)
        pnl = cash.groupby(cash.index.normalize()).sum()
        periods = {f"1분봉 {pnl.index[0]:%Y-%m-%d} ~": (None, None)}
        note = (f"LS MNQ 1분봉 {len(pnl)}일, 매매 {len(pts)}번: 승률(비용 전) {(pts > 0).mean():.0%}, 1회 평균 {pts.mean():+.2f}포인트(비용 전). "
                "기간이 아주 짧아 근거가 약하다")
    elif cand.get("ichimoku"):
        h = hourly()
        pos = ichimoku(h)["side"]
        cash = (pos.shift(1).fillna(0.0) * h["close"].diff().fillna(0.0) * config.MULTIPLIER
                - config.FEE_PER_CONTRACT * pos.diff().abs().fillna(0.0)) / config.CAPITAL  # 1계약, 사고팔 때마다 비용
        pnl = cash.groupby(cash.index.normalize()).sum()
        pnl = pnl[pnl.index.dayofweek < 5]
        periods = {f"1시간봉 {pnl.index[0]:%Y-%m} ~": (None, None)}
        note = f"나스닥100 선물(NQ=F) 1시간봉 {pnl.index[0]:%Y-%m-%d} ~, 1계약. 표준값 9·26·52 그대로(고른 파라미터 없음)"
    elif "intraday" in cand:
        hp = mix_pnl(key, px)
        pnl = hp.groupby(hp.index.normalize()).sum()
        pnl = pnl[pnl.index.dayofweek < 5]
        periods = {f"1시간봉 {pnl.index[0]:%Y-%m} ~": (None, None)}
        note = f"나스닥100 선물(NQ=F) 1시간봉 {pnl.index[0]:%Y-%m-%d} ~. 장중 갈래 때문에 2년치뿐이라 일봉 후보들보다 근거가 약하다"
    else:
        expo = exposure(key, px) * cand["lever"]
        ret = px.pct_change().fillna(0.0)
        pnl = (expo.shift(1).fillna(0.0) * ret - COST * expo.diff().abs().fillna(0.0))["2000":]
        periods = {"설계 2000 ~ 2019": ("2000", "2019"), "확인 2020 ~": ("2020", None)}
        note = "나스닥100 지수(^NDX) 일봉 2000 ~. 선물은 지수보다 배당·금리만큼(4주에 0.1 ~ 0.3%) 다르게 움직인다"
    eq = (1 + pnl).cumprod()
    return {
        "note": note,
        "curve": [[int(t.timestamp()), float(v)] for t, v in eq.items()],
        "periods": {name: {"stats": stats(pnl[a:b]), "windows": windows(pnl[a:b])} for name, (a, b) in periods.items()},
        "years": {int(y): {"total": float((1 + g).prod() - 1), "drawdown": float(((1 + g).cumprod() / (1 + g).cumprod().cummax() - 1).min())}
                  for y, g in pnl.groupby(pnl.index.year)},
        "until": int(px.index[-1].timestamp()),
    }
