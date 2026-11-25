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
login_manager.login_message = "請先登入才能使用此功能。"

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


@app.context_processor
def inject_today():
    return {"today_iso": date.today().isoformat()}


@login_manager.user_loader
def load_user(user_id: str):
    return db.session.get(User, int(user_id))


with app.app_context():
    db.create_all()


# ---------------------------------------------------------------------------
# 工具函式
# ---------------------------------------------------------------------------

EMAIL_PATTERN = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")


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
        elif User.query.filter(
            (User.username == username) | (User.email == email)
        ).first():
            error = "使用者名稱或電子郵件已經被使用。"

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
        # 依教學以電子郵件登入；同時接受使用者名稱，方便記不得信箱時使用
        account = request.form.get("email", "").strip()
        password = request.form.get("password", "")
        user = User.query.filter(
            (User.email == account.lower()) | (User.username == account)
        ).first()

        if user is None or not user.check_password(password):
            flash("電子郵件或密碼錯誤。", "error")
        else:
            login_user(user, remember=bool(request.form.get("remember")))
            flash("登入成功。", "success")
            return redirect(_safe_next(request.args.get("next")) or url_for("dashboard"))

    return render_template("login.html")


@app.route("/logout", methods=["GET", "POST"])
@login_required
def logout():
    logout_user()
    flash("你已經登出。", "success")
    return redirect(url_for("index"))


@app.route("/dashboard")
@login_required
def dashboard():
    records = (
        MoodRecord.query.filter_by(user_id=current_user.id)
        .order_by(MoodRecord.record_date.desc())
        .limit(5)
        .all()
    )
    today_record = next((r for r in records if r.record_date == date.today()), None)
    total = MoodRecord.query.filter_by(user_id=current_user.id).count()
    return render_template(
        "dashboard.html", records=records, today_record=today_record, total=total
    )


def _parse_record_form(form):
    """檢查每日紀錄表單，回傳 (資料 dict, 錯誤訊息)；成功時錯誤訊息為 None。"""
    record_date_text = form.get("record_date", "").strip()
    mood_score_text = form.get("mood_score", "").strip()
    stress_score_text = form.get("stress_score", "").strip()
    sleep_hours_text = form.get("sleep_hours", "").strip()
    exercise_minutes_text = form.get("exercise_minutes", "").strip()
    note = form.get("note", "").strip()[:1000]

    if not all(
        [
            record_date_text,
            mood_score_text,
            stress_score_text,
            sleep_hours_text,
            exercise_minutes_text,
        ]
    ):
        return None, "請完整填寫必要欄位。"

    try:
        record_date = datetime.strptime(record_date_text, "%Y-%m-%d").date()
        mood_score = int(mood_score_text)
        stress_score = int(stress_score_text)
        sleep_hours = float(sleep_hours_text)
        exercise_minutes = int(exercise_minutes_text)
    except ValueError:
        return None, "請確認日期、分數與時間格式正確。"

    if record_date > date.today():
        return None, "紀錄日期不能是未來的日期。"
    if not 1 <= mood_score <= 10:
        return None, "心情分數必須介於 1 到 10。"
    if not 1 <= stress_score <= 10:
        return None, "壓力分數必須介於 1 到 10。"
    if not 0 <= sleep_hours <= 24:
        return None, "睡眠時間必須介於 0 到 24 小時。"
    if not 0 <= exercise_minutes <= 1440:
        return None, "運動時間必須介於 0 到 1440 分鐘。"

    return {
        "record_date": record_date,
        "mood_score": mood_score,
        "stress_score": stress_score,
        "sleep_hours": sleep_hours,
        "exercise_minutes": exercise_minutes,
        "note": note or None,
    }, None


def _date_taken(record_date, exclude_id=None):
    """這位使用者在這一天是否已經有其他紀錄。"""
    query = MoodRecord.query.filter_by(user_id=current_user.id, record_date=record_date)
    if exclude_id is not None:
        query = query.filter(MoodRecord.id != exclude_id)
    return query.first() is not None


@app.route("/records/new", methods=["GET", "POST"])
@login_required
def new_record():
    if request.method == "POST":
        data, error = _parse_record_form(request.form)
        if error is None and _date_taken(data["record_date"]):
            error = "這一天已經有紀錄，請選擇其他日期。"

        if error:
            flash(error, "error")
            return render_template(
                "record_form.html", values=request.form, mood_levels=MOOD_LEVELS
            )

        db.session.add(MoodRecord(user_id=current_user.id, **data))
        db.session.commit()
        flash("情緒紀錄新增成功。", "success")
        return redirect(url_for("dashboard"))

    return render_template(
        "record_form.html",
        values={"record_date": date.today().isoformat()},
        mood_levels=MOOD_LEVELS,
    )


@app.route("/records")
@login_required
def records():
    """歷史情緒紀錄：只查目前使用者的紀錄，依日期由新到舊。"""
    user_records = (
        MoodRecord.query.filter_by(user_id=current_user.id)
        .order_by(MoodRecord.record_date.desc())
        .all()
    )
    return render_template("records.html", records=user_records)


@app.route("/records/<int:record_id>/edit", methods=["GET", "POST"])
@login_required
def edit_record(record_id: int):
    record = MoodRecord.query.filter_by(
        id=record_id, user_id=current_user.id
    ).first_or_404()

    if request.method == "POST":
        data, error = _parse_record_form(request.form)
        if error is None and _date_taken(data["record_date"], exclude_id=record.id):
            error = "這一天已經有紀錄，請選擇其他日期。"

        if error:
            flash(error, "error")
            return render_template(
                "record_form.html",
                values=request.form,
                record=record,
                mood_levels=MOOD_LEVELS,
            )

        for key, value in data.items():
            setattr(record, key, value)
        db.session.commit()
        flash("情緒紀錄已更新。", "success")
        return redirect(_safe_next(request.args.get("next")) or url_for("dashboard"))

    values = {
        "record_date": record.record_date.isoformat(),
        "mood_score": record.mood_score,
        "stress_score": record.stress_score,
        "sleep_hours": "%g" % record.sleep_hours,
        "exercise_minutes": record.exercise_minutes,
        "note": record.note or "",
    }
    return render_template(
        "record_form.html", values=values, record=record, mood_levels=MOOD_LEVELS
    )


@app.post("/records/<int:record_id>/delete")
@login_required
def delete_record(record_id: int):
    record = MoodRecord.query.filter_by(id=record_id, user_id=current_user.id).first()
    if record:
        db.session.delete(record)
        db.session.commit()
        flash("情緒紀錄已刪除。", "success")
    return redirect(_safe_next(request.form.get("next")) or url_for("dashboard"))


if __name__ == "__main__":
    app.run(debug=True)
