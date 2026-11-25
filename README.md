# MoodTrack（python_FP）

個人情緒與壓力紀錄分析平台——程式設計 Python 課程期末專案，使用 Flask + SQLite。

## 目前進度（第一階段：最小可執行版本）

- [x] Flask 網站可以啟動
- [x] 首頁可以顯示，能選擇心情、寫一句話並記錄
- [x] SQLite 資料庫自動建立（專案根目錄的 `moodtrack.db`）
- [x] 專案提交到 GitHub

## 環境需求

- Python 3.10 以上

## 快速開始（PowerShell）

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
python app.py
```

打開瀏覽器到 <http://127.0.0.1:5000> 就能看到首頁。

> 啟用虛擬環境遇到執行原則錯誤，先執行
> `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` 再重新啟用。

## 專案結構

```
python_FP/
├── app.py             # Flask 主程式：路由、資料表、啟動點
├── requirements.txt   # 套件清單（pip install -r 安裝）
├── templates/
│   └── index.html     # 首頁
├── static/
│   └── style.css      # 樣式
├── moodtrack.db       # 執行後自動產生的 SQLite 資料庫（不提交）
└── venv/              # 虛擬環境（不提交）
```

## Git 使用方式

### 第一次把專案拉到自己電腦

GitHub 上已經有完整歷史，**請用 `clone`，不要用 `git init` 從頭建立**，否則推送時會被拒絕。

```powershell
cd "C:\Users\a9409\Desktop\大學報告\通識&語言"
git clone https://github.com/cooldragonboat/python_FP.git
cd python_FP
```

第一次使用 Git 要設定身分（信箱換成 GitHub 註冊信箱）：

```powershell
git config --global user.name "cooldragonboat"
git config --global user.email "你的GitHub註冊信箱"
```

### 之後更新程式碼

每次開始改之前先拉最新版本，改完再推上去：

```powershell
git pull
git add .
git commit -m "描述這次修改內容"
git push
```

例如：

```powershell
git add .
git commit -m "新增 MoodTrack 首頁"
git push
```

### 常見錯誤

| 錯誤訊息 | 原因 | 解法 |
| --- | --- | --- |
| `rejected because the remote contains work` | GitHub 上有你本機沒有的提交 | `git pull origin main --rebase` 後再 `git push` |
| `remote origin already exists` | 已設定過遠端 | `git remote set-url origin https://github.com/cooldragonboat/python_FP.git` |
| `src refspec main does not match any` | 還沒有任何提交 | 先 `git add .` 和 `git commit` 再推送 |
| GitHub 要求登入 | 尚未驗證 | 依瀏覽器指示登入，或使用 Personal Access Token |

> 發生衝突時**不要強制推送**（`git push --force`），先保留錯誤訊息、確認內容再處理。
>
> GitHub 密碼或 Personal Access Token **絕對不要**寫進程式碼或提交到 Repository；需要的話只放在 `.env`（已被 `.gitignore` 排除）。
