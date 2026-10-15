"""모의 운용: 후보마다 $100,000짜리 가상 계좌로 MNQ 근월물을 실제 가격(LS증권 시세)으로 따라간다. 주문은 내지 않는다.

- 하루 한 번(뉴욕 16:10 뒤) 그날 나스닥100 종가로 노출을 정하고, 그 순간의 MNQ 가격에 정수 계약으로 맞춘다
- 1분마다 MNQ 가격으로 계좌를 다시 계산하고, 시간이 바뀔 때 기록을 남긴다
- 계좌는 store/paper.json에 남아 프로그램을 껐다 켜도 이어진다. 꺼져 있던 동안의 결정은 되짚지 않는다
"""
import json
import logging
import threading
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from . import config, ls, strategies

log = logging.getLogger(__name__)
NY = ZoneInfo("America/New_York")
PATH = config.STORE / "paper.json"
_lock = threading.Lock()
_state = None
_cache = {"backtests": None, "at": 0}
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


def _decide(key, acct, px, price, now):
    expo = float(strategies.exposure(key, px).iloc[-1]) * config.CANDIDATES[key]["lever"]
    want = _contracts(expo, acct["equity"], price)
    held = acct["contracts"]
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
    acct["decided"] = px.index[-1].strftime("%Y-%m-%d")
    acct["trades"].append({"time": int(now), "date": acct["decided"], "expo": round(expo, 3), "from": want - trade, "to": want,
                           "price": price, "fee": fee, "close": float(px.iloc[-1])})
    del acct["trades"][:-500]


def tick():
    """감시 스레드가 1분마다 부른다."""
    now = time.time()
    try:
        symbol = status["symbol"] or ls.front_month()["Symbol"]
        price = float(ls.quote(symbol)["TrdP"])
    except Exception as e:
        status["error"] = f"{datetime.now():%m-%d %H:%M} MNQ 시세를 받지 못했습니다: {e}"
        return
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
            if due and acct["decided"] != ny.strftime("%Y-%m-%d"):
                if px is None:
                    px = strategies.history()
                if px.index[-1].strftime("%Y-%m-%d") == ny.strftime("%Y-%m-%d"):  # 오늘 종가가 나왔을 때만(휴장일 제외)
                    _decide(key, acct, px, price, now)
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
                      close=[px.index[-1].strftime("%Y-%m-%d"), float(px.iloc[-1])])
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
        item = {"key": key, "name": cand["name"], "note": cand["note"], "lever": cand["lever"], "backtest": bt["backtests"][key],
                "signal": {"expo": bt["signal"][key], "contracts": _contracts(bt["signal"][key], acct["equity"] if acct else config.CAPITAL, price) if price else None}}
        if acct:
            values = [config.CAPITAL] + [v for _, v in acct["marks"]]
            peak, mdd = values[0], 0.0
            for v in values:
                peak = max(peak, v)
                mdd = min(mdd, v / peak - 1)
            item.update(started=acct["started"], equity=acct["equity"], contracts=acct["contracts"], drawdown=mdd,
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
        out.append({"key": key, "name": cand["name"], "equity": equity, "contracts": acct.get("contracts", 0), "entry": acct.get("entry"),
                    "started": acct.get("started"), "trades": acct.get("trades", [])[::-1][:50],
                    "signal": {"expo": bt["signal"][key], "contracts": _contracts(bt["signal"][key], equity, price) if price else None}})
    return {"candidates": out, "capital": config.CAPITAL, "multiplier": config.MULTIPLIER, "fee": config.FEE_PER_CONTRACT,
            "close": bt["close"], "next_decision": _next_decision(), "error": status["error"]}


def _next_decision():
    """다음 결정 시각(초): 평일 뉴욕 16:10. 휴장일은 따지지 않는다."""
    ny = datetime.now(NY)
    at = ny.replace(hour=config.DECIDE_AFTER[0], minute=config.DECIDE_AFTER[1], second=0, microsecond=0)
    while at <= ny or at.weekday() >= 5:
        at += timedelta(days=1)
    return int(at.timestamp())
