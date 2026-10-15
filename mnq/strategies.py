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


def _vt(px):
    vol = px.pct_change().rolling(20).std() * np.sqrt(252)
    return (0.15 / vol).clip(upper=2.0)  # 연 변동성 15%에 맞춘 노출, 최대 2배


def _dip(px):
    return (px <= px.rolling(5).min()).astype(float).rolling(5).max()  # 5일 중 최저로 마감하면 5일 동안 롱


RULES = {
    "hold": lambda px: pd.Series(1.0, index=px.index),
    "vt": _vt,
    "trend_vt": lambda px: (px > px.rolling(200).mean()) * _vt(px),
    "dip_trend": lambda px: _dip(px) * (px > px.rolling(200).mean()),
}


def exposure(key, px):
    """종가 표 px로 정한 날마다의 노출(배율 적용 전)."""
    return RULES[config.CANDIDATES[key]["rule"]](px).fillna(0.0)


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


def backtest(key, px):
    """과거 시험: 그래프용 곡선(배율 적용), 설계·확인 구간과 연도별 성적, 4주 구간 분포."""
    lever = config.CANDIDATES[key]["lever"]
    expo = exposure(key, px) * lever
    ret = px.pct_change().fillna(0.0)
    pnl = (expo.shift(1).fillna(0.0) * ret - COST * expo.diff().abs().fillna(0.0))["2000":]
    eq = (1 + pnl).cumprod()
    return {
        "curve": [[int(t.timestamp()), float(v)] for t, v in eq.items()],
        "periods": {name: {"stats": stats(pnl[a:b]), "windows": windows(pnl[a:b])}
                    for name, (a, b) in (("설계 2000 ~ 2019", ("2000", "2019")), ("확인 2020 ~", ("2020", None)))},
        "years": {int(y): {"total": float((1 + g).prod() - 1), "drawdown": float(((1 + g).cumprod() / (1 + g).cumprod().cummax() - 1).min())}
                  for y, g in pnl.groupby(pnl.index.year)},
        "until": int(px.index[-1].timestamp()),
    }
