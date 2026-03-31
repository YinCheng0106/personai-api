# PesonAI-api
AI 智慧健身教練後端服務，使用 FastAPI 框架，整合 MediaPipe 姿態偵測、OpenCV 影像處理與 WebSocket 即時串流

## Development Commands

```bash
# 安裝依賴
pip install -e .

# 啟動開發伺服器
fastapi dev src/personai-api/main.py

# 型別檢查
hatch run types:check

# 執行測試 (搭配 coverage)
hatch run pytest
hatch run pytest tests/test_specific.py::test_name   # 單一測試
```

## Architecture

- **Framework:** FastAPI + Pydantic models
- **Build system:** Hatchling (pyproject.toml)，版本由 `src/personai-api/__about__.py` 管理
- **Entrypoint:** `personai-api.main:app`
- **Source layout:** `src/personai-api/` (src layout pattern)
- **Python:** >=3.11

### Key Dependencies

| 套件 | 用途 |
|------|------|
| mediapipe | 姿態捕捉 (pose detection) |
| opencv-python | 影像處理 |
| websockets | 即時影像串流通訊 |
| numpy | 幾何角度與矩陣運算 |
| pandas | InBody 數據與運動紀錄處理 |
| pyttsx3 | 文字轉語音 |