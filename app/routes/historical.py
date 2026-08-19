"""五年历史回测 API。"""
import json
import os

from flask import Blueprint, jsonify, request

from config import BASE_DIR


historical_bp = Blueprint("historical", __name__)
RESULT_PATH = os.path.join(BASE_DIR, "app", "data", "history_backtest.json")


@historical_bp.route("/historical")
def historical_data():
    if not os.path.exists(RESULT_PATH):
        return jsonify({"error": "尚未生成历史回测，请运行 python3 run_historical_backtest.py"}), 404
    with open(RESULT_PATH, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    scheme = request.args.get("scheme", "B").upper()
    if scheme not in payload.get("schemes", {}):
        scheme = "B"
    return jsonify({
        "generated_at": payload["generated_at"],
        "period": payload["period"],
        "initial_capital": payload["initial_capital"],
        "methodology": payload["methodology"],
        "quality": payload["quality"],
        "anomalies": payload["anomalies"],
        "scheme": scheme,
        **payload["schemes"][scheme],
    })
