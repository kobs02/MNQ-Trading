"""3차: 대회 조건 그대로의 위험 관리. 4주마다 $100,000에서 시작해 정수 계약으로 굴리고, 낙폭 제어를 붙였을 때.

- 정수 계약: 1계약 = 그날 지수 × $2. 노출 → round(노출 × 계좌 / 계약 금액). 지수가 오를수록 1계약이 계좌에서 차지하는 몫이 커진다
  (2020년 지수 9,000 → 1계약 0.18배, 지금 29,800 → 0.6배). 그래서 계약 금액을 지금 가격으로 맞춰(지수를 29,800으로 비례 환산) 대회 조건을 흉내 낸다
- 낙폭 제어(Grossman·Zhou 1993의 단순형): 노출에 min(1, 남은 여유 ÷ 허용 낙폭)을 곱한다. 남은 여유 = 허용 낙폭 - (고점 대비 지금 낙폭).
  허용 낙폭을 다 쓰면 노출 0. 4주 구간 안에서만 잰다(대회 MDD처럼). 하루 사이의 갭은 막지 못한다
- 시세: ^NDX 일봉 종가 2020 ~ (확인 구간), 비용 계약당 $3
    cd C:\\dev\\MNQ-Trader && .venv\\Scripts\\python research\\r3_risk.py
"""
import sys
import numpy as np, pandas as pd

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, ".")
from mnq import config, strategies

CAP, FEE, WIN, NOW_PRICE = 100_000.0, 3.0, 20, 29_800.0
px = strategies.history()
start = px.index.searchsorted(pd.Timestamp("2020-01-01"))


def run(key, lever=1.0, dd=None):
    expo = (strategies.exposure(key, px) * lever).to_numpy()
    p = px.to_numpy()
    out = []
    for i in range(start, len(p) - WIN, 5):
        scale = NOW_PRICE / p[i]  # 구간 시작 가격을 지금 가격으로 맞춘다(1계약 = 계좌의 약 0.6배)
        eq, peak, n, path = CAP, CAP, 0, [CAP]
        for j in range(i, i + WIN):
            room = 1.0
            if dd:
                room = max(0.0, min(1.0, (dd - (1 - eq / peak)) / dd))
            want = int(round(expo[j] * room * eq / (p[j] * scale * config.MULTIPLIER)))
            eq -= abs(want - n) * FEE
            n = want
            eq += n * config.MULTIPLIER * scale * (p[j + 1] - p[j])
            peak = max(peak, eq)
            path.append(eq)
        path = np.array(path)
        out.append((path[-1] / CAP - 1, (path / np.maximum.accumulate(path) - 1).min(), n))
    r, m, _ = np.array(out).T
    return r, m


def show(name, r, m):
    print(f"{name:36s} 4주 수익 중앙 {np.median(r):+6.2%} 평균 {r.mean():+6.2%} 하위10% {np.percentile(r, 10):+6.2%} 플러스 {np.mean(r > 0):4.0%} | "
          f"MDD 중앙 {np.median(m):6.2%} 하위10% {np.percentile(m, 10):6.2%} 최악 {m.min():6.2%} | 수익+MDD {np.mean(r + m):+6.2%} | 수익/|MDD| {np.median(r / np.maximum(-m, 0.002)):+5.2f}")


print("===== 정수 계약, $100,000, 2020 ~ (4주 구간 5일 간격) =====")
for key in config.CANDIDATES:
    show(config.CANDIDATES[key]["name"].split(" · ")[0], *run(key))
print("\n===== 배율(계약 수)에 따라: 보유 =====")
for lever in (0.6, 1.2, 1.8, 3.0):
    show(f"보유 노출 {lever}배(약 {lever / 0.6:.0f}계약)", *run("hold", lever))
print("\n===== 낙폭 제어: 허용 낙폭을 다 쓰면 쉰다 =====")
for key in ("hold", "vt", "dip_trend"):
    for dd in (None, 0.06, 0.04, 0.03):
        show(f"{config.CANDIDATES[key]['name'].split(' · ')[0]} {'제어 없음' if dd is None else f'허용 {dd:.0%}'}", *run(key, 1.0, dd))
