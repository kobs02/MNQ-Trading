"""LS 모의계좌 자동 주문: 모의 운용 후보 하나(config.LIVE["candidate"])의 가상 계좌 계약 수를 목표로, 실제 포지션과의 차이만 시장가로 낸다.

모의 운용(mnq/paper.py)이 두뇌, 이 파일이 손이다. 무엇을 살지는 여기서 정하지 않는다.
- 대회 시작(config.LIVE["start"]) 전에는 계산만 하고 주문하지 않는다
- 30초마다: 목표 계약 수와 실제 포지션(CIDBQ01500)을 비교 → 다르면 차이만큼 시장가 주문(CIDBT00100)
- 안전장치: 최대 계약 수, 하루 주문 횟수, CME가 쉬는 시간(뉴욕 17 ~ 18시, 주말)에는 내지 않음,
  주문을 낸 뒤 CONFIRM초 동안은 체결 확인만 하고 다시 내지 않음(애매한 응답에 다시 보내면 두 번 접수될 수 있다),
  미체결 주문이 남아 있으면 새로 내지 않음. 켜고 끄기는 store/live.json의 enabled(작업 표시줄 아이콘 메뉴)
- 기록: store/live.json(주문, 판단, 오류)
"""
import json
import logging
import threading
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from . import config, console, ls, paper

log = logging.getLogger(__name__)
NY = ZoneInfo("America/New_York")
PATH = config.STORE / "live.json"
CONFIRM = 90       # 주문을 낸 뒤 이만큼(초)은 새로 내지 않고 체결을 확인한다
EVERY = 30
_lock = threading.Lock()
_state = None
status = {"checked": None, "target": None, "actual": None, "error": None, "note": None}


def _load():
    global _state
    if _state is None:
        try:
            _state = json.loads(PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _state = {}
        _state.setdefault("enabled", True)
        _state.setdefault("orders", [])
    return _state


def _save():
    config.STORE.mkdir(exist_ok=True)
    tmp = PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(_state, ensure_ascii=False), encoding="utf-8")
    tmp.replace(PATH)


def started(now=None):
    return (now or time.time()) >= config.LIVE["start"]


def market_open(ny):
    """CME Globex(MNQ)가 열려 있는가: 일요일 18시 ~ 금요일 17시(뉴욕), 매일 17 ~ 18시는 쉰다. 휴장일은 따지지 않는다."""
    wd, h = ny.weekday(), ny.hour
    if wd == 5 or (wd == 6 and h < 18) or (wd == 4 and h >= 17):
        return False
    return h != 17


def set_enabled(on):
    with _lock:
        _load()["enabled"] = bool(on)
        _save()
    log.warning("LS 모의계좌 자동 주문을 %s", "켰습니다" if on else "껐습니다(포지션은 그대로 둡니다)")


def enabled():
    with _lock:
        return _load()["enabled"]


def _target():
    """따라가는 후보의 가상 계좌 계약 수."""
    acct = paper._load().get(config.LIVE["candidate"]) or {}
    return int(acct.get("contracts", 0))


def tick():
    now = time.time()
    ny = datetime.now(NY)
    symbol = paper.status["symbol"]
    if not symbol:
        return
    target = _target()
    net, _ = ls.positions()
    actual = int(net.get(symbol, 0))
    others = {s: q for s, q in net.items() if s != symbol and q}
    status.update(checked=int(now), target=target, actual=actual, error=None, others=others)
    with _lock:
        state = _load()
        today = ny.strftime("%Y-%m-%d")
        sent_today = sum(1 for o in state["orders"] if o.get("day") == today and o.get("ok"))
        last = state["orders"][-1] if state["orders"] else None
        why = None
        if not state["enabled"]:
            why = "자동 주문이 꺼져 있습니다"
        elif not started(now):
            why = f"대회 시작({datetime.fromtimestamp(config.LIVE['start']):%m-%d %H:%M}) 전이라 주문하지 않습니다"
        elif target == actual:
            why = "목표와 같습니다"
        elif abs(target) > config.LIVE["max_contracts"]:
            why = f"목표 {target}계약이 최대({config.LIVE['max_contracts']}계약)를 넘어 내지 않습니다"
        elif not market_open(ny):
            why = "CME가 쉬는 시간입니다"
        elif sent_today >= config.LIVE["max_orders_per_day"]:
            why = f"오늘 주문이 {sent_today}건으로 하루 한도에 닿았습니다"
        elif last and now - last["time"] < CONFIRM:
            why = "방금 낸 주문의 체결을 확인하는 중입니다"
        status["note"] = why
        if why:
            return
    # 미체결 주문이 남아 있으면 새로 내지 않는다(조회는 잠금 밖에서)
    pending = [o for o in ls.orders(symbol) if int(o.get("UnercQty") or 0) > 0]
    if pending:
        status["note"] = f"미체결 주문 {len(pending)}건이 있어 기다립니다"
        return
    qty = target - actual
    entry = {"time": int(now), "day": ny.strftime("%Y-%m-%d"), "symbol": symbol, "qty": qty, "from": actual, "to": target,
             "price": paper.status["price"]}
    try:
        entry.update(ok=True, number=ls.market_order(symbol, qty))
        console.success(log, "LS 모의계좌 주문: %s %d계약 (%d → %d, 목표 = %s 가상 계좌) 주문번호 %s", "매수" if qty > 0 else "매도",
                        abs(qty), actual, target, config.LIVE["candidate"], entry["number"])
    except Exception as e:
        entry.update(ok=False, error=str(e))
        log.error("LS 모의계좌 주문 실패: %s %d계약 · %s", "매수" if qty > 0 else "매도", abs(qty), e)
    with _lock:
        state = _load()
        state["orders"].append(entry)
        del state["orders"][:-1000]
        _save()


def run():
    def loop():
        waiting = None
        while True:
            try:
                tick()
                if status["note"] != waiting and status["note"] and "대회 시작" in status["note"]:
                    log.info("LS 모의계좌: %s (목표 %s계약, 지금 %s계약)", status["note"], status["target"], status["actual"])
                waiting = status["note"]
            except Exception as e:
                if status["error"] is None:
                    log.warning("LS 모의계좌 자동 주문 확인 실패: %s", e)
                status["error"] = f"{datetime.now():%m-%d %H:%M} {e}"
            time.sleep(EVERY)
    threading.Thread(target=loop, name="live", daemon=True).start()


def get_live():
    """화면용: 연결 상태, 목표와 실제, 최근 주문."""
    with _lock:
        state = json.loads(json.dumps(_load()))
    return {"enabled": state["enabled"], "started": started(), "start": config.LIVE["start"], "candidate": config.LIVE["candidate"],
            "max_contracts": config.LIVE["max_contracts"], **status, "orders": state["orders"][::-1][:20]}


if __name__ == "__main__":
    # 점검: LS를 가짜로 바꿔 끼우고 판단만 확인한다(실제 주문 없음, store/live.json도 건드리지 않음)
    import tempfile
    from pathlib import Path
    PATH = Path(tempfile.mkdtemp()) / "live.json"
    _state = None
    sent, book = [], {"pos": 0, "pending": []}
    ls.positions = lambda: ({"MNQZ26": book["pos"]}, [])
    ls.orders = lambda symbol: book["pending"]
    ls.market_order = lambda symbol, qty: sent.append(qty) or "0000000001"
    paper.status["symbol"], paper.status["price"] = "MNQZ26", 30000.0
    paper._load = lambda: {config.LIVE["candidate"]: {"contracts": 2}}
    market_open = lambda ny: True
    config.LIVE["start"] = 0
    tick()
    assert sent == [2], sent                      # 0 → 2: 매수 2
    tick()
    assert sent == [2] and "확인" in status["note"]  # 방금 냈으니 기다린다
    _state["orders"][-1]["time"] -= CONFIRM + 1
    book["pos"] = 2
    tick()
    assert sent == [2] and status["note"] == "목표와 같습니다"
    paper._load = lambda: {config.LIVE["candidate"]: {"contracts": -1}}
    book["pending"] = [{"UnercQty": "1"}]
    tick()
    assert sent == [2] and "미체결" in status["note"]  # 미체결이 있으면 기다린다
    book["pending"] = []
    tick()
    assert sent == [2, -3], sent                  # 롱 2 → 숏 1: 매도 3
    _state["orders"][-1]["time"] -= CONFIRM + 1
    paper._load = lambda: {config.LIVE["candidate"]: {"contracts": 9}}
    tick()
    assert sent == [2, -3] and "최대" in status["note"]
    paper._load = lambda: {config.LIVE["candidate"]: {"contracts": 0}}
    _state["enabled"] = False
    tick()
    assert sent == [2, -3] and "꺼져" in status["note"]
    print("live 점검 통과")
