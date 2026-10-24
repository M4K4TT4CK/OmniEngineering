"""A Starlette endpoint that refuses large bodies (ARB-083)."""
from starlette.requests import Request
from starlette.responses import JSONResponse

MAX_BODY_BYTES = 1024 * 1024


async def receive(request: Request) -> JSONResponse:
    if int(request.headers.get("content-length", "0")) > MAX_BODY_BYTES:
        return JSONResponse({"error": "too large"}, status_code=413)
    body = b""
    async for chunk in request.stream():
        body += chunk
        if len(body) > MAX_BODY_BYTES:
            return JSONResponse({"error": "too large"}, status_code=413)
    return JSONResponse({"bytes": len(body)})
