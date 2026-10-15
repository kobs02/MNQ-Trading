"""10차: 섞기 후보의 배율을 낮추는 방안. $100,000에서 1계약 ≈ 계좌의 0.6배라 갈래마다 1계약이 최소 단위다.
두 갈래의 노출을 더한 뒤 합에 상한을 걸어 본다(1시간봉 2024-05 ~, 비용은 합친 노출이 바뀐 만큼). MDD는 종가 기준과 장중(시간별) 기준 둘 다.
    cd C:\\dev\\MNQ-Trader && .venv\\Scripts\\python research\\r10_mix_lever.py
"""
import sys
import numpy as np, pandas as pd

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, ".")
from mnq import strategies

px = strategies.history()


def ev(hp):
    d = hp.groupby(hp.index.normalize()).sum()
    d = d[d.index.dayofweek < 5]
    w = strategies.windows(d)
    days, rows = d.index, []
    for i in range(0, len(days) - 20 + 1, 5):
        part = hp[(hp.index >= days[i]) & (hp.index < days[i + 19] + pd.Timedelta(days=1))]
        eq = np.cumprod(np.concatenate([[1.0], 1 + part.to_numpy()]))
        rows.append((eq[-1] - 1, (eq / np.maximum.accumulate(eq) - 1).min()))
    r, m = np.array(rows).T
    return d.mean() / d.std() * np.sqrt(252), w, np.median(m), np.percentile(m, 10), np.mean(r + m)


print("방안 (1계약 ≈ 0.6배)                  샤프 | 4주 수익 중앙 하위10% | MDD 중앙: 종가 / 장중, 장중 하위10% | 수익+MDD: 종가 / 장중")
for name, lv, cap in (("지금: 각 1계약, 최대 2계약", (0.6, 0.6), None),
                      ("합을 최대 1계약으로", (0.6, 0.6), 0.6),
                      ("일봉만 1계약", (0.6, 0.0), None),
                      ("장중만 1계약", (0.0, 0.6), None)):
    s, w, mi, m10, si = ev(strategies.mix_pnl("mix", px, lv, cap))
    print(f"{name:30s} {s:5.2f} | {w['ret_median']:+6.2%} {w['ret_p10']:+6.2%} | {w['mdd_median']:6.2%} / {mi:6.2%}, {m10:6.2%} | {w['score']:+6.2%} / {si:+6.2%}")
