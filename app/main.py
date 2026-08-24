from fastapi import FastAPI

from app.api.routes import router

app = FastAPI(
    title="FAB AI Assistant",
    version="0.1.0",
    description="SMT2020 기반 FAB 운영 질의 응답 API",
)
app.include_router(router, prefix="/api")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}

