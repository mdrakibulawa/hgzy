import os
from pathlib import Path
from flask import Flask, jsonify, render_template

from scraper import scrape_plans, analyze_big_small


def create_app() -> Flask:
    app = Flask(__name__)

    @app.get("/")
    def index():
        return render_template("index.html")

    @app.get("/api/plans")
    def api_plans():
        script_dir = Path(__file__).resolve().parent
        html_path = script_dir / "hgzy.html"
        data = scrape_plans(html_path)
        return jsonify(data)

    @app.get("/api/result")
    def api_result():
        script_dir = Path(__file__).resolve().parent
        html_path = script_dir / "hgzy.html"
        data = scrape_plans(html_path)
        analysis = analyze_big_small(data.get("items") or [])
        return jsonify({"result": analysis})

    return app


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app = create_app()
    app.run(host="0.0.0.0", port=port, debug=True)


