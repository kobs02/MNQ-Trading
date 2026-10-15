"""LS증권에서 MNQ 근월물 1분봉을 받을 수 있는 데까지 거슬러 받아 store/data/ls_<종목>_1m.pkl에 둔다(스캘핑 연구용).

o3103은 초당 1회, 한 번에 500개. 서버가 켜져 있어도 된다(한도를 넘으면 ls.call이 1초 쉬고 다시 보낸다).
    cd C:\\dev\\MNQ-Trader && .venv\\Scripts\\python research\\fetch_1m.py [최대 봉 수, 기본 300000]
"""
import sys
import time
import pandas as pd

sys.path.insert(0, ".")
from mnq import config, ls

limit = int(sys.argv[1]) if len(sys.argv) > 1 else 300_000
symbol = ls.front_month()["Symbol"]
rows, cont = ls.minute_bars(symbol, 1, 1000, more=True)
while cont and len(rows) < limit:
    part, cont = ls.minute_bars(symbol, 1, 5000, cont=cont, more=True)
    rows = part + rows
    print(len(rows), time.strftime("%Y-%m-%d %H:%M", time.localtime(rows[0]["t"])), flush=True)
    if not part:
        break
df = pd.DataFrame(rows).drop_duplicates("t").set_index("t").sort_index()
df = df[["open", "high", "low", "close", "volume"]].astype(float)
df.index = pd.to_datetime(df.index, unit="s", utc=True).tz_convert("America/New_York")
path = config.STORE / "data" / f"ls_{symbol}_1m.pkl"
df.to_pickle(path)
print("저장:", path, len(df), df.index[0], "~", df.index[-1])
