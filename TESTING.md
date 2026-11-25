# MoodTrack 測試紀錄

## 自動化測試

測試程式在 `tests/test_app.py`，用 Python 內建的 `unittest`，不需要額外安裝套件。
測試使用**記憶體中的暫時資料庫**，不會動到 `moodtrack.db`。

```powershell
.\venv\Scripts\Activate.ps1
python -m unittest -v
```

最後出現 `OK` 就代表全部通過。

## 測試結果

測試日期：2026-11-25　環境：Python 3.13、Flask 3.1.3、Flask-SQLAlchemy 3.1.1、Flask-Login 0.6.3

### 帳號功能

| 編號 | 測試項目 | 預期結果 | 對應測試 | 結果 |
|---|---|---|---|---|
| A1 | 註冊新帳號 | 寫入資料庫並導向登入頁 | `test_register_new_account` | ✅ 通過 |
| A2 | 密碼不以明文儲存 | 資料庫存 `scrypt:...` 雜湊 | `test_password_not_stored_in_plain_text` | ✅ 通過 |
| A3 | 空白欄位、密碼少於 6 字元 | 顯示錯誤、拒絕註冊 | `test_register_rejects_blank_and_short_password` | ✅ 通過 |
| A4 | 重複帳號（名稱或 email） | 「使用者名稱或電子郵件已經被使用。」 | `test_duplicate_account_rejected` | ✅ 通過 |
| A5 | 正確帳密登入 | 進入儀表板，顯示「歡迎，testuser！」 | `test_login_success` | ✅ 通過 |
| A6 | 錯誤密碼登入 | 「電子郵件或密碼錯誤。」 | `test_wrong_password_rejected` | ✅ 通過 |
| A7 | 登出 | 回首頁，之後進不了儀表板 | `test_logout` | ✅ 通過 |
| A8 | 未登入開啟受保護頁面 | 導向登入頁 | `test_protected_pages_require_login` | ✅ 通過 |

### 情緒紀錄功能

| 編號 | 測試項目 | 預期結果 | 對應測試 | 結果 |
|---|---|---|---|---|
| R1 | 新增正常紀錄 | 「情緒紀錄新增成功。」 | `test_add_record` | ✅ 通過 |
| R2 | 心情輸入 0、11 | 「心情分數必須介於 1 到 10。」 | `test_mood_score_range` | ✅ 通過 |
| R3 | 壓力輸入 0、11 | 「壓力分數必須介於 1 到 10。」 | `test_stress_score_range` | ✅ 通過 |
| R4 | 睡眠 25、運動負數 | 顯示錯誤，不寫入資料庫 | `test_sleep_and_exercise_range` | ✅ 通過 |
| R5 | 同一天重複紀錄 | 「這一天已經有紀錄，請選擇其他日期。」 | `test_same_day_cannot_be_added_twice` | ✅ 通過 |
| R6 | 查看歷史紀錄 | 依日期由新到舊 | `test_view_history_sorted_newest_first` | ✅ 通過 |
| R7 | 歷史紀錄篩選 | 日期區間、壓力條件正確 | `test_history_filters` | ✅ 通過 |
| R8 | 歷史紀錄分頁 | 每頁 20 筆，換頁保留篩選 | `test_history_pagination_keeps_filters` | ✅ 通過 |
| R9 | 編輯紀錄 | 顯示原資料，更新成功 | `test_edit_record` | ✅ 通過 |
| R10 | 刪除紀錄 | 只接受 POST，刪除成功 | `test_delete_record` | ✅ 通過 |
| R11 | 刪除前確認 | 頁面有確認視窗 | `test_delete_asks_for_confirmation` | ✅ 通過 |

### 分析與圖表

| 編號 | 測試項目 | 預期結果 | 對應測試 | 結果 |
|---|---|---|---|---|
| D1 | 沒有資料 | 儀表板、分析、歷史頁都不出錯 | `test_no_data_does_not_crash` | ✅ 通過 |
| D2 | 最近 7 天統計 | 第 8 天的紀錄不算入平均，但算入總天數 | `test_statistics_last_7_days` | ✅ 通過 |
| D3 | 最近一筆紀錄 | 顯示日期最新的一筆 | `test_latest_record_shown` | ✅ 通過 |
| D4 | 圖表 | 心情壓力、睡眠、運動圖表與 Chart.js 正常載入 | `test_charts_rendered` | ✅ 通過 |
| D6 | 連續記錄天數與心情日曆 | 連續 3 天、最長 4 天；日曆依心情分數上色 | `test_streak_and_mood_calendar` | ✅ 通過 |
| D7 | 今天還沒記錄時的連續天數 | 從昨天往回算 | `test_streak_counts_from_yesterday_if_today_missing` | ✅ 通過 |
| D5 | 資料分析頁 | 平均比較、最高最低、運動與壓力、散佈圖 | `test_analysis_page` | ✅ 通過 |

### 資料安全

| 編號 | 測試項目 | 預期結果 | 對應測試 | 結果 |
|---|---|---|---|---|
| S1 | 只看得到自己的紀錄 | 歷史、儀表板、分析頁都看不到別人的資料 | `test_users_only_see_own_records` | ✅ 通過 |
| S2 | 只能編輯自己的紀錄 | 改網址編輯別人的紀錄 → 404 | `test_users_only_edit_own_records` | ✅ 通過 |
| S3 | 只能刪除自己的紀錄 | 改網址刪除別人的紀錄 → 404 | `test_users_only_delete_own_records` | ✅ 通過 |
| S4 | 登入後導向不會跳到外部網站 | `?next=//evil...` 導回儀表板 | `test_login_redirect_stays_on_site` | ✅ 通過 |
| S5 | 自訂錯誤頁 | 不存在的網址顯示「找不到頁面」 | `test_error_page` | ✅ 通過 |
| S6 | 機密檔案沒有上傳 | Git 沒有追蹤 `*.db`、`venv/`、`.env` | `test_secret_files_not_committed` | ✅ 通過 |

**合計：32 項，全部通過。**

## 手動測試（瀏覽器）

| 編號 | 測試項目 | 結果 |
|---|---|---|
| M1 | 心情滑桿拖動時表情即時變化 | ✅ 通過 |
| M2 | 刪除時跳出「確定要刪除這筆紀錄嗎？」 | ✅ 通過 |
| M3 | 圖表滑鼠移上去顯示當天數值 | ✅ 通過 |
| M4 | 手機版畫面（寬 390px）：導覽列、表格改卡片、圖表 | ✅ 通過 |
| M5 | 瀏覽器 Console 沒有錯誤 | ✅ 通過 |
