from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from personai_api.config import get_settings
from personai_api.routers import analyze, inbody, pk, server, user, workout

settings = get_settings()
app = FastAPI(title="PersonAI")

app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.cors_origins),
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)

# 掛載所有 router
app.include_router(server.router)
app.include_router(user.router)
app.include_router(inbody.router)
app.include_router(workout.router)
app.include_router(analyze.router)
app.include_router(pk.router)