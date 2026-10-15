"""1차: 일봉 규칙들을 대회와 같은 4주(거래일 20일) 구간으로 잘라 수익률과 MDD를 본다.

- 시세: 나스닥100 지수(^NDX) 종가. 선물 수익률은 지수 + 배당 - 금리라서 4주에 0.1 ~ 0.3%쯤 다르다(무시)
- 체결: t일 종가로 정한 노출을 t+1일 수익률에 적용. 비용은 노출이 바뀐 만큼 0.01%(MNQ 수수료 + 1틱 미끄러짐보다 넉넉히)
- 노출은 계좌 대비 배수(1 = 계좌만큼 롱). 실제로는 1계약 = 계좌의 약 0.6배라 그 단위로 반올림된다
- 2000 ~ 2019년에서 규칙을 고르고, 2020년 이후는 확인용
    cd C:\\dev\\MNQ-Trader && .venv\\Scripts\\python research\\r1_daily.py
"""
import sys
import numpy as np, pandas as pd

sys.stdout.reconfigure(encoding="utf-8")
px = pd.read_pickle("store/data/NDX_1d.pkl")["Close"].dropna()
ret = px.pct_change().fillna(0.0)
WIN, STEP, COST = 20, 5, 0.0001


def vol(n=20):
    return ret.rolling(n).std() * np.sqrt(252)


def rules():
    sma = {n: px.rolling(n).mean() for n in (50, 100, 200)}
    vt = (0.15 / vol(20)).clip(upper=2.0)  # 연 변동성 15%에 맞춘 노출, 최대 2배
    low5 = px.rolling(5).min()
    return {
        "보유 1배": pd.Series(1.0, index=px.index),
        "변동성 맞춤(15%)": vt,
        "추세 SMA50 롱/쉼": (px > sma[50]).astype(float),
        "추세 SMA100 롱/쉼": (px > sma[100]).astype(float),
        "추세 SMA200 롱/쉼": (px > sma[200]).astype(float),
        "추세 SMA100 롱/숏": np.sign(px - sma[100]),
        "추세100 + 변동성 맞춤": (px > sma[100]) * vt,
        "추세200 + 변동성 맞춤": (px > sma[200]) * vt,
        "되돌림: 5일 최저면 롱(5일 보유)": (px <= low5).astype(float).rolling(5).max(),
        "되돌림 + 추세200": ((px <= low5).astype(float).rolling(5).max()) * (px > sma[200]),
    }


def daily_pnl(expo):
    expo = expo.reindex(px.index).fillna(0.0)
    held = expo.shift(1).fillna(0.0)
    return held * ret - COST * expo.diff().abs().fillna(0.0)


def windows(pnl, start, end):
    """4주 구간마다 (수익률, MDD). MDD는 구간 시작 금액부터 잰 일별 종가 기준 최대 낙폭(음수)."""
    p = pnl[start:end].to_numpy()
    out = []
    for i in range(0, len(p) - WIN + 1, STEP):
        eq = np.cumprod(np.concatenate([[1.0], 1 + p[i:i + WIN]]))
        out.append((eq[-1] - 1, (eq / np.maximum.accumulate(eq) - 1).min()))
    return np.array(out)


def row(name, w, pnl, start, end):
    r, m = w[:, 0], w[:, 1]
    d = pnl[start:end]
    sharpe = d.mean() / d.std() * np.sqrt(252) if d.std() else 0
    return (f"{name:28s} 샤프 {sharpe:5.2f} | 4주 수익 중앙 {np.median(r):+6.2%} 평균 {r.mean():+6.2%} 플러스 {np.mean(r > 0):4.0%} "
            f"하위10% {np.percentile(r, 10):+6.2%} | MDD 중앙 {np.median(m):6.2%} 하위10% {np.percentile(m, 10):6.2%} | 수익+MDD 평균 {np.mean(r + m):+6.2%}")


if __name__ == "__main__":
    for label, start, end in (("설계 2000 ~ 2019", "2000", "2019"), ("확인 2020 ~ 2026", "2020", "2026")):
        print(f"\n===== {label} (4주 구간 {STEP}일 간격) =====")
        for name, expo in rules().items():
            pnl = daily_pnl(expo)
            print(row(name, windows(pnl, start, end), pnl, start, end))
