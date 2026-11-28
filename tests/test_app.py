"""MoodTrack 自動化測試：對應「系統完成標準」的每一項。

執行方式（在專案資料夾）：

    python -m unittest -v

測試使用記憶體中的暫時資料庫，不會動到 moodtrack.db。
"""

import os
import re
import subprocess
import unittest
from datetime import date, timedelta
from pathlib import Path

# 必須在匯入 app 之前設定，讓測試使用暫時資料庫
os.environ["DATABASE_URL"] = "sqlite://"

import rag  # noqa: E402
from app import Feedback, HelpQuestion, MoodRecord, User, app, db  # noqa: E402

PROJECT_DIR = Path(__file__).resolve().parent.parent


def day(days_ago=0):
    return (date.today() - timedelta(days=days_ago)).isoformat()


class MoodTrackTestCase(unittest.TestCase):
    def setUp(self):
        app.config["TESTING"] = True
        # 功能測試直接送表單，不帶 CSRF token；CSRF 另外在 CsrfTests 測
        app.config["WTF_CSRF_ENABLED"] = False
        with app.app_context():
            db.drop_all()
            db.create_all()

    # ---- 小工具 ----------------------------------------------------------

    def register(self, client, username, email, password="123456", confirm=None):
        return client.post(
            "/register",
            data={
                "username": username,
                "email": email,
                "password": password,
                "confirm": password if confirm is None else confirm,
            },
            follow_redirects=True,
        )

    def login(self, client, email, password="123456"):
        return client.post(
            "/login",
            data={"email": email, "password": password},
            follow_redirects=True,
        )

    def user_client(self, name):
        client = app.test_client()
        self.register(client, name, f"{name}@example.com")
        self.login(client, f"{name}@example.com")
        return client

    def add_record(self, client, record_date=None, **fields):
        data = {
            "record_date": record_date or day(0),
            "mood_score": "7",
            "stress_score": "5",
            "sleep_hours": "7",
            "exercise_minutes": "30",
            "note": "",
        }
        data.update({k: str(v) for k, v in fields.items()})
        return client.post("/records/new", data=data, follow_redirects=True)

    def text(self, response):
        return response.get_data(as_text=True)

    def record_id(self, **filters):
        with app.app_context():
            return MoodRecord.query.filter_by(**filters).one().id


class AccountTests(MoodTrackTestCase):
    """帳號功能"""

    def test_register_new_account(self):
        r = self.register(app.test_client(), "testuser", "test@example.com")
        self.assertEqual(r.request.path, "/login")
        self.assertIn("註冊成功", self.text(r))
        with app.app_context():
            self.assertEqual(User.query.count(), 1)

    def test_password_not_stored_in_plain_text(self):
        self.register(app.test_client(), "testuser", "test@example.com", "123456")
        with app.app_context():
            stored = User.query.one().password_hash
        self.assertNotIn("123456", stored)
        self.assertTrue(stored.startswith(("scrypt:", "pbkdf2:")))

    def test_register_rejects_blank_and_short_password(self):
        client = app.test_client()
        self.assertIn("請完整填寫所有欄位。", self.text(self.register(client, "", "", "")))
        self.assertIn(
            "密碼至少需要 6 個字元。",
            self.text(self.register(client, "testuser", "t@example.com", "123")),
        )

    def test_duplicate_account_rejected(self):
        client = app.test_client()
        self.register(client, "testuser", "test@example.com")
        for username, email in [("testuser", "new@example.com"), ("newuser", "TEST@example.com")]:
            r = self.register(client, username, email)
            self.assertIn("使用者名稱或電子郵件已經被使用。", self.text(r))
        with app.app_context():
            self.assertEqual(User.query.count(), 1)

    def test_login_success(self):
        client = app.test_client()
        self.register(client, "testuser", "test@example.com")
        r = self.login(client, "test@example.com")
        self.assertEqual(r.request.path, "/dashboard")
        self.assertIn("歡迎，testuser！", self.text(r))

    def test_wrong_password_rejected(self):
        client = app.test_client()
        self.register(client, "testuser", "test@example.com")
        r = self.login(client, "test@example.com", "wrong-password")
        self.assertIn("電子郵件或密碼錯誤。", self.text(r))
        self.assertEqual(client.get("/dashboard").status_code, 302)

    def test_logout(self):
        client = self.user_client("testuser")
        r = client.get("/logout", follow_redirects=True)
        self.assertEqual(r.request.path, "/")
        self.assertIn("你已經登出。", self.text(r))
        self.assertEqual(client.get("/dashboard").status_code, 302)

    def test_protected_pages_require_login(self):
        client = app.test_client()
        for path in ["/dashboard", "/records", "/records/new", "/analysis", "/records/1/edit"]:
            r = client.get(path)
            self.assertEqual(r.status_code, 302, path)
            self.assertIn("/login", r.location, path)


class RecordTests(MoodTrackTestCase):
    """情緒紀錄功能"""

    def test_add_record(self):
        client = self.user_client("testuser")
        r = self.add_record(client, note="今天完成了部分專題功能")
        self.assertIn("情緒紀錄新增成功。", self.text(r))
        self.assertIn("今天完成了部分專題功能", self.text(r))

    def test_mood_score_range(self):
        client = self.user_client("testuser")
        for value in ("0", "11"):
            r = self.add_record(client, mood_score=value)
            self.assertIn("心情分數必須介於 1 到 10。", self.text(r))

    def test_stress_score_range(self):
        client = self.user_client("testuser")
        for value in ("0", "11"):
            r = self.add_record(client, stress_score=value)
            self.assertIn("壓力分數必須介於 1 到 10。", self.text(r))

    def test_sleep_and_exercise_range(self):
        client = self.user_client("testuser")
        self.assertIn("睡眠時間必須介於 0 到 24 小時。", self.text(self.add_record(client, sleep_hours="25")))
        self.assertIn("運動時間必須介於 0 到 1440 分鐘。", self.text(self.add_record(client, exercise_minutes="-10")))
        with app.app_context():
            self.assertEqual(MoodRecord.query.count(), 0)

    def test_same_day_cannot_be_added_twice(self):
        client = self.user_client("testuser")
        self.add_record(client)
        r = self.add_record(client)
        self.assertIn("這一天已經有紀錄，請選擇其他日期。", self.text(r))
        with app.app_context():
            self.assertEqual(MoodRecord.query.count(), 1)

    def test_view_history_sorted_newest_first(self):
        client = self.user_client("testuser")
        for n in (3, 0, 1):
            self.add_record(client, record_date=day(n), note=f"第{n}天前")
        html = self.text(client.get("/records"))
        self.assertLess(html.index("第0天前"), html.index("第1天前"))
        self.assertLess(html.index("第1天前"), html.index("第3天前"))

    def test_edit_record(self):
        client = self.user_client("testuser")
        self.add_record(client, note="原本")
        rid = self.record_id(note="原本")
        self.assertIn('value="7"', self.text(client.get(f"/records/{rid}/edit")))
        r = client.post(
            f"/records/{rid}/edit",
            data={"record_date": day(0), "mood_score": "8", "stress_score": "4",
                  "sleep_hours": "7.5", "exercise_minutes": "30", "note": "改過"},
            follow_redirects=True,
        )
        self.assertIn("情緒紀錄更新成功。", self.text(r))
        with app.app_context():
            record = db.session.get(MoodRecord, rid)
            self.assertEqual((record.mood_score, record.sleep_hours, record.note), (8, 7.5, "改過"))

    def test_delete_record(self):
        client = self.user_client("testuser")
        self.add_record(client, note="要刪掉")
        rid = self.record_id(note="要刪掉")
        self.assertEqual(client.get(f"/records/{rid}/delete").status_code, 405)  # 不能用網址直接刪
        r = client.post(f"/records/{rid}/delete", follow_redirects=True)
        self.assertIn("情緒紀錄已刪除。", self.text(r))
        with app.app_context():
            self.assertEqual(MoodRecord.query.count(), 0)

    def test_delete_asks_for_confirmation(self):
        client = self.user_client("testuser")
        self.add_record(client)
        self.assertIn("return confirm('確定要刪除這筆紀錄嗎？')", self.text(client.get("/records")))

    def test_history_filters(self):
        client = self.user_client("testuser")
        self.add_record(client, record_date=day(0), stress_score=8, note="壓力大")
        self.add_record(client, record_date=day(10), stress_score=2, note="很久以前")
        html = self.text(client.get("/records?min_stress=7"))
        self.assertIn("壓力大", html)
        self.assertNotIn("很久以前", html)
        html = self.text(client.get(f"/records?start={day(3)}&end={day(0)}"))
        self.assertIn("壓力大", html)
        self.assertNotIn("很久以前", html)


    def test_history_pagination_keeps_filters(self):
        client = self.user_client("testuser")
        for n in range(25):
            self.add_record(client, record_date=day(n), stress_score=8, note=f"day{n:02d}")
        html = self.text(client.get("/records?min_stress=7"))
        self.assertIn("第 1 / 2 頁", html)
        self.assertIn("day00", html)
        self.assertNotIn("day24", html)
        self.assertIn("page=2", html)
        self.assertIn("min_stress=7", html)
        html = self.text(client.get("/records?min_stress=7&page=2"))
        self.assertIn("day24", html)
        self.assertNotIn("day00", html)


class DashboardTests(MoodTrackTestCase):
    """分析與圖表"""

    def stat_values(self, html):
        return re.findall(r'class="stat-value">([^<]+)<', html)

    def test_no_data_does_not_crash(self):
        client = self.user_client("testuser")
        for path in ["/dashboard", "/analysis", "/records"]:
            self.assertEqual(client.get(path).status_code, 200, path)
        html = self.text(client.get("/dashboard"))
        self.assertIn("目前沒有足夠資料可以顯示圖表", html)
        self.assertIn("目前還沒有情緒紀錄。", html)

    def test_statistics_last_7_days(self):
        client = self.user_client("testuser")
        self.add_record(client, record_date=day(0), mood_score=7, stress_score=5, sleep_hours=7)
        self.add_record(client, record_date=day(1), mood_score=8, stress_score=4, sleep_hours=6.5)
        self.add_record(client, record_date=day(6), mood_score=7, stress_score=5, sleep_hours=7)
        self.add_record(client, record_date=day(7), mood_score=1, stress_score=10, sleep_hours=3)  # 第 8 天，不算
        html = self.text(client.get("/dashboard"))
        # 總紀錄天數、最近 7 天紀錄數、平均心情、平均壓力、平均睡眠
        self.assertEqual(self.stat_values(html)[:5], ["4", "3", "7.3", "4.7", "6.8"])

    def test_average_uses_normal_rounding(self):
        client = self.user_client("testuser")
        for n, stress in enumerate((5, 4, 2, 6)):  # 平均 4.25
            self.add_record(client, record_date=day(n), stress_score=stress)
        self.assertEqual(self.stat_values(self.text(client.get("/dashboard")))[3], "4.3")

    def test_latest_record_shown(self):
        client = self.user_client("testuser")
        self.add_record(client, record_date=day(2), note="比較舊")
        self.add_record(client, record_date=day(0), note="最新的一筆")
        html = self.text(client.get("/dashboard"))
        latest = html[html.index("最近一筆紀錄"):]
        self.assertIn("最新的一筆", latest)
        self.assertNotIn("比較舊", latest)

    def test_charts_rendered(self):
        client = self.user_client("testuser")
        self.add_record(client)
        html = self.text(client.get("/dashboard"))
        for chart_id in ("moodStressChart", "sleepChart", "exerciseChart"):
            self.assertIn(f'id="{chart_id}"', html)
        self.assertIn("vendor/chart.umd.min.js", html)
        r = client.get("/static/vendor/chart.umd.min.js")
        self.assertEqual(r.status_code, 200)
        r.close()

    def test_streak_and_mood_calendar(self):
        client = self.user_client("testuser")
        for n in (0, 1, 2, 5, 6, 7, 8):
            self.add_record(client, record_date=day(n), mood_score=9 if n == 0 else 3)
        html = self.text(client.get("/dashboard"))
        self.assertIn("已連續記錄 <strong>3</strong> 天", html)
        self.assertIn("最長 4 天", html)
        self.assertIn("心情日曆", html)
        self.assertIn("cal-cell level-9 today", html)
        self.assertEqual(html.count("cal-cell level-3"), 6)

    def test_streak_counts_from_yesterday_if_today_missing(self):
        client = self.user_client("testuser")
        for n in (1, 2):
            self.add_record(client, record_date=day(n))
        self.assertIn("已連續記錄 <strong>2</strong> 天", self.text(client.get("/dashboard")))

    def test_analysis_page(self):
        client = self.user_client("testuser")
        self.add_record(client, record_date=day(0), exercise_minutes=30, stress_score=3)
        self.add_record(client, record_date=day(1), exercise_minutes=0, stress_score=8)
        html = self.text(client.get("/analysis"))
        for text in ("平均值比較", "心情最高的一天", "運動與壓力比較", "sleepMoodChart"):
            self.assertIn(text, html)


class SecurityTests(MoodTrackTestCase):
    """資料安全"""

    def setUp(self):
        super().setUp()
        self.alice = self.user_client("alice")
        self.add_record(self.alice, note="Alice 的秘密")
        self.alice_record = self.record_id(note="Alice 的秘密")
        self.bob = self.user_client("bob")

    def test_users_only_see_own_records(self):
        for path in ["/records", "/dashboard", "/analysis", "/records?min_stress=1"]:
            self.assertNotIn("Alice 的秘密", self.text(self.bob.get(path)), path)

    def test_users_only_edit_own_records(self):
        self.assertEqual(self.bob.get(f"/records/{self.alice_record}/edit").status_code, 404)
        r = self.bob.post(
            f"/records/{self.alice_record}/edit",
            data={"record_date": day(0), "mood_score": "1", "stress_score": "10",
                  "sleep_hours": "0", "exercise_minutes": "0", "note": "被改了"},
        )
        self.assertEqual(r.status_code, 404)
        with app.app_context():
            self.assertEqual(db.session.get(MoodRecord, self.alice_record).note, "Alice 的秘密")

    def test_users_only_delete_own_records(self):
        self.assertEqual(self.bob.post(f"/records/{self.alice_record}/delete").status_code, 404)
        with app.app_context():
            self.assertIsNotNone(db.session.get(MoodRecord, self.alice_record))

    def test_login_redirect_stays_on_site(self):
        client = app.test_client()
        self.register(client, "carol", "carol@example.com")
        r = client.post("/login?next=//evil.example.com",
                        data={"email": "carol@example.com", "password": "123456"})
        self.assertTrue(r.location.endswith("/dashboard"))

    def test_error_page(self):
        r = self.bob.get("/this-page-does-not-exist")
        self.assertEqual(r.status_code, 404)
        self.assertIn("找不到頁面", self.text(r))

    def test_secret_files_not_committed(self):
        try:
            tracked = subprocess.run(
                ["git", "ls-files"], cwd=PROJECT_DIR, capture_output=True, text=True, check=True
            ).stdout.splitlines()
        except (OSError, subprocess.CalledProcessError):
            self.skipTest("不是 git 專案，略過")
        for path in tracked:
            self.assertFalse(path.endswith((".db", ".sqlite", ".sqlite3")), path)
            self.assertFalse(path.startswith(("venv/", ".venv/")), path)
            self.assertNotEqual(Path(path).name, ".env", path)


class CsrfTests(MoodTrackTestCase):
    """CSRF 保護：沒有正確 token 的 POST 一律拒絕"""

    def setUp(self):
        super().setUp()
        app.config["WTF_CSRF_ENABLED"] = True

    def tearDown(self):
        app.config["WTF_CSRF_ENABLED"] = False

    def token(self, client, path):
        html = self.text(client.get(path))
        return re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)

    def test_post_without_token_is_rejected(self):
        client = app.test_client()
        r = client.post("/register", data={"username": "eve", "email": "eve@example.com",
                                           "password": "123456", "confirm": "123456"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("表單已過期", self.text(r))
        with app.app_context():
            self.assertEqual(User.query.count(), 0)

    def test_forms_work_with_token(self):
        client = app.test_client()
        r = client.post("/register", data={"csrf_token": self.token(client, "/register"),
                                           "username": "carol", "email": "carol@example.com",
                                           "password": "123456", "confirm": "123456"})
        self.assertEqual(r.status_code, 302)
        r = client.post("/login", data={"csrf_token": self.token(client, "/login"),
                                        "email": "carol@example.com", "password": "123456"})
        self.assertTrue(r.location.endswith("/dashboard"))
        r = client.post("/records/new", data={"csrf_token": self.token(client, "/records/new"),
                                              "record_date": day(0), "mood_score": "7", "stress_score": "5",
                                              "sleep_hours": "7", "exercise_minutes": "30"})
        self.assertEqual(r.status_code, 302)
        rid = self.record_id()
        # 沒有 token 的刪除會被擋，紀錄還在
        self.assertEqual(client.post(f"/records/{rid}/delete").status_code, 400)
        r = client.post(f"/records/{rid}/delete", data={"csrf_token": self.token(client, "/records")})
        self.assertEqual(r.status_code, 302)
        with app.app_context():
            self.assertEqual(MoodRecord.query.count(), 0)


class CalendarTests(MoodTrackTestCase):
    """心情日曆：任何一天都可以點進去記錄或修改"""

    def test_empty_day_links_to_new_record_with_date(self):
        client = self.user_client("testuser")
        html = self.text(client.get("/dashboard"))
        self.assertIn(f"/records/new?date={day(3)}&amp;next=/dashboard", html)

    def test_new_record_prefills_date_and_returns_to_dashboard(self):
        client = self.user_client("testuser")
        html = self.text(client.get(f"/records/new?date={day(3)}&next=/dashboard"))
        self.assertIn(f'value="{day(3)}"', html)
        self.assertIn("正在補記", html)
        r = client.post(f"/records/new?date={day(3)}&next=/dashboard",
                        data={"record_date": day(3), "mood_score": "6", "stress_score": "4",
                              "sleep_hours": "7", "exercise_minutes": "0"})
        self.assertTrue(r.location.endswith("/dashboard"))

    def test_day_with_record_goes_to_edit(self):
        client = self.user_client("testuser")
        self.add_record(client, record_date=day(2))
        r = client.get(f"/records/new?date={day(2)}&next=/dashboard")
        self.assertIn(f"/records/{self.record_id()}/edit", r.location)

    def test_new_without_date_never_redirects_to_edit(self):
        # 今天已經記錄過，按「新增」想補記別天時，不能被帶去編輯今天那筆
        client = self.user_client("testuser")
        self.add_record(client, record_date=day(0), note="今天")
        r = client.get("/records/new")
        self.assertEqual(r.status_code, 200)
        self.add_record(client, record_date=day(1), note="昨天補記")
        with app.app_context():
            self.assertEqual(MoodRecord.query.count(), 2)
            self.assertEqual(MoodRecord.query.filter_by(record_date=date.today()).one().note, "今天")

    def test_future_or_invalid_date_falls_back_to_today(self):
        client = self.user_client("testuser")
        r = client.get(f"/records/new?date={day(-2)}", follow_redirects=True)
        self.assertIn("不能記錄未來的日期", self.text(r))
        self.assertIn(f'value="{day(0)}"', self.text(r))
        self.assertIn(f'value="{day(0)}"', self.text(client.get("/records/new?date=garbage")))


class RagTests(unittest.TestCase):
    """說明小幫手：檢索、生成與退回"""

    def setUp(self):
        self._orig = rag.call_ollama

    def tearDown(self):
        rag.call_ollama = self._orig

    def test_retrieval_finds_relevant_section(self):
        cases = {
            "怎麼刪除紀錄": "刪除紀錄",
            "忘記密碼怎麼辦": "忘記密碼怎麼辦？",
            "表單已過期": "為什麼送出表單時出現「表單已過期」？",
            "管理員看得到我的心情嗎": "管理員看得到我的心情紀錄嗎？",
        }
        for q, heading in cases.items():
            self.assertEqual(rag.get_retriever().search(q)[0][0].heading, heading, q)

    def test_unrelated_question_finds_nothing(self):
        a = rag.answer_question("今天天氣如何", use_llm=False)
        self.assertEqual(a.mode, "none")
        self.assertFalse(a.sources)

    def test_llm_answer_uses_retrieved_context(self):
        seen = {}

        def fake(system, user):
            seen["system"], seen["user"] = system, user
            return "到歷史紀錄按「刪除」即可。"

        rag.call_ollama = fake
        a = rag.answer_question("怎麼刪除紀錄", use_llm=True)
        self.assertEqual(a.mode, "llm")
        self.assertEqual(a.answer, "到歷史紀錄按「刪除」即可。")
        self.assertIn("確定要刪除這筆紀錄嗎", seen["user"])  # 檢索到的段落有放進提示詞
        self.assertIn("怎麼刪除紀錄", seen["user"])
        self.assertIn("不提供醫療診斷", seen["system"])

    def test_falls_back_to_retrieval_when_llm_unavailable(self):
        def broken(system, user):
            raise rag.LLMUnavailable("連不到 Ollama")

        rag.call_ollama = broken
        a = rag.answer_question("怎麼刪除紀錄", use_llm=True)
        self.assertEqual(a.mode, "retrieval")
        self.assertIn("確定要刪除這筆紀錄嗎", a.answer)
        self.assertTrue(a.error)

    def test_crisis_always_includes_hotlines(self):
        a = rag.answer_question("我真的不想活了", use_llm=False)
        self.assertTrue(a.crisis)
        self.assertEqual(a.sources[0].heading, rag.CRISIS_HEADING)
        self.assertIn("1925", a.answer)


class HelpCenterTests(MoodTrackTestCase):
    """說明與回報頁面"""

    def setUp(self):
        super().setUp()
        self._orig = rag.RAG_USE_LLM
        rag.RAG_USE_LLM = False  # 測試不連 Ollama

    def tearDown(self):
        rag.RAG_USE_LLM = self._orig

    def test_ask_question_shows_answer_and_logs_it(self):
        client = self.user_client("testuser")
        html = self.text(client.get("/help?q=怎麼刪除紀錄"))
        self.assertIn("確定要刪除這筆紀錄嗎", html)
        self.assertIn("回報給管理員", html)
        with app.app_context():
            q = HelpQuestion.query.one()
            self.assertEqual((q.question, q.mode), ("怎麼刪除紀錄", "retrieval"))

    def test_crisis_question_shows_hotline_box(self):
        client = self.user_client("testuser")
        html = self.text(client.get("/help?q=我想自殺"))
        self.assertIn("你不是一個人", html)
        self.assertIn("1925", html)

    def test_help_requires_login(self):
        self.assertEqual(app.test_client().get("/help").status_code, 302)

    def test_submit_feedback_and_only_see_own(self):
        alice, bob = self.user_client("alice"), self.user_client("bob")
        self.assertIn("請填寫標題與內容", self.text(alice.post(
            "/feedback/new", data={"category": "bug", "title": "", "content": ""})))
        r = alice.post("/feedback/new", data={"category": "bug", "title": "圖表沒出現",
                                              "content": "儀表板空白"}, follow_redirects=True)
        self.assertIn("已送出回報", self.text(r))
        self.assertIn("圖表沒出現", self.text(r))
        self.assertNotIn("圖表沒出現", self.text(bob.get("/feedback")))

    def test_feedback_form_prefill_from_question(self):
        client = self.user_client("testuser")
        html = self.text(client.get("/feedback/new?category=question&title=怎麼匯出"))
        self.assertIn('value="怎麼匯出"', html)


class AdminTests(MoodTrackTestCase):
    """管理頁：只有 ADMIN_EMAILS 裡的帳號能進入"""

    def setUp(self):
        super().setUp()
        os.environ["ADMIN_EMAILS"] = "boss@example.com"
        self.user = self.user_client("alice")
        self.add_record(self.user, note="非常私密的心情")
        self.user.post("/feedback/new", data={"category": "bug", "title": "按鈕壞了", "content": "按了沒反應"})
        self.admin = self.user_client("boss")

    def tearDown(self):
        os.environ.pop("ADMIN_EMAILS", None)

    def test_non_admin_cannot_access(self):
        self.assertEqual(self.user.get("/admin").status_code, 404)
        with app.app_context():
            fid = Feedback.query.one().id
        self.assertEqual(self.user.post(f"/admin/feedback/{fid}", data={"status": "resolved"}).status_code, 404)
        self.assertNotIn('href="/admin"', self.text(self.user.get("/dashboard")))

    def test_admin_sees_stats_but_not_mood_content(self):
        html = self.text(self.admin.get("/admin"))
        self.assertIn("按鈕壞了", html)
        self.assertIn("dailyChart", html)
        self.assertNotIn("非常私密的心情", html)
        self.assertIn('href="/admin"', self.text(self.admin.get("/dashboard")))

    def test_admin_reply_visible_to_user(self):
        with app.app_context():
            fid = Feedback.query.one().id
        self.admin.post(f"/admin/feedback/{fid}", data={"status": "resolved", "admin_reply": "已修好，請再試一次"})
        html = self.text(self.user.get("/feedback"))
        self.assertIn("已修好，請再試一次", html)
        self.assertIn("已解決", html)


if __name__ == "__main__":
    unittest.main()
