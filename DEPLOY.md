# 部署到 PythonAnywhere

照這份步驟做完，MoodTrack 會有一個公開網址，例如 `https://你的帳號.pythonanywhere.com`，
電腦關機也能一直使用。大約需要 15～20 分鐘。

> 下面所有的 `你的帳號` 都要換成你在 PythonAnywhere 註冊的使用者名稱。
> PythonAnywhere 的介面和免費方案條件可能會更新，以官網實際畫面為準。

---

## 一、註冊帳號

1. 打開 <https://www.pythonanywhere.com>，按 **Pricing & signup**
2. 選 **Create a Beginner account**（免費）
3. 填使用者名稱、Email、密碼後完成註冊並驗證信箱

使用者名稱會變成網址的一部分，例如 `moodtrack-demo` → `https://moodtrack-demo.pythonanywhere.com`。

---

## 二、下載程式碼

1. 登入後點上方的 **Consoles** → **Bash**，會打開一個黑色的指令視窗
2. 輸入：

```bash
git clone https://github.com/cooldragonboat/python_FP.git
```

完成後程式碼會在 `/home/你的帳號/python_FP`。

---

## 三、建立虛擬環境並安裝套件

在同一個 Bash 視窗輸入：

```bash
mkvirtualenv moodtrack --python=python3.12
```

看到提示字元前面出現 `(moodtrack)` 就代表成功。接著安裝套件：

```bash
cd ~/python_FP
pip install -r requirements.txt
```

> 如果出現找不到 `python3.12`，改用 `python3.11` 或 `python3.10`（需要 3.10 以上）。
> 下一步設定網站時，要選**同一個** Python 版本。

---

## 四、產生 SECRET_KEY

`SECRET_KEY` 用來保護登入狀態，**每個網站都要用自己隨機產生的值，不能用程式裡的預設值，也不能上傳到 GitHub。**

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

會印出一長串英數字，**先複製起來**，第六步會用到。

---

## 五、建立網站

1. 點右上角 **☰ → Web**（或上方的 **Web**）
2. 按 **Add a new web app** → **Next**
3. 選 **Manual configuration**（⚠️ 不要選 Flask，那個會產生範例程式）
4. 選和第三步相同的 Python 版本（例如 Python 3.12）→ **Next**

建立好後，在同一頁往下找到這兩個欄位：

| 區塊 | 欄位 | 填入 |
| --- | --- | --- |
| Code | **Source code** | `/home/你的帳號/python_FP` |
| Virtualenv | **Enter path to a virtualenv** | `/home/你的帳號/.virtualenvs/moodtrack` |

---

## 六、設定 WSGI 檔案

1. 在 Web 頁面的 **Code** 區塊，點 **WSGI configuration file** 後面的連結
   （檔名類似 `/var/www/你的帳號_pythonanywhere_com_wsgi.py`）
2. **把檔案裡原本的內容全部刪掉**，換成下面這段：

```python
import os
import sys

# 專案資料夾
project_home = "/home/你的帳號/python_FP"
if project_home not in sys.path:
    sys.path.insert(0, project_home)

# 換成第四步產生的那串隨機字元
os.environ["SECRET_KEY"] = "貼上你的SECRET_KEY"
# 網站走 HTTPS，Cookie 只透過加密連線傳送
os.environ["COOKIE_SECURE"] = "1"

from app import app as application  # noqa: E402
```

3. 把 `你的帳號` 和 `貼上你的SECRET_KEY` 換掉，按右上角 **Save**

> 這個檔案在 PythonAnywhere 主機上，**不在 GitHub 裡**，所以 SECRET_KEY 不會外洩。

---

## 七、設定靜態檔案與 HTTPS

回到 **Web** 頁面：

1. **Static files** 區塊，新增一列：

   | URL | Directory |
   | --- | --- |
   | `/static/` | `/home/你的帳號/python_FP/static` |

2. **Security** 區塊，把 **Force HTTPS** 打開

---

## 八、啟動網站

在 Web 頁面最上方按綠色的 **Reload 你的帳號.pythonanywhere.com**，
然後打開 `https://你的帳號.pythonanywhere.com`，看到 MoodTrack 首頁就成功了 🎉

第一次開啟時會自動建立資料庫 `moodtrack.db`（在 `/home/你的帳號/python_FP/` 裡）。

---

## 之後更新網站

程式在 GitHub 更新後，打開 **Consoles → Bash**：

```bash
cd ~/python_FP
git pull
workon moodtrack
pip install -r requirements.txt
```

再到 **Web** 頁面按 **Reload**。資料庫不在 Git 裡，`git pull` 不會影響已經存在的紀錄。

> 如果更新內容改了資料表結構，需要先備份再刪掉 `moodtrack.db` 讓它重建，資料會清空。

---

## 免費方案要注意的事

- **定期延長**：免費網站每隔一段時間（目前約 3 個月）要登入 Web 頁面，按 **Run until 3 months from today**，不然網站會被停用
- **備份資料**：可在 **Files** 頁面找到 `python_FP/moodtrack.db`，下載備份
- **對外連線受限**：免費帳號只能連到白名單網站，MoodTrack 不需要對外連線，不受影響

---

## 遇到錯誤怎麼辦

打開網站出現 **Something went wrong :-(** 時：

1. 到 **Web** 頁面，**Log files** 區塊點 **Error log**
2. 拉到最下面看最新的錯誤訊息

| 錯誤訊息 | 原因與解法 |
| --- | --- |
| `ModuleNotFoundError: No module named 'flask'` | Virtualenv 路徑沒填對，或沒有在虛擬環境裡安裝套件（回到第三步、第五步） |
| `ModuleNotFoundError: No module named 'app'` | WSGI 檔案裡 `project_home` 路徑打錯 |
| 頁面沒有樣式、圖表不出現 | 第七步的 Static files 沒設定，或路徑打錯 |
| 送出表單出現「表單已過期」 | 頁面開太久，重新整理後再送出一次 |

改完設定後都要再按一次 **Reload**。
