"""LS 모의계좌 자동 주문: 대회 매매법으로 고른 후보 하나(candidate(), 기본은 config.LIVE["candidate"])의 가상 계좌 계약 수를 목표로, 실제 포지션과의 차이만 시장가로 낸다.

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
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from . import config, console, ls, paper, report

log = logging.getLogger(__name__)
NY = ZoneInfo("America/New_York")
KST = ZoneInfo("Asia/Seoul")
PATH = config.STORE / "live.json"
CONFIRM = 90       # 주문을 낸 뒤 이만큼(초)은 새로 내지 않고 체결을 확인한다
EVERY = 30
_lock = threading.Lock()
_last_order = [0.0]  # 마지막 주문 시각(_watch_pending이 그 뒤 1분 동안 미체결을 자주 본다)
_busy = threading.Lock()  # 자동 주문(tick)과 화면의 직접 주문이 동시에 나가지 않게
_acted = [0]  # 버튼 주문(정리·시장가·지정가·정정·취소) 횟수: tick이 그 전에 읽은 포지션으로 주문하지 않게
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


@contextmanager
def _button(stop_auto=False):
    """화면 버튼의 주문: tick은 LS 조회를 잠금 밖에서 하므로 버튼은 tick의 주문 한 건만 기다린다.
    stop_auto면 자동 주문을 먼저 끈다(켜 두면 30초 뒤 목표 계약 수로 되돌린다)."""
    with _busy:
        try:
            if stop_auto:
                with _lock:
                    _load().update(enabled=False, flatten=False)
                    _save()
            yield
        finally:
            _acted[0] += 1


def _ready(symbol):
    """버튼 주문을 낼 수 있는가: 종목을 알고, 대회가 시작했고, CME가 열려 있을 때."""
    if not symbol:
        raise ValueError("아직 종목을 모릅니다(시세 연결 전)")
    if not started():
        raise ValueError("대회 시작 전에는 주문하지 않습니다")
    if not market_open(datetime.now(NY)):
        raise ValueError("CME가 쉬는 시간입니다")


def _read(symbol):
    """미체결 행과 포지션({종목: 순수량})을 같이 묻는다: TR이 달라 초당 한도를 따로 세므로 차례로 묻는 것보다 주문 전 기다림이 반."""
    with ThreadPoolExecutor(2) as pool:
        rows, net = pool.submit(_pending, symbol), pool.submit(ls.positions)
        return rows.result(), net.result()[0]


def _entry(symbol, qty, actual, price, **flags):
    ny = datetime.now(NY)
    return {"time": int(time.time()), "day": ny.strftime("%Y-%m-%d"), "symbol": symbol, "qty": qty, "from": actual, "to": actual + qty,
            "price": price, **flags}


def _send(entry, send, what, done=""):
    """주문을 보내고 결과를 기록에 남긴다. 주문 TR은 다시 보내지 않는다(두 번 접수될 수 있다)."""
    try:
        entry.update(ok=True, number=send())
        console.success(log, "LS 모의계좌 %s 주문번호 %s%s", what, entry["number"], done)
    except Exception as e:
        entry.update(ok=False, error=str(e))
        log.error("LS 모의계좌 %s 실패: %s", what, e)
    _record(entry)
    return entry


def _side(qty):
    return "매수" if qty > 0 else "매도"


def set_enabled(on):
    with _lock:
        _load().update(enabled=bool(on), flatten=False)
        _save()
    log.warning("LS 모의계좌 자동 주문을 %s", "켰습니다" if on else "껐습니다(포지션은 그대로 둡니다)")


def flatten():
    """비상 정지·화면의 '포지션 정리': 자동 주문을 끄고 미체결을 취소한 뒤 지금 포지션을 시장가로 바로 닫는다.
    CME가 쉬는 시간(또는 대회 전)이면 목표를 0계약으로 걸어 두고, 열린 뒤 30초 확인이 닫고 0이 확인되면 자동 주문을 끈다."""
    symbol = paper.status["symbol"]
    if symbol and started() and market_open(datetime.now(NY)):
        with _button(stop_auto=True):
            rows, net = _read(symbol)
            actual = int(net.get(symbol, 0))
            for o in rows:  # 지정가가 남아 있으면 닫은 뒤에 체결돼 반대 포지션이 생길 수 있다
                ls.cancel(symbol, o["OvrsFutsOrdNo"])
                log.warning("LS 모의계좌: 정리 전에 미체결 주문 %s를 취소했습니다", o["OvrsFutsOrdNo"])
            old = [o for part in _old["rows"].values() for o in part]
            for o in old:  # 이미 끝난 주문이면 취소가 거절될 수 있다: 그래도 포지션은 닫는다
                try:
                    ls.cancel(symbol, o["OvrsFutsOrdNo"], o["OrdDt"])
                    log.warning("LS 모의계좌: 정리 전에 지난 거래일(%s) 미체결 주문 %s를 취소했습니다", o["OrdDt"], o["OvrsFutsOrdNo"])
                except Exception as e:
                    log.error("LS 모의계좌: 지난 거래일(%s) 미체결 주문 %s를 취소하지 못했습니다: %s", o["OrdDt"], o["OvrsFutsOrdNo"], e)
            if rows or old:  # 취소 전에 체결됐을 수 있으니 취소한 경우에만 다시 읽는다
                _pending(symbol)
                actual = int(ls.positions()[0].get(symbol, 0))
            if actual:
                _send(_entry(symbol, -actual, actual, paper.status["price"], manual=True, flatten=True), lambda: ls.market_order(symbol, -actual),
                      f"포지션 정리: {_side(-actual)} {abs(actual)}계약", " · 자동 주문은 껐습니다")
        return
    with _lock:
        _load().update(enabled=True, flatten=True, flatten_at=int(time.time()))
        _save()
    log.warning("LS 모의계좌: 전부 정리하고 멈춤을 눌렀습니다. 0계약이 되면 자동 주문을 끕니다")


def enabled():
    with _lock:
        return _load()["enabled"]


def candidate():
    """대회 매매법: 화면에서 고른 후보(store/live.json). 없거나 지금 후보 목록에 없으면 config.LIVE["candidate"]."""
    key = _load().get("candidate")
    return key if key in config.CANDIDATES and not config.CANDIDATES[key].get("watch") else config.LIVE["candidate"]


def set_candidate(key):
    """대회 매매법을 바꾼다. 자동 주문이 켜져 있으면 다음 확인(30초 안)에 새 후보의 목표 계약 수로 맞춘다."""
    if key not in config.CANDIDATES or config.CANDIDATES[key].get("watch"):
        raise ValueError(f"대회 매매법으로 고를 수 없는 후보입니다: {key}")
    with _lock:
        _load()["candidate"] = key
        _save()
    log.warning("LS 모의계좌: 대회 매매법을 %s(으)로 바꿨습니다", config.CANDIDATES[key]["name"].split(" · ")[0])


def _target():
    """따라가는 후보의 가상 계좌 계약 수."""
    acct = paper._load().get(candidate()) or {}
    return int(acct.get("contracts", 0))


def _record(entry):
    with _lock:
        state = _load()
        state["orders"].append(entry)
        del state["orders"][:-1000]
        _save()
    _account["at"] = _history["at"] = 0  # 다음 화면 조회가 5·10초 저장본이 아니라 LS의 새 값을 받게
    _last_order[0] = time.time()


def manual(qty=None, flip=False, reduce=None):
    """화면의 매수(+)·매도(-) 버튼. 자동 주문을 먼저 끈다(켜 두면 30초 뒤 목표 계약 수로 되돌린다).
    flip이면 주문 직전에 읽은 실제 포지션을 닫고 반대로 qty계약(화면에서 고른 비중, 없으면 같은 계약 수) 든다:
    롱 5에 qty 3이면 매도 8 한 건 → 숏 3(정리와 진입을 시장가 한 건으로 해 사이에 가격이 움직이지 않게).
    reduce = n이면 분할 정리: 실제 포지션에서 n계약만 시장가로 닫는다."""
    if reduce is not None:
        if type(reduce) is not int or flip or qty is not None:
            raise ValueError("정리할 계약 수가 올바르지 않습니다")
    elif (not flip or qty is not None) and (type(qty) is not int or not qty or (flip and qty < 0)):
        raise ValueError("계약 수가 올바르지 않습니다")
    symbol = paper.status["symbol"]
    _ready(symbol)
    with _button(stop_auto=True):
        rows, net = _read(symbol)
        if rows:
            raise ValueError("미체결 주문이 있습니다")
        actual = int(net.get(symbol, 0))
        if flip:
            if not actual:
                raise ValueError("뒤집을 포지션이 없습니다")
            qty = -actual - (1 if actual > 0 else -1) * (qty or abs(actual))
        if reduce is not None:
            qty = _reduce(actual, reduce)
        if abs(actual + qty) > config.LIVE["max_contracts"]:
            raise ValueError(f"주문 뒤 {actual + qty}계약이 최대({config.LIVE['max_contracts']}계약)를 넘습니다")
        return _send(_entry(symbol, qty, actual, paper.status["price"], manual=True), lambda: ls.market_order(symbol, qty),
                     f"직접 주문: {_side(qty)} {abs(qty)}계약 ({actual} → {actual + qty})", " · 자동 주문은 껐습니다")


# 미체결만 묻기(OrdStatCode 2)가 정말 미체결을 다 주는지는 미체결이 있을 때 전체 내역과 맞춰 봐야 안다(10-19엔 미체결이 없어 못 봄).
# 확인 전이나 어긋난 적이 있으면 전체 내역(오늘 68건이면 4쪽, 3 ~ 8초)을 읽는다. 결과는 store/ls_unfilled.json에 남긴다
UNFILLED = config.STORE / "ls_unfilled.json"
_unfilled = {"ok": None, "checked": 0}


def _unfilled_ok():
    if _unfilled["ok"] is None:
        try:
            _unfilled["ok"] = json.loads(UNFILLED.read_text(encoding="utf-8"))["ok"]
        except Exception:
            _unfilled["ok"] = False
    return _unfilled["ok"]


def _open_rows(symbol):
    """지금 종목의 미체결 주문 행(UnercQty > 0)."""
    return [o for o in ls.orders(symbol, unfilled=_unfilled_ok()) if int(o.get("UnercQty") or 0) > 0]


# 주문내역은 거래일별이라 거래일이 넘어가면 남은 지정가가 지금 거래일 조회에서 빠진다(11-02: 10-30의 8313이 화면에서 사라졌다).
# 유효기간이 끝나도 LS에 미체결로 남는지 몰라, 보여 주고 취소만 할 수 있게 하고 자동 주문·새 주문은 막지 않는다
OLD_DAYS = 5
_old = {"today": None, "rows": {}}  # 거래일 → 미체결 행


def _refresh_old(symbol):
    """지난 거래일의 미체결: 거래일이 바뀌면 최근 OLD_DAYS일을 훑고, 그 뒤로는 남아 있는 날만 다시 묻는다(LS 조회를 아끼려고)."""
    today = ls.order_day()
    days = set(_old["rows"])
    if _old["today"] != today:
        base = datetime.strptime(today, "%Y%m%d")
        days |= {(base - timedelta(days=i)).strftime("%Y%m%d") for i in range(1, OLD_DAYS + 1)}
    rows = {day: [o for o in ls.orders(symbol, day=day, unfilled=_unfilled_ok()) if int(o.get("UnercQty") or 0) > 0] for day in days - {today}}
    _old.update(today=today, rows={day: r for day, r in rows.items() if r})


def _verify_unfilled(symbol, now):
    """미체결만 묻기를 전체 내역과 맞춰 본다: 확인 전에는 30초 확인마다, 확인 뒤엔 10분마다(주문 잠금 밖에서, 주문을 막지 않게).
    전체 내역의 미체결이 미체결만 묻기(앞·뒤 두 번)에 다 있으면 믿고, 하나라도 빠지면 다시 전체 내역으로 돌아간다."""
    ok = _unfilled_ok()
    if ok and now - _unfilled["checked"] < 600:
        return
    _unfilled["checked"] = now
    fast = {o.get("OvrsFutsOrdNo") for o in ls.orders(symbol, unfilled=True) if int(o.get("UnercQty") or 0) > 0}
    full = {o.get("OvrsFutsOrdNo") for o in ls.orders(symbol) if int(o.get("UnercQty") or 0) > 0}
    if not full - fast:
        if full and not ok:
            log.info("LS 미체결만 묻기가 전체 내역과 맞습니다(%d건): 이제 주문 전 확인을 빠른 쪽으로 합니다", len(full))
            _unfilled["ok"] = True
        else:
            return
    else:
        fast |= {o.get("OvrsFutsOrdNo") for o in ls.orders(symbol, unfilled=True) if int(o.get("UnercQty") or 0) > 0}  # 그 사이 새로 낸 주문일 수 있다
        if not full - fast:
            return
        log.error("LS 미체결만 묻기에 미체결 %s가 빠졌습니다: 전체 내역으로 돌아갑니다", sorted(full - fast))
        _unfilled["ok"] = False
    UNFILLED.write_text(json.dumps({"ok": _unfilled["ok"], "time": int(now)}), encoding="utf-8")


# LS 주문내역은 방금 낸·정정한·취소한 주문을 몇 초 늦게 보여 준다. 그동안 화면이 옛 상태로 되돌아가지 않게 HOLD초 동안은 우리가 아는 쪽을 믿는다:
# 주문번호 → (언제까지, 보일 미체결 또는 None = 없어진 주문)
HOLD = 10
_hold = {}


def _hold_pending(number, entry=None):
    _hold[number] = (time.time() + HOLD, entry)


def _pending(symbol):
    """미체결 주문(LS 주문내역에서 UnercQty > 0)을 읽고 화면용으로 status["pending"]에 줄여 둔다(_hold를 덧씌워서). LS 행을 그대로 돌려준다."""
    rows = _open_rows(symbol)
    view = lambda o, number: {"number": number, "qty": (1 if o.get("BnsTpCode") == "2" else -1) * int(o["UnercQty"]),
                              "price": float(o.get("OvrsDrvtOrdPrc") or 0), "limit": o.get("AbrdFutsOrdPtnCode") == "2"}
    shown = {o.get("OvrsFutsOrdNo"): view(o, o.get("OvrsFutsOrdNo")) for o in rows}
    for day, old in _old["rows"].items():
        for o in old:
            number = f"{day}-{o['OvrsFutsOrdNo']}"  # 번호는 거래일마다 새로 매겨져 겹칠 수 있다
            shown[number] = {**view(o, number), "day": day}
    now = time.time()
    for number, (until, entry) in list(_hold.items()):
        if now > until:
            _hold.pop(number, None)
        elif entry is None:
            shown.pop(number, None)
        else:
            shown.setdefault(number, entry)
    status["pending"] = list(shown.values())
    return rows


def _watch_pending():
    """미체결이 있거나 방금 주문했으면 2초마다 LS 미체결만 묻기로 화면의 미체결을 새로 한다(30초 확인을 기다리면 체결된 선이 남아 있었다).
    체결·취소로 미체결이 바뀌면 계좌·주문 기록 저장본도 바로 새로 받게 한다. 미체결만 묻기를 아직 못 믿으면(전체 내역은 3 ~ 8초) 하지 않는다."""
    while True:
        time.sleep(2)
        symbol = paper.status["symbol"]
        if not symbol or not _unfilled_ok() or not (status.get("pending") or time.time() - _last_order[0] < 60):
            continue
        try:
            before = {p["number"] for p in status.get("pending") or []}
            _pending(symbol)
            if before != {p["number"] for p in status["pending"]}:
                _account["at"] = _history["at"] = 0
        except Exception as e:
            log.debug("미체결 새로 묻기 실패: %s", e)


def _reduce(actual, n):
    """분할 정리: 지금 포지션에서 n계약만 닫는 주문 수량. 주문 직전의 실제 포지션으로 계산해 0을 넘어 뒤집히지 않게 한다."""
    if not actual:
        raise ValueError("정리할 포지션이 없습니다")
    if not 1 <= n <= abs(actual):
        raise ValueError(f"정리할 계약 수({n})가 지금 포지션({abs(actual)}계약)과 맞지 않습니다")
    return -n if actual > 0 else n


def limit_close(price, qty=None, reduce=None):
    """화면의 '지정가로 정리'(qty 없음): 지금 포지션 전부(reduce = n이면 n계약만)를 그 가격의 지정가 한 건으로 닫는다.
    '지정가 매수·매도'(qty = +n·-n): 그 가격에 n계약 지정가 진입. 자동 주문은 먼저 끈다
    (켜 두면 미체결 동안은 기다리다가 체결 뒤 목표 계약 수로 다시 들기 때문). 미체결은 cancel_pending으로 취소한다."""
    if qty is not None and (type(qty) is not int or not qty):
        raise ValueError("계약 수가 올바르지 않습니다")
    if reduce is not None and (type(reduce) is not int or qty is not None):
        raise ValueError("정리할 계약 수가 올바르지 않습니다")
    symbol = _check_price(price)
    _ready(symbol)
    with _button(stop_auto=True):
        rows, net = _read(symbol)
        if rows:
            raise ValueError("미체결 주문이 있습니다(먼저 취소하세요)")
        actual = int(net.get(symbol, 0))
        close = qty is None
        if close and not actual:
            raise ValueError("정리할 포지션이 없습니다")
        qty = (-actual if reduce is None else _reduce(actual, reduce)) if close else qty
        if abs(actual + qty) > config.LIVE["max_contracts"]:
            raise ValueError(f"체결 뒤 {actual + qty}계약이 최대({config.LIVE['max_contracts']}계약)를 넘습니다")
        entry = _send(_entry(symbol, qty, actual, price, manual=True, limit=True), lambda: ls.limit_order(symbol, qty, price),
                      f"지정가 {'정리' if close else '진입'}: {_side(qty)} {abs(qty)}계약 @ {price:.2f}", " · 자동 주문은 껐습니다")
        # 위에서 미체결이 없음을 봤으니 지금 미체결은 방금 낸 것뿐이다: LS를 다시 묻지 않고 바로 보여 준다(LS 내역에 늦게 잡혀도 차트에 선이 바로 생긴다).
        # 바로 체결됐으면 다음 30초 확인이 LS 내역으로 지운다
        status["pending"] = [{"number": entry["number"], "qty": qty, "price": price, "limit": True}] if entry["ok"] else []
        if entry["ok"]:
            _hold_pending(entry["number"], status["pending"][0])
    return entry


def _check_price(price):
    """지정가로 쓸 수 있는 가격인가(0.25 단위, 지금 가격에서 5% 안). 지금 종목을 돌려준다."""
    symbol, now_px = paper.status["symbol"], paper.status["price"]
    if type(price) not in (int, float) or price <= 0 or round(price * 4) != price * 4:
        raise ValueError("가격은 0.25 단위의 양수여야 합니다")
    if not symbol or not now_px:
        raise ValueError("아직 시세를 모릅니다")
    if abs(price / now_px - 1) > 0.05:  # 자릿수를 잘못 친 가격 막기
        raise ValueError(f"지금 가격({now_px:,.2f})에서 5% 넘게 떨어진 가격입니다")
    return symbol


def modify_limit(number, price):
    """화면에서 지정가 선을 끌어 옮기면: 그 미체결 지정가 주문의 가격을 정정한다. 정정되면 주문번호가 바뀌므로 기록도 새 번호로 고친다."""
    if "-" in number:
        raise ValueError("지난 거래일 주문은 정정할 수 없습니다(취소만 됩니다)")
    symbol = _check_price(price)
    with _button():
        row = next((o for o in _open_rows(symbol) if o.get("OvrsFutsOrdNo") == number), None)
        held = _hold.get(number)
        if row and row.get("AbrdFutsOrdPtnCode") == "2":
            qty = (1 if row.get("BnsTpCode") == "2" else -1) * int(row["UnercQty"])
        elif not row and held and time.time() < held[0] and held[1] and held[1].get("limit"):
            # 방금 낸·정정한 지정가는 LS 주문내역에 몇 초 늦게 잡힌다: 그사이 다시 끌면 아는 수량으로 보낸다(이미 체결됐으면 LS가 거절한다)
            qty, row = held[1]["qty"], {"OvrsDrvtOrdPrc": held[1]["price"]}
        else:
            raise ValueError("정정할 미체결 지정가 주문이 없습니다(이미 체결됐거나 취소됐을 수 있습니다)")
        new = ls.modify(symbol, number, qty, price)
        console.success(log, "LS 모의계좌 지정가 정정: %s %d계약 %s → %.2f (주문번호 %s → %s)", _side(qty), abs(qty),
                        row.get("OvrsDrvtOrdPrc"), price, number, new)
        with _lock:
            # 주문번호는 거래일마다 새로 매겨진다: 가장 최근 것 하나만 고친다(10-26 정정이 10-23의 같은 번호 기록까지 바꿔 대회 수익률이 틀렸다)
            for o in reversed(_load()["orders"]):
                if o.get("number") and int(o["number"]) == int(number):  # LS는 0000000778, 기록은 778로 올 때가 있다
                    o.update(number=new, price=price)
                    break
            _save()
        # LS 주문내역에 새 번호가 늦게 잡히므로 다시 묻지 않고 바로 바꿔 둔다(다시 물으면 옛 번호·옛 가격이 와서 화면 선이 되돌아갔다)
        status["pending"] = [{**p, "number": new, "price": price} if p.get("number") == number else p for p in status.get("pending", [])]
        _hold_pending(number)
        _hold_pending(new, next((p for p in status["pending"] if p["number"] == new), None) or {"number": new, "qty": qty, "price": price, "limit": True})
    return {"number": new, "price": price}


def cancel_pending(number=None):
    """화면의 '미체결 취소': 지금 종목의 미체결 주문을 모두(number를 주면 그 주문만, 차트 이름표의 X) 취소한다. 취소한 건수."""
    symbol = paper.status["symbol"]
    if not symbol:
        raise ValueError("아직 종목을 모릅니다")
    with _button():
        _pending(symbol)
        # 방금 낸 주문은 LS 내역에 아직 없을 수 있으므로 화면에 보이는 미체결(_hold 포함)을 취소한다
        numbers = [p["number"] for p in status["pending"] if number is None or p["number"] == number]
        if number is not None and not numbers:
            raise ValueError("취소할 미체결 주문이 없습니다(이미 체결됐거나 취소됐을 수 있습니다)")
        done, errors = 0, []
        for n in numbers:
            try:
                day, _, no = n.rpartition("-")  # 지난 거래일 주문은 '거래일-번호'
                ls.cancel(symbol, no, day or None)
                _hold_pending(n)
                if day:
                    _old["rows"][day] = [o for o in _old["rows"].get(day, []) if o["OvrsFutsOrdNo"] != no]
                done += 1
                log.warning("LS 모의계좌: 미체결 주문 %s를 취소했습니다", n)
            except Exception as e:  # 그 사이 체결된 주문 등: 나머지는 계속 취소한다
                errors.append(f"{n}: {e}")
        _pending(symbol)
    _account["at"] = 0
    if errors and not done:
        raise ValueError("취소하지 못했습니다(이미 체결됐을 수 있습니다) " + "; ".join(errors))
    return done


def week_fills(orders, now=None):
    """이번 주(대회가 쓰는 한국 시각, 월 ~ 일)에 접수된 주문 수."""
    week = lambda t: datetime.fromtimestamp(t, KST).isocalendar()[:2]
    this = week(now or time.time())
    return sum(1 for o in orders if o.get("ok") and week(o["time"]) == this)


def filler_due(orders, ny, now=None):
    """주간 최소 거래(대회 규정 '주당 2회 이상', '회'가 왕복일 수도 있어 체결 MIN_WEEKLY건)를 채워야 하는가: 금요일 뉴욕 14 ~ 17시에 모자랄 때."""
    return ny.weekday() == 4 and 14 <= ny.hour < 17 and week_fills(orders, now) < config.LIVE["min_weekly"]


def tick():
    if paper.status["symbol"]:
        try:
            _verify_unfilled(paper.status["symbol"], time.time())
            _refresh_old(paper.status["symbol"])
        except Exception as e:
            log.warning("LS 미체결 점검 실패: %s", e)
    symbol = paper.status["symbol"]
    if not symbol:
        return
    seen = _acted[0]
    # LS 조회는 잠금 밖에서 같이: 버튼 주문이 자동 확인의 조회(1 ~ 2초)를 기다리지 않게.
    # 미체결은 자동 주문이 꺼져 있어도 화면에 (지정가 정리 등을) 보이게 늘 읽는다
    pending, net = _read(symbol)
    with _busy:
        if _acted[0] != seen:  # 읽는 사이 버튼 주문이 나갔다: 읽은 포지션이 낡았으니 다음 확인에 맞춘다
            return
        _tick(symbol, net, pending)


def _tick(symbol, net, pending):
    now = time.time()
    ny = datetime.now(NY)
    target = _target()
    actual = int(net.get(symbol, 0))
    others = {s: q for s, q in net.items() if s != symbol and q}
    status.update(checked=int(now), target=target, actual=actual, error=None, others=others)
    with _lock:
        state = _load()
        today = ny.strftime("%Y-%m-%d")
        sent_today = sum(1 for o in state["orders"] if o.get("day") == today and o.get("ok"))
        last = state["orders"][-1] if state["orders"] else None
        why = None
        status["week"] = week_fills(state["orders"], now)
        if state.get("flatten"):
            target = status["target"] = 0
            if actual == 0:
                state.update(enabled=False, flatten=False)
                _save()
                console.success(log, "LS 모의계좌: 전부 정리했고 자동 주문을 껐습니다")
        if not state["enabled"]:
            why = "자동 주문이 꺼져 있습니다"
        elif not started(now):
            why = f"대회 시작({datetime.fromtimestamp(config.LIVE['start']):%m-%d %H:%M}) 전이라 주문하지 않습니다"
        elif target == actual and not filler_due(state["orders"], ny, now):
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
    # 미체결 주문이 남아 있으면 새로 내지 않는다(위에서 잠금 밖에서 읽은 것)
    if pending:
        status["note"] = f"미체결 주문 {len(pending)}건이 있어 기다립니다"
        return
    # 목표와 같은데 여기 왔으면 주간 최소 거래 채우기: 0 쪽으로 1계약(없으면 매수 1) 벗어났다가, 다음 확인 때 위 맞추기가 되돌린다(체결 2건)
    filler = target == actual
    qty = (-1 if actual > 0 else 1) if filler else target - actual
    flags = {**({"filler": True} if filler else {}), **({"flatten": True} if state.get("flatten") else {})}
    why = f"주간 최소 거래 채우기, 이번 주 {status['week']}건" if filler else f"목표 = {candidate()} 가상 계좌"
    _send(_entry(symbol, qty, actual, paper.status["price"], **flags), lambda: ls.market_order(symbol, qty),
          f"주문: {_side(qty)} {abs(qty)}계약 ({actual} → {actual + qty}, {why})")


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
    threading.Thread(target=_watch_pending, name="live-pending", daemon=True).start()


_account = {"at": 0, "data": None}


def account():
    """차트 아래 칸: LS 모의계좌의 USD 자산과 미결제 포지션(바이낸스의 포지션 칸처럼). 조회 한도 때문에 5초 동안은 같은 값을 준다."""
    if _account["data"] and time.time() - _account["at"] < 5:
        return _account["data"]
    num = lambda d, k: float(d.get(k) or 0)
    usd = next((r for r in ls.assets()[0] if r.get("CrcyCode") == "USD"), {})
    _, rows = ls.positions()
    deposit, pnl, margin = num(usd, "OvrsFutsDps"), num(usd, "AbrdFutsEvalPnlAmt"), num(usd, "AbrdFutsCsgnMgn")
    # 예탁금(OvrsFutsDps)은 장중에 청산손익·수수료가 반영되지 않는다(10-19 확인: 정리 뒤에도 100,000).
    # 평가예탁총액 = 예탁금 + 청산손익(AbrdFutsLqdtPnlAmt) - 수수료(AbrdFutsCmsnAmt) + 평가손익
    equity = num(usd, "AbrdFutsEvalDpstgTotAmt") or deposit + pnl
    held = sum(int(r["BalQty"]) for r in rows) or 1
    out = []
    for r in rows:
        n = (1 if r["BnsTpCode"] == "2" else -1) * int(r["BalQty"])
        now, p = num(r, "OvrsDrvtNowPrc"), num(r, "AbrdFutsEvalPnlAmt")
        im = margin * abs(n) / held  # 이 포지션 몫의 증거금
        # 청산가(추정): 평가 자산이 증거금까지 줄어드는 가격. LS의 실제 반대매매 기준은 모른다
        liq = now - (equity - margin) / (n * config.MULTIPLIER)
        out.append({"symbol": r["IsuCodeVal"], "name": r.get("IsuNm"), "qty": n, "entry": num(r, "PchsPrc"), "price": now, "pnl": p,
                    "roe": p / im if im else None, "of_capital": p / config.CAPITAL, "margin": im, "fee": num(r, "CsgnCmsn"),
                    "liq": liq if liq > 0 else None})
    # 대회 기준 자산(평가손익 전): 주문 기록의 실현 손익 - 체결 1계약당 $2. LS 자산은 그날 수수료를 늦게 빼서(10-24 확인: 10-23 206계약분 $206이 아직 안 빠짐) 쓰지 않는다
    booked = config.CAPITAL + sum(d[2] for d in report.daily(report.fills(history()["orders"]), config.MULTIPLIER).values())
    _account.update(at=time.time(), data={"contest_cash": booked, "equity": equity, "deposit": deposit, "cash": equity - pnl, "realized": num(usd, "AbrdFutsLqdtPnlAmt"),
                                          "fees": num(usd, "AbrdFutsCmsnAmt"), "pnl": pnl, "margin": margin, "capital": config.CAPITAL,
                                          "ret": equity / config.CAPITAL - 1, "positions": out, "at": int(time.time())})
    return _account["data"]


_history = {"at": 0, "data": None, "busy": False}
HISTORY = config.STORE / "ls_history.json"  # 마지막으로 만든 주문 기록: 서버를 켠 직후에도 기다리지 않고 바로 보여 준다


def history():
    """주문 기록 탭: 만들어 둔 기록을 바로 주고, 10초가 지났으면 뒤에서 LS에 새로 물어 바꿔 둔다(화면이 15초마다 다시 읽는다).
    처음 한 번(저장본도 없을 때)만 다 받을 때까지 기다린다."""
    if not _history["data"]:
        try:
            _history["data"] = json.loads(HISTORY.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return _build_history()
    if time.time() - _history["at"] >= 10 and not _history["busy"]:
        _history["busy"] = True
        threading.Thread(target=_refresh_history, daemon=True).start()
    return _history["data"]


def _refresh_history():
    try:
        _build_history()
    except Exception:
        log.exception("주문 기록을 새로 받지 못했습니다")
    finally:
        _history["busy"] = False


def _build_history():
    """이 프로그램이 낸 주문(store/live.json)에 LS 주문내역의 체결가·체결 수량·상태를 붙이고, LS에만 있는 주문(증권사 앱에서 낸 것)도 넣는다.
    LS 주문내역은 대회 첫날부터 거래일마다 받는다. 지난 거래일은 끝난 날이라 한 번 받으면 store/ls_orders/에 두고 다시 묻지 않는다."""
    with _lock:
        mine = json.loads(json.dumps(_load()["orders"]))
    symbol = paper.status["symbol"]
    rows = {}
    if symbol:
        day, today = datetime.fromtimestamp(config.LIVE["start"], KST).date(), datetime.strptime(ls.order_day(), "%Y%m%d").date()
        while day <= today:  # 지금 거래일만 LS에 다시 묻는다
            for r in _day_orders(symbol, day, done=day < today):
                # LS 주문번호는 거래일마다 새로 매긴다(10-19와 10-23에 둘 다 2229): 번호만으로 찾으면 지난 주문이 오늘 체결을 가져간다
                rows[f"{day:%Y%m%d}", int(r["OvrsFutsOrdNo"])] = r
            day += timedelta(days=1)
    later = {(d, int(r["OvrsFutsOrgOrdNo"])): r for (d, _), r in rows.items() if r.get("TrdTpNm") == "정정"}  # 원래 번호 → 그것을 정정한 주문
    earlier = {k: (k[0], int(r["OvrsFutsOrgOrdNo"])) for k, r in rows.items() if r.get("TrdTpNm") == "정정"}
    num = lambda r, k: float(r.get(k) or 0)

    def chain(n):
        """정정으로 이어진 주문들(처음 ~ 마지막). n = (거래일, 번호). 기록의 번호는 처음 것일 수도 마지막 것일 수도 있다."""
        while n in earlier and earlier[n] in rows:
            n = earlier[n]
        rs = [rows[n]] if n in rows else []
        while rs and int(rs[-1].get("MdfyQty") or 0) and (n[0], int(rs[-1]["OvrsFutsOrdNo"])) in later:
            rs.append(later[n[0], int(rs[-1]["OvrsFutsOrdNo"])])
        return rs

    def filled(rs, ordered):
        """정정 전에 일부 체결된 몫은 앞 번호에 남으므로 체결 수량은 모두 더하고 체결가는 수량 가중 평균(10-22: 14계약 중 5계약이 빠졌었다)."""
        q = sum(int(r.get("ExecQty") or 0) for r in rs)
        last, done = rs[-1], max(rs, key=lambda r: r.get("ExecDttm") or "")
        return {**_exec_time(done, ordered), "exec_price": sum(num(r, "AbrdFutsExecPrc") * int(r.get("ExecQty") or 0) for r in rs) / q if q else 0.0, "exec_qty": q,
                "unfilled": int(last.get("UnercQty") or 0), "canceled": int(last.get("CancQty") or 0), "state": last.get("TpCodeNm")}

    out, seen = [], set()
    for o in mine:
        # shortcut: 낸 시각의 거래일로 찾는다. 거래일을 넘겨 정정한 지정가는 못 찾는데, 그런 일이 생기면 정정 기록에 거래일을 남긴다
        day = ls.order_day(o["time"])
        rs = chain((day, int(o["number"]))) if o.get("number") else []
        seen.update((day, int(r["OvrsFutsOrdNo"])) for r in rs)
        out.append({**o, "kind": "정리" if o.get("flatten") else "지정가" if o.get("limit") else "횟수 채우기" if o.get("filler") else "직접" if o.get("manual") else "자동",
                    **(filled(rs, o["time"]) if rs else {})})
    for n, r in rows.items():  # 이 프로그램 밖에서 낸 주문
        if r.get("TrdTpNm") != "신규" or n in seen:  # seen: 이 프로그램이 낸(정정한) 지정가
            continue
        t = datetime.strptime(r["OrdDt"] + r["OrdTime"][:6], "%Y%m%d%H%M%S").replace(tzinfo=KST)
        if t.hour < 7:  # OrdDt는 CME 거래일(한국 07 ~ 08시에 시작): 그 앞 시각은 다음 날 새벽이다
            t += timedelta(days=1)
        sign = 1 if r.get("BnsTpCode") == "2" else -1
        out.append({"time": int(t.timestamp()), "symbol": r.get("IsuCodeVal"), "qty": sign * int(r.get("OrdQty") or 0), "number": str(n[1]), "ok": True,
                    "kind": "LS 앱", "price": num(r, "OvrsDrvtOrdPrc") or None, **filled(chain(n), int(t.timestamp()))})
    out.sort(key=lambda o: o["time"])
    _history.update(at=time.time(), data={"orders": out, "capital": config.CAPITAL, "multiplier": config.MULTIPLIER, "fee": 1.0,
                                          "start": config.LIVE["start"], "end": config.LIVE["end"]})
    HISTORY.write_text(json.dumps(_history["data"], ensure_ascii=False), encoding="utf-8")
    return _history["data"]


def _exec_time(r, ordered):
    """체결됐으면 time을 체결 시각으로(차트·달력·평균가 계산이 체결 순서를 따르게), 낸 시각은 order_time에. 지정가는 낸 뒤 한참 뒤에 체결된다."""
    if not r or not r.get("ExecDttm"):
        return {}
    t = datetime.strptime(r["ExecDttm"][:14], "%Y%m%d%H%M%S").replace(tzinfo=KST)
    return {"time": int(t.timestamp()), "order_time": ordered}


def _day_orders(symbol, day, done):
    """그 거래일의 LS 주문내역. done(끝난 날)이면 store/ls_orders/에 두고 다시 묻지 않는다."""
    path = config.STORE / "ls_orders" / f"{symbol}_{day:%Y%m%d}.json"
    if done and path.exists():
        rows = json.loads(path.read_text(encoding="utf-8"))
        if not any(r.get("ExecQty") for r in rows) or any("ExecDttm" in r for r in rows):  # 10-20 전 저장본에는 체결 시각이 없어 한 번 다시 받는다
            return rows
    rows = ls.orders(symbol, f"{day:%Y%m%d}")
    last = {}  # 주문번호 → 마지막 체결 시각(나눠 체결되면 마지막 것)
    for f in ls.fills(symbol, f"{day:%Y%m%d}"):
        n = int(f["OvrsFutsOrdNo"])
        last[n] = max(last.get(n, ""), f.get("ExecDttm") or "")
    for r in rows:
        if last.get(int(r["OvrsFutsOrdNo"])):
            r["ExecDttm"] = last[int(r["OvrsFutsOrdNo"])]
    if done:
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    return rows


def get_live():
    """화면용: 연결 상태, 목표와 실제, 최근 주문."""
    with _lock:
        state = json.loads(json.dumps(_load()))
    return {"enabled": state["enabled"], "started": started(), "start": config.LIVE["start"], "candidate": candidate(),
            "choices": {k: c["name"] for k, c in config.CANDIDATES.items() if not c.get("watch")},
            "max_contracts": config.LIVE["max_contracts"], "contract_margin": paper.status.get("margin"), "min_weekly": config.LIVE["min_weekly"], **status, "orders": state["orders"][::-1][:20]}


if __name__ == "__main__":
    # 점검: LS를 가짜로 바꿔 끼우고 판단만 확인한다(실제 주문 없음, store/live.json도 건드리지 않음)
    import tempfile
    from pathlib import Path
    PATH = Path(tempfile.mkdtemp()) / "live.json"
    UNFILLED = PATH.with_name("ls_unfilled.json")
    _state = None
    _old["today"] = ls.order_day()  # 가짜 LS는 날짜를 안 가려서 처음 훑기를 건너뛴다
    sent, book = [], {"pos": 0, "pending": []}
    ls.positions = lambda: ({"MNQZ26": book["pos"]}, [])
    ls.orders = lambda symbol, **k: book["pending"]
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
    paper._load = lambda: {config.LIVE["candidate"]: {"contracts": 999}}
    tick()
    assert sent == [2, -3] and "최대" in status["note"]
    paper._load = lambda: {config.LIVE["candidate"]: {"contracts": 0}}
    _state["enabled"] = False
    tick()
    assert sent == [2, -3] and "꺼져" in status["note"]
    paper._load = lambda: {config.LIVE["candidate"]: {"contracts": 1}}
    book["pos"] = -1
    market_open = lambda ny: False
    flatten()                                     # 장이 쉬면 걸어 두기만 하고
    assert sent == [2, -3] and _state["flatten"], sent
    market_open = lambda ny: True
    tick()
    assert sent == [2, -3, 1], sent              # 열리면 목표와 상관없이 숏 1 → 0: 매수 1
    _state["orders"][-1]["time"] -= CONFIRM + 1
    book["pos"] = 0
    tick()
    assert sent == [2, -3, 1] and not _state["enabled"] and not _state["flatten"]  # 0이 되면 끈다
    _state["enabled"] = True
    started = lambda now=None: True
    manual(-2)
    assert sent == [2, -3, 1, -2] and not _state["enabled"] and _state["orders"][-1]["manual"]  # 직접 주문은 자동 주문을 끈다
    book["pos"] = -2
    for bad in (-200, 0, 1.5, True):  # 최대 초과(-202), 0, 소수, 참거짓은 거절
        try:
            manual(bad)
            raise AssertionError(bad)
        except ValueError:
            pass
    assert sent == [2, -3, 1, -2]
    manual(flip=True)                             # 뒤집기: 숏 2 → 롱 2, 매수 4 한 건
    assert sent == [2, -3, 1, -2, 4] and _state["orders"][-1]["to"] == 2, sent
    book["pos"] = 2
    manual(qty=3, flip=True)                      # 고른 비중으로 뒤집기: 롱 2 → 숏 3, 매도 5 한 건
    assert sent[-1] == -5 and _state["orders"][-1]["to"] == -3, sent
    sent.pop()
    book["pos"] = 0
    try:
        manual(flip=True)
        raise AssertionError("포지션 없이 뒤집음")
    except ValueError:
        pass
    # 지정가 정리: 롱 2 → 매도 2 지정가, 자동 주문은 끔. 미체결이면 다시 못 내고, 취소하면 비운다
    limits, cancels = [], []
    ls.limit_order = lambda symbol, qty, price: limits.append((qty, price)) or "0000000002"
    ls.cancel = lambda symbol, number, day=None: cancels.append((number, day) if day else number) or "0000000003"
    book["pos"], _state["enabled"] = 2, True
    limit_close(30010.25)
    assert limits == [(-2, 30010.25)] and not _state["enabled"] and _state["orders"][-1]["limit"], limits
    assert status["pending"] == [{"number": "0000000002", "qty": -2, "price": 30010.25, "limit": True}]  # LS에 늦게 잡혀도 바로 미체결로 보인다
    limit_close(29990.0, 3)                       # 지정가 진입: 롱 2에 매수 3 → 체결되면 롱 5
    assert limits[-1] == (3, 29990.0) and _state["orders"][-1]["to"] == 5, limits
    for bad in (199, 0, 1.5):                     # 최대 초과(201), 0, 소수는 거절
        try:
            limit_close(29990.0, bad)
            raise AssertionError(bad)
        except ValueError:
            pass
    limit_close(30010.0, reduce=1)                # 분할 정리: 롱 2에서 1계약만 매도
    assert limits[-1] == (-1, 30010.0) and _state["orders"][-1]["to"] == 1, limits
    for bad in (3, 0, 1.5):                       # 포지션보다 많이(뒤집힘), 0, 소수는 거절
        try:
            limit_close(30010.0, reduce=bad)
            raise AssertionError(bad)
        except ValueError:
            pass
    manual(reduce=2)                              # 시장가 분할 정리: 롱 2 → 0
    assert sent[-1] == -2 and _state["orders"][-1]["to"] == 0, sent
    limits.pop(); limits.pop()
    book["pending"] = [{"UnercQty": "2", "OvrsFutsOrdNo": "0000000002", "BnsTpCode": "1", "OvrsDrvtOrdPrc": "30010.25", "AbrdFutsOrdPtnCode": "2"}]
    tick()
    assert status["pending"] == [{"number": "0000000002", "qty": -2, "price": 30010.25, "limit": True}], status["pending"]
    for bad in (30010.1, 33000.0, "30000"):  # 틱 단위가 아님, 5% 넘게 떨어짐, 문자
        try:
            limit_close(bad)
            raise AssertionError(bad)
        except ValueError:
            pass
    try:
        limit_close(30000.0)
        raise AssertionError("미체결 있는데 또 냄")
    except ValueError:
        pass
    # 지정가 정정: 새 번호로 바뀌고 기록도 따라 바뀐다. 시장가 주문이나 없는 번호는 거절
    mods = []
    ls.modify = lambda symbol, number, qty, price: mods.append((number, qty, price)) or "0000000004"
    _state["orders"].insert(0, {"time": 1, "number": "2", "qty": 5, "price": 29000.0, "ok": True})  # 지난 거래일의 같은 번호
    assert modify_limit("0000000002", 30020.0) == {"number": "0000000004", "price": 30020.0}
    assert _state["orders"].pop(0) == {"time": 1, "number": "2", "qty": 5, "price": 29000.0, "ok": True}  # 그건 그대로
    assert mods == [("0000000002", -2, 30020.0)] and any(o.get("number") == "0000000004" and o["price"] == 30020.0 for o in _state["orders"]), mods
    assert status["pending"] == [{"number": "0000000004", "qty": -2, "price": 30020.0, "limit": True}], status["pending"]
    # 새 번호가 LS 내역에 아직 없어도(가짜 LS는 정정을 반영하지 않는다) 바로 다시 옮길 수 있다
    assert modify_limit("0000000004", 30030.0)["price"] == 30030.0 and mods[-1] == ("0000000004", -2, 30030.0), mods
    for number, price in (("0000000009", 30020.0), ("0000000002", 30020.1)):
        try:
            modify_limit(number, price)
            raise AssertionError((number, price))
        except ValueError:
            pass
    _hold.clear()  # 가짜 LS는 정정을 내역에 반영하지 않는다: 위 정정의 새 번호(4)를 화면에 붙들어 둔 것을 지운다
    ls.orders = lambda symbol, **k: [] if cancels else book["pending"]  # 취소하면 미체결이 사라진다
    assert cancel_pending() == 1 and cancels == ["0000000002"] and status["pending"] == []
    _hold.clear()
    # 지난 거래일 미체결: 보이고, 그 거래일로 취소되고, 정정은 거절. 주문을 막지 않고, 취소가 거절돼도 정리는 나간다
    old_row = {"UnercQty": 20, "OvrsFutsOrdNo": "0000008313", "OrdDt": "20261030", "BnsTpCode": "2", "OvrsDrvtOrdPrc": "28062.5", "AbrdFutsOrdPtnCode": "2"}
    orders, cancels[:] = ls.orders, []
    ls.orders = lambda symbol, day=None, **k: [old_row] if day == "20261030" else []
    day_was, ls.order_day = ls.order_day, lambda now=None: "20261102"
    _old.update(today=None, rows={})
    _refresh_old("MNQZ26")
    ls.order_day = day_was
    _pending("MNQZ26")
    assert status["pending"] == [{"number": "20261030-0000008313", "qty": 20, "price": 28062.5, "limit": True, "day": "20261030"}], status["pending"]
    assert _read("MNQZ26")[0] == []               # 자동 주문·새 지정가를 막는 쪽에는 안 들어간다
    try:
        modify_limit("20261030-0000008313", 30020.0)
        raise AssertionError("지난 거래일 주문 정정")
    except ValueError:
        pass
    assert cancel_pending("20261030-0000008313") == 1 and cancels == [("0000008313", "20261030")] and status["pending"] == [], cancels
    _hold.clear()
    _old["rows"] = {"20261030": [old_row]}
    n, cancel, market_open_, book["pos"] = len(sent), ls.cancel, market_open, 1
    def refuse(*a):
        raise ls.LSError("이미 끝난 주문")
    ls.cancel, market_open = refuse, lambda ny: True
    flatten()
    assert sent[n:] == [-1], sent[n:]
    ls.cancel, market_open, ls.orders = cancel, market_open_, orders
    _old["rows"], book["pos"], cancels[:] = {}, 0, ["0000000002"]
    _hold.clear()
    _last_order[0] = 0
    _state["enabled"] = False
    ls.orders = lambda symbol, **k: book["pending"]
    _hold_pending("0000000007", {"number": "0000000007", "qty": 1, "price": 30000.0, "limit": True})
    _hold_pending("0000000002")
    _pending("MNQZ26")
    assert status["pending"] == [{"number": "0000000007", "qty": 1, "price": 30000.0, "limit": True}], status["pending"]
    _hold.clear()
    # 장중의 포지션 정리는 루프를 기다리지 않고 바로: 미체결 취소 → 시장가로 닫기, 자동 주문 끔
    book["pos"], _state["enabled"], cancels[:] = 2, True, []
    ls.orders = lambda symbol, **k: [] if cancels else book["pending"]
    flatten()
    assert cancels == ["0000000002"] and sent[-1] == -2 and _state["orders"][-1]["flatten"] and not _state["enabled"] and not _state["flatten"], sent
    ls.orders = lambda symbol, **k: book["pending"]
    book["pending"] = []
    book["pos"] = -2
    # 주간 최소 거래: 목표와 같아도 모자라면 0 쪽으로 1계약, 다음 확인 때 목표로 되돌린다
    _state.update(enabled=True)
    _state["orders"][-1]["time"] -= CONFIRM + 1
    paper._load = lambda: {config.LIVE["candidate"]: {"contracts": -2}}
    filler_due = lambda orders, ny, now=None: True
    tick()
    assert sent[-1] == 1 and _state["orders"][-1]["filler"], sent  # 숏 2 → 숏 1
    _state["orders"][-1]["time"] -= CONFIRM + 1
    book["pos"] = -1
    tick()
    assert sent[-1] == -1, sent                  # 다시 숏 2로
    # tick이 LS를 읽는 사이 버튼 주문이 끝나면 낡은 포지션으로 주문하지 않는다
    _state["orders"][-1]["time"] -= CONFIRM + 1
    n, positions = len(sent), ls.positions
    ls.positions = lambda: (_acted.__setitem__(0, _acted[0] + 1), positions())[1]
    tick()
    ls.positions = positions
    assert len(sent) == n, sent
    # 주 세기(한국 시각 월 ~ 일)와 금요일 뉴욕 14 ~ 17시
    mon = datetime(2026, 10, 19, 8, tzinfo=KST).timestamp()
    kst = lambda *d: datetime(*d, tzinfo=KST).timestamp()  # LS 주문 날짜는 CME 거래일: 한국 자정 넘어도 전날, 07시(겨울 08시)에 넘어간다
    assert ls.order_day(kst(2026, 10, 20, 1, 28)) == "20261019" and ls.order_day(kst(2026, 10, 20, 7, 30)) == "20261020"
    assert ls.order_day(kst(2026, 11, 10, 7, 30)) == "20261109" and ls.order_day(kst(2026, 11, 10, 8, 5)) == "20261110"
    assert week_fills([{"ok": True, "time": mon}, {"ok": True, "time": mon - 9 * 3600}, {"ok": False, "time": mon}], mon) == 1
    fri = datetime(2026, 10, 23, 14, 30, tzinfo=NY)
    import mnq.live as original  # 위에서 바꿔 끼운 것 말고 원래 함수로 확인
    assert original.filler_due([], fri, fri.timestamp()) and not original.filler_due([], fri.replace(hour=13), fri.timestamp())
    assert not original.filler_due([{"ok": True, "time": mon}] * 4, fri, fri.timestamp())
    # 대회 매매법 고르기: 목록에 있는 것만, 고른 후보의 계약 수를 목표로
    set_candidate("dip_trend")
    paper._load = lambda: {"dip_trend": {"contracts": 2}, config.LIVE["candidate"]: {"contracts": 0}}
    assert candidate() == "dip_trend" and _target() == 2
    try:
        set_candidate("scalp")  # 관찰용은 못 고른다
        raise AssertionError("없는 후보를 받았다")
    except ValueError:
        pass
    # 미체결만 묻기 점검: 전체 내역의 미체결이 다 있으면 믿고, 빠지면 전체 내역으로 돌아간다
    one = [{"OvrsFutsOrdNo": "7", "UnercQty": "1"}]
    _unfilled.update(ok=False, checked=0)
    ls.orders = lambda symbol, unfilled=False: one
    _verify_unfilled("MNQZ26", 1000)
    assert _unfilled["ok"] and json.loads(UNFILLED.read_text())["ok"]
    _verify_unfilled("MNQZ26", 1100)              # 믿은 뒤 10분 안에는 다시 안 본다
    ls.orders = lambda symbol, unfilled=False: [] if unfilled else one
    _verify_unfilled("MNQZ26", 1700)
    assert not _unfilled["ok"] and not json.loads(UNFILLED.read_text())["ok"]
    print("live 점검 통과")
