# PersonAI API

AI 智慧健身教練後端服務 — 即時生物力學分析、動作計數與姿勢錯誤偵測。

前端（Next.js）透過 MediaPipe 進行姿態偵測，將 33 個人體關鍵點座標經 WebSocket 傳送至後端；後端專注於 **角度計算**、**FSM 狀態機判定**、**姿勢錯誤偵測** 與 **即時卡路里精算**，實現前後端職責分離的輕量化架構。

[English](./README.md) | **繁體中文**

## 系統架構

```
┌─────────────────────────┐         WebSocket (JSON)         ┌──────────────────────────┐
│        Frontend         │ ──────────────────────────────▶  │        Backend           │
│       (Next.js)         │                                  │     (FastAPI)            │
│                         │  33 keypoints / frame            │                          │
│  Webcam                 │ ◀──────────────────────────────  │  One-Euro Filter         │
│    ↓                    │     分析結果 JSON                 │    ↓                     │
│  MediaPipe Pose         │                                  │  角度計算 (NumPy)         │
│    ↓                    │                                  │    ↓                     │
│  Keypoints 擷取         │                                  │  FSM 狀態機              │
│    ↓                    │                                  │    ↓                     │
│  JSON 送出              │                                  │  錯誤偵測 + 卡路里        │
└─────────────────────────┘                                  └──────────────────────────┘
```

## 功能特色

- **即時動作分析** — WebSocket 逐幀接收 keypoints，回傳角度、計數、錯誤提示
- **FSM 狀態機** — 精準追蹤動作階段（IDLE → DESCENDING → BOTTOM → ASCENDING），避免誤計
- **姿勢錯誤偵測** — 深蹲膝蓋內扣、軀幹前傾；伏地挺身身體下沉、臀部過高
- **One-Euro Filter** — 自適應平滑濾波，減少關鍵點抖動同時保持低延遲
- **METs 卡路里精算** — 基於代謝當量，依運動類型與體重即時累計消耗卡路里
- **InBody 生理數據** — BMI / BMR / 淨體重計算，支援 Katch-McArdle 公式
- **運動紀錄統計** — Pandas 分組統計報表與每日摘要

## 支援的運動類型

| 運動 | WebSocket 路徑 | FSM 狀態 | 偵測的錯誤 |
|------|---------------|----------|-----------|
| 深蹲 (Squat) | `/ws/analyze/squat` | IDLE → DESCENDING → BOTTOM → ASCENDING | 膝蓋內扣、深度不足、軀幹過度前傾 |
| 伏地挺身 (Push-up) | `/ws/analyze/pushup` | UP → DESCENDING → BOTTOM → ASCENDING | 身體下沉、臀部過高、深度不足 |

## 快速開始

### 環境需求

- Python >= 3.11

### 安裝

```bash
git clone https://github.com/YinCheng0106/personai-api.git
cd personai-api
pip install -e .
```

### 啟動開發伺服器

```bash
fastapi dev src/personai_api/main.py
```

伺服器預設於 `http://localhost:8000` 啟動，互動式 API 文件位於 `/docs`。

## API 端點

### REST API

| 方法 | 路徑 | 說明 |
|------|------|------|
| `GET` | `/server` | 伺服器健康檢查 |
| `GET` | `/user/me` | 從已驗證 JWT 取得目前使用者身分 |
| `POST` | `/inbody/me` | 儲存／更新登入者的 InBody 數據 |
| `GET` | `/inbody/me` | 取得登入者的 BMI、BMR、LBM |
| `POST` | `/inbody/me/calories` | 計算特定運動消耗卡路里 |
| `GET` | `/wk/me` | 取得登入者的運動紀錄 |
| `POST` | `/wk/me/record` | 儲存單次運動紀錄 |
| `GET` | `/wk/me/summary` | 依運動類型統計 |
| `GET` | `/wk/me/daily` | 每日統計摘要（供熱力圖使用） |

所有 `/me` 端點皆需 `Authorization: Bearer <jwt>`。使用者 ID 只讀取已驗證 JWT 的 `sub`，不接受前端自填 ID。

### WebSocket API

#### `WS /ws/analyze/{exercise_type}?weight_kg=70`

即時生物力學分析。每個連線維持獨立的 FSM 狀態與計數器。

前端必須以 `["personai.v1", "<jwt>"]` 要求 WebSocket subprotocol。後端驗證 JWT 與 Origin 後才接受連線，並只回應 `personai.v1`。每幀需帶 `frame_id`，回應會帶回同值與 `processing_ms`。

**Query Parameters:**

| 參數 | 型別 | 預設值 | 說明 |
|------|------|--------|------|
| `exercise_type` | `string` | — | 運動類型：`squat` 或 `pushup` |
| `weight_kg` | `float` | `70.0` | 使用者體重（公斤），用於卡路里計算 |

**送出格式 — Keypoints Frame:**

```json
{
  "keypoints": [
    { "x": 0.5, "y": 0.3, "z": 0.0, "visibility": 0.99 },
    { "x": 0.6, "y": 0.4, "z": 0.0, "visibility": 0.95 }
  ],
  "timestamp": 1234567890.123
}
```

> `keypoints` 陣列需包含完整的 33 個 MediaPipe Pose landmarks，`timestamp` 為選填。

**回傳格式 — 分析結果:**

```json
{
  "rep_count": 5,
  "state": "descending",
  "angles": {
    "left_knee": 95.2,
    "right_knee": 97.8,
    "left_hip": 85.1,
    "right_hip": 86.3
  },
  "errors": ["膝蓋內扣：請將膝蓋對齊腳尖方向"],
  "confidence": 0.85,
  "is_visible": true,
  "calories": 12.5
}
```

**控制指令 — 重置計數器:**

```json
{ "action": "reset" }
```

回傳：`{ "action": "reset", "status": "ok" }`

## 專案結構

```
src/personai_api/
├── main.py                             # FastAPI app 入口
├── __about__.py                        # 版本號
├── models/
│   ├── biomechanics_schema.py          # WebSocket I/O schemas
│   ├── inbody_schema.py                # InBody 生理數據 schemas
│   ├── server_schema.py                # 伺服器狀態 schema
│   ├── user_schema.py                  # 使用者 schema
│   └── workout_schema.py              # 運動紀錄 schemas
├── routers/
│   ├── analyze.py                      # WebSocket 即時分析端點
│   ├── inbody.py                       # InBody 生理數據 API
│   ├── server.py                       # 伺服器健康檢查
│   ├── user.py                         # 使用者資料 API
│   └── workout.py                      # 運動紀錄 API
└── services/
    ├── biomechanics.py                 # 角度計算、濾波器、FSM 狀態機
    └── inbody.py                       # BMR/BMI 計算、METs 卡路里、Pandas 報表
```

## 核心演算法

### One-Euro Filter

基於 [Casiez et al. (CHI 2012)](https://dl.acm.org/doi/10.1145/2207676.2208639) 的自適應低通濾波器。訊號緩慢變化時強力平滑以減少抖動，快速變化時降低平滑以減少延遲。每個關鍵點的 x、y 座標各配置一個濾波器（33 點 x 2 = 66 個濾波器）。

### FSM（有限狀態機）

以遲滯區間（hysteresis）防止狀態抖動，確保計數精準：

```
深蹲：   IDLE ──膝角<155°──▶ DESCENDING ──膝角≤100°──▶ BOTTOM ──膝角>105°──▶ ASCENDING ──膝角≥160°──▶ IDLE (+1 rep)
伏地挺身：UP ──肘角<155°──▶ DESCENDING ──肘角≤90°───▶ BOTTOM ──肘角>95°───▶ ASCENDING ──肘角≥160°──▶ UP (+1 rep)
```

### METs 卡路里計算

```
卡路里 (kcal) = METs × 體重 (kg) × 時間 (hr)
```

| 運動 | 輕度 | 中度 | 高強度 |
|------|------|------|--------|
| 深蹲 | 3.5 | 5.0 | 8.0 |
| 伏地挺身 | 3.8 | 5.5 | 8.0 |

> 資料來源：Ainsworth BE, et al. "Compendium of Physical Activities" (2011)

## 開發指令

```bash
# 型別檢查
hatch run types:check

# 執行測試
hatch run test

# Coverage（最低 70%）
hatch run test-cov

# 套用應用程式 migration
hatch run alembic upgrade head

# 執行單一測試
hatch run pytest tests/test_specific.py::test_name
```

若 Supabase 已有 `workout_records` 與 `inbody_profiles`，請先核對欄位，再執行 `hatch run alembic stamp 0001`，最後執行 `hatch run alembic upgrade head`。這會保留既有 `u001` 資料，只套用 RLS／Data API 保護 migration。

NAS 部署範本位於 `Dockerfile`、`compose.production.yml`、`deploy/nginx.conf` 與 `.env.production.example`；使用前必須替換示範網域與憑證路徑。

## 技術棧

| 技術 | 用途 |
|------|------|
| [FastAPI](https://fastapi.tiangolo.com/) | 非同步 Web 框架 + WebSocket |
| [Pydantic](https://docs.pydantic.dev/) | 資料驗證與序列化 |
| [NumPy](https://numpy.org/) | 向量角度計算與矩陣運算 |
| [Pandas](https://pandas.pydata.org/) | 運動數據統計報表 |
| [Hatch](https://hatch.pypa.io/) | 建置系統與環境管理 |

## 授權條款

MIT License
