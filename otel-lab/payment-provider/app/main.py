from __future__ import annotations

from fastapi import FastAPI


app = FastAPI(title="EvoSRE payment provider", version="1.0.0")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/primary/authorize")
async def primary_authorize() -> dict[str, str]:
    return {"status": "authorized", "provider": "primary-pay"}


@app.post("/backup/authorize")
async def backup_authorize() -> dict[str, str]:
    return {"status": "authorized", "provider": "backup-pay"}
