"""Offline trust-boundary regressions; never import the database/application."""
import asyncio
import httpx
from starlette.applications import Starlette
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.responses import JSONResponse
from starlette.routing import Route
from app.backend.security import LocalAccessMiddleware

async def handler(request):
    return JSONResponse({'ok': True})

def test_local_api_access_boundary():
    app = Starlette(routes=[Route('/api-keys/', handler, methods=['GET', 'POST'])])
    app.add_middleware(LocalAccessMiddleware)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=['localhost', '127.0.0.1'])
    async def run():
        cases = [
            ('127.0.0.1', 'localhost', None, 200),
            ('127.0.0.1', 'localhost', 'http://localhost:5173', 200),
            ('127.0.0.1', '127.0.0.1', 'http://127.0.0.1:5173', 200),
            ('127.0.0.1', 'localhost', 'https://attacker.invalid', 403),
            ('127.0.0.1', 'localhost', 'null', 403),
            ('192.0.2.10', 'localhost', None, 403),
            ('127.0.0.1', 'attacker.invalid', None, 400),
        ]
        for peer, host, origin, expected in cases:
            transport = httpx.ASGITransport(app=app, client=(peer, 12345))
            async with httpx.AsyncClient(transport=transport, base_url='http://' + host) as client:
                headers = {} if origin is None else {'Origin': origin}
                res = await client.post('/api-keys/', headers=headers, json={})
                assert res.status_code == expected, (peer, host, origin)
                if expected == 200:
                    assert res.headers['cache-control'] == 'no-store'
    asyncio.run(run())
