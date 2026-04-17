"""
FastAPI middleware for error handling and request processing.

Converts domain exceptions into appropriate HTTP responses.
"""

import logging

from fastapi import Request, status
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

from ..utils.exceptions import (
    AmbiguousSymbolError,
    IBKRAPIError,
    IBKRConnectionError,
    InsufficientDataError,
    InvalidQuantityError,
    MissingBidAskError,
    MixedExpirationError,
    OptionAnalyzerError,
    RateLimitedError,
    SessionExpiredError,
    SymbolNotFoundError,
    ValidationError,
)
from .schemas import ErrorResponse


async def error_handler_middleware(request: Request, call_next):
    """
    Catch domain exceptions and convert them to HTTP error responses.

    Args:
        request: FastAPI request
        call_next: Next middleware/handler in chain

    Returns:
        Response or JSONResponse with error details

    Exception Mapping:
        - SymbolNotFoundError → 404 Not Found
        - ValidationError subclasses → 400 Bad Request
        - IBKRConnectionError → 503 Service Unavailable
        - IBKRAPIError → 502 Bad Gateway
        - RateLimitedError → 429 Too Many Requests
        - SessionExpiredError → 401 Unauthorized
        - Other OptionAnalyzerError → 500 Internal Server Error
    """
    try:
        response = await call_next(request)
        return response
    except SymbolNotFoundError as e:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content=ErrorResponse(error=e.message, code=e.code).model_dump(),
        )
    except AmbiguousSymbolError as e:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content=ErrorResponse(error=e.message, code=e.code).model_dump(),
        )
    except (
        ValidationError,
        MixedExpirationError,
        InvalidQuantityError,
        MissingBidAskError,
    ) as e:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content=ErrorResponse(error=e.message, code=e.code).model_dump(),
        )
    except InsufficientDataError as e:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content=ErrorResponse(error=e.message, code=e.code).model_dump(),
        )
    except IBKRConnectionError as e:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content=ErrorResponse(error=e.message, code=e.code).model_dump(),
        )
    except IBKRAPIError as e:
        return JSONResponse(
            status_code=status.HTTP_502_BAD_GATEWAY,
            content=ErrorResponse(error=e.message, code=e.code).model_dump(),
        )
    except RateLimitedError as e:
        return JSONResponse(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            content=ErrorResponse(error=e.message, code=e.code).model_dump(),
        )
    except SessionExpiredError as e:
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content=ErrorResponse(error=e.message, code=e.code).model_dump(),
        )
    except OptionAnalyzerError as e:
        # Catch-all for other custom exceptions
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=ErrorResponse(error=e.message, code=e.code).model_dump(),
        )
    except Exception:
        logger.exception("Unhandled exception in request handler")
        # Unexpected errors - don't expose internals
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=ErrorResponse(
                error="An unexpected error occurred",
                code="INTERNAL_SERVER_ERROR",
            ).model_dump(),
        )
