"""13차: 4시간봉 추세 방향으로만 장중 매매. 장중 갈래(노이즈 영역 돌파, 10 ~ 15시 정시 판단)의 신호 가운데
판단 시각 직전에 끝난 4시간봉의 추세(종가 vs EMA N개, 또는 EMA 기울기)와 같은 방향만 든다.

실험 전에 정한 통과 기준(확인 구간 = 뒤쪽 절반에서 모두):
  1) 샤프가 필터 없는 노이즈 돌파보다 0.3 이상 높다   2) 4주 수익 + MDD가 필터 없는 것보다 낫다
  3) 같은 비율의 신호를 무작위로 지운 것 50번보다 낫다(샤프 상위 5%)
설정(EMA 길이, 정의)은 앞쪽 절반(설계)에서 고른다. 참고로 '4시간봉 추세대로 그 시간 내내 든다'(타점 없음)도 보여 준다.
    cd C:\\dev\\MNQ-Trader && .venv\\Scripts\\python research\\r13_trend4h.py
"""
import sys
import numpy as np, pandas as pd

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, ".")
sys.path.insert(0, "research")
from mnq import strategies
from r12_ml_hourly import H, target, daily_pnl, evaluate, show

RNG = np.random.default_rng(13)


def trend4h(h, n, kind):
    """판단 시각 t마다: t 전에 끝난 마지막 4시간봉으로 본 추세(+1, -1). 4시간봉은 뉴욕 18시(선물 세션 시작)에 맞춘다."""
    c4 = h["close"].resample("4h", offset="2h").last().dropna()  # 18, 22, 2, 6, 10, 14시 시작
    ema = c4.ewm(span=n, adjust=False).mean()
    sig = np.sign(c4 - ema) if kind == "위치" else np.sign(ema.diff())
    sig.index = sig.index + pd.Timedelta(hours=4)  # 그 봉이 끝난 시각부터 쓸 수 있다
    return sig


def at(sig, keys):
    t = pd.DatetimeIndex([d + pd.Timedelta(hours=hr) for d, hr in keys])
    i = sig.index.searchsorted(t, side="right") - 1
    return pd.Series(np.where(i >= 0, sig.to_numpy()[np.maximum(i, 0)], np.nan), index=keys)


def check_no_leak(keys):
    full = {k: trend4h(H, 20, "위치") for k in [0]}[0]
    for d, hr in keys[RNG.choice(np.arange(300, len(keys)), 15, replace=False)]:
        t = d + pd.Timedelta(hours=hr)
        cut = trend4h(H[H.index < t], 20, "위치")
        assert at(full, pd.MultiIndex.from_tuples([(d, hr)])).iloc[0] == at(cut, pd.MultiIndex.from_tuples([(d, hr)])).iloc[0], (d, hr)
    print("자르기 점검 통과")


if __name__ == "__main__":
    noise = strategies.noise_sides(strategies.moves(H), 1.0).stack()
    y = target(H).reindex(noise.index)
    ok = y.notna()
    noise, y = noise[ok], y[ok]
    check_no_leak(noise.index)
    days = pd.DatetimeIndex(np.unique(noise.index.get_level_values(0)))
    cut = days[len(days) // 2]
    print(f"설계 {days[0]:%Y-%m-%d} ~ / 확인 {cut:%Y-%m-%d} ~ {days[-1]:%Y-%m-%d}\n")

    def split(side):
        p = daily_pnl(side, y)
        return {"설계": evaluate(p[p.index < cut]), "확인": evaluate(p[p.index >= cut])}

    base = split(noise)
    print(show("필터 없음(노이즈 돌파)", base))
    res, sides = {}, {}
    for kind in ("위치", "기울기"):
        for n in (10, 20, 50, 100):
            tr = at(trend4h(H, n, kind), noise.index)
            sides[(kind, n)] = noise.where(noise == tr, 0.0)
            res[(kind, n)] = split(sides[(kind, n)])
            print(show(f"4h {kind} EMA{n} 방향만", res[(kind, n)]))
            print(show(f"  (참고) 4h {kind} EMA{n} 그대로 들기", split(tr)))
    chosen = max(res, key=lambda k: res[k]["설계"]["score"])
    e, b = res[chosen]["확인"], base["확인"]
    kept = (sides[chosen] != 0).sum() / (noise != 0).sum()
    null = []
    for _ in range(50):
        drop = (noise != 0) & (RNG.random(len(noise)) > kept)
        null.append(split(noise.where(~drop, 0.0))["확인"]["sharpe"])
    print(f"\n===== 설계 구간에서 고른 설정: 4h {chosen[0]} EMA{chosen[1]} (신호 {kept:.0%} 남김) =====")
    print(f"무작위로 지운 것의 확인 구간 샤프: 평균 {np.mean(null):.2f}, 최고 {np.max(null):.2f}")
    crit = {"샤프가 필터 없는 것 + 0.3 이상": e["sharpe"] >= b["sharpe"] + 0.3, "4주 수익 + MDD가 필터 없는 것보다 나음": e["score"] > b["score"],
            "무작위로 지운 것 50번보다 나음(상위 5%)": np.mean(np.array(null) < e["sharpe"]) >= 0.95}
    for k, v in crit.items():
        print(f"  {'통과' if v else '실패'} · {k}")
    print("최종:", "통과" if all(crit.values()) else "불통과")
