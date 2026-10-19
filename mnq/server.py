"""로컬 웹 서버: 화면(web/)과 데이터 API.

    .venv\\Scripts\\python -m mnq.server   → http://127.0.0.1:8775
"""
import logging

from flask import Flask, jsonify, redirect, request
from werkzeug.exceptions import HTTPException

from . import chart, config, live, paper

app = Flask(__name__, static_folder=str(config.ROOT / "web"), static_url_path="")
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0  # 화면과 스크립트의 버전이 어긋나지 않게 매번 확인한다
app.json.sort_keys = False


@app.errorhandler(Exception)
def handle_error(e):
    if isinstance(e, HTTPException):
        return e
    app.logger.exception(e)
    return jsonify({"error": str(e)}), 500


@app.get("/")
def index():
    return redirect("/chart.html")


@app.get("/api/paper")
def api_paper():
    return jsonify(paper.get_paper())


@app.get("/api/chart")
def api_chart():
    key = request.args.get("interval", "1")
    if key not in chart.INTERVALS:
        raise ValueError(f"지원하지 않는 봉 단위: {key}")
    since = request.args.get("since")
    return jsonify(chart.get_chart(key, int(since) if since else None))


@app.get("/api/chart/older")
def api_chart_older():
    key = request.args.get("interval", "1")
    if key not in chart.INTERVALS:
        raise ValueError(f"지원하지 않는 봉 단위: {key}")
    return jsonify(chart.older(key))


@app.get("/api/signals")
def api_signals():
    return jsonify(chart.signals())


@app.get("/api/macd_mtf")
def api_macd_mtf():
    return jsonify(chart.macd_mtf())


@app.get("/api/replay")
def api_replay():
    data, key = chart.replay(), request.args.get("key")  # key: 그 후보만(스캘핑은 1분마다 새로 받으므로 가볍게)
    return jsonify({key: data.get(key, [])} if key else data)


@app.get("/api/live")
def api_live():
    return jsonify(live.get_live())


@app.get("/api/live/history")
def api_live_history():
    return jsonify(live.history())


@app.get("/api/account")
def api_account():
    return jsonify(live.account())


def _json():
    """주문 요청은 JSON만 받는다: 다른 사이트가 폼으로 몰래 보내는 요청(CSRF)은 브라우저가 JSON으로 보낼 수 없다."""
    if not request.is_json:
        raise ValueError("JSON 요청만 받습니다")
    return request.get_json() or {}


@app.post("/api/live/order")
def api_live_order():
    body = _json()
    return jsonify(live.manual(body.get("qty"), flip=body.get("flip") is True))


@app.post("/api/live/limit")
def api_live_limit():
    body = _json()
    return jsonify(live.limit_close(body.get("price"), body.get("qty")))


@app.post("/api/live/modify")
def api_live_modify():
    body = _json()
    return jsonify(live.modify_limit(str(body.get("number") or ""), body.get("price")))


@app.post("/api/live/cancel")
def api_live_cancel():
    _json()
    return jsonify({"canceled": live.cancel_pending()})


@app.post("/api/live/enabled")
def api_live_enabled():
    on = _json().get("on")
    if type(on) is not bool:
        raise ValueError("on은 true 또는 false여야 합니다")
    live.set_enabled(on)
    return jsonify(live.get_live())


@app.post("/api/live/candidate")
def api_live_candidate():
    live.set_candidate(_json().get("key"))
    return jsonify(live.get_live())


@app.post("/api/live/flatten")
def api_live_flatten():
    _json()
    live.flatten()
    return jsonify(live.get_live())


@app.get("/api/trading")
def api_trading():
    return jsonify({**paper.get_trading(), "live": live.candidate()})


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    paper.run()
    app.run(host=config.HOST, port=config.PORT, threaded=True)
