# 後端測試與涵蓋率

## 測試層次

| 層次 | 範圍 | 資料來源／限制 |
| --- | --- | --- |
| Unit | 幾何、濾波、FSM、Pose Guard、生理公式 | 合成輸入，無網路 |
| Application integration | FastAPI REST／WebSocket、授權、PK 與持久化 | TestClient + in-memory SQLite；JWT 使用本機金鑰 |
| SQLite migration | `0002` 到 head 的舊資料回填 | pytest 暫存檔案，不涵蓋 PostgreSQL RLS |
| PostgreSQL integration | 真正 Alembic schema、RLS／grants、回填、降版保護、跨請求持久化與隔離 | opt-in，本機 PostgreSQL 17，每個案例獨立資料庫 |
| Manual acceptance | 真實影像、真人動作辨識、部署連線與前端協作 | 不由 pytest 涵蓋率推論 |

`tests/conftest.py` 的一般 `db_session` fixture 使用 SQLite `StaticPool`，
以 ORM metadata 建表。這適合 API 行為測試，但不等於 migration 或 PostgreSQL 驗證。
一般 `client` fixture 覆寫登入身分；`test_auth.py` 另以本機 RSA 簽章驗證真正的 JWT
與 REST／WebSocket 邊界，僅替換 JWKS 取得方式。測試不需 Supabase 或遠端 Auth 憑證。

## 單元與應用整合測試

```bash
hatch run test
hatch run pytest tests/test_auth.py tests/test_websocket.py
hatch run pytest tests/test_pose_visibility.py tests/test_pk_websocket.py
hatch run pytest tests/test_body_profile_api.py tests/test_api.py
```

未設定 `PERSONAI_TEST_POSTGRES_URL` 時，PostgreSQL 案例會明確 skip；若已設定但
服務無法連線或權限不足，則會失敗，不會假裝通過。

新增測試先列出應保護的行為與失敗情境。Pose 幾何需要真實運算時，建立 landmark
座標，保留正式角度計算與濾波器。網路邊界可替換為本機 JWKS 金鑰，不能把所有驗證
與授權一併 mock 掉。時間由 frame timestamp 或模組內可控時鐘驅動，不依賴 sleep。
不得為通過測試調整 squat／push-up／visibility 門檻或 PK 計分規則。

## 涵蓋率與 JUnit

```bash
# 終端明細 + coverage.xml；原有 70% gate 不變
hatch run test-cov

# 與 CI 相同的單次 pytest 執行，同時產生 coverage 與 JUnit
hatch run test-cov --junitxml=test-results/junit.xml -o junit_family=legacy
```

使用 pytest-cov，包含 line 與 branch coverage；`--cov=personai_api` 量測正式套件。
XML 與終端結果來自同一次執行，不另外跑一套測試製造重複上傳。
JUnit 記錄案例、失敗、skip 與耗時；legacy 格式提供 Codecov 可使用的檔案位置資訊。
`coverage.xml`、`.coverage*` 與 `test-results/` 都不納入版本控制。

## 本機 PostgreSQL

現有 `compose.test.yml` 使用獨立 PostgreSQL 17 服務，連接埠為 55432：

```bash
docker compose -f compose.test.yml up -d --wait
PERSONAI_TEST_POSTGRES_URL=postgresql://personai:personai_test_only@127.0.0.1:55432/personai_test \
  hatch run pytest -m postgres

# 完整套件包含 PostgreSQL，仍保留原 coverage gate
PERSONAI_TEST_POSTGRES_URL=postgresql://personai:personai_test_only@127.0.0.1:55432/personai_test \
  hatch run test-cov --junitxml=test-results/junit.xml -o junit_family=legacy

docker compose -f compose.test.yml down
```

這些是公開的本機測試帳密。不要改成正式資料庫的連線字串。
也可使用自己啟動、UTF-8 編碼的本機 PostgreSQL 17，但需先建立 `personai_test`。
測試帳號需要建立／刪除測試資料庫、建立測試角色及 `SET ROLE` 的權限；
建議使用專用可丟棄 instance，與 CI 的測試 superuser 相同。不要對一般共用開發 instance 執行。

`tests/test_postgres.py` 只接受 loopback host（127.0.0.1／localhost／::1）、
名為 `personai_test` 的入口資料庫與無 query options 的 PostgreSQL URL。
它不讀取 `DATABASE_URL` 作為測試目標，而是自行建立 `personai_test_<uuid>`，
只在這些資料庫執行 migration 與資料寫入，結束後刪除。
為覆蓋正式 migration 的條件分支，fixture 會在專用本機 instance 建立缺少的
`anon`／`authenticated` NOLOGIN 角色、預設 table grants，結束後只移除自己建立的角色。
不要平行啟動多份此組測試於同一個 PostgreSQL instance。

測試會確認四張應用表均啟用 RLS，且兩個 Data API 角色的 SELECT／INSERT／UPDATE／
DELETE／TRUNCATE 權限被撤銷；也實際 `SET LOCAL ROLE` 嘗試讀取。
另在 rollback transaction 中暫時 grant SELECT，確認有資料仍被 RLS 隱藏。
這驗證本機 migration 的 SQL 行為，不能證明遠端 Supabase 部署設定已正確套用。

## Migration 測試

```bash
hatch run pytest tests/test_migrations.py
PERSONAI_TEST_POSTGRES_URL=postgresql://personai:personai_test_only@127.0.0.1:55432/personai_test \
  hatch run pytest tests/test_postgres.py
```

涵蓋舊 InBody 資料回填、PostgreSQL timezone-aware timestamp、composition CHECK constraint、
nullable 熱量的獨立 session 持久化，以及有未知熱量時 `0004` 降版拒絕且保留資料／revision。
移除測試自建資料後才測試降到 base、再升到 head 與 `alembic check`，不填入假的熱量。
`alembic check` 驗證 ORM 與 migration schema 一致，不能取代資料與權限斷言。

CI 保留原本對服務入口資料庫的 upgrade，並追加 schema 檢查。若要手動執行等效指令，
先確認下面 URL 指向可丟棄的本機測試服務：

```bash
DATABASE_URL=postgresql://personai:personai_test_only@127.0.0.1:55432/personai_test \
  hatch run alembic upgrade head
DATABASE_URL=postgresql://personai:personai_test_only@127.0.0.1:55432/personai_test \
  hatch run alembic check
```

不要在測試指令中使用 `.env` 的遠端連線執行 migration。
`0003` 降版會刪除新身體資料表，`0002` 降版停用 RLS；測試可降版不代表正式環境可安全回復。

## Codecov 與 Test Analytics

CI 在 `push` 與 `pull_request` 執行，同一個 quality job 各上傳一次：

- `codecov/codecov-action@v6`，`report_type: coverage`，只上傳 `./coverage.xml`。
- 同一個 Action，`report_type: test_results`，只上傳 `./test-results/junit.xml`。

兩者使用 `disable_search: true` 與 `plugins: noop`，避免自動搜到其他報表。
上傳步驟在 pytest 失敗但已有報表時仍執行；取消工作或報表不存在時跳過。
pytest、lint、typing 與 Alembic 維持會使 CI 失敗；Codecov 上傳本身初期為非阻擋。
沒有使用已淘汰的獨立 `codecov/test-results-action`。

OIDC 需要 quality job 的 `id-token: write`；另只有 `contents: read`，不需要
`pull-requests: write` 或硬編碼 upload token。Codecov GitHub App 處理網站上的 PR
呈現與整合，不能因此推論 Actions token 需要 repository write 權限。
v6 對 fork PR 自動改用 public repository 的 tokenless upload，不用 `pull_request_target`。

`codecov.yml` 將 project 設為 `target: auto`、`threshold: 1%`，patch 設為
`target: 80%`，兩者均 `informational: true`。不要立即加入 GitHub required checks。
初次比較需先有 default branch 的報表；缺少 base 時的 Codecov 畫面不代表測試失敗。

Test Analytics 可呈現失敗案例／stack trace、耗時與歷史失敗率；flaky 判定需要多次 CI
結果，不能靠單次綠燈宣稱沒有 flaky tests。官方目前說明：公開／開源儲存庫各方案可用
flaky detection，私人儲存庫需 Pro／Enterprise。資料目前保留 60 天；方案與額度可能變更。
本次查核此 GitHub repository 為 public，未存取或確認擁有者的 Codecov 帳號／方案。

維護者尚需完成的外部設定：

1. 使用 GitHub 登入 Codecov，安裝／授權 [Codecov GitHub App](https://github.com/apps/codecov)
   存取 `YinCheng0106/personai-api`，確認儲存庫已啟用。
2. 於 [repository 頁面](https://app.codecov.io/gh/YinCheng0106/personai-api) 確認 default
   branch 為 `main`。本流程採 OIDC，不需新增 `CODECOV_TOKEN` secret。
3. 在這份 workflow 經正常 review 整合後，確認一次 `main` push 與一次 PR 的 Actions
   兩個 upload 步驟成功，並在 Codecov 分別看到 coverage 與 Tests。若 UI 有 Test Analytics
   啟用提示，完成該 repository 的啟用。fork PR 的 tokenless 路徑也需首輪 CI 實測。
4. 確認方案與功能權限；不修改 GitHub branch protection，不把 informational 狀態加為 required。

本機僅驗證報表與設定，不會上傳本機結果冒充 CI。設定查核依據（2026-10-15）：
[Action v6 inputs](https://github.com/codecov/codecov-action/blob/v6/action.yml)、
[狀態設定](https://docs.codecov.com/docs/commit-status)、
[YAML 驗證](https://docs.codecov.com/docs/codecov-yaml)、
[Test Analytics](https://docs.codecov.com/docs/test-analytics)、
[功能與方案](https://about.codecov.io/product/feature/test-analytics/)、
[tokenless uploads](https://docs.codecov.com/docs/codecov-tokens)。
部分 Test Analytics 舊範例仍寫獨立 action；應以目前 Action v6 的 `report_type` input 為準。

## 風險盤點與待補證據

2026-10-15 的實作前基準：`main` = `a47d67a`，74 tests passed，line／branch 合計
86.74%，最低門檻 70%。既有模板與 CONTRIBUTING／TESTING／Codecov 設定均不存在，
因此本次新增；既有 README、身體資料契約與測試均保留。

本次本機驗證（macOS、Python 3.13.11、PostgreSQL 17.11；尚未執行遠端 CI）：

| 執行方式 | 結果 | Line／branch 合計涵蓋率 |
| --- | --- | --- |
| 變更前 `hatch run test-cov` | 74 passed | 86.74% |
| 變更後，不啟用 PostgreSQL | 118 passed、4 skipped | 90.56% |
| 變更後，啟用本機 PostgreSQL 並產生 JUnit | 122 passed、0 skipped | 90.68% |

共新增 48 個參數化後的測試案例，既有測試函式全部保留。lint、typing、
`git diff --check`、本機 `alembic upgrade head`／`alembic check` 與 actionlint 均通過。
Codecov 官方 `https://codecov.io/validate` 回覆 `Valid!`；JUnit 的案例數、耗時與檔案屬性、
兩份 XML 及模板 YAML 已解析檢查。僅保留既有 SQLite datetime adapter deprecation warning。

| 優先序 | 缺口與處置 |
| --- | --- |
| P0 | Squat 誤判：`e86e2aa` 在 `fix/squat-false-positive`，查核時尚未整合 main；本分支沒有合併或重做它。其真實幾何回歸測試需於修正整合後重新驗證。 |
| P0 | Pose Guard：補 acquisition streak 中斷、LOST 後 reset 等同新 analyzer，以及真正 WebSocket frame → Guard → recovery 路徑。 |
| P0 | Frame 驗證：補 malformed JSON／非物件、kind、無效 frame id／timestamp、UTF-8 byte limit、速率視窗到期邊界與拒絕後的連線清理；速率測試使用可控時鐘。 |
| P0 | JWT／Origin：補必要 claims、空／非字串 subject、禁用演算法、JWKS 故障 fail-closed、實際簽章 JWT 經 REST／WebSocket 與精確 Origin／subprotocol。 |
| P0 | Ownership／unknown：補另一人的體重不能啟用熱量、measured history 不回填假 current defaults、失敗修正不寫入部分欄位。 |
| P1 | PK：補 duplicate user／READY／score、跳分與無效 score、離開後 ready 清除、替補玩家與房間清理。 |
| P1 | Persistence／calorie／history：補 PostgreSQL 跨 session NULL 保留、混合已知 0／未知熱量 aggregate，以及歷史修正重排與衍生值清除。 |
| P1 | Database failures／migrations：既有 profile lookup 失敗降級測試保留；補降版失敗的資料保留、PostgreSQL 回填／CHECK／RLS／ACL 與升降版。REST commit 失敗的完整 HTTP 錯誤契約仍需後續定義與故障注入。 |

未涵蓋的後續情境包括：PK 廣播送出失敗／同時加入離開、跨 worker 的房間狀態、真實 JWKS
rotation／HTTP 故障、實際部署 Origin 設定與 SQLite 的降版拒絕分支。
這些不是以增加公式或 boilerplate 測試即可替代的風險。

## 真人 Pose 驗收

自動測試證明指定輸入下的程式行為，不能量測真實運動辨識準確率。
正式驗收仍需取得同意的真人案例，記錄影像條件與人工標註的完整／未完成次數：

- squat／push-up 完整動作、淺蹲／半程、坐姿擺腿與非運動活動。
- 身體局部出框、遮蔽、短暫消失與重新進入畫面，確認不產生 ghost reps。
- 不同鏡頭視角、距離、長寬比、光線、衣著與體型；左右側可見度與提示是否合理。
- reset、重新連線及 PK ready／斷線，確認前後端顯示一致。
- 未提供體重時顯示熱量不可用；提供資料時仍只稱為公式估算。

在深蹲修正尚未整合前，不把此分支的綠色測試結果當成坐姿誤判問題已解決的證據。
