import os
import httpx
from app.db import connect


def dependency_status():
    status = {}
    try:
        with connect() as conn:
            conn.execute("SELECT 1").fetchone()
        status["postgresql"] = "ok"
    except Exception:
        status["postgresql"] = "unavailable"
    try:
        base = os.environ.get("CHROMA_URL", "http://chroma:8000")
        response = httpx.get(base + "/api/v2/heartbeat", timeout=3, follow_redirects=False)
        response.raise_for_status()
        status["chromadb"] = "ok"
    except Exception:
        status["chromadb"] = "unavailable"
    return status
