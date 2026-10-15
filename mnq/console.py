"""서버 창(콘솔)의 로그 모양: 이름표 · 왼쪽 컬러 바 · 경과 시간 · 레벨 · 분류 · 내용을 줄 맞춰 찍는다.

    MNQ Server ▌ 00:00:01.234  SUCCESS  연결      Running on http://127.0.0.1:8775

- 로그 내용과 서버 동작은 그대로 두고 표준 logging에 서식만 붙인다. werkzeug(요청 기록)와 Flask 시작 문구도 같은 모양으로 맞춘다
- 색은 ANSI 코드다. Windows 콘솔은 가상 터미널 처리를 켜서 보이게 하고, 켤 수 없거나 파일로 보낼 때는 색 없이 같은 모양으로 찍는다
- 줄마다 끝에 색을 되돌려 다음 줄로 번지지 않는다
- 화면이 몇 초마다 묻는 요청(차트, 매매 정보)은 정상 응답이면 찍지 않는다. 창이 그 기록으로 뒤덮이지 않게
- 미리 보기: python -m mnq.console
"""

import logging
import os
import re
import shutil
import sys
import unicodedata


SUCCESS = 25  # INFO와 WARNING 사이: 서버가 뜨거나 연결이 된 것처럼 잘 끝난 일
logging.addLevelName(SUCCESS, "SUCCESS")

def _rgb(r, g, b):
    """24비트 글자색. Windows Terminal과 Windows 10 이후의 콘솔이 지원한다."""
    return f"\x1b[38;2;{r};{g};{b}m"


RESET = "\x1b[0m"
BOLD = "\x1b[1m"  # 레벨 이름: 굵게 해서 은은하게 빛나 보이게
# 파스텔 색: 어두운 창 바탕에서 부드럽되 조금 밝고 맑게
DIM = _rgb(136, 144, 166)      # 시간
TAG = _rgb(168, 184, 232)      # 분류
TEXT = _rgb(236, 238, 248)     # 내용
FAINT = _rgb(146, 154, 176)    # Traceback처럼 덜 중요한 줄
VERSION = _rgb(198, 170, 255)  # 버전
# 레벨 → (바와 레벨 글자색, 화면에 쓰는 이름)
LEVELS = {
    logging.DEBUG: (_rgb(166, 174, 194), "DEBUG"),
    logging.INFO: (_rgb(122, 222, 156), "INFO"),
    SUCCESS: (_rgb(128, 252, 186), "SUCCESS"),
    logging.WARNING: (_rgb(255, 214, 112), "WARN"),
    logging.ERROR: (_rgb(255, 132, 146), "ERROR"),
    logging.CRITICAL: (_rgb(255, 108, 166), "FATAL"),
}
# 로거 이름 → 분류. 요청 기록과 서버 시작은 내용을 보고 따로 나눈다
TAGS = {"mnq.paper": "모의운용", "mnq.chart": "차트", "mnq.ls": "LS", "mnq.tray": "아이콘", "__main__": "서버"}
QUIET = ("/api/chart", "/api/trading", "/api/paper")  # 화면이 주기적으로 묻는 주소
TAG_WIDTH = 8  # 화면 칸 수(한글은 두 칸)
BAR = "▌"

_ANSI = re.compile(r"\x1b\[[0-9;]*m")
# werkzeug의 요청 한 줄: 127.0.0.1 - - [14/Oct/2026 23:40:01] "GET /api/chart?interval=5 HTTP/1.1" 200 -
_REQUEST = re.compile(r'^(?P<ip>\S+) - - \[[^\]]*\] "(?P<line>.*)" (?P<code>\S+) (?P<size>\S+)$')


def _width(text):
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in text)


def _pad(text, width):
    return text + " " * max(width - _width(text), 0)


def _wrap(text, width):
    """화면 칸 수 width에 맞춰 줄을 나눈다. 띄어쓰기에서 나누고, 한 낱말이 한 줄보다 길면 그 안에서 자른다."""
    if width <= 0 or _width(text) <= width:
        return [text]
    lines, line = [], ""
    for word in re.split(r"(?<= )", text):  # 띄어쓰기는 앞 낱말에 붙여 둔다
        if line and _width(line + word.rstrip()) > width:
            lines.append(line.rstrip())
            line = ""
        while _width(line + word.rstrip()) > width:  # 빈 줄에도 안 들어가는 긴 낱말
            cut, used = 0, 0
            while used + _width(word[cut]) <= width:
                used += _width(word[cut])
                cut += 1
            lines.append(word[:cut])
            word = word[cut:]
        line += word
    if line.strip():
        lines.append(line.rstrip())
    return lines


def _clock(ms):
    """프로그램 시작 뒤 지난 시간: 00:00:00.000"""
    ms = int(ms)
    return f"{ms // 3_600_000:02d}:{ms // 60_000 % 60:02d}:{ms // 1000 % 60:02d}.{ms % 1000:03d}"


def _request(message):
    """요청 기록 한 줄을 (레벨, 내용)으로. 요청 기록이 아니면 None."""
    match = _REQUEST.match(message)
    if not match:
        return None
    line = re.sub(r" HTTP/\d(\.\d)?$", "", match["line"])  # 요청 방식과 주소만 남긴다
    code = match["code"]
    level = logging.ERROR if code.startswith("5") else logging.WARNING if code.startswith("4") else logging.INFO
    return level, f"{line}  {code}  · {match['ip']}"


def _classify(record):
    """기록 하나를 [(레벨, 분류, 내용, 이어지는 줄인가), ...]로 나눈다."""
    message = _ANSI.sub("", record.getMessage())
    tag = TAGS.get(record.name, record.name.rsplit(".", 1)[-1])
    if record.name == "werkzeug":
        parsed = _request(message)
        if parsed:
            return [(parsed[0], "요청", parsed[1], False)]
        rows = []
        for line in message.splitlines():
            text = line.strip().removeprefix("* ").strip()
            if not text:
                continue
            if text.startswith("Running on"):
                rows.append((SUCCESS, "연결", text, False))
            elif text.startswith("WARNING:"):
                rows.append((logging.WARNING, "서버", text, False))
            else:
                rows.append((record.levelno, "서버", text, False))
        return rows
    lines = message.splitlines() or [""]
    return [(record.levelno, tag, lines[0], False)] + [(record.levelno, "", line, True) for line in lines[1:]]


class ConsoleFormatter(logging.Formatter):
    LABEL = "MNQ Server"
    HEAD = len(LABEL) + 1 + 2 + 12 + 2 + 7 + 2 + TAG_WIDTH + 2  # 버전 · 바 · 시간 · 레벨 · 분류가 차지하는 칸

    def __init__(self, color, wrap=False):
        super().__init__()
        self.color = color
        self.wrap = wrap  # 창 폭에 맞춰 줄을 나눌까(콘솔일 때만). 안 나누면 긴 줄이 맨 왼쪽부터 이어져 바가 끊긴다

    def _paint(self, code, text):
        return f"{code}{text}{RESET}" if self.color else text

    def _row(self, level, clock, tag, text, cont=False, dim=False):
        """한 줄. cont면 앞 줄에 이어지는 줄이라 버전 · 시간 · 레벨을 비운다."""
        code, name = LEVELS.get(level, LEVELS[logging.INFO])
        bar = self._paint(code, BAR)
        label = self.LABEL
        if cont:
            clock, name, label = " " * len(clock), "", " " * len(label)
        head = f"{self._paint(DIM, clock)}  {self._paint(BOLD + code, f'{name:<7}')}  {self._paint(TAG, _pad(tag, TAG_WIDTH))}"
        body = self._paint(FAINT if dim else TEXT, text)
        return f"{self._paint(VERSION, label)} {bar} {head}  {body}" + (RESET if self.color else "")  # 줄 끝마다 되돌려 다음 줄로 번지지 않게

    def format(self, record):
        clock = _clock(record.relativeCreated)
        items = [(level, tag, text, cont, False) for level, tag, text, cont in _classify(record)]
        if record.exc_info:
            items += [(record.levelno, "", line, True, True) for line in self.formatException(record.exc_info).splitlines()]
        # 맨 끝 칸까지 쓰면 콘솔이 빈 줄을 하나 더 넣으므로 한 칸 남긴다
        width = shutil.get_terminal_size((120, 30)).columns - self.HEAD - 1 if self.wrap else 0
        rows = []
        for level, tag, text, cont, dim in items:
            for i, part in enumerate(_wrap(text, width)):
                rows.append(self._row(level, clock, tag if i == 0 else "", part, cont or i > 0, dim))
        return "\n".join(rows)


def _quiet(record):
    """주기적으로 묻는 주소의 정상 응답 기록이면 False(찍지 않는다)."""
    if record.name != "werkzeug":
        return True
    match = _REQUEST.match(_ANSI.sub("", record.getMessage()))
    return not (match and match["code"][0] in "23" and match["line"].split(" ")[1].startswith(QUIET))


def _enable_windows_color(stream):
    """Windows 콘솔에서 ANSI 색을 쓸 수 있게 한다. 쓸 수 있으면 True."""
    if os.name != "nt":
        return True
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-12 if stream is sys.stderr else -11)
        mode = ctypes.c_uint32()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        return bool(kernel32.SetConsoleMode(handle, mode.value | 0x0004))  # ENABLE_VIRTUAL_TERMINAL_PROCESSING
    except Exception:
        return False


def _flask_banner(debug, app_import_path):
    """Flask 시작 문구(원래 click.echo로 바로 찍는다)를 같은 모양의 로그로."""
    log = logging.getLogger("werkzeug")
    if app_import_path is not None:
        log.info("Serving Flask app '%s'", app_import_path)
    if debug is not None:
        log.debug("Debug mode: %s", "on" if debug else "off")


def setup(level=logging.INFO):
    """프로그램을 켤 때 한 번 부른다."""
    stream = sys.stderr
    try:
        stream.reconfigure(errors="replace")  # 콘솔이 아닌 곳으로 보낼 때 못 쓰는 글자가 있어도 멈추지 않게
    except (AttributeError, ValueError):
        pass
    tty = stream.isatty()
    color = not os.environ.get("NO_COLOR") and tty and _enable_windows_color(stream)
    handler = logging.StreamHandler(stream)
    handler.setFormatter(ConsoleFormatter(color, wrap=tty))
    handler.addFilter(_quiet)
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    logging.getLogger("werkzeug").setLevel(logging.DEBUG)  # 시작 문구의 Debug mode 줄까지 보인다
    try:
        import flask.cli
        flask.cli.show_server_banner = _flask_banner
    except ImportError:
        pass


def success(logger, message, *args):
    logger.log(SUCCESS, message, *args)


if __name__ == "__main__":
    # 점검: 요청 기록 해석, 조용히 둘 요청, 칸 맞춤, 시간 형식
    assert _request('127.0.0.1 - - [14/Oct/2026 23:40:01] "GET /api/chart?interval=5 HTTP/1.1" 200 -') == (logging.INFO, "GET /api/chart?interval=5  200  · 127.0.0.1")
    assert _request('1.2.3.4 - - [x] "GET /api/paper HTTP/1.1" 500 -')[0] == logging.ERROR
    assert _request("가상 매매") is None
    rec = lambda msg: logging.LogRecord("werkzeug", logging.INFO, "", 0, msg, None, None)
    assert not _quiet(rec('127.0.0.1 - - [x] "GET /api/chart?interval=5 HTTP/1.1" 200 -'))
    assert _quiet(rec('127.0.0.1 - - [x] "GET /api/chart?interval=5 HTTP/1.1" 500 -'))
    assert _quiet(rec('127.0.0.1 - - [x] "GET /chart.html HTTP/1.1" 200 -'))
    assert _width(_pad("모의운용", TAG_WIDTH)) == TAG_WIDTH and _width(_pad("요청", TAG_WIDTH)) == TAG_WIDTH
    assert _clock(3_723_004) == "01:02:03.004"
    assert _wrap("aaa bbb ccc", 7) == ["aaa bbb", "ccc"] and _wrap("abcdefghij", 4) == ["abcd", "efgh", "ij"]
    # 미리 보기
    setup(logging.DEBUG)
    log = logging.getLogger("__main__")
    log.info("MNQ Trader 서버 실행 중: http://127.0.0.1:8775  (종료: Ctrl+C)")
    _flask_banner(False, "mnq.server")
    logging.getLogger("werkzeug").info("WARNING: This is a development server. Do not use it in a production deployment. Use a production WSGI server instead.\n * Running on http://127.0.0.1:8775")
    logging.getLogger("werkzeug").info('127.0.0.1 - - [15/Oct/2026 23:40:01] "GET /chart.html HTTP/1.1" 200 -')
    logging.getLogger("werkzeug").info('127.0.0.1 - - [15/Oct/2026 23:40:02] "GET /api/chart?interval=5 HTTP/1.1" 200 -')
    logging.getLogger("werkzeug").info('127.0.0.1 - - [15/Oct/2026 23:40:02] "GET /nothing HTTP/1.1" 404 -')
    success(logging.getLogger("mnq.paper"), "가상 매매 · 되돌림 + 추세: 없음 → 롱 2계약 @ 29,812.50 (노출 1.00배)")
    logging.getLogger("mnq.paper").warning("MNQ 시세를 받지 못했습니다(예시)")
    try:
        1 / 0
    except ZeroDivisionError:
        logging.getLogger("mnq.paper").exception("모의 운용 계산 실패")
