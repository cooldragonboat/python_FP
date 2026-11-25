"""MoodTrack：個人情緒與壓力紀錄分析平台。

第二階段：使用者系統（註冊 / 登入 / 登出）與每日情緒紀錄。
"""

import os
import re
from datetime import date, datetime
from pathlib import Path

from flask import Flask, flash, redirect, render_template, request, url_for
from flask_login import (
    LoginManager,
    UserMixin,
    current_user,
    login_required,
    login_user,
    logout_user,
)
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import check_password_hash, generate_password_hash

BASE_DIR = Path(__file__).resolve().parent

app = Flask(__name__)

# 正式部署時用環境變數 SECRET_KEY 覆蓋，不要把真正的金鑰寫進程式碼
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-secret-key")
# 資料庫放在專案根目錄的 moodtrack.db（*.db 已被 .gitignore 排除）
app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{BASE_DIR / 'moodtrack.db'}"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)

login_manager = LoginManager(app)
login_manager.login_view = "login"
login_manager.login_message = "請先登入才能使用這個頁面。"

# 心情分數 1~10：(最低分數, 表情, 名稱)，由高到低比對
MOOD_LEVELS = [
    (9, "😄", "超開心"),
    (7, "🙂", "不錯"),
    (5, "😐", "普通"),
    (3, "😢", "難過"),
    (1, "😠", "很糟"),
]


def mood_face(score: int) -> tuple[str, str]:
    """把 1~10 的心情分數轉成 (表情, 名稱)。"""
    for low, emoji, label in MOOD_LEVELS:
        if score >= low:
            return emoji, label
    return "❔", "未知"


# ---------------------------------------------------------------------------
# 資料模型
# ---------------------------------------------------------------------------


class User(UserMixin, db.Model):
    """使用者帳號。密碼只存雜湊值，不存明文。"""

    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.now)

    mood_records = db.relationship(
        "MoodRecord", backref="user", lazy=True, cascade="all, delete-orphan"
    )

    def set_password(self, password: str) -> None:
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)


class MoodRecord(db.Model):
    """每日情緒與壓力紀錄：每位使用者每天一筆，同一天再存會更新。"""

    __tablename__ = "mood_records"
    __table_args__ = (
        db.UniqueConstraint("user_id", "record_date", name="uq_user_record_date"),
    )

    id = db.Column(db.Integer, primary_key=True)
    record_date = db.Column(db.Date, nullable=False)
    mood_score = db.Column(db.Integer, nullable=False)  # 心情 1~10
    stress_score = db.Column(db.Integer, nullable=False)  # 壓力 1~10
    sleep_hours = db.Column(db.Float, nullable=False)
    exercise_minutes = db.Column(db.Integer, nullable=False)
    note = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.now)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)

    @property
    def emoji(self) -> str:
        return mood_face(self.mood_score)[0]

    @property
    def mood_label(self) -> str:
        return mood_face(self.mood_score)[1]


@login_manager.user_loader
def load_user(user_id: str):
    return db.session.get(User, int(user_id))


with app.app_context():
    db.create_all()


# ---------------------------------------------------------------------------
# 工具函式
# ---------------------------------------------------------------------------

EMAIL_PATTERN = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")


def _int_in_range(value, low, high):
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if low <= number <= high else None


def _float_in_range(value, low, high):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if low <= number <= high else None


def _safe_next(target):
    """只允許站內相對路徑，避免登入後被導到外部網站。"""
    if target and target.startswith("/") and not target.startswith("//"):
        return target
    return None


# ---------------------------------------------------------------------------
# 頁面
# ---------------------------------------------------------------------------


@app.route("/")
def index():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))
    return render_template("index.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        confirm = request.form.get("confirm", "")

        error = None
        if not username or not email or not password or not confirm:
            error = "請完整填寫所有欄位。"
        elif not 3 <= len(username) <= 30:
            error = "使用者名稱長度需為 3～30 個字。"
        elif not EMAIL_PATTERN.fullmatch(email) or len(email) > 120:
            error = "請輸入有效的電子郵件。"
        elif len(password) < 6:
            error = "密碼至少需要 6 個字元。"
        elif password != confirm:
            error = "兩次輸入的密碼不一致。"
        elif User.query.filter_by(username=username).first():
            error = "這個使用者名稱已經有人使用了。"
        elif User.query.filter_by(email=email).first():
            error = "這個電子郵件已經註冊過了。"

        if error:
            flash(error, "error")
        else:
            user = User(username=username, email=email)
            user.set_password(password)
            db.session.add(user)
            db.session.commit()
            flash(f"註冊成功，歡迎 {username}！請登入開始記錄。", "success")
            return redirect(url_for("login"))

    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        account = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user = User.query.filter(
            (User.username == account) | (User.email == account.lower())
        ).first()

        if user is None or not user.check_password(password):
            flash("使用者名稱／電子郵件或密碼錯誤。", "error")
        else:
            login_user(user, remember=bool(request.form.get("remember")))
            flash(f"歡迎回來，{user.username}！", "success")
            return redirect(_safe_next(request.args.get("next")) or url_for("dashboard"))

    return render_template("login.html")


@app.route("/logout", methods=["POST"])
@login_required
def logout():
    logout_user()
    flash("已登出，明天見！", "success")
    return redirect(url_for("index"))


@app.route("/dashboard")
@login_required
def dashboard():
    today_record = MoodRecord.query.filter_by(
        user_id=current_user.id, record_date=date.today()
    ).first()
    records = (
        MoodRecord.query.filter_by(user_id=current_user.id)
        .order_by(MoodRecord.record_date.desc())
        .limit(30)
        .all()
    )
    return render_template(
        "dashboard.html",
        mood_levels=MOOD_LEVELS,
        today=date.today(),
        today_record=today_record,
        records=records,
    )


@app.post("/records")
@login_required
def save_record():
    record_date = request.form.get("date") or date.today().isoformat()
    try:
        record_date = date.fromisoformat(record_date)
    except ValueError:
        record_date = None

    mood_score = _int_in_range(request.form.get("mood_score"), 1, 10)
    stress_score = _int_in_range(request.form.get("stress_score"), 1, 10)
    sleep_hours = _float_in_range(request.form.get("sleep_hours") or 0, 0, 24)
    exercise_minutes = _int_in_range(request.form.get("exercise_minutes") or 0, 0, 1440)
    note = request.form.get("note", "").strip()[:500]

    if record_date is None or record_date > date.today():
        flash("日期不正確（不能選未來的日期）。", "error")
    elif None in (mood_score, stress_score, sleep_hours, exercise_minutes):
        flash("請確認心情、壓力、睡眠與運動欄位都填寫正確。", "error")
    else:
        record = MoodRecord.query.filter_by(
            user_id=current_user.id, record_date=record_date
        ).first()
        is_new = record is None
        if is_new:
            record = MoodRecord(user_id=current_user.id, record_date=record_date)
            db.session.add(record)
        record.mood_score = mood_score
        record.stress_score = stress_score
        record.sleep_hours = sleep_hours
        record.exercise_minutes = exercise_minutes
        record.note = note or None
        db.session.commit()
        flash("紀錄已新增！" if is_new else "已更新這一天的紀錄。", "success")

    return redirect(url_for("dashboard"))


@app.post("/records/<int:record_id>/delete")
@login_required
def delete_record(record_id: int):
    record = MoodRecord.query.filter_by(id=record_id, user_id=current_user.id).first()
    if record:
        db.session.delete(record)
        db.session.commit()
        flash("紀錄已刪除。", "success")
    return redirect(url_for("dashboard"))


if __name__ == "__main__":
    app.run(debug=True)
