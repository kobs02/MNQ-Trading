"""실시간 차트: LS증권 MNQ 근월물 분봉(o3103)을 받아 두고, 실시간 체결(웹소켓, mnq/stream.py)로 마지막 봉과 현재가를 바로 고친다.

화면은 0.5초마다 물어도 되고, LS로는 분봉 조회를 봉 단위마다 RESYNC초에 한 번만 보낸다(o3103은 초당 1회까지).
체결이 끊겼으면(장이 쉬거나 웹소켓이 끊김) 분봉 조회를 FALLBACK초마다 해서 맞춘다.
"""
import threading
import time

from . import config, ls, paper, strategies, stream

INTERVALS = {"1": 1, "5": 5, "15": 15, "60": 60, "D": None}  # 화면의 봉 단위 → 분. D: 일봉(야후 NQ=F, 2021 ~)
DAILY_FROM = "2021-01-01"
FIRST = 1000     # 처음에 받는 봉 수. 화면을 왼쪽 끝까지 끌면 OLDER개씩 더 과거를 받는다
OLDER = 1000
RESYNC = 60.0    # 체결이 들어오는 동안: 웹소켓으로 만든 봉을 거래소 분봉과 맞추는 간격(초)
FALLBACK = 3.0   # 체결이 끊겼을 때 분봉을 다시 받는 간격(초)
QUOTE_EVERY = 10.0
_bars = {}       # 분 → {봉 시작 시각: 봉}
_at = {}
_cont = {}       # 분 → 더 과거의 봉을 이어 받을 열쇠(None이면 LS에 더 없음)
_quote = {"at": 0, "data": None}
_lock = threading.Lock()
_fetching = threading.Lock()  # LS 조회는 한 번에 하나만(동시에 온 요청이 같은 조회를 두 번 보내지 않게)


def _symbol():
    if not paper.status["symbol"]:
        paper.status["symbol"] = ls.front_month()["Symbol"]
    return paper.status["symbol"]


def _bar(r):
    return {"time": r["t"], "open": float(r["open"]), "high": float(r["high"]), "low": float(r["low"]),
            "close": float(r["close"]), "volume": int(r["volume"])}


def _live():
    return time.time() - stream.state["last_tick"] < 15


def _tick(t, price, qty):
    """체결 하나: 받아 둔 봉 단위마다 그 체결이 속한 봉을 고치거나 새로 연다. 현재가도 고친다."""
    with _lock:
        for minutes, bars in _bars.items():
            start = t - t % (minutes * 60)
            last = max(bars) if bars else None
            if last is not None and start < last:
                continue  # 늦게 온 체결은 이미 지난 봉이라 버린다(다음 분봉 조회 때 맞춰진다)
            bar = bars.get(start)
            if bar is None:
                bars[start] = {"time": start, "open": price, "high": price, "low": price, "close": price, "volume": qty}
            else:
                bar.update(high=max(bar["high"], price), low=min(bar["low"], price), close=price, volume=bar["volume"] + qty)
        q = _quote["data"]
        if q:
            q.update(price=price, diff=price - q["prev"], high=max(q["high"], price), low=min(q["low"], price), volume=q["volume"] + qty)
    paper.status.update(price=price, price_at=int(time.time()))


stream.on_tick(_tick)


_daily = {"at": 0, "bars": []}


def daily():
    """2021년부터의 일봉. LS증권은 지금 월물의 기록만 주므로 야후의 나스닥100 선물 연결 시세(NQ=F)를 쓴다(10분마다 새로).
    시각은 그날 0시(UTC)라 차트에 날짜로 보인다. 오늘 봉의 종가는 실시간 체결 가격으로 고친다."""
    if time.time() - _daily["at"] > 600:
        import yfinance as yf
        df = yf.download("NQ=F", start=DAILY_FROM, interval="1d", progress=False, auto_adjust=False, multi_level_index=False).dropna()
        if len(df):
            _daily.update(at=time.time(), bars=[{"time": int(t.timestamp()), "open": float(r.Open), "high": float(r.High), "low": float(r.Low),
                                                  "close": float(r.Close), "volume": int(r.Volume)} for t, r in df.iterrows()])
    out = [dict(b) for b in _daily["bars"]]
    price = paper.status["price"]
    if out and price and _live():
        last = out[-1]
        last.update(close=price, high=max(last["high"], price), low=min(last["low"], price))
    return out


def bars(key, since=None):
    """그 봉 단위의 봉들(오래된 것부터). since를 주면 그 시각 이후의 봉만."""
    if key == "D":
        return [b for b in daily() if since is None or b["time"] >= since]
    minutes = INTERVALS[key]
    stale = lambda: minutes not in _bars or time.time() - _at[minutes] > (RESYNC if _live() else FALLBACK)
    if stale():
        with _fetching:  # 체결 반영(_lock)은 막지 않는다
            if stale():  # 기다리는 사이에 다른 요청이 받아 왔으면 건너뛴다
                first = minutes not in _bars
                rows, cont = ls.minute_bars(_symbol(), minutes, FIRST if first else 3, more=True)
                with _lock:
                    store = _bars.setdefault(minutes, {})
                    for r in rows:
                        store[r["t"]] = _bar(r)
                    _at[minutes] = time.time()
                    if first:
                        _cont[minutes] = cont
    with _lock:
        store = _bars[minutes]
        return [store[t] for t in sorted(store) if since is None or t >= since]


def older(key):
    """화면을 왼쪽 끝까지 끌었을 때: 받아 둔 것보다 오래된 봉 OLDER개(오래된 것부터)와, LS에 더 남았는지."""
    minutes = INTERVALS[key]
    if key == "D" or minutes not in _bars:
        return {"bars": [], "done": True}
    with _fetching:
        cont = _cont.get(minutes)
        if not cont:
            return {"bars": [], "done": True}
        rows, cont = ls.minute_bars(_symbol(), minutes, OLDER, cont=cont, more=True)
        with _lock:
            store = _bars[minutes]
            first = min(store)
            new = [_bar(r) for r in rows if r["t"] < first]
            for b in new:
                store[b["time"]] = b
            _cont[minutes] = cont
    return {"bars": new, "done": cont is None or not new}


def quote():
    """현재가 정보. 조회는 QUOTE_EVERY초에 한 번이고, 그 사이에는 실시간 체결로 가격을 고친다."""
    due = lambda: time.time() - _quote["at"] > QUOTE_EVERY
    if due():
        with _fetching:
            if due():  # 기다리는 사이에 다른 요청이 받아 왔으면 건너뛴다
                q = ls.quote(_symbol())
                with _lock:
                    _quote.update(at=time.time(), data={
                        "symbol": q["Symbol"], "name": q["SymbolNm"], "price": float(q["TrdP"]), "diff": float(q["YdiffP"]),
                        "prev": float(q["TrdP"]) - float(q["YdiffP"]), "open": float(q["OpenP"]), "high": float(q["HighP"]), "low": float(q["LowP"]),
                        "volume": int(q["TotQ"]), "expiry": q["MtrtDt"], "tick": float(q["UntPrc"]), "tick_value": float(q["MnChgAmt"]),
                        "margin": float(q["OpngMgn"]), "time": q["KorDate"] + q["RcvTm"]})
    with _lock:
        return dict(_quote["data"], live=_live())


_signals = {"at": 0, "data": None}


def signals():
    """후보마다 2021년부터 규칙대로 계약 수를 바꾼 날들(일봉 차트에 진입·청산 자리를 그리는 용도). 6시간마다 새로."""
    if not _signals["data"] or time.time() - _signals["at"] > 6 * 3600:
        px = strategies.history()
        _signals.update(at=time.time(), data={key: strategies.signals(key, px, DAILY_FROM) for key in config.CANDIDATES})
    return _signals["data"]


def get_chart(key, since=None):
    return {"interval": key, "bars": bars(key, since), "quote": quote()}


def start():
    """실시간 체결 웹소켓을 붙인다."""
    stream.start(_symbol())
