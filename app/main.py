from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router

app = FastAPI(
    title="FAB AI Assistant",
    version="0.1.0",
    description="SMT2020 기반 FAB 운영 질의 응답 API",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:8501",
        "http://127.0.0.1:8501",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(router, prefix="/api")


@app.get("/")
def root() -> dict[str, str]:
    return {
        "message": "FAB AI Assistant API",
        "docs": "/docs",
        "health": "/health",
        "frontend": "http://localhost:8501",
    }


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
