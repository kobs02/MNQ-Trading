"""15차: 일목 구름 반전(1시간봉). 선행스팬 A(전환 9·기준 26의 평균)와 B(52봉 가운데)를 지금 값(26봉 앞으로 밀기 전)으로 비교해
A가 위면 롱, 아래면 숏, 늘 1계약(롱·숏 뒤집기). 표준값 9·26·52를 그대로 써서 고른 파라미터가 없다.

- 데이터: 야후 NQ=F 1시간봉(약 2년, 24시간). 봉 i 마감에 정한 방향을 봉 i+1 동안 든다(종가 → 종가)
- 비용: 계약을 사고팔 때마다 config.FEE_PER_CONTRACT($3, 뒤집기는 2계약 = $6)
- 앞 60%(날짜)와 뒤 40%로 나눠 본다. 비교: 늘 롱 1계약, 섞기(mix) 후보
- 통과 기준(미리 정함): 뒤 40%에서 샤프 0.5 이상, 4주 수익+MDD 평균이 섞기보다 좋고, 앞 60%에서도 샤프 플러스
    cd C:\\dev\\MNQ-Trader && .venv\\Scripts\\python research\\r15_ichimoku.py
"""
import sys
import numpy as np, pandas as pd

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, ".")
from mnq import config, strategies

h = strategies.hourly()
side = strategies.ichimoku(h)["side"]
move = h["close"].diff().fillna(0.0)


def daily(pos):
    """봉마다 든 계약 수 → 날짜별 손익(계좌 대비, 비용 뒤)."""
    cash = (pos.shift(1).fillna(0.0) * move * config.MULTIPLIER - config.FEE_PER_CONTRACT * pos.diff().abs().fillna(0.0)) / config.CAPITAL
    d = cash.groupby(cash.index.normalize()).sum()
    return d[d.index.dayofweek < 5]


px = strategies.history()
mix = strategies.mix_pnl("mix", px)
mix = mix.groupby(mix.index.normalize()).sum()
mix = mix[mix.index.dayofweek < 5]
runs = {"일목 구름 반전 1계약": daily(side), "늘 롱 1계약": daily(pd.Series(1.0, index=h.index)), "섞기(mix)": mix}
days = runs["일목 구름 반전 1계약"].index
cut = days[int(len(days) * 0.6)]
flips = int((side.diff().abs() > 0).sum())
print(f"데이터: NQ=F 1시간봉 {h.index[0]:%Y-%m-%d} ~ {h.index[-1]:%Y-%m-%d}, 뒤집기 {flips}번(주당 {flips / (len(days) / 5):.1f}번), 앞/뒤 경계 {cut:%Y-%m-%d}\n")
for label, a, b in (("앞 60%", None, cut - pd.Timedelta(days=1)), ("뒤 40%", cut, None)):
    print(f"===== {label} =====")
    for name, d in runs.items():
        part = d[a:b]
        st, w = strategies.stats(part), strategies.windows(part)
        print(f"  {name:16s} 샤프 {st['sharpe']:5.2f} | 합계 {st['total']:+6.1%} | 최대 낙폭 {st['drawdown']:6.1%} | 4주 수익 중앙 {w['ret_median']:+6.2%} | 수익+MDD 평균 {w['score']:+6.2%}")
