"""MNQ Trader 실행 진입점: 로컬 서버를 띄우고 브라우저에서 실시간 차트를 연다.

    .venv\\Scripts\\python main.py   (바탕화면의 "MNQ Trader 서버" 바로가기가 이것을 실행한다)
"""
import logging
import os
import sys
import threading
import webbrowser

from mnq import config, console, paper, tray
from mnq.server import app

log = logging.getLogger(__name__)


def _lock_single_instance():
    """저장 폴더의 잠금 파일을 잡아서 돌려준다. 다른 프로그램이 이미 잡고 있으면 None.

    두 개가 동시에 뜨면 가상 계좌 기록(store/paper.json)을 서로 덮어쓴다. 잠금은 프로그램이 어떻게 끝나든 운영체제가 풀어 준다.
    """
    config.STORE.mkdir(exist_ok=True)
    handle = open(config.STORE / "running.lock", "a+")
    try:
        if os.name == "nt":
            import msvcrt
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None
    return handle


if __name__ == "__main__":
    console.setup()  # 서버 창의 로그 모양(컬러 바 · 경과 시간 · 레벨)
    url = f"http://{config.HOST}:{config.PORT}"
    lock = _lock_single_instance()  # 끝날 때까지 들고 있어야 잠금이 유지된다
    if lock is None:
        log.error("이미 실행 중인 MNQ Trader 서버가 있습니다: %s", url)
        log.error("새로 켜지 않고 브라우저만 엽니다. 기존 서버를 끄려면 그 창을 닫거나 작업 표시줄 아이콘에서 끄세요.")
        webbrowser.open(url)
        sys.exit(1)
    log.info("이 창이 열려 있는 동안 서버와 가상 계좌가 돌아갑니다. 끄려면 이 창을 닫으세요.")
    log.info("작업 표시줄 오른쪽 아래에도 아이콘이 뜹니다. 아이콘을 오른쪽 클릭하면 끌 수 있습니다.")
    log.info("MNQ Trader 서버 실행 중: %s  (종료: Ctrl+C)", url)
    threading.Timer(1.5, webbrowser.open, args=(url,)).start()
    paper.run()      # 가상 계좌: 1분마다 시세, 평일 뉴욕 16:10에 결정
    tray.start(url)  # 작업 표시줄 알림 영역의 아이콘: 서버가 켜져 있는 동안 떠 있다
    app.run(host=config.HOST, port=config.PORT, threaded=True)
