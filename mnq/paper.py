"""모의 운용: 후보마다 $100,000짜리 가상 계좌로 MNQ 근월물을 실제 가격(LS증권 시세)으로 따라간다. 주문은 내지 않는다.

- 하루 한 번(뉴욕 16:10 뒤) 그날 나스닥100 종가로 노출을 정하고, 그 순간의 MNQ 가격에 정수 계약으로 맞춘다(일봉 갈래)
- 섞기 후보는 장중 갈래도 있다: 평일 뉴욕 10 ~ 15시 정시에 LS 1시간봉으로 방향을 정하고 16시에 정리한다.
  계좌는 갈래마다의 계약 수(parts)를 따로 기억하고, 그 합을 든다. 차이만큼만 사고팔고 비용도 그만큼 뗀다
- 1분마다 MNQ 가격으로 계좌를 다시 계산하고, 시간이 바뀔 때 기록을 남긴다
- 계좌는 store/paper.json에 남아 프로그램을 껐다 켜도 이어진다. 꺼져 있던 동안의 매매는 되짚지 않고,
  켜면 놓친 마지막 일봉 결정만 그 순간 가격으로 따라잡는다
"""
import json
import logging
import threading
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

from . import config, console, ls, strategies

log = logging.getLogger(__name__)
NY = ZoneInfo("America/New_York")
KST = ZoneInfo("Asia/Seoul")
PATH = config.STORE / "paper.json"
_lock = threading.Lock()
_state = None
_cache = {"backtests": None, "at": 0}
_side = {}  # (날짜, 판단 시각, k) → 장중 갈래의 방향. 후보들이 나눠 쓴다
status = {"error": None, "price": None, "symbol": None, "price_at": None}


def _load():
    global _state
    if _state is None:
        try:
            _state = json.loads(PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _state = {}
    return _state


def _save():
    config.STORE.mkdir(exist_ok=True)
    tmp = PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(_state, ensure_ascii=False), encoding="utf-8")
    tmp.replace(PATH)


def _contracts(expo, equity, price):
    """노출(계좌 대비 배수)을 정수 계약으로: 1계약 = 가격 × 2달러."""
    return int(round(expo * equity / (price * config.MULTIPLIER)))


def _mark(acct, price, now):
    acct["equity"] += acct["contracts"] * config.MULTIPLIER * (price - acct["price"])
    acct["price"] = price
    hour = int(now // 3600) * 3600
    if acct["marks"][-1][0] < hour:
        acct["marks"].append([hour, round(acct["equity"], 2)])
    else:
        acct["marks"][-1][1] = round(acct["equity"], 2)


def _pos(n):
    return f"롱 {n}계약" if n > 0 else f"숏 {-n}계약" if n < 0 else "없음"


def _set(key, acct, sleeve, n, price, now, record):
    """갈래 sleeve의 계약 수를 n으로 바꾸고, 계좌 전체(갈래의 합)를 그만큼 사고판다. record: 매매 기록에 덧붙일 내용."""
    parts = acct.setdefault("parts", {"daily": acct["contracts"]})
    parts[sleeve] = n
    cap = config.CANDIDATES[key].get("max_contracts")
    held, want = acct["contracts"], sum(parts.values())
    if cap:
        want = max(-cap, min(cap, want))  # 갈래들을 더한 포지션의 상한
    trade = want - held
    fee = abs(trade) * config.FEE_PER_CONTRACT
    acct["equity"] -= fee
    # 평균 진입가: 같은 방향으로 늘리면 평균하고, 줄이면 그대로, 새로 들거나 방향이 바뀌면 지금 가격
    if want == 0:
        acct["entry"] = None
    elif held == 0 or (held > 0) != (want > 0):
        acct["entry"] = price
    elif abs(want) > abs(held):
        acct["entry"] = (acct.get("entry", price) * abs(held) + price * abs(trade)) / abs(want)
    acct["contracts"] = want
    acct["trades"].append({"time": int(now), "from": held, "to": want, "price": price, "fee": fee, "sleeve": sleeve, **record})
    del acct["trades"][:-500]
    name = config.CANDIDATES[key]["name"].split(" · ")[0] + (" (장중)" if sleeve == "intraday" else "")
    if trade:
        console.success(log, "가상 매매 · %s: %s → %s @ %s (%s, 비용 $%.0f)", name, _pos(held), _pos(want), f"{price:,.2f}", record.get("why", ""), fee)
    else:
        log.info("가상 매매 · %s: %s 그대로 (%s)", name, _pos(want), record.get("why", ""))


def _decide(key, acct, px, price, now):
    """일봉 갈래: 그날 종가로 정한 노출을 정수 계약으로."""
    expo = float(strategies.exposure(key, px).iloc[-1]) * config.CANDIDATES[key]["lever"]
    close = float(px.iloc[-1])
    acct["decided"] = px.index[-1].strftime("%Y-%m-%d")
    _set(key, acct, "daily", _contracts(expo, acct["equity"], price), price, now,
         {"date": acct["decided"], "expo": round(expo, 3), "close": close, "why": f"종가 {close:,.2f} → 노출 {expo:.2f}배"})


def _intraday_side(ny, k):
    """장중 갈래의 이번 시각 방향(+1, -1, 0). LS 1시간봉으로 계산하고, 같은 시각에는 후보들이 나눠 쓴다. 계산할 수 없으면 None."""
    date, hour = pd.Timestamp(ny.date()), ny.hour
    if (date, hour, k) not in _side:
        _side[(date, hour, k)] = strategies.noise_side(strategies.moves(_hour_frame()), date, hour, k)
    return _side[(date, hour, k)]


def _hour_frame():
    """LS 1시간봉 약 3주(지난 14일 + 오늘): 뉴욕 시각(봉 시작) 색인, open·high·low·close."""
    rows = ls.minute_bars(status["symbol"], 60, 500)
    return pd.DataFrame({k: [float(r[k]) for r in rows] for k in ("open", "high", "low", "close")},
                        index=pd.to_datetime([r["t"] for r in rows], unit="s", utc=True).tz_convert(NY).tz_localize(None))


_bands = {}


def bands(k):
    """장중 갈래 설명용: 마지막 거래일(뉴욕)의 9시 시가와 판단 시각마다의 노이즈 경계 {시: [위, 아래]}. 한 시간에 한 번 LS로 계산."""
    stamp = (datetime.now(NY).strftime("%Y-%m-%d %H"), k)
    if _bands.get("stamp") != stamp and status["symbol"]:
        _bands["stamp"] = stamp  # 실패해도 이 시간에는 다시 묻지 않는다(15초마다 LS를 부르지 않게)
        frame = _hour_frame()
        table = strategies.moves(frame)
        date = table.index[-1]
        o = float(frame.loc[frame.index == date + pd.Timedelta(hours=9), "open"].iloc[0])
        out = {}
        for hour in strategies.INTRADAY_HOURS:
            past = table.loc[table.index < date, hour - 1].dropna().tail(strategies.NOISE_DAYS)
            if len(past) == strategies.NOISE_DAYS:
                n = k * float(past.abs().mean())
                out[hour] = [o * (1 + n), o * (1 - n)]
        _bands.update(stamp=stamp, data={"date": f"{date:%Y-%m-%d}", "today": date.date() == datetime.now(NY).date(), "open": o, "bands": out})
    return _bands.get("data")


def _intraday(key, acct, ny, price, now):
    """장중 갈래: 10 ~ 15시 정시에 방향을 정하고 16시에 정리한다. 이미 그 시각에 정했으면 하지 않는다."""
    cand = config.CANDIDATES[key]["intraday"]
    stamp = f"{ny:%Y-%m-%d} {ny.hour}"
    held = acct.get("parts", {}).get("intraday", 0)
    if acct.get("intraday_at") == stamp:
        return
    if ny.weekday() >= 5 or ny.hour >= 16 or ny.hour < 9:
        if held:  # 장 마감 뒤: 정리
            acct["intraday_at"] = stamp
            _set(key, acct, "intraday", 0, price, now, {"date": f"{ny:%Y-%m-%d}", "expo": 0.0, "why": "16시 정리"})
        return
    if ny.hour not in strategies.INTRADAY_HOURS or ny.minute >= 10:  # 정시 뒤 10분 안에만 판단(늦으면 그 시간은 건너뛴다)
        return
    side = _intraday_side(ny, cand["k"])
    acct["intraday_at"] = stamp
    if side is None:
        return
    n = side * _contracts(cand["lever"], acct["equity"], price)
    if n != held:
        why = "9시 시가 대비 위로 노이즈 밖 → 롱" if side > 0 else "9시 시가 대비 아래로 노이즈 밖 → 숏" if side < 0 else "노이즈 안 → 쉼"
        _set(key, acct, "intraday", n, price, now, {"date": f"{ny:%Y-%m-%d}", "expo": side * cand["lever"], "why": f"{ny.hour}시 판단: {why}"})


def _ichimoku(key, acct, ny, price, now):
    """일목 구름 반전 갈래(관찰용): 매시 정각 뒤 10분 안에, 마감된 LS 1시간봉으로 과거 시험과 같은 strategies.ichimoku를 돌려 방향을 따른다."""
    stamp = f"{ny:%Y-%m-%d %H}"
    wd, hour = ny.weekday(), ny.hour
    closed = wd == 5 or (wd == 6 and hour < 18) or (wd == 4 and hour >= 17) or hour == 17  # CME가 쉬는 동안은 새 봉이 없다
    if acct.get("ichimoku_at") == stamp or ny.minute >= 10 or closed:
        return
    acct["ichimoku_at"] = stamp
    frame = _hour_frame()
    frame = frame[frame.index + pd.Timedelta(hours=1) <= ny.replace(tzinfo=None)]  # 마감된 봉만
    k = strategies.ichimoku(frame).iloc[-1]
    held, want = acct.get("parts", {}).get("ichimoku", 0), int(k.side)
    if want and want != held:
        why = f"구름 반전: A {k.a:,.2f} {'>' if want > 0 else '<'} B {k.b:,.2f} → {'롱' if want > 0 else '숏'}"
        _set(key, acct, "ichimoku", want, price, now, {"date": f"{ny:%Y-%m-%d}", "expo": float(want), "why": why})


def _minute_frame():
    """LS 1분봉 500개(약 8시간, 마감된 봉만): 뉴욕 시각(봉 시작) 색인, open·high·low·close."""
    rows = [r for r in ls.minute_bars(status["symbol"], 1, 500) if r["t"] + 60 <= time.time()]
    return pd.DataFrame({k: [float(r[k]) for r in rows] for k in ("open", "high", "low", "close")},
                        index=pd.to_datetime([r["t"] for r in rows], unit="s", utc=True).tz_convert(NY).tz_localize(None))


def _scalp(key, acct, ny, price, now):
    """스캘핑 갈래(관찰용): 이번 한국 시각 밤 세션(config의 kst, 22:30 ~ 05:00) 시작부터의 LS 1분봉에 과거 시험과 같은 scalp_trades를 돌려,
    지금 들고 있어야 할 방향을 따른다. 끝난 뒤에도 마지막 매매가 끝날 때까지(최대 hold분) 지켜보고 그 뒤에 정리한다.
    가상 체결은 그 순간의 가격이라 익절·손절 가격과 조금 다르다."""
    cfg = config.CANDIDATES[key]["scalp"]
    held = acct.get("parts", {}).get("scalp", 0)
    a, b = cfg["kst"]
    kst = datetime.now(KST)
    m = kst.hour * 60 + kst.minute
    start = kst.replace(hour=a // 60, minute=a % 60, second=0, microsecond=0) - timedelta(days=1 if m < a else 0)  # 이번(또는 방금 끝난) 세션의 시작
    if start.weekday() >= 5 or kst >= start + timedelta(days=1 if a > b else 0, minutes=b - a + cfg["hold"]):
        if held:
            _set(key, acct, "scalp", 0, price, now, {"date": f"{ny:%Y-%m-%d}", "expo": 0.0, "why": "세션 끝 정리"})
        return
    trades = strategies.scalp_trades(_minute_frame(), **cfg, since=pd.Timestamp(start.astimezone(NY).replace(tzinfo=None)))
    last = trades[-1] if trades else None
    want = last["side"] if last and last["t_out"] is None else 0
    if want != held:
        why = (f"{cfg['look']}분 {'고가' if want > 0 else '저가'} 돌파 → {'롱' if want > 0 else '숏'} (진입 {last['entry']:,.2f})" if want
               else f"{last['why']} ({last['exit']:,.2f})" if last and last["t_out"] is not None else "정리")
        _set(key, acct, "scalp", want, price, now, {"date": f"{ny:%Y-%m-%d}", "expo": float(want), "why": why})


def tick():
    """감시 스레드가 1분마다 부른다."""
    now = time.time()
    try:
        symbol = status["symbol"] or ls.front_month()["Symbol"]
        price = float(ls.quote(symbol)["TrdP"])
    except Exception as e:
        if not status["error"]:  # 끊긴 순간에 한 번만 알린다(1분마다 찍지 않게)
            log.warning("MNQ 시세를 받지 못했습니다: %s", e)
        status["error"] = f"{datetime.now():%m-%d %H:%M} MNQ 시세를 받지 못했습니다: {e}"
        return
    if status["error"] or not status["price"]:
        console.success(log, "MNQ 시세 %s: %s · 가상 계좌 %d개를 1분마다 계산합니다", "다시 연결" if status["error"] else "연결", f"{price:,.2f} ({symbol})", len(config.CANDIDATES))
    status.update(symbol=symbol, price=price, price_at=int(now), error=None)
    ny = datetime.now(NY)
    due = (ny.hour, ny.minute) >= config.DECIDE_AFTER and ny.weekday() < 5
    px = None
    with _lock:
        states = _load()
        for key in config.CANDIDATES:
            acct = states.setdefault(key, {"started": int(now), "equity": config.CAPITAL, "contracts": 0, "price": price,
                                           "marks": [[int(now // 3600) * 3600, config.CAPITAL]], "trades": [], "decided": None})
            _mark(acct, price, now)
            if "intraday" in config.CANDIDATES[key]:
                try:
                    _intraday(key, acct, ny, price, now)
                except Exception:
                    log.exception("장중 갈래 판단 실패(%s)", key)
            if "scalp" in config.CANDIDATES[key]:
                try:
                    _scalp(key, acct, ny, price, now)
                except Exception:
                    log.exception("스캘핑 판단 실패(%s)", key)
            if config.CANDIDATES[key].get("ichimoku"):
                try:
                    _ichimoku(key, acct, ny, price, now)
                except Exception:
                    log.exception("일목 판단 실패(%s)", key)
            if acct["decided"] != ny.strftime("%Y-%m-%d"):
                if px is None:
                    px = strategies.history()
                    done = px[px.index < pd.Timestamp(ny.date())]  # 지난 거래일까지(야후 일봉의 오늘 장중 값 제외)
                if due and px.index[-1].strftime("%Y-%m-%d") == ny.strftime("%Y-%m-%d"):  # 오늘 종가가 나왔을 때만(휴장일 제외)
                    _decide(key, acct, px, price, now)
                elif (acct["decided"] or "") < done.index[-1].strftime("%Y-%m-%d"):  # 꺼져 있어 놓친 마지막 결정을 지금 가격으로 따라잡는다
                    _decide(key, acct, done, price, now)
        _save()


def run():
    def loop():
        while True:
            try:
                tick()
            except Exception:
                log.exception("모의 운용 계산 실패")
            time.sleep(60)
    threading.Thread(target=loop, name="paper", daemon=True).start()


def _backtests():
    if not _cache["backtests"] or time.time() - _cache["at"] > 6 * 3600:
        px = strategies.history()
        _cache.update(backtests={key: strategies.backtest(key, px) for key in config.CANDIDATES}, at=time.time(),
                      signal={key: float(strategies.exposure(key, px).iloc[-1]) * c["lever"] for key, c in config.CANDIDATES.items()},
                      close=[px.index[-1].strftime("%Y-%m-%d"), float(px.iloc[-1])],
                      explain={key: strategies.explain(key, px) for key in config.CANDIDATES},
                      left={key: strategies.days_left(key, px) for key in config.CANDIDATES})
    return _cache


def get_paper():
    """모의 운용 페이지용 데이터."""
    bt = _backtests()
    with _lock:
        states = json.loads(json.dumps(_load()))
    price = status["price"]
    out = []
    for key, cand in config.CANDIDATES.items():
        acct = states.get(key)
        item = {"key": key, "name": cand["name"], "note": cand["note"], "lever": cand["lever"], "intraday": "intraday" in cand, "backtest": bt["backtests"][key],
                "signal": {"expo": bt["signal"][key], "contracts": _contracts(bt["signal"][key], acct["equity"] if acct else config.CAPITAL, price) if price else None}}
        if acct:
            values = [config.CAPITAL] + [v for _, v in acct["marks"]]
            peak, mdd = values[0], 0.0
            for v in values:
                peak = max(peak, v)
                mdd = min(mdd, v / peak - 1)
            item.update(started=acct["started"], equity=acct["equity"], contracts=acct["contracts"], drawdown=mdd, parts=acct.get("parts"),
                        curve=acct["marks"], trades=acct["trades"][::-1][:100], decided=acct["decided"])
        out.append(item)
    return {"candidates": out, "capital": config.CAPITAL, "fee": config.FEE_PER_CONTRACT, "multiplier": config.MULTIPLIER,
            "symbol": status["symbol"], "price": price, "price_at": status["price_at"], "error": status["error"],
            "close": bt["close"], "next_decision": _next_decision()}


def get_trading():
    """실시간 차트 페이지용: 후보마다 계좌와 포지션, 매매 기록만(과거 시험 곡선은 빼서 가볍게)."""
    bt = _backtests()
    with _lock:
        states = json.loads(json.dumps(_load()))
    price = status["price"]
    out = []
    for key, cand in config.CANDIDATES.items():
        acct = states.get(key) or {}
        equity = acct.get("equity", config.CAPITAL)
        noise = None
        if "intraday" in cand:
            try:
                noise = bands(cand["intraday"]["k"])
            except Exception as e:  # 설명용이라 실패해도 화면은 그대로
                log.warning("장중 경계 계산 실패: %s", e)
        out.append({"key": key, "name": cand["name"], "rule": cand["rule"], "scalp": "scalp" in cand, "hourly": bool(cand.get("ichimoku")), "equity": equity, "contracts": acct.get("contracts", 0), "entry": acct.get("entry"),
                    "parts": acct.get("parts"), "next_intraday": _next_intraday() if "intraday" in cand else None,
                    "started": acct.get("started"), "trades": acct.get("trades", [])[::-1][:50], "explain": bt["explain"][key], "noise": noise,
                    "exit": _exits(cand, acct.get("parts") or {}, bt["close"][0], bt["left"][key]),
                    "signal": {"expo": bt["signal"][key], "contracts": _contracts(bt["signal"][key], equity, price) if price else None}})
    return {"candidates": out, "capital": config.CAPITAL, "multiplier": config.MULTIPLIER, "fee": config.FEE_PER_CONTRACT,
            "close": bt["close"], "next_decision": _next_decision(), "error": status["error"]}


def _exits(cand, parts, close_day, left):
    """갈래마다 포지션을 정리할 시각(초): 일봉은 마지막 종가일로부터 left거래일 뒤 뉴욕 16:10, 장중은 오늘 뉴욕 16시. 휴장일은 따지지 않는다."""
    out = {}
    if left and parts.get("daily", 1):  # parts가 없는 후보는 일봉 갈래뿐이다
        at = datetime.strptime(close_day, "%Y-%m-%d").replace(hour=config.DECIDE_AFTER[0], minute=config.DECIDE_AFTER[1], tzinfo=NY)
        while left:
            at += timedelta(days=1)
            left -= at.weekday() < 5
        out["daily"] = int(at.timestamp())
    if "intraday" in cand and parts.get("intraday"):
        out["intraday"] = int(datetime.now(NY).replace(hour=16, minute=0, second=0, microsecond=0).timestamp())
    return out


def _next_intraday():
    """장중 갈래의 다음 판단 시각(초): 평일 뉴욕 10 ~ 15시 정시, 16시는 정리."""
    ny = datetime.now(NY).replace(minute=0, second=0, microsecond=0)
    at = ny + timedelta(hours=1)
    while at.weekday() >= 5 or not 10 <= at.hour <= 16:
        at += timedelta(hours=1)
    return int(at.timestamp())


def _next_decision():
    """다음 결정 시각(초): 평일 뉴욕 16:10. 휴장일은 따지지 않는다."""
    ny = datetime.now(NY)
    at = ny.replace(hour=config.DECIDE_AFTER[0], minute=config.DECIDE_AFTER[1], second=0, microsecond=0)
    while at <= ny or at.weekday() >= 5:
        at += timedelta(days=1)
    return int(at.timestamp())
