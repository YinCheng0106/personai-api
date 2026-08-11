"""Send fixed 33-point frames at 12 FPS and report WebSocket RTT percentiles."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import time

from websockets.asyncio.client import connect


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="ws://localhost:8000/ws/analyze/squat")
    parser.add_argument("--origin", default="http://localhost:3000")
    parser.add_argument("--duration", type=float, default=600)
    parser.add_argument("--fps", type=float, default=12)
    return parser.parse_args()


async def run() -> None:
    args = parse_args()
    token = os.getenv("PERSONAI_TEST_JWT")
    if not token:
        raise SystemExit("Set PERSONAI_TEST_JWT to a current Better Auth JWT")

    latencies: list[float] = []
    started = time.monotonic()
    frame_id = 0
    async with connect(
        args.url,
        origin=args.origin,
        subprotocols=["personai.v1", token],
        max_size=65536,
    ) as websocket:
        while time.monotonic() - started < args.duration:
            frame_started = time.perf_counter()
            frame_id += 1
            await websocket.send(
                json.dumps(
                    {
                        "frame_id": frame_id,
                        "timestamp": time.time(),
                        "keypoints": [
                            {"x": 0.5, "y": 0.5, "z": 0, "visibility": 1}
                            for _ in range(33)
                        ],
                    }
                )
            )
            response = json.loads(await websocket.recv())
            if response.get("frame_id") != frame_id:
                raise RuntimeError("frame_id mismatch")
            latencies.append((time.perf_counter() - frame_started) * 1000)
            await asyncio.sleep(
                max(0, 1 / args.fps - (time.perf_counter() - frame_started))
            )

    ordered = sorted(latencies)
    p95_index = max(0, int(len(ordered) * 0.95) - 1)
    print(f"frames={len(ordered)}")
    print(f"average_ms={statistics.fmean(ordered):.2f}")
    print(f"p95_ms={ordered[p95_index]:.2f}")


if __name__ == "__main__":
    asyncio.run(run())
