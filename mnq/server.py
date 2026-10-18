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


@app.get("/api/replay")
def api_replay():
    return jsonify(chart.replay())


@app.get("/api/live")
def api_live():
    return jsonify(live.get_live())


@app.get("/api/trading")
def api_trading():
    return jsonify(paper.get_trading())


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    paper.run()
    app.run(host=config.HOST, port=config.PORT, threaded=True)
