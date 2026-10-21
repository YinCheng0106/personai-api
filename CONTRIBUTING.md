# 參與 PersonAI API 開發

本專案使用 FastAPI、SQLAlchemy、Alembic 與 Hatch。測試方法與限制見
[TESTING.md](docs/TESTING.md)，身體資料契約見
[body-profile-backend.md](docs/body-profile-backend.md)。

## 開發環境

需要 Python 3.11 以上；CI 使用 Python 3.12 與 PostgreSQL 17。

```bash
git clone https://github.com/YinCheng0106/personai-api.git
cd personai-api
python -m pip install hatch
hatch env create
cp .env.sample .env
```

編輯 `.env`，只填入自己的開發環境設定。PostgreSQL URL 會由程式轉成 psycopg
driver；單機 API 開發也可設定 `DATABASE_URL=sqlite+pysqlite:///./personai.db`。
啟動前先對開發資料庫套用 migration：

```bash
hatch run alembic upgrade head
hatch run fastapi dev src/personai_api/main.py
```

REST 與 WebSocket 使用 Better Auth 簽發的 JWT。開發時需另啟前端驗證服務，並對齊
`AUTH_ISSUER`、`AUTH_AUDIENCE`、`AUTH_JWKS_URL` 與 `CORS_ORIGINS`。
自動測試使用本機簽章與測試身分，不需要遠端 JWKS 或 Supabase 憑證。
不要提交 `.env`、JWT、連線密碼或真實個人生理資料。

## 分支與提交

從最新 `main` 建立單一目的的分支，例如 `feat/<topic>`、`fix/<topic>`、
`test/<topic>`、`docs/<topic>` 或 `chore/<topic>`。不要把尚未整合的修正默默帶入另一項工作；
先列出依賴並與維護者確認整合順序。保留其他人的未提交變更。

提交訊息採英文 Conventional Commits：`<type>(<scope>): <description>`。
type 使用小寫 `feat`、`fix`、`refactor`、`docs`、`style`、`test`、`chore` 或 `perf`；
scope 在能清楚辨識範圍時加入，描述使用祈使語氣，結尾不加句點。

```text
test(auth): cover missing JWT claims
chore(ci): upload coverage with OIDC
```

## PR 與審查

以 `main` 為目標建立範圍明確的 PR，填寫 PR 範本並連結 issue。
說明問題、變更後行為、相容性、migration 風險及實際測試證據。需要前端配合時，
列出契約變更與部署順序。尚未完成的測試或依賴應保持明確可見。

審查優先確認 JWT 身分、Origin 與資料所有權，再檢查 FSM／Pose Guard、PK
生命週期、nullable 生理資料及 migration。不要為了綠燈移除斷言、降低涵蓋率門檻、
捏造生理預設值或調整動作辨識門檻。辨識演算法變更需另附可重現案例與真人驗證規劃。

## 品質閘門

```bash
hatch run lint
hatch run types:check
hatch run test-cov
git diff --check
```

需要格式整理時使用 `hatch run format`，避免順手修改無關檔案。
`test-cov` 同時量測 line／branch coverage，輸出終端明細與 `coverage.xml`，
既有 70% 最低門檻維持不變。CI 另以本機 PostgreSQL 服務執行 opt-in 整合測試、
`alembic upgrade head` 與 `alembic check`。Codecov 初期提供資訊，不列為 required check；
上傳失敗不會取代或掩蓋 lint、typing、pytest 與 migration 的失敗。

## 資料庫 migration 規則

- Schema 變更使用 Alembic revision，提交前審查產生的 SQL，不只依賴 autogenerate。
- 不重寫已發布的 revision；新增 forward migration 並記錄相依關係。
- 先在可丟棄的本機資料庫驗證空庫升版、既有資料回填、模型一致性與降版前提。
- PostgreSQL 的 RLS、role grants、型別、時區與 transaction 行為必須在 PostgreSQL 測試；
  SQLite 通過不能取代這些證據。
- 公開 schema 的應用表需保留 RLS 與 Data API 權限限制；Better Auth JWT 與應用層
  `user_id` 篩選仍是獨立的授權邊界。
- 降版前評估資料遺失、NOT NULL、索引與鎖定風險；先準備備份與 forward recovery。
  `0004` 遇到未知熱量必須拒絕降版，不可填入假的 0 來通過檢查；
  `0003` 降版會刪除新身體資料表，`0002` 降版會停用 RLS，均須另行評估。
- 不將自動測試或破壞性 migration 指向遠端／正式資料庫。既有資料庫的
  `alembic stamp` 是人工確認 schema 後的接管程序，不是一般測試或初始化步驟。
- Better Auth 的資料庫 migration 由前端專案管理，不屬於本專案 Alembic revisions。
