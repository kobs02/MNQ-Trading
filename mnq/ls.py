"""LS증권 OPEN API(REST)로 해외선물 시세·계좌·주문을 다룬다.

- 접근 토큰은 store/token.json에 두고 만료 전까지 다시 쓴다(발급 요청을 아끼려고)
- TR마다 초당 호출 한도가 있어서(시세·계좌는 대개 1회) 한도를 넘지 않게 기다렸다 보낸다
- 요청 형식은 LS OPEN API 포털의 TR 명세와 예시를 따랐다. 주문 TR은 대회 전(CME 권한이 열리기 전)에는 확인하지 못했다
"""
import json
import threading
import time
from datetime import datetime, timedelta, timezone

import requests

from . import config

KST = timezone(timedelta(hours=9))

PATHS = {  # TR → 주소
    "o3101": "/overseas-futureoption/market-data",  # 해외선물 마스터
    "o3105": "/overseas-futureoption/market-data",  # 현재가(종목정보)
    "o3103": "/overseas-futureoption/chart",        # 분봉
    "o3108": "/overseas-futureoption/chart",        # 일·주·월봉
    "CIDBQ01500": "/overseas-futureoption/accno",   # 미결제잔고
    "CIDBQ01800": "/overseas-futureoption/accno",   # 주문내역
    "CIDBQ05300": "/overseas-futureoption/accno",   # 예탁자산
    "CIDBT00100": "/overseas-futureoption/order",   # 신규주문
    "CIDBT01000": "/overseas-futureoption/order",   # 취소주문
}
PER_SEC = {"o3105": 2, "CIDBT00100": 5, "CIDBT01000": 5}  # 나머지는 초당 1회

_session = requests.Session()
_lock = threading.Lock()
_last = {}   # TR → 마지막으로 보낸 시각
_token = None


class LSError(Exception):
    """LS증권이 요청을 거절했거나 답이 이상하다."""


def _keys():
    try:
        keys = json.loads(config.KEYS_PATH.read_text(encoding="utf-8"))
        return keys["appkey"], keys["appsecret"]
    except (OSError, ValueError, KeyError):
        raise LSError(f"키 파일이 없거나 형식이 다릅니다: {config.KEYS_PATH} 에 {{\"appkey\": \"...\", \"appsecret\": \"...\"}} 를 넣어 주세요.")


def token(fresh=False):
    """접근 토큰. 만료 10분 전이면 새로 받는다."""
    global _token
    path = config.STORE / "token.json"
    if _token is None and path.exists():
        _token = json.loads(path.read_text(encoding="utf-8"))
    if fresh or not _token or _token["expires"] - time.time() < 600:
        appkey, appsecret = _keys()
        resp = _session.post(config.BASE + "/oauth2/token", timeout=15, headers={"content-type": "application/x-www-form-urlencoded"},
                             data={"grant_type": "client_credentials", "appkey": appkey, "appsecretkey": appsecret, "scope": "oob"})
        if resp.status_code != 200 or "access_token" not in resp.text:
            raise LSError(f"토큰을 받지 못했습니다({resp.status_code}): {resp.text[:300]}")
        data = resp.json()
        _token = {"token": data["access_token"], "expires": time.time() + int(data.get("expires_in", 86400))}
        config.STORE.mkdir(exist_ok=True)
        path.write_text(json.dumps(_token), encoding="utf-8")
    return _token["token"]


def call(tr, body, cont_key=None, _retried=False, _busy=0):
    """TR 하나를 보낸다: (응답, 다음 쪽을 이어 받을 키 또는 None)."""
    with _lock:
        wait = _last.get(tr, 0) + 1 / PER_SEC.get(tr, 1) + 0.05 - time.time()
        if wait > 0:
            time.sleep(wait)
        _last[tr] = time.time()
    resp = _session.post(config.BASE + PATHS[tr], json=body, timeout=15, headers={
        "content-type": "application/json; charset=utf-8", "authorization": f"Bearer {token()}",
        "tr_cd": tr, "tr_cont": "Y" if cont_key else "N", "tr_cont_key": cont_key or ""})
    try:
        data = resp.json()
    except ValueError:
        raise LSError(f"{tr}: 응답을 읽지 못했습니다({resp.status_code}): {resp.text[:300]}")
    # 토큰이 만료됐거나 취소됐다. IGW00121(유효하지 않은 token)은 401/403이 아닌 코드로도 온다
    if (resp.status_code in (401, 403) or data.get("rsp_cd") == "IGW00121") and not _retried:
        token(fresh=True)
        return call(tr, body, cont_key, _retried=True, _busy=_busy)
    # 초당 한도는 앱키마다 세므로 다른 프로그램(서버와 연구 스크립트)이 같은 TR을 부르면 넘을 수 있다. 1초 쉬고 다시 보낸다.
    # 주문 TR은 다시 보내면 두 번 접수될 수 있으므로 하지 않는다
    if data.get("rsp_cd") == "IGW00201" and not tr.startswith("CIDBT") and _busy < 3:
        time.sleep(1.1)
        return call(tr, body, cont_key, _retried, _busy + 1)
    if resp.status_code != 200:
        raise LSError(f"{tr}: {data.get('rsp_cd')} {data.get('rsp_msg') or resp.text[:300]}")
    more = resp.headers.get("tr_cont") == "Y"
    return data, (resp.headers.get("tr_cont_key") if more else None)


def today():
    return datetime.now().strftime("%Y%m%d")


# ---------- 시세 ----------

def front_month(product=config.PRODUCT):
    """그 상품의 거래 가능한 종목 가운데 만기가 가장 가까운 것(예: MNQZ26)의 마스터 정보."""
    data, _ = call("o3101", {"o3101InBlock": {"gubun": ""}})
    rows = [r for r in data.get("o3101OutBlock", []) if r["BscGdsCd"] == product and r.get("DlPsblCd") == "1"]
    if not rows:
        raise LSError(f"{product} 종목을 마스터에서 찾지 못했습니다.")
    return min(rows, key=lambda r: (r["LstngYr"], "FGHJKMNQUVXZ".index(r["LstngM"])))


def quote(symbol):
    data, _ = call("o3105", {"o3105InBlock": {"symbol": symbol}})
    return data["o3105OutBlock"]


def minute_bars(symbol, minutes=1, count=500, cont=None, more=False):
    """분봉(오래된 것부터). date·time은 거래소(시카고) 시각이고 봉이 끝나는 시각이다.
    t: 봉이 시작한 시각(유닉스 초). 응답의 timediff(거래소 시각 - 한국 시각, 시간)로 바꾼다.

    최근 것부터 거슬러 받는다. more=True면 그보다 더 오래된 봉을 이어 받을 열쇠 cont(없으면 None)도 함께 돌려준다:
    minute_bars(..., cont=그 열쇠, more=True)로 부르면 거기서부터 더 과거로 받는다."""
    rows = []
    cts_date, cts_time, key = cont or ("", "", None)
    while len(rows) < count:
        data, key = call("o3103", {"o3103InBlock": {"shcode": symbol, "ncnt": minutes, "readcnt": min(500, count - len(rows)),
                                                    "cts_date": cts_date, "cts_time": cts_time}}, key)
        part = data.get("o3103OutBlock1") or []
        head = data.get("o3103OutBlock") or {}
        diff = int(head.get("timediff") or 0)
        for r in part:
            end = datetime.strptime(r["date"] + r["time"], "%Y%m%d%H%M%S").replace(tzinfo=KST) - timedelta(hours=diff)
            r["t"] = int(end.timestamp()) - minutes * 60
        rows += part
        cts_date, cts_time = head.get("cts_date", ""), head.get("cts_time", "")
        if not key or not part:
            key = None
            break
    rows.sort(key=lambda r: r["t"])
    return (rows, (cts_date, cts_time, key) if key else None) if more else rows


# ---------- 계좌 ----------

def assets():
    """예탁자산: 통화별 행 목록과 합계."""
    data, _ = call("CIDBQ05300", {"CIDBQ05300InBlock1": {"RecCnt": 1, "OvrsAcntTpCode": "1", "FcmAcntNo": " ", "CrcyCode": "ALL"}})
    return data.get("CIDBQ05300OutBlock2") or [], data.get("CIDBQ05300OutBlock3") or {}


def positions():
    """미결제 포지션: {종목: 순수량(롱 +, 숏 -)}과 원래 행들."""
    data, _ = call("CIDBQ01500", {"CIDBQ01500InBlock1": {"RecCnt": 1, "AcntTpCode": "1", "FcmAcntNo": " ", "QryDt": today(), "BalTpCode": "1"}})
    rows = data.get("CIDBQ01500OutBlock2") or []
    net = {}
    for r in rows:
        sign = 1 if r["BnsTpCode"] == "2" else -1  # 1 매도, 2 매수
        net[r["IsuCodeVal"]] = net.get(r["IsuCodeVal"], 0) + sign * int(r["BalQty"])
    return net, rows


def orders(symbol):
    """오늘의 주문내역."""
    data, _ = call("CIDBQ01800", {"CIDBQ01800InBlock1": {"RecCnt": 1, "IsuCodeVal": symbol, "OrdDt": today(), "ThdayTpCode": " ",
                                                         "OrdStatCode": "0", "BnsTpCode": "0", "QryTpCode": "2", "OrdPtnCode": "00",
                                                         "OvrsDrvtFnoTpCode": "A"}})
    return data.get("CIDBQ01800OutBlock2") or []


# ---------- 주문 ----------

def market_order(symbol, qty):
    """시장가 신규주문. qty > 0 매수, < 0 매도. 주문번호를 돌려준다(체결 여부는 orders/positions로 확인)."""
    if not qty:
        raise ValueError("수량이 0입니다")
    data, _ = call("CIDBT00100", {"CIDBT00100InBlock1": {
        "RecCnt": 1, "OrdDt": today(), "BrnCode": " ", "IsuCodeVal": symbol, "FutsOrdTpCode": "1",  # 1 신규
        "BnsTpCode": "2" if qty > 0 else "1", "AbrdFutsOrdPtnCode": "1",  # 1 시장가
        "CrcyCode": " ", "OvrsDrvtOrdPrc": 0, "CndiOrdPrc": 0, "OrdQty": abs(int(qty)),
        "PrdtCode": " ", "DueYymm": " ", "ExchCode": " "}})
    number = (data.get("CIDBT00100OutBlock2") or {}).get("OvrsFutsOrdNo")
    if not number or not number.strip("0"):  # 성공 코드가 TR마다 달라서 주문번호로 판단한다
        raise LSError(f"주문이 접수되지 않았습니다: {data.get('rsp_cd')} {data.get('rsp_msg')}")
    return number
