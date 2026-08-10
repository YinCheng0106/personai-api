from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from personai_api.database import init_db
from personai_api.routers import analyze, inbody, server, user, workout


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(title="PersonAI", lifespan=lifespan)  # API 主程式

# 本機前後端分離開發來源。allow_credentials=True 預留給後續 Session Cookie 串接。
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "https://localhost:3000",
        "https://127.0.0.1:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 掛載所有 router
app.include_router(server.router)
app.include_router(user.router)
app.include_router(inbody.router)
app.include_router(workout.router)
app.include_router(analyze.router)
