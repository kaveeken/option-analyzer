"""
Health check endpoint.

Provides a simple endpoint to verify the API is running.
"""

import subprocess
from pathlib import Path

from fastapi import APIRouter

from ..schemas import HealthCheckResponse

router = APIRouter(tags=["health"])


def get_git_commit() -> str:
    """Get the current git commit hash, or 'unknown' if not available."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            cwd=Path(__file__).parent.parent.parent,
            timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()[:8]
    except Exception:
        pass
    return "unknown"


# Cache the commit hash at module load time
_COMMIT = get_git_commit()


@router.get("/health", response_model=HealthCheckResponse)
async def health_check() -> HealthCheckResponse:
    """
    Health check endpoint.

    Returns:
        Service status, version, and git commit information

    Example:
        GET /health
        Response: {"status": "healthy", "version": "0.1.0", "commit": "abc12345"}
    """
    return HealthCheckResponse(status="healthy", version="0.1.0", commit=_COMMIT)
