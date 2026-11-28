"""MoodTrack：個人情緒與壓力紀錄分析平台。

第二階段：使用者系統（註冊 / 登入 / 登出）與每日情緒紀錄。
"""

import os
import re
from decimal import ROUND_HALF_UP, Decimal
from datetime import date, datetime, timedelta
from pathlib import Path

from flask import Flask, abort, flash, redirect, render_template, request, url_for
from flask_login import (
    LoginManager,
    UserMixin,
    current_user,
    login_required,
    login_user,
    logout_user,
)
from flask_sqlalchemy import SQLAlchemy
from flask_wtf.csrf import CSRFError, CSRFProtect
from werkzeug.security import check_password_hash, generate_password_hash

import rag

BASE_DIR = Path(__file__).resolve().parent

app = Flask(__name__)

# 正式部署時用環境變數 SECRET_KEY 覆蓋，不要把真正的金鑰寫進程式碼
# （PythonAnywhere 在 WSGI 設定檔裡設定，見 DEPLOY.md）
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-secret-key")
# Cookie 只能由伺服器讀取，且不會在跨站請求時送出
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["REMEMBER_COOKIE_HTTPONLY"] = True
app.config["REMEMBER_COOKIE_SAMESITE"] = "Lax"
# 部署在 HTTPS 上時設 COOKIE_SECURE=1，Cookie 只會透過加密連線傳送
if os.environ.get("COOKIE_SECURE") == "1":
    app.config["SESSION_COOKIE_SECURE"] = True
    app.config["REMEMBER_COOKIE_SECURE"] = True
# 資料庫放在專案根目錄的 moodtrack.db（*.db 已被 .gitignore 排除）
# 自動化測試會用環境變數 DATABASE_URL 換成暫時的資料庫，不會動到真正的資料
app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get(
    "DATABASE_URL", f"sqlite:///{BASE_DIR / 'moodtrack.db'}"
)
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)

# CSRF 保護：每個 POST 表單都要帶 csrf_token，防止其他網站冒用登入狀態送出表單
csrf = CSRFProtect(app)

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


class Feedback(db.Model):
    """使用者送出的問題回報；管理員可以更新狀態並回覆。"""

    __tablename__ = "feedback"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    category = db.Column(db.String(20), nullable=False)
    title = db.Column(db.String(100), nullable=False)
    content = db.Column(db.Text, nullable=False)
    status = db.Column(db.String(20), nullable=False, default="open")
    admin_reply = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)

    user = db.relationship("User", backref=db.backref("feedback", cascade="all, delete-orphan"))


class HelpQuestion(db.Model):
    """說明小幫手的提問紀錄：用來找出說明文件不足的地方。"""

    __tablename__ = "help_questions"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    question = db.Column(db.String(300), nullable=False)
    mode = db.Column(db.String(20), nullable=False)  # llm / retrieval / none
    created_at = db.Column(db.DateTime, default=datetime.now)

    user = db.relationship("User", backref=db.backref("help_questions", cascade="all, delete-orphan"))


FEEDBACK_CATEGORIES = {
    "bug": "系統錯誤",
    "account": "帳號問題",
    "question": "使用問題",
    "suggestion": "功能建議",
    "other": "其他",
}
FEEDBACK_STATUS = {
    "open": "待處理",
    "in_progress": "處理中",
    "resolved": "已解決",
}


def is_admin(user) -> bool:
    """管理員由環境變數 ADMIN_EMAILS 指定（多個用逗號分隔），不存在資料庫裡。"""
    admins = {
        e.strip().lower()
        for e in os.environ.get("ADMIN_EMAILS", "").split(",")
        if e.strip()
    }
    return bool(getattr(user, "is_authenticated", False)) and user.email.lower() in admins


@app.context_processor
def inject_today():
    return {
        "today_iso": date.today().isoformat(),
        "is_admin": is_admin(current_user),
        "feedback_categories": FEEDBACK_CATEGORIES,
        "feedback_status": FEEDBACK_STATUS,
    }


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


RANGE_OPTIONS = (7, 30, 90)


def _round1(number):
    """四捨五入到小數第一位（4.25 → 4.3）。

    Python 內建 round() 會受浮點誤差與「銀行家捨入」影響（round(4.25, 1) 是 4.2），
    和手算結果不同，所以改用 Decimal 做一般的四捨五入。
    """
    # 先收掉浮點誤差（例如 6.8499999999 其實是 6.85）再四捨五入
    exact = Decimal(str(round(number, 9)))
    return float(exact.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


def _average(values):
    values = [v for v in values if v is not None]
    return _round1(sum(values) / len(values)) if values else None


def _compare(records, condition, yes_label, no_label):
    """比較符合 / 不符合條件的日子，平均心情差多少；兩組都要有資料才回傳。"""
    yes = [r.mood_score for r in records if condition(r)]
    no = [r.mood_score for r in records if not condition(r)]
    if not yes or not no:
        return None
    return {
        "yes_label": yes_label,
        "no_label": no_label,
        "yes_avg": _average(yes),
        "no_avg": _average(no),
        "yes_count": len(yes),
        "no_count": len(no),
        "diff": _round1(_average(yes) - _average(no)),
    }


def _streaks(record_dates, today):
    """回傳 (目前連續天數, 最長連續天數)。今天還沒記錄時，從昨天往回算。"""
    dates = set(record_dates)
    current = 0
    cursor = today if today in dates else today - timedelta(days=1)
    while cursor in dates:
        current += 1
        cursor -= timedelta(days=1)

    longest = run = 0
    previous = None
    for d in sorted(dates):
        run = run + 1 if previous and d - previous == timedelta(days=1) else 1
        longest = max(longest, run)
        previous = d
    return current, longest


def _mood_calendar(records_by_date, today, weeks=5):
    """最近幾週的心情日曆：週一開始，每格是一天。"""
    end = today + timedelta(days=6 - today.weekday())  # 這週日
    start = end - timedelta(days=weeks * 7 - 1)
    rows = []
    for w in range(weeks):
        row = []
        for d in range(7):
            day_ = start + timedelta(days=w * 7 + d)
            r = records_by_date.get(day_)
            row.append({
                "date": day_,
                "record": r,
                "future": day_ > today,
                "today": day_ == today,
            })
        rows.append(row)
    return rows


def _greeting(now):
    hour = now.hour
    if 5 <= hour < 11:
        return "☀️", "早安"
    if 11 <= hour < 18:
        return "🌤️", "午安"
    return "🌙", "晚安"


@app.route("/dashboard")
@login_required
def dashboard():
    days = request.args.get("days", type=int)
    if days not in RANGE_OPTIONS:
        days = 7  # 預設最近 7 天（今天 + 過去 6 天）
    today = date.today()
    start = today - timedelta(days=days - 1)

    # 只查目前使用者的資料
    user_records = MoodRecord.query.filter_by(user_id=current_user.id)
    latest_record = user_records.order_by(MoodRecord.record_date.desc()).first()
    today_record = (
        latest_record
        if latest_record and latest_record.record_date == today
        else None
    )
    total_records = user_records.count()

    # 區間內的紀錄（給圖表與統計用）
    in_range = (
        MoodRecord.query.filter(
            MoodRecord.user_id == current_user.id,
            MoodRecord.record_date >= start,
            MoodRecord.record_date <= today,
        )
        .order_by(MoodRecord.record_date)
        .all()
    )
    by_date = {r.record_date: r for r in in_range}

    # 每天一格，沒紀錄的日子是 None → 圖上留空，不假裝有資料
    chart = {"labels": [], "dates": [], "mood": [], "stress": [], "sleep": [], "exercise": []}
    for offset in range(days):
        day = start + timedelta(days=offset)
        r = by_date.get(day)
        chart["labels"].append(f"{day.month}/{day.day}")
        chart["dates"].append(day.isoformat())
        chart["mood"].append(r.mood_score if r else None)
        chart["stress"].append(r.stress_score if r else None)
        chart["sleep"].append(r.sleep_hours if r else None)
        chart["exercise"].append(r.exercise_minutes if r else None)

    stats = {
        "total_records": total_records,
        "recent_count": len(in_range),
        "mood": _average([r.mood_score for r in in_range]),
        "stress": _average([r.stress_score for r in in_range]),
        "sleep": _average([r.sleep_hours for r in in_range]),
        "exercise_total": sum(r.exercise_minutes for r in in_range),
    }
    all_dates = [
        d for (d,) in db.session.query(MoodRecord.record_date)
        .filter(MoodRecord.user_id == current_user.id)
        .all()
    ]
    streak, longest_streak = _streaks(all_dates, today)
    calendar_start = today - timedelta(days=40)
    calendar_records = {
        r.record_date: r
        for r in MoodRecord.query.filter(
            MoodRecord.user_id == current_user.id,
            MoodRecord.record_date >= calendar_start,
        )
    }

    insights = [
        i
        for i in (
            _compare(in_range, lambda r: r.sleep_hours >= 7, "睡滿 7 小時", "睡不到 7 小時"),
            _compare(in_range, lambda r: r.exercise_minutes > 0, "有運動", "沒運動"),
        )
        if i
    ]

    return render_template(
        "dashboard.html",
        latest_record=latest_record,
        today_record=today_record,
        days=days,
        range_options=RANGE_OPTIONS,
        chart=chart,
        stats=stats,
        insights=insights,
        streak=streak,
        longest_streak=longest_streak,
        calendar=_mood_calendar(calendar_records, today),
        greeting=_greeting(datetime.now()),
    )


def _parse_record_form(form, format_error="請確認日期、分數與時間格式正確。"):
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
        return None, format_error

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
        return redirect(_safe_next(request.args.get("next")) or url_for("dashboard"))

    # 從心情日曆點進來時會帶 ?date=YYYY-MM-DD，表單直接預填那一天
    requested = _parse_date(request.args.get("date", "").strip())
    target = requested or date.today()
    if target > date.today():
        flash("不能記錄未來的日期。", "error")
        target, requested = date.today(), None
    if requested:
        # 明確指定的那天已經記錄過了，直接帶去編輯。
        # 沒指定日期時不跳轉：使用者可能是想改日期補記別天，不能把他帶去編輯今天那筆。
        existing = MoodRecord.query.filter_by(
            user_id=current_user.id, record_date=requested
        ).first()
        if existing:
            return redirect(url_for(
                "edit_record", record_id=existing.id, next=request.args.get("next")
            ))

    return render_template(
        "record_form.html",
        values={"record_date": target.isoformat()},
        mood_levels=MOOD_LEVELS,
    )


RECORDS_PER_PAGE = 20


def _parse_date(text):
    try:
        return date.fromisoformat(text) if text else None
    except ValueError:
        return None


@app.route("/records")
@login_required
def records():
    """歷史情緒紀錄：只查目前使用者的紀錄，依日期由新到舊，可依條件篩選。"""
    start = _parse_date(request.args.get("start", "").strip())
    end = _parse_date(request.args.get("end", "").strip())
    min_mood = request.args.get("min_mood", type=int)
    max_stress = request.args.get("max_stress", type=int)
    min_stress = request.args.get("min_stress", type=int)

    query = MoodRecord.query.filter_by(user_id=current_user.id)
    if start:
        query = query.filter(MoodRecord.record_date >= start)
    if end:
        query = query.filter(MoodRecord.record_date <= end)
    if min_mood is not None:
        query = query.filter(MoodRecord.mood_score >= min_mood)
    if max_stress is not None:
        query = query.filter(MoodRecord.stress_score <= max_stress)
    if min_stress is not None:
        query = query.filter(MoodRecord.stress_score >= min_stress)

    filters = {
        "start": start.isoformat() if start else "",
        "end": end.isoformat() if end else "",
        "min_mood": min_mood,
        "max_stress": max_stress,
        "min_stress": min_stress,
    }
    filtering = any(v not in ("", None) for v in filters.values())
    page = query.order_by(MoodRecord.record_date.desc()).paginate(
        page=request.args.get("page", 1, type=int),
        per_page=RECORDS_PER_PAGE,
        error_out=False,
    )
    total = MoodRecord.query.filter_by(user_id=current_user.id).count()
    # 換頁時保留篩選條件
    page_args = {k: v for k, v in filters.items() if v not in ("", None)}
    return render_template(
        "records.html",
        records=page.items,
        page=page,
        page_args=page_args,
        filters=filters,
        filtering=filtering,
        total=total,
    )


def _summary(records):
    """一組紀錄的平均值；沒有資料時回傳 None。"""
    if not records:
        return None
    return {
        "count": len(records),
        "mood": _average([r.mood_score for r in records]),
        "stress": _average([r.stress_score for r in records]),
        "sleep": _average([r.sleep_hours for r in records]),
        "exercise": _average([r.exercise_minutes for r in records]),
    }


def _extreme(records, key, highest=True):
    """最高（或最低）的一天；同分時取最近的一天。"""
    if not records:
        return None
    ordered = sorted(records, key=lambda r: r.record_date, reverse=True)
    return (max if highest else min)(ordered, key=key)


@app.route("/analysis")
@login_required
def analysis():
    """資料分析：不同期間平均、最高最低紀錄、運動與壓力、睡眠與心情。"""
    all_records = (
        MoodRecord.query.filter_by(user_id=current_user.id)
        .order_by(MoodRecord.record_date)
        .all()
    )
    today = date.today()
    week_start = today - timedelta(days=6)
    month_start = today.replace(day=1)

    periods = [
        ("最近 7 天", _summary([r for r in all_records if week_start <= r.record_date <= today])),
        (f"本月（{today.month} 月）", _summary([r for r in all_records if month_start <= r.record_date <= today])),
        ("全部紀錄", _summary(all_records)),
    ]

    extremes = [
        ("😄", "心情最高的一天", _extreme(all_records, lambda r: r.mood_score), "mood"),
        ("😢", "心情最低的一天", _extreme(all_records, lambda r: r.mood_score, highest=False), "mood"),
        ("🔥", "壓力最高的一天", _extreme(all_records, lambda r: r.stress_score), "stress"),
        ("😴", "睡眠最長的一天", _extreme(all_records, lambda r: r.sleep_hours), "sleep"),
        ("🥱", "睡眠最短的一天", _extreme(all_records, lambda r: r.sleep_hours, highest=False), "sleep"),
    ]

    exercised = [r for r in all_records if r.exercise_minutes > 0]
    rested = [r for r in all_records if r.exercise_minutes == 0]
    exercise_compare = {
        "with": _summary(exercised),
        "without": _summary(rested),
    }
    if exercise_compare["with"] and exercise_compare["without"]:
        exercise_compare["stress_diff"] = _round1(
            exercise_compare["without"]["stress"] - exercise_compare["with"]["stress"]
        )

    scatter = [
        {"x": r.sleep_hours, "y": r.mood_score, "date": r.record_date.isoformat()}
        for r in all_records
    ]

    return render_template(
        "analysis.html",
        total=len(all_records),
        periods=periods,
        extremes=extremes,
        exercise_compare=exercise_compare,
        scatter=scatter,
        correlation=_correlation([r.sleep_hours for r in all_records],
                                 [r.mood_score for r in all_records]),
    )


def _correlation(xs, ys):
    """皮爾森相關係數；資料少於 3 筆或沒有變化時回傳 None。"""
    n = len(xs)
    if n < 3:
        return None
    mean_x, mean_y = sum(xs) / n, sum(ys) / n
    cov = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    var_x = sum((x - mean_x) ** 2 for x in xs)
    var_y = sum((y - mean_y) ** 2 for y in ys)
    if var_x == 0 or var_y == 0:
        return None
    return round(cov / (var_x * var_y) ** 0.5, 2)


@app.route("/records/<int:record_id>/edit", methods=["GET", "POST"])
@login_required
def edit_record(record_id: int):
    record = MoodRecord.query.filter_by(
        id=record_id, user_id=current_user.id
    ).first_or_404()

    if request.method == "POST":
        data, error = _parse_record_form(
            request.form, format_error="請確認所有資料格式正確。"
        )
        if error is None and _date_taken(data["record_date"], exclude_id=record.id):
            error = "這一天已經有其他紀錄。"

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
        flash("情緒紀錄更新成功。", "success")
        return redirect(_safe_next(request.args.get("next")) or url_for("records"))

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
    # 只找得到自己的紀錄；改網址刪別人的會得到 404
    record = MoodRecord.query.filter_by(
        id=record_id, user_id=current_user.id
    ).first_or_404()

    db.session.delete(record)
    db.session.commit()

    flash("情緒紀錄已刪除。", "success")
    return redirect(_safe_next(request.form.get("next")) or url_for("records"))


# ---------------------------------------------------------------------------
# 說明與回報
# ---------------------------------------------------------------------------

SUGGESTED_QUESTIONS = [
    "怎麼補記以前的心情？",
    "怎麼刪除一筆紀錄？",
    "為什麼出現「表單已過期」？",
    "管理員看得到我的心情紀錄嗎？",
    "平均值是怎麼算的？",
    "忘記密碼怎麼辦？",
]


@app.route("/help")
@login_required
def help_center():
    question = request.args.get("q", "").strip()[:300]
    result = None
    if question:
        result = rag.answer_question(question)
        db.session.add(HelpQuestion(user_id=current_user.id, question=question, mode=result.mode))
        db.session.commit()
    my_feedback = (
        Feedback.query.filter_by(user_id=current_user.id)
        .order_by(Feedback.created_at.desc())
        .limit(5)
        .all()
    )
    return render_template(
        "help.html",
        question=question,
        result=result,
        suggestions=SUGGESTED_QUESTIONS,
        my_feedback=my_feedback,
        llm_model=rag.OLLAMA_MODEL,
    )


@app.route("/feedback")
@login_required
def feedback_list():
    items = (
        Feedback.query.filter_by(user_id=current_user.id)
        .order_by(Feedback.created_at.desc())
        .all()
    )
    return render_template("feedback_list.html", items=items)


@app.route("/feedback/new", methods=["GET", "POST"])
@login_required
def feedback_new():
    values = {
        "category": request.args.get("category", "question"),
        "title": request.args.get("title", "")[:100],
        "content": request.args.get("content", "")[:2000],
    }
    if request.method == "POST":
        values = {k: request.form.get(k, "").strip() for k in ("category", "title", "content")}
        error = None
        if values["category"] not in FEEDBACK_CATEGORIES:
            error = "請選擇問題類型。"
        elif not values["title"] or not values["content"]:
            error = "請填寫標題與內容。"
        elif len(values["title"]) > 100 or len(values["content"]) > 2000:
            error = "標題最多 100 字，內容最多 2000 字。"
        if error:
            flash(error, "error")
        else:
            db.session.add(Feedback(user_id=current_user.id, **values))
            db.session.commit()
            flash("已送出回報，管理員回覆後會顯示在「我的回報」。", "success")
            return redirect(url_for("feedback_list"))
    return render_template("feedback_form.html", values=values)


# ---------------------------------------------------------------------------
# 管理頁：整體使用統計（不顯示任何人的心情內容）與回報處理
# ---------------------------------------------------------------------------


def _admin_required():
    if not is_admin(current_user):
        abort(404)  # 不讓一般使用者知道有這個頁面


@app.route("/admin")
@login_required
def admin():
    _admin_required()
    today = date.today()
    week_ago = today - timedelta(days=6)
    week_ago_dt = datetime.combine(week_ago, datetime.min.time())

    total_users = User.query.count()
    total_records = MoodRecord.query.count()
    active_users = (
        db.session.query(MoodRecord.user_id)
        .filter(MoodRecord.created_at >= week_ago_dt)
        .distinct()
        .count()
    )
    stats = {
        "total_users": total_users,
        "new_users": User.query.filter(User.created_at >= week_ago_dt).count(),
        "active_users": active_users,
        "total_records": total_records,
        "records_week": MoodRecord.query.filter(MoodRecord.created_at >= week_ago_dt).count(),
        "avg_records": round(total_records / total_users, 1) if total_users else 0,
        "questions": HelpQuestion.query.count(),
        "open_feedback": Feedback.query.filter(Feedback.status != "resolved").count(),
    }

    # 最近 30 天每天新增的紀錄數（依建立時間）
    start = today - timedelta(days=29)
    counts = dict(
        db.session.query(db.func.date(MoodRecord.created_at), db.func.count())
        .filter(MoodRecord.created_at >= datetime.combine(start, datetime.min.time()))
        .group_by(db.func.date(MoodRecord.created_at))
        .all()
    )
    daily = [
        {"label": f"{d.month}/{d.day}", "count": counts.get(d.isoformat(), 0)}
        for d in (start + timedelta(days=i) for i in range(30))
    ]

    status_filter = request.args.get("status", "unresolved")
    fq = Feedback.query
    if status_filter == "unresolved":
        fq = fq.filter(Feedback.status != "resolved")
    elif status_filter in FEEDBACK_STATUS:
        fq = fq.filter_by(status=status_filter)
    feedback_items = fq.order_by(Feedback.created_at.desc()).limit(50).all()

    # 說明文件找不到答案的提問：可以據此補充說明文件
    unanswered = (
        HelpQuestion.query.filter_by(mode="none")
        .order_by(HelpQuestion.created_at.desc())
        .limit(20)
        .all()
    )
    return render_template(
        "admin.html",
        stats=stats,
        daily=daily,
        feedback_items=feedback_items,
        status_filter=status_filter,
        unanswered=unanswered,
    )


@app.post("/admin/feedback/<int:feedback_id>")
@login_required
def admin_feedback_update(feedback_id: int):
    _admin_required()
    item = db.get_or_404(Feedback, feedback_id)
    status = request.form.get("status", item.status)
    if status in FEEDBACK_STATUS:
        item.status = status
    reply = request.form.get("admin_reply", "").strip()[:2000]
    item.admin_reply = reply or None
    db.session.commit()
    flash(f"已更新回報 #{item.id}。", "success")
    return redirect(url_for("admin", status=request.args.get("status", "unresolved")))


# ---------------------------------------------------------------------------
# 錯誤頁面
# ---------------------------------------------------------------------------


@app.errorhandler(404)
def page_not_found(error):
    return (
        render_template(
            "error.html", title="找不到頁面", message="你要尋找的頁面不存在。"
        ),
        404,
    )


@app.errorhandler(CSRFError)
def csrf_error(error):
    return (
        render_template(
            "error.html",
            title="表單已過期",
            message="這個表單已經過期或無效，請重新整理頁面後再送出一次。",
        ),
        400,
    )


@app.errorhandler(500)
def internal_server_error(error):
    db.session.rollback()
    return (
        render_template(
            "error.html", title="系統錯誤", message="系統發生錯誤，請稍後再試。"
        ),
        500,
    )


if __name__ == "__main__":
    # 只在自己電腦上用 python app.py 執行時會跑到這裡；
    # 部署到 PythonAnywhere 時由 WSGI 載入 app，不會開啟除錯模式。
    # 想關掉除錯模式可設環境變數 FLASK_DEBUG=0
    app.run(debug=os.environ.get("FLASK_DEBUG", "1") == "1")
