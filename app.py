"""MoodTrack：記錄每天心情的 Flask 網站（第一階段：最小可執行版本）。"""

import os
from datetime import datetime
from pathlib import Path

from flask import Flask, redirect, render_template, request, url_for
from flask_sqlalchemy import SQLAlchemy

BASE_DIR = Path(__file__).resolve().parent

app = Flask(__name__)

# 正式部署時用環境變數 SECRET_KEY 覆蓋，不要把真正的金鑰寫進程式碼
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-secret-key")
# 資料庫放在專案根目錄的 moodtrack.db（*.db 已被 .gitignore 排除）
app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{BASE_DIR / 'moodtrack.db'}"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)

# 心情選項：代碼 -> (表情, 名稱)
MOODS = {
    "great": ("😄", "超開心"),
    "good": ("🙂", "不錯"),
    "okay": ("😐", "普通"),
    "sad": ("😢", "難過"),
    "angry": ("😠", "生氣"),
}


class MoodEntry(db.Model):
    """一筆心情紀錄。"""

    id = db.Column(db.Integer, primary_key=True)
    mood = db.Column(db.String(20), nullable=False)
    note = db.Column(db.String(200), nullable=False, default="")
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.now)

    @property
    def emoji(self) -> str:
        return MOODS.get(self.mood, ("❔", ""))[0]

    @property
    def label(self) -> str:
        return MOODS.get(self.mood, ("", "未知"))[1]


with app.app_context():
    db.create_all()


@app.route("/")
def index():
    entries = MoodEntry.query.order_by(MoodEntry.created_at.desc()).limit(20).all()
    return render_template("index.html", moods=MOODS, entries=entries)


@app.post("/add")
def add_entry():
    mood = request.form.get("mood", "")
    note = request.form.get("note", "").strip()[:200]
    if mood in MOODS:
        db.session.add(MoodEntry(mood=mood, note=note))
        db.session.commit()
    return redirect(url_for("index"))


if __name__ == "__main__":
    app.run(debug=True)
