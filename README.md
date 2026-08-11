# PersonAI API

AI-powered fitness coach backend — real-time biomechanical analysis, rep counting, and form error detection.

The frontend (Next.js) performs pose detection via MediaPipe and streams 33 body keypoints over WebSocket to the backend. The backend focuses exclusively on **angle computation**, **FSM-based state tracking**, **form error detection**, and **real-time calorie estimation** — a lightweight, separation-of-concerns architecture with zero computer vision dependencies on the server.

**English** | [繁體中文](./README_zh_tw.md)

## Architecture

```
┌─────────────────────────┐         WebSocket (JSON)         ┌──────────────────────────┐
│        Frontend         │ ──────────────────────────────▶  │        Backend           │
│       (Next.js)         │                                  │     (FastAPI)            │
│                         │  33 keypoints / frame            │                          │
│  Webcam                 │ ◀──────────────────────────────  │  One-Euro Filter         │
│    ↓                    │      Analysis result JSON        │    ↓                     │
│  MediaPipe Pose         │                                  │  Angle calculation       │
│    ↓                    │                                  │    ↓                     │
│  Keypoint extraction    │                                  │  FSM state machine       │
│    ↓                    │                                  │    ↓                     │
│  JSON dispatch          │                                  │  Error detection +       │
│                         │                                  │  calorie tracking        │
└─────────────────────────┘                                  └──────────────────────────┘
```

## Features

- **Real-time motion analysis** — frame-by-frame keypoint processing via WebSocket, returning angles, rep counts, and error alerts
- **Finite State Machine (FSM)** — precise phase tracking (IDLE → DESCENDING → BOTTOM → ASCENDING) with hysteresis to prevent miscounts
- **Form error detection** — squat: knee valgus, excessive forward lean; push-up: hip sagging, hip piking
- **One-Euro Filter** — adaptive low-pass smoothing that reduces landmark jitter while maintaining low latency
- **METs calorie estimation** — metabolic equivalent-based real-time calorie accumulation by exercise type and body weight
- **InBody body composition** — BMI / BMR / lean body mass calculations with Katch-McArdle formula
- **Workout statistics** — Pandas-powered grouped reports and daily summaries

## Supported Exercises

| Exercise | WebSocket Path | FSM States | Detected Errors |
|----------|---------------|------------|-----------------|
| Squat | `/ws/analyze/squat` | IDLE → DESCENDING → BOTTOM → ASCENDING | Knee valgus, insufficient depth, excessive forward lean |
| Push-up | `/ws/analyze/pushup` | UP → DESCENDING → BOTTOM → ASCENDING | Hip sagging, hip piking, insufficient depth |

## Getting Started

### Prerequisites

- Python >= 3.11

### Installation

```bash
git clone https://github.com/YinCheng0106/personai-api.git
cd personai-api
pip install -e .
```

### Start Development Server

```bash
fastapi dev src/personai_api/main.py
```

The server starts at `http://localhost:8000` by default. Interactive API docs are available at `/docs`.

## API Endpoints

### REST API

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/server` | Server health check |
| `GET` | `/user/me` | Get identity from the verified JWT |
| `POST` | `/inbody/me` | Save / update the signed-in user's InBody data |
| `GET` | `/inbody/me` | Get the signed-in user's BMI, BMR, and LBM |
| `POST` | `/inbody/me/calories` | Calculate exercise calorie expenditure |
| `GET` | `/wk/me` | Get the signed-in user's workout records |
| `POST` | `/wk/me/record` | Save a workout record |
| `GET` | `/wk/me/summary` | Workout statistics by exercise type |
| `GET` | `/wk/me/daily` | Daily summary for heatmaps and charts |

All `/me` endpoints require `Authorization: Bearer <jwt>`. The user ID comes only from the verified JWT `sub`; client-supplied IDs are ignored.

### WebSocket API

#### `WS /ws/analyze/{exercise_type}?weight_kg=70`

Real-time biomechanical analysis. Each connection maintains its own independent FSM state and rep counter.

The client must request WebSocket subprotocols `["personai.v1", "<jwt>"]`. The server validates the JWT and Origin before accepting, then replies with only `personai.v1`. Frames include `frame_id`; responses echo it and add `processing_ms`.

**Query Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `exercise_type` | `string` | — | Exercise type: `squat` or `pushup` |
| `weight_kg` | `float` | `70.0` | User body weight (kg) for calorie calculation |

**Request — Keypoints Frame:**

```json
{
  "keypoints": [
    { "x": 0.5, "y": 0.3, "z": 0.0, "visibility": 0.99 },
    { "x": 0.6, "y": 0.4, "z": 0.0, "visibility": 0.95 }
  ],
  "timestamp": 1234567890.123
}
```

> The `keypoints` array must contain all 33 MediaPipe Pose landmarks. `timestamp` is optional.

**Response — Analysis Result:**

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

**Control Command — Reset Counter:**

```json
{ "action": "reset" }
```

Response: `{ "action": "reset", "status": "ok" }`

## Project Structure

```
src/personai_api/
├── main.py                             # FastAPI app entrypoint
├── __about__.py                        # Version number
├── models/
│   ├── biomechanics_schema.py          # WebSocket I/O schemas
│   ├── inbody_schema.py                # InBody physiological data schemas
│   ├── server_schema.py                # Server status schema
│   ├── user_schema.py                  # User schema
│   └── workout_schema.py              # Workout record schemas
├── routers/
│   ├── analyze.py                      # WebSocket real-time analysis endpoint
│   ├── inbody.py                       # InBody physiological data API
│   ├── server.py                       # Server health check
│   ├── user.py                         # User profile API
│   └── workout.py                      # Workout record API
└── services/
    ├── biomechanics.py                 # Angle calculation, filters, FSM
    └── inbody.py                       # BMR/BMI calculation, METs calories, Pandas reports
```

## Core Algorithms

### One-Euro Filter

An adaptive low-pass filter based on [Casiez et al. (CHI 2012)](https://dl.acm.org/doi/10.1145/2207676.2208639). It applies strong smoothing when signals change slowly (reducing jitter) and reduces smoothing when signals change rapidly (reducing latency). Each landmark's x and y coordinates have their own filter instance (33 landmarks x 2 = 66 filters).

### FSM (Finite State Machine)

Hysteresis thresholds prevent state oscillation, ensuring accurate rep counting:

```
Squat:   IDLE ──knee<155°──▶ DESCENDING ──knee≤100°──▶ BOTTOM ──knee>105°──▶ ASCENDING ──knee≥160°──▶ IDLE (+1 rep)
Push-up: UP ──elbow<155°──▶ DESCENDING ──elbow≤90°──▶ BOTTOM ──elbow>95°──▶ ASCENDING ──elbow≥160°──▶ UP (+1 rep)
```

### METs Calorie Calculation

```
Calories (kcal) = METs × Weight (kg) × Duration (hr)
```

| Exercise | Light | Moderate | Vigorous |
|----------|-------|----------|----------|
| Squat | 3.5 | 5.0 | 8.0 |
| Push-up | 3.8 | 5.5 | 8.0 |

> Source: Ainsworth BE, et al. "Compendium of Physical Activities" (2011)

## Development

```bash
# Type checking
hatch run types:check

# Run tests
hatch run test

# Coverage (minimum 70%)
hatch run test-cov

# Apply application migrations
hatch run alembic upgrade head

# Run a specific test
hatch run pytest tests/test_specific.py::test_name
```

For an existing Supabase database that already has `workout_records` and `inbody_profiles`, first verify their columns, run `hatch run alembic stamp 0001`, then `hatch run alembic upgrade head`. This preserves existing `u001` rows and applies the RLS/Data API protection migration only.

NAS deployment templates are provided in `Dockerfile`, `compose.production.yml`, `deploy/nginx.conf`, and `.env.production.example`. Replace example domains and certificate paths before use.

## Tech Stack

| Technology | Purpose |
|------------|---------|
| [FastAPI](https://fastapi.tiangolo.com/) | Async web framework + WebSocket |
| [Pydantic](https://docs.pydantic.dev/) | Data validation and serialization |
| [NumPy](https://numpy.org/) | Vector angle calculation and matrix operations |
| [Pandas](https://pandas.pydata.org/) | Workout data statistical reports |
| [Hatch](https://hatch.pypa.io/) | Build system and environment management |

## License

MIT License
