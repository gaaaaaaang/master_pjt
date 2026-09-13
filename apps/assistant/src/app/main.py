import psycopg
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from psycopg import sql

from app.api.routes import router
from app.config import get_settings
from app.db.fab_catalog import ALLOWED_FABS

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
        "http://localhost:5173",
        "http://127.0.0.1:5173",
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
        "frontend": "http://localhost:5173",
    }


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


def database_readiness() -> dict:
    """Check demo observation availability without running a model or exposing credentials."""
    dsn = get_settings().postgres_dsn
    if not dsn:
        return {"status": "degraded", "database": "not_configured"}
    try:
        fabs = {}
        with psycopg.connect(dsn, connect_timeout=2) as conn, conn.cursor() as cur:
            cur.execute("SET TRANSACTION READ ONLY")
            cur.execute("SET LOCAL statement_timeout = '1s'")
            for fab in sorted(ALLOWED_FABS):
                table = f"live_process_snapshots_{fab}"
                cur.execute("SELECT to_regclass(%s)", (f"{fab}.{table}",))
                if cur.fetchone()[0] is None:
                    fabs[fab] = "missing_table"
                    continue
                cur.execute(sql.SQL("SELECT EXISTS (SELECT 1 FROM {}.{})").format(
                    sql.Identifier(fab), sql.Identifier(table)))
                fabs[fab] = "ready" if cur.fetchone()[0] else "empty"
        return {"status": "ready" if all(v == "ready" for v in fabs.values()) else "degraded",
                "database": "connected", "fabs": fabs}
    except psycopg.Error:
        return {"status": "degraded", "database": "unavailable"}


@app.get("/health/ready")
def readiness() -> JSONResponse:
    result = database_readiness()
    return JSONResponse(result, status_code=200 if result["status"] == "ready" else 503)
