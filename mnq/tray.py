"""작업 표시줄 알림 영역(트레이)의 아이콘: 서버가 켜져 있는 동안 떠 있어서, 브라우저를 닫아도 돌고 있는지 알 수 있다.

그림은 프로젝트 폴더의 icon_png.png이고, 오른쪽 아래의 작은 점이 상태다:
초록은 LS증권 시세를 제때 받고 있을 때, 노랑은 시세를 아직 못 받았거나 3분 넘게 끊겼을 때다.
마우스를 올리면 MNQ 가격이 보인다.
"""
import os
import time
import webbrowser

from . import config, live, paper

PICTURE = config.ROOT / "icon_png.png"
COLORS = {True: (46, 204, 138), False: (240, 180, 60)}


def _status():
    """(시세가 살아 있는가, 아이콘에 마우스를 올리면 보이는 글)."""
    s = paper.status
    alive = bool(s["price_at"]) and time.time() - s["price_at"] < 180
    price = f"{s['symbol']} {s['price']:,.2f}" if s["price"] else "MNQ 시세 기다리는 중"
    return alive, f"MNQ Trader 서버 켜짐 · {price}" + ("" if alive else " · 시세 끊김")


def start(url):
    """아이콘을 띄운다. 아이콘 라이브러리가 없으면 아무것도 하지 않는다(서버는 그대로 돈다)."""
    try:
        import pystray
        from PIL import Image, ImageDraw
    except ImportError:
        return

    def image(active):
        try:
            picture = Image.open(PICTURE).convert("RGBA").resize((64, 64), Image.LANCZOS)
        except OSError:  # 그림 파일이 없으면 어두운 바탕만
            picture = Image.new("RGBA", (64, 64), (29, 36, 49, 255))
        draw = ImageDraw.Draw(picture)
        draw.ellipse((38, 38, 63, 63), fill=COLORS[active] + (255,), outline=(255, 255, 255, 255), width=3)
        return picture

    def watch(icon):
        """시세 상태와 가격이 바뀌면 점의 색과 글이 따라 바뀐다."""
        icon.visible = True
        shown = None
        while True:
            try:
                now = _status()
                if now[0] != (shown or (None,))[0]:
                    icon.icon = image(now[0])
                if now != shown:
                    icon.title = now[1]
                    shown = now
            except Exception:
                pass  # 아이콘은 곁다리다: 여기서 난 문제로 서버가 멈추면 안 된다
            time.sleep(10)

    def quit_server(icon):
        icon.stop()
        os._exit(0)  # 서버 스레드까지 한꺼번에 끝낸다. 잠금 파일은 운영체제가 풀어 준다

    active, title = _status()
    icon = pystray.Icon("mnq-trader", image(active), title, menu=pystray.Menu(
        pystray.MenuItem("실시간 차트 열기", lambda: webbrowser.open(url + "/chart.html"), default=True),  # 아이콘을 두 번 눌러도 열린다
        pystray.MenuItem("모의 운용 열기", lambda: webbrowser.open(url + "/paper.html")),
        pystray.MenuItem("LS 모의계좌 자동 주문", lambda: live.set_enabled(not live.enabled()), checked=lambda item: live.enabled()),
        pystray.MenuItem("서버 끄기", quit_server),
    ))
    icon.run_detached(setup=watch)
