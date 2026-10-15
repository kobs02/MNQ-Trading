"""LS증권 실시간 체결(OVC) 웹소켓: MNQ 근월물의 체결이 날 때마다 받아 차트의 마지막 봉과 현재가를 바로 고친다.

TR 조회(o3103 분봉은 초당 1회)와 달리 호출 건수 제한이 없어서, 화면이 0.5초마다 물어도 LS로 가는 요청은 늘지 않는다.
끊기면 몇 초 뒤에 다시 붙는다. 장이 쉬는 동안에는 체결이 오지 않으므로 차트는 분봉 조회로 맞춘다(mnq/chart.py).
"""
import json
import logging
import threading
import time
from datetime import datetime

from . import config, ls

log = logging.getLogger(__name__)
state = {"symbol": None, "last_tick": 0, "connected": False}
_listeners = []  # 체결 하나마다 부를 함수(t 유닉스 초, 가격, 수량)


def on_tick(func):
    _listeners.append(func)


def _number(text):
    """체결가 칸에서 숫자만. 모의투자는 "29859.50", 명세의 예시는 "0. 14997.75" 꼴이라 둘 다 받는다."""
    return float(str(text).split()[-1])


def _handle(message):
    data = json.loads(message)
    code = (data.get("header") or {}).get("rsp_cd")
    if code and code != "00000":  # 등록 거절(예: 모의 토큰으로 실거래 서버에 붙음)
        log.warning("실시간 체결 등록 실패: %s %s", code, data["header"].get("rsp_msg"))
    body = data.get("body")
    if not body or (data.get("header") or {}).get("tr_cd") != "OVC" or not body.get("curpr"):
        return
    t = int(datetime.strptime(body["kordate"] + body["kortm"], "%Y%m%d%H%M%S").replace(tzinfo=ls.KST).timestamp())
    price, qty = _number(body["curpr"]), int(body.get("trdq") or 0)
    state["last_tick"] = time.time()
    for func in _listeners:
        func(t, price, qty)


def _run(symbol):
    import websocket  # websocket-client
    first = True
    while True:
        try:
            ws = websocket.create_connection(config.WS_URL, timeout=30)
            ws.send(json.dumps({"header": {"token": ls.token(), "tr_type": "3"},  # 3: 실시간 등록
                                "body": {"tr_cd": "OVC", "tr_key": symbol.ljust(8)}}))
            state["connected"] = True
            if first:
                log.info("실시간 체결 연결: %s", symbol)
            first = False
            while True:
                try:
                    message = ws.recv()
                except websocket.WebSocketTimeoutException:
                    ws.ping()  # 장이 조용해서 체결이 없을 뿐인지, 연결이 죽었는지(그러면 여기서 오류가 난다) 확인한다
                    continue
                if message:
                    _handle(message)
        except Exception as e:
            if state["connected"]:
                log.warning("실시간 체결이 끊겼습니다(5초 뒤 다시 연결): %s", e)
            state["connected"] = False
            time.sleep(5)


def start(symbol):
    state["symbol"] = symbol
    threading.Thread(target=_run, args=(symbol,), name="ls-stream", daemon=True).start()
