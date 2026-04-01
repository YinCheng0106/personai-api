from fastapi import FastAPI

from src.personai_api.routers import analyze, inbody, server, user, workout

app = FastAPI(title="PersonAI")  # API 主程式

# 掛載所有 router
app.include_router(server.router)
app.include_router(user.router)
app.include_router(inbody.router)
app.include_router(workout.router)
app.include_router(analyze.router)
