"""실시간 차트: LS증권 MNQ 근월물 분봉(o3103)을 받아 두고, 화면이 물어올 때 2초가 지났으면 마지막 봉 몇 개만 다시 받아 덮어쓴다."""
import threading
import time

from . import ls, paper

INTERVALS = {"1": 1, "5": 5, "15": 15, "60": 60}  # 화면의 봉 단위 → 분
FIRST = 1000     # 처음에 받는 봉 수
FRESH = 2.0      # 이보다 오래된 마지막 봉은 다시 받는다(초). o3103은 초당 1회까지
_bars = {}       # 분 → {봉 시작 시각: 봉}
_at = {}
_quote = {"at": 0, "data": None}
_lock = threading.Lock()


def _symbol():
    if not paper.status["symbol"]:
        paper.status["symbol"] = ls.front_month()["Symbol"]
    return paper.status["symbol"]


def _bar(r):
    return {"time": r["t"], "open": float(r["open"]), "high": float(r["high"]), "low": float(r["low"]),
            "close": float(r["close"]), "volume": int(r["volume"])}


def bars(key):
    """그 봉 단위의 최근 봉들(오래된 것부터)."""
    minutes = INTERVALS[key]
    with _lock:
        if minutes not in _bars:
            _bars[minutes] = {r["t"]: _bar(r) for r in ls.minute_bars(_symbol(), minutes, FIRST)}
            _at[minutes] = time.time()
        elif time.time() - _at[minutes] > FRESH:
            for r in ls.minute_bars(_symbol(), minutes, 3):
                _bars[minutes][r["t"]] = _bar(r)
            _at[minutes] = time.time()
            if len(_bars[minutes]) > FIRST * 2:
                for t in sorted(_bars[minutes])[:-FIRST]:
                    del _bars[minutes][t]
        return [_bars[minutes][t] for t in sorted(_bars[minutes])]


def quote():
    """현재가 정보(2초마다 새로)."""
    with _lock:
        if time.time() - _quote["at"] > FRESH:
            q = ls.quote(_symbol())
            _quote.update(at=time.time(), data={
                "symbol": q["Symbol"], "name": q["SymbolNm"], "price": float(q["TrdP"]), "diff": float(q["YdiffP"]),
                "prev": float(q["TrdP"]) - float(q["YdiffP"]), "open": float(q["OpenP"]), "high": float(q["HighP"]), "low": float(q["LowP"]),
                "volume": int(q["TotQ"]), "expiry": q["MtrtDt"], "tick": float(q["UntPrc"]), "tick_value": float(q["MnChgAmt"]),
                "margin": float(q["OpngMgn"]), "time": q["KorDate"] + q["RcvTm"]})
        return _quote["data"]


def get_chart(key):
    return {"interval": key, "bars": bars(key), "quote": quote()}
