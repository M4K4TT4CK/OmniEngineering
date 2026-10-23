"""A Starlette endpoint that reads the body whole (ARB-083)."""
from starlette.requests import Request
from starlette.responses import JSONResponse


async def receive(request: Request) -> JSONResponse:
    body = await request.body()  # expect: arbiter/python.request-body-unbounded
    return JSONResponse({"bytes": len(body)})
