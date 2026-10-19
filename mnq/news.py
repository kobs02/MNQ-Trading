"""뉴스·심리 페이지(10-19): 나스닥 관련 최신 뉴스·커뮤니티 글 제목과 CNN 공포·탐욕 지수, VIX·VXN으로 본 시장 분위기.

Recommender(recommender/news.py)의 방식을 그대로 옮겼다: RSS로 제목을 받고, 긍정·부정 단어 사전으로 글마다 분위기를 매기고,
최근 글일수록 크게(12시간이면 절반) 반영한 뒤 공포·탐욕 지수와 합친다. 단어 세기라 문맥·반어는 못 읽고, 가격 방향과의 관계는 검증하지 않았다.
스레드(Threads)·X는 공개 검색 API가 없어(유료·앱 심사) 넣지 않았다. 커뮤니티는 레딧으로 대신한다.
"""
import html
import math
import re
import threading
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import requests

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0 Safari/537.36"}
ATOM = "{http://www.w3.org/2005/Atom}"

FOREIGN, DOMESTIC, COMMUNITY = "foreign", "domestic", "community"
GROUP_NAMES = {FOREIGN: "해외 뉴스", DOMESTIC: "국내 뉴스", COMMUNITY: "커뮤니티"}

# (이름, 묶음, 주소). CNN의 RSS(rss.cnn.com)는 2018년에 멈춰 있어 CNN은 공포·탐욕 지수만 쓴다(10-19 확인)
SOURCES = [
    ("CNBC 시장", FOREIGN, "https://www.cnbc.com/id/20910258/device/rss/rss.html"),
    ("CNBC 기술", FOREIGN, "https://www.cnbc.com/id/19854910/device/rss/rss.html"),
    ("Yahoo Finance", FOREIGN, "https://feeds.finance.yahoo.com/rss/2.0/headline?s=%5ENDX,QQQ,NQ=F&region=US&lang=en-US"),
    ("MarketWatch", FOREIGN, "https://feeds.content.dowjones.io/public/rss/mw_topstories"),
    ("Google 뉴스(영문)", FOREIGN, "https://news.google.com/rss/search?q=nasdaq+stock+market+when:1d&hl=en-US&gl=US&ceid=US:en"),
    ("Google 뉴스(한글)", DOMESTIC, "https://news.google.com/rss/search?q=%EB%82%98%EC%8A%A4%EB%8B%A5+when:1d&hl=ko&gl=KR&ceid=KR:ko"),
    ("연합뉴스 경제", DOMESTIC, "https://www.yna.co.kr/rss/economy.xml"),
    ("Reddit r/wallstreetbets", COMMUNITY, "https://www.reddit.com/r/wallstreetbets/hot/.rss?limit=40"),
    ("Reddit r/StockMarket", COMMUNITY, "https://www.reddit.com/r/StockMarket/hot/.rss?limit=40"),
]

MAX_AGE_HOURS = 48
HALF_LIFE_HOURS = 12
PER_SOURCE_LIMIT = 40
CACHE_SECONDS = 300
WEIGHTS = {"news": 0.45, "community": 0.20, "fear_greed": 0.35}
OUTLOOK_THRESHOLD = 0.2

# 나스닥·미국 증시와 관련 없는 글(일반 경제·생활 기사)은 뺀다
MARKET_WORDS = re.compile(
    r"나스닥|뉴욕증시|뉴욕 증시|미 증시|미국 증시|미국증시|월가|S&P|다우지수|다우존스|연준|파월|빅테크|엔비디아|애플|마이크로소프트|테슬라|메타|아마존|알파벳|반도체|"
    r"nasdaq|\bqqq\b|\bndx\b|s&p|\bdow\b|wall street|stocks?\b|equit|futures|\bfed\b|powell|treasur|yields?\b|earnings|"
    r"nvidia|apple|microsoft|tesla|\bmeta\b|amazon|alphabet|google|broadcom|semiconductor|chip|\bai\b|tech", re.I)

POSITIVE = [
    r"\bsurg", r"\bsoar", r"\brall", r"\bgain", r"\bbullish", r"\bbreakout", r"\brecord high", r"\ball-time high", r"\brecord\b",
    r"\brebound", r"\brecover", r"\bjump", r"\bclimb", r"\bupgrade", r"\boptimis", r"\bboost", r"\boutperform", r"\bbeats?\b",
    r"\brises?\b", r"\brose\b", r"\bhigher\b", r"\bgreen\b", r"\brate cuts?\b", r"\bsoft landing", r"\bcalls\b", r"\bmoon", r"\brips?\b",
    "급등", "상승", "강세", "돌파", "반등", "신고가", "호재", "매수세", "순유입", "랠리", "회복", "최고치", "사상 최고", "낙관",
    "훈풍", "기대감", "청신호", "폭등", "상승세", "오름세", "수혜", "금리 인하", "호실적", "어닝 서프라이즈",
]
NEGATIVE = [
    r"\bplung", r"\bcrash", r"\bdrop", r"\bfalls?\b", r"\bfell\b", r"\bslump", r"\bbearish", r"\bsell-?off", r"\blower\b",
    r"\boutflow", r"\blawsuit", r"\bfears?\b", r"\bwarn", r"\brisk", r"\bdeclin", r"\btumbl", r"\bdump", r"\bloss",
    r"\bsink", r"\bslid", r"\bslip", r"\bpanic", r"\bcollaps", r"\bred\b", r"\brecession", r"\binflation", r"\btariff",
    r"\bmiss(?:es|ed)?\b", r"\bdowngrade", r"\bbubble", r"\bnightmare", r"\bputs\b", r"\bselling\b", r"\bslips?\b",
    "급락", "하락", "약세", "폭락", "붕괴", "악재", "매도세", "순유출", "공포", "우려", "경고", "조정", "손실", "관세",
    "하락세", "내림세", "투매", "패닉", "적신호", "불안", "침체", "인플레", "거품", "어닝 쇼크", "실적 부진",
]
SINGLE_STOCK = re.compile(r"\((?:NASDAQ|NYSE|NYSEARCA|OTC|AMEX)\s*:\s*[A-Z.]+\)", re.I)
POSITIVE_RE = re.compile("|".join(POSITIVE), re.I)
NEGATIVE_RE = re.compile("|".join(NEGATIVE), re.I)

_lock = threading.Lock()
_news_cache = None
_fng_cache = None
_vol_cache = None
_last_good = {}


def _clean(text):
    text = re.sub(r"<[^>]+>", " ", html.unescape(text or ""))
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def _parse_time(text):
    if not text:
        return None
    text = text.strip()
    try:
        when = parsedate_to_datetime(text)
    except (TypeError, ValueError):
        try:
            when = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when.timestamp()


def _child_text(node, *names):
    for name in names:
        child = node.find(name)
        if child is not None and (child.text or "").strip():
            return child.text
    return ""


def _fetch_source(source):
    """한 출처의 글 목록. 실패하면 빈 목록과 오류."""
    name, group, url = source
    try:
        resp = requests.get(url, headers=HEADERS, timeout=8)
        if resp.status_code == 429:  # 레딧은 몰리면 거절한다: 한 번만 다시
            time.sleep(2)
            resp = requests.get(url, headers=HEADERS, timeout=8)
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
    except Exception as e:
        return [], {"name": name, "group": group, "ok": False, "count": 0, "error": type(e).__name__}
    now = time.time()
    items = []
    for node in root.findall(".//item") + root.findall(f".//{ATOM}entry"):
        title = _clean(_child_text(node, "title", f"{ATOM}title"))
        link = _child_text(node, "link").strip()
        if not link:
            atom_link = node.find(f"{ATOM}link")
            link = atom_link.get("href", "") if atom_link is not None else ""
        when = _parse_time(_child_text(node, "pubDate", f"{ATOM}updated", f"{ATOM}published"))
        summary = _clean(_child_text(node, "description", f"{ATOM}summary", f"{ATOM}content"))[:300]
        if not title or when is None or not 0 <= now - when <= MAX_AGE_HOURS * 3600:
            continue
        if group != COMMUNITY and not MARKET_WORDS.search(title + " " + summary):  # 레딧 주식 게시판은 다 증시 얘기다
            continue
        if SINGLE_STOCK.search(title):  # "IBEX (NASDAQ:IBEX) Hits New 12-Month High" 같은 개별 종목 자동 기사는 시장 분위기가 아니다
            continue
        items.append({"source": name, "group": group, "title": title, "link": link, "time": int(when), "summary": summary})
    items.sort(key=lambda item: -item["time"])
    items = items[:PER_SOURCE_LIMIT]
    return items, {"name": name, "group": group, "ok": True, "count": len(items)}


def _score(item):
    """글 한 편의 분위기 -1 ~ +1. 제목의 단어로, 제목에 없으면 요약에 단어가 둘 이상일 때만 약하게."""
    title, summary = item["title"], item["summary"]
    pos, neg = POSITIVE_RE.findall(title), NEGATIVE_RE.findall(title)
    strength = 1.0
    if not pos and not neg:
        pos, neg = POSITIVE_RE.findall(summary), NEGATIVE_RE.findall(summary)
        strength = 0.6
        if len(pos) + len(neg) < 2:
            pos, neg = [], []
    item["score"] = round(strength * (len(pos) - len(neg)) / (len(pos) + len(neg)), 2) if pos or neg else 0.0
    item["sentiment"] = 1 if item["score"] > 0.2 else -1 if item["score"] < -0.2 else 0
    item["words"] = {"positive": sorted({w.lower() for w in pos})[:5], "negative": sorted({w.lower() for w in neg})[:5]}
    del item["summary"]
    return item


def _load_news():
    global _news_cache
    if _news_cache and time.time() - _news_cache[0] < CACHE_SECONDS:
        return _news_cache
    reddit = [s for s in SOURCES if "reddit.com" in s[2]]
    rest = [s for s in SOURCES if s not in reddit]
    with ThreadPoolExecutor(6) as pool:
        fetched = dict(zip([s[0] for s in rest], pool.map(_fetch_source, rest)))
    for source in reddit:  # 레딧은 동시에 부르면 거절한다
        fetched[source[0]] = _fetch_source(source)
        time.sleep(3)
    results = []
    for source in SOURCES:
        source_items, status = fetched[source[0]]
        if status["ok"]:
            _last_good[source[0]] = source_items
        elif source[0] in _last_good:  # 이번에 실패하면 지난번 것을 쓴다
            source_items = [dict(i) for i in _last_good[source[0]] if time.time() - i["time"] <= MAX_AGE_HOURS * 3600]
            status = {**status, "count": len(source_items), "stale": True}
        results.append((source_items, status))
    seen, items = set(), []
    for source_items, _ in results:
        for item in source_items:
            key = re.sub(r"\s+-\s+[^-]+$", "", item["title"])  # 구글 뉴스는 제목 끝에 " - 매체 이름"을 붙인다
            if key not in seen:
                seen.add(key)
                items.append(_score(dict(item)))
    items.sort(key=lambda item: -item["time"])
    _news_cache = (time.time(), items, [status for _, status in results])
    return _news_cache


def _mood(items):
    """최근 글일수록 크게 반영한 긍정·부정 균형(-1 ~ +1). 분모에 3을 더해 글이 적을 때 극단으로 튀지 않게."""
    now = time.time()
    pos = neg = 0.0
    counts = {"positive": 0, "negative": 0, "neutral": 0}
    for item in items:
        weight = math.exp(-math.log(2) * (now - item["time"]) / 3600 / HALF_LIFE_HOURS)
        if item["sentiment"] > 0:
            pos += weight
            counts["positive"] += 1
        elif item["sentiment"] < 0:
            neg += weight
            counts["negative"] += 1
        else:
            counts["neutral"] += 1
    return {"score": round((pos - neg) / (pos + neg + 3), 2), "total": len(items), **counts}


# CNN 공포·탐욕 지수의 일곱 구성 요소(CNN 사이트의 이름)
FG_PARTS = {"market_momentum_sp500": "S&P 500 모멘텀(125일선 대비)", "stock_price_strength": "52주 신고가 vs 신저가",
            "stock_price_breadth": "상승·하락 거래량", "put_call_options": "풋·콜 옵션 비율", "market_volatility_vix": "VIX(50일 평균 대비)",
            "junk_bond_demand": "정크본드 수요", "safe_haven_demand": "안전자산(채권) 수요"}
FG_RATING = {"extreme fear": "극단적 공포", "fear": "공포", "neutral": "중립", "greed": "탐욕", "extreme greed": "극단적 탐욕"}


def _load_fear_greed():
    """CNN 공포·탐욕 지수(미국 주식, 0 ~ 100): 지금 값, 최근 30일, 구성 요소. CNN 사이트가 쓰는 공개 자료 주소라 바뀌면 끊길 수 있다."""
    global _fng_cache
    if _fng_cache and time.time() - _fng_cache[0] < CACHE_SECONDS:
        return _fng_cache[1]
    try:
        d = requests.get("https://production.dataviz.cnn.io/index/fearandgreed/graphdata", headers=HEADERS, timeout=10).json()
        fg = d["fear_and_greed"]
        hist = [{"time": int(p["x"] / 1000), "value": round(p["y"])} for p in d["fear_and_greed_historical"]["data"]][-30:]
        parts = [{"name": name, "value": round(d[k]["score"]), "rating": FG_RATING.get(d[k].get("rating"), d[k].get("rating"))}
                 for k, name in FG_PARTS.items() if isinstance(d.get(k), dict) and "score" in d[k]]
        result = {"value": round(fg["score"]), "rating": FG_RATING.get(fg["rating"], fg["rating"]), "week_ago": round(fg.get("previous_1_week") or 0),
                  "month_ago": round(fg.get("previous_1_month") or 0), "history": hist, "parts": parts}
    except Exception:
        result = None
    _fng_cache = (time.time(), result)
    return result


def _load_vol():
    """VIX(S&P 500 30일 내재 변동성)와 VXN(나스닥 100). 야후 일봉 종가, 장중이면 마지막 값."""
    global _vol_cache
    if _vol_cache and time.time() - _vol_cache[0] < CACHE_SECONDS:
        return _vol_cache[1]
    out = {}
    try:
        import yfinance as yf
        for key, sym in (("vix", "^VIX"), ("vxn", "^VXN")):
            c = yf.Ticker(sym).history(period="3mo")["Close"].dropna()
            if len(c) > 1:
                out[key] = {"value": round(float(c.iloc[-1]), 2), "prev": round(float(c.iloc[-2]), 2), "avg": round(float(c.tail(60).mean()), 2),
                            "history": [round(float(v), 2) for v in c.tail(30)], "times": [int(t.timestamp()) for t in c.tail(30).index]}
    except Exception:
        pass
    _vol_cache = (time.time(), out)
    return out


def _outlook(groups, fng):
    """비중대로 합친 종합 분위기. 자료가 없는 요소는 빼고 나머지로."""
    news_scores = [groups[g]["score"] for g in (FOREIGN, DOMESTIC) if groups[g]["total"]]
    parts = []
    if news_scores:
        parts.append({"key": "news", "name": "뉴스 (해외 + 국내)", "score": round(sum(news_scores) / len(news_scores), 2)})
    if groups[COMMUNITY]["total"]:
        parts.append({"key": "community", "name": "커뮤니티 (레딧)", "score": groups[COMMUNITY]["score"]})
    if fng:
        parts.append({"key": "fear_greed", "name": "CNN 공포·탐욕 지수", "score": round((fng["value"] - 50) / 50, 2)})
    total = sum(WEIGHTS[p["key"]] for p in parts)
    for p in parts:
        p["weight"] = round(WEIGHTS[p["key"]] / total, 2)
    score = round(sum(p["score"] * p["weight"] for p in parts), 2) if parts else 0.0
    notes = []
    signs = {p["key"]: p["score"] for p in parts}
    if "news" in signs and "fear_greed" in signs and signs["news"] * signs["fear_greed"] < -0.03:
        notes.append("뉴스 분위기와 공포·탐욕 지수의 방향이 서로 엇갈립니다.")
    if fng and (fng["value"] >= 75 or fng["value"] <= 25):
        notes.append(f"공포·탐욕 지수가 '{fng['rating']}' 구간입니다. 이 지수와 그 뒤 나스닥 방향의 관계는 이 프로그램에서 검증하지 않았습니다.")
    return {"score": score, "direction": 1 if score >= OUTLOOK_THRESHOLD else -1 if score <= -OUTLOOK_THRESHOLD else 0,
            "threshold": OUTLOOK_THRESHOLD, "parts": parts, "notes": notes}


def get_news():
    with _lock:
        fetched_at, items, sources = _load_news()
        fng = _load_fear_greed()
        vol = _load_vol()
    groups = {g: _mood([i for i in items if i["group"] == g]) for g in GROUP_NAMES}
    return {"updated_at": int(fetched_at), "outlook": _outlook(groups, fng),
            "groups": [{"key": k, "name": n, **groups[k]} for k, n in GROUP_NAMES.items()],
            "fear_greed": fng, "vol": vol, "sources": sources, "items": items[:200], "window_hours": MAX_AGE_HOURS}


if __name__ == "__main__":
    # 점검: 단어 판정과 분위기 계산(네트워크 없음)
    assert _score({"title": "Nasdaq rallies to record high", "summary": ""})["sentiment"] == 1
    assert _score({"title": "나스닥 급락, AI 거품 우려", "summary": ""})["sentiment"] == -1
    assert _score({"title": "Stocks open Monday", "summary": ""})["sentiment"] == 0
    assert _score({"title": "Netflix earnings miss estimates", "summary": ""})["words"]["negative"] == ["miss"]
    assert SINGLE_STOCK.search("VeriSign (NASDAQ:VRSN) Stock Price Target Raised") and not MARKET_WORDS.search("중앙일보 인수에 다우키움 선정")
    now = time.time()
    m = _mood([{"time": now, "sentiment": 1}] * 3 + [{"time": now, "sentiment": -1}])
    assert m["score"] == round(2 / 7, 2) and m["positive"] == 3, m
    o = _outlook({FOREIGN: {"score": 0.5, "total": 3}, DOMESTIC: {"score": 0.0, "total": 0}, COMMUNITY: {"score": 0.0, "total": 0}},
                 {"value": 20, "rating": "극단적 공포"})
    assert [p["key"] for p in o["parts"]] == ["news", "fear_greed"] and len(o["notes"]) == 2, o
    print("news 점검 통과")
