# nueip-mcp

Personal-use MCP server for **NUEiP 雲端人資系統** (cloud.nueip.com). Drives
the XHR endpoints the web UI uses, behind a simulated login. Lets any MCP
client (Claude Code, Claude Desktop, …) query attendance / leave balance /
team leaves / team attendance / pending approvals from natural-language
prompts.

> ⚠️ **Personal use only.** 不要架在 shared / multi-user 機器、不要 commit
> 密碼、不要在 `~/.claude.json` 放明文密碼。用 macOS Keychain（見下方
> Quick start）。

> 📜 **免責聲明 / Disclaimer**：本專案為作者個人逆向工程 NUEiP 網頁 UI
> 行為所做的研究／自用工具，**與 NUEiP 官方無任何關聯、未經其授權或背書**。
> 所有 endpoint、欄位、cookie 行為皆來自瀏覽器公開可觀察的 XHR 流量。
> 使用者需自行確認其使用方式符合 NUEiP 服務條款 (TOS) 與所在公司／國家
> 的相關法規；因使用本工具造成的任何後果（帳號鎖定、勞動契約爭議、資料
> 外洩、法律責任等）由使用者自行承擔，作者不負任何責任。詳見文末
> [License](#license)。

> 💡 **配套 Claude Code Skill**：[`tankfinal/agent-skills-pub`](https://github.com/tankfinal/agent-skills-pub) — `/toolkit-pub:nueip` 把常用工具包成單一入口、8 個子指令（today / week / recent / team / pending / balance / brief / me），比直接喊工具好用。`team_attendance`（manager 視角部門出勤）目前還沒進 wrapper，要用直接呼叫 `mcp__nueip__team_attendance`。

---

## Tools

| Tool | What it does |
|---|---|
| `my_attendance(start_date?, end_date?)` | 我的出勤紀錄；省略日期則查今日 |
| `my_leave_balance(year?, raw?)` | 我的假期餘額。預設瘦身模式（同 `v_name` group_by + 本期 only），數字直接對齊 NUEiP UI 顯示；要全部桶做 audit 才設 `raw=true` |
| `team_leaves(start_date?, end_date?, scope?, filt_method?)` | 部門 / 子部門當日請假；`scope`=`dept` (預設) / `team`；`filt_method`=`passed` / `ongoing` / `all` |
| `team_attendance(start_date?, end_date?, scope?, name_filter?, raw?)` | 🔒 主管：部門 / 子部門出勤紀錄。`scope`=`dept` (預設) / `team`；`name_filter` 姓名子字串（case-insensitive）。預設 slim view 一列 (user, date)，含打卡時間、遲到 / 早退分鐘、工時、`has_leave`。`raw=true` 回原始 payload（單月 30 人約 1-2 MB） |
| `pending_approvals(type_?, scope?, start_date?, end_date?)` | 待我簽核項目；`type_`=`leave` (預設) / `overtime` / `attendance` / `business_trip` …。`attendance` 類別多回傳當天實際打卡 + 補卡後仍差秒數，方便判斷 |
| `whoami` | 印出 公司 / 部門 / user ID（除錯用） |

---

## Quick start (macOS)

### 一鍵安裝（推薦）

需要先有 [uv](https://docs.astral.sh/uv/) 跟 [Claude Code CLI](https://claude.com/code)。然後：

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/tankfinal/nueip-mcp/main/install.sh)
```

腳本會：
1. 問你要裝在哪（預設 `~/nueip-mcp`，可改；或先設 `NUEIP_MCP_DIR=...` 跳過提問）
2. `uv sync` 裝 Python 依賴
3. chmod launch.sh
4. 互動輸入 NUEiP 密碼，存進 macOS Keychain
5. 問你的員編 + 公司簡碼（公司簡碼 = 你 NUEiP 登入頁 URL 的子網域）
6. `claude mcp add` 註冊到 Claude Code

完成後完全 quit Claude Code (cmd+Q) 再重開，就能用了。

> 💡 **自動更新提示**：`launch.sh` 每 24 小時會偷偷 `git fetch` 一次比對版本，有新版會在 stderr 印「更新指令」（不影響 MCP 啟動，沒網就 silent skip）。手動更新：`cd <你的安裝路徑> && git pull && uv sync`。

> 不放心 `curl | bash`？先看腳本內容：[install.sh](https://github.com/tankfinal/nueip-mcp/blob/main/install.sh) — 約 120 行純 bash，沒有黑魔法。或走下面手動流程。

---

### 手動安裝

```bash
# 1. Clone & install
git clone https://github.com/tankfinal/nueip-mcp ~/nueip-mcp
cd ~/nueip-mcp
uv sync
chmod +x launch.sh

# 2. 把 NUEiP 密碼存進 Keychain（互動輸入，不會回顯、不留 shell history）
security add-generic-password -s nueip -a "$USER" -U -w
#  ↑ `-w` 放最後不給值 → 系統 prompt 你貼密碼。
#  注意是小寫 -w，不是 -W（大寫不存在會印 usage）。

# 3. 驗證
security find-generic-password -s nueip -a "$USER" > /dev/null && echo "KEYCHAIN_OK"
```

然後手動跑下面的 Configure Claude Code 把 MCP 註冊上去。

---

## Configure Claude Code

> 用一鍵安裝的可跳過這段（腳本已經幫你跑 `claude mcp add`）。

加到 `~/.claude.json` 的 `mcpServers`：

```json
{
  "mcpServers": {
    "nueip": {
      "type": "stdio",
      "command": "/Users/<your-name>/nueip-mcp/launch.sh",
      "env": {
        "NUEIP_COMPANY": "your_company_code",
        "NUEIP_ACCOUNT": "your_account"
      }
    }
  }
}
```

- `command` 指向內附的 `launch.sh`，它會從 Keychain 拿密碼後 exec `uv run nueip-mcp`
- **不要**把 `NUEIP_PASSWORD` 也寫進 env block，那就回到明文密碼老路
- `NUEIP_COMPANY` = 公司簡碼（看你 NUEiP 登入頁 URL 的子網域，例：`portal.nueip.com/login?ondemand=acme` → `acme`）
- `NUEIP_ACCOUNT` = **你的員編**（NUEiP 登入用的 ID）— ⚠️ **每人不同，務必改成自己的**，不要直接複製範例

CLI 等效寫法（記得換 `<your-company-code>` 和 `<your-employee-id>` 成你自己的）：

```bash
claude mcp add nueip \
  --env NUEIP_COMPANY=<your-company-code> \
  --env NUEIP_ACCOUNT=<your-employee-id> \
  -- ~/nueip-mcp/launch.sh
```

完全 quit Claude Code（cmd+Q，不是 `/clear`）再重開，新 MCP 設定才會生效。

---

## Verify

最快測試：問 Claude *「用 nueip 查我是誰」*。回的姓名 / 部門 / 員編對得上 NUEiP UI 就代表 auth + transport 都通了。

或直接：

```
/toolkit-pub:nueip me              ← 如果你也裝了 agent-skills-pub
```

---

## Troubleshooting

**`KEYCHAIN_MISSING` 或 launch.sh 報 "password not found"**

Keychain 沒存或 account 名稱不一致。重跑：

```bash
security add-generic-password -s nueip -a "$USER" -U -w
```

**Claude 說「沒看到 nueip 工具 / mcp__nueip__*」**

Claude Code 沒讀到新 mcp config。完全 quit（cmd+Q）重開 — `/clear` 不夠，MCP 是 startup 時 spawn 的。

**Auth 失敗 / 401 / 一直要重新登入**

- 先試直接登入 https://cloud.nueip.com 確認密碼沒過期沒鎖
- 還不行就是 NUEiP 改 endpoint，看 [Maintenance](#maintenance)

**不在 macOS（Linux / WSL）**

Keychain workflow 不通用。改用 `pass`（Unix password manager）、`direnv` + `.envrc` (檔案權限 600、gitignored)、或你公司的 secret manager，把密碼注進 env 後改寫 `launch.sh`：

```bash
#!/bin/bash
export NUEIP_PASSWORD=$(pass nueip)   # 或你的取密碼方式
exec uv --directory "$(dirname "$0")" run nueip-mcp
```

**真的想用明文密碼（強烈不推薦）**

直接放 env 就好，跳過 launch.sh：

```json
"nueip": {
  "type": "stdio",
  "command": "uv",
  "args": ["--directory", "/Users/<you>/nueip-mcp", "run", "nueip-mcp"],
  "env": {
    "NUEIP_COMPANY": "...",
    "NUEIP_ACCOUNT": "...",
    "NUEIP_PASSWORD": "..."
  }
}
```

⚠️ `~/.claude.json` 沒加密。Time Machine / Dropbox / iCloud / IDE plugin 都讀得到。這個密碼換來的麻煩比省下 2 分鐘 Keychain 設定大。

---

## How it works

Reverse-engineered from the NUEiP web UI:

| Tool | Endpoint | Auth bits |
|---|---|---|
| login | `POST /login/index/param` | form: `inputCompany/inputID/inputPassword`; success = 302 → `/home` |
| my_attendance | `POST /attendance_record/ajax` | date filter via `Search_124_*` cookies; needs `X-Csrf-Token`, `Fe-Pno: 124` |
| my_leave_balance | `POST /personal_leave_resource` | layer + year + date range in form; `Fe-Pno: 44` |
| team_leaves | `POST /leave_application/personal_leave_application_manager/` | `employee=COMP_DEPT_all`; `Fe-Pno: 303` |
| team_attendance | `POST /attendance_record/ajax` | 同 my_attendance；`Search_124_FLayer/SLayer/TLayer` cookies 帶部門 / 子部門 layer（`COMP_DEPT_all` 或 own subdept）；`Fe-Pno: 124` |
| pending_approvals | `POST /leader_audit_work_list/index/{type}` | `Fe-Pno: 225` |
| whoami | scrapes `my_cmpny / my_dept / my_deptsn` from leave page + decodes `cuid` cookie | — |

`X-Csrf-Token` 直接讀 login 時 set 的 `csrf_token` cookie（不需 HTML 解析）。
Session 在記憶體 cache 45 分鐘後自動 re-auth。

---

## Security notes

- Credentials 在 process 啟動時讀 env，**永不寫 log**
- Session cookie / CSRF 只存記憶體，不落地
- **TLS verification 預設關閉** — NUEiP cert chain 缺 Subject Key Identifier 擴充，OpenSSL 3 拒絕驗證。寫死指向 `cloud.nueip.com`，`NUEIP_VERIFY_TLS=true` 可開回（如果你的 runtime 認得 cert）
- 唯一外送目的地：`cloud.nueip.com`
- `launch.sh` 從 Keychain 讀密碼後 export 給 subprocess，**過程中不落 disk**

---

## Scheduled routines: not supported

Claude.ai 的 scheduled / Run Now / RemoteTrigger sandbox 載不了 MCP connector
（上游 Anthropic bug）。**只能在本地 interactive session 用**。

---

## Maintenance

NUEiP 改 form params 或換 endpoint 時，編輯 `src/nueip_mcp/client.py`。檔頭
四個 `*_URL` constants + `FE_PNO` table 是最常需要動的地方。

---

## License & Disclaimer

**License**: 個人研究／自用，未附正式 OSS license。Fork、修改自用 OK，
請勿用於商業用途或大規模散佈。

**Disclaimer（重要，請務必閱讀）**：

- 本專案由作者**個人逆向工程** NUEiP (cloud.nueip.com) 網頁前端 XHR 行為
  整理而成，純為個人自動化自己帳號資料而做，**非 NUEiP 官方產品**、與
  NUEiP 公司**無任何合作、授權、背書關係**。
- 所有 endpoint / 參數皆來自瀏覽器 DevTools 可公開觀察的請求，未涉及破解
  加密、繞過權限、或存取非本人有權限的資料。
- 使用者**僅能用於查詢自己帳號的資料**。請勿用於：爬取他人資料、繞過
  簽核流程、自動化打卡造假、商業用途、或任何違反 NUEiP 服務條款
  (Terms of Service)、貴公司內部規定、或所在司法管轄區法律的行為。
- NUEiP 隨時可能修改 API、封鎖非官方 client、或對使用者帳號採取行動。
  使用本工具導致的**帳號停權、勞動契約爭議、資料外洩、法律責任**等
  任何後果，均由使用者**自行承擔**，作者與貢獻者**不負任何責任**。
- 軟體依「現狀」(AS IS) 提供，不附任何明示或默示之擔保。若你不接受
  以上條款，請勿使用本專案。

Use at your own risk. 遵守 NUEiP TOS、遵守貴公司內部規定。
