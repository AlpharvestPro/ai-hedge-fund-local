"""Keep this single-user development API on the local trust boundary."""
from ipaddress import ip_address
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

LOCAL_ORIGINS = {
    'http://localhost:5173', 'http://127.0.0.1:5173',
    'http://localhost:8000', 'http://127.0.0.1:8000',
}

class LocalAccessMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        try:
            local = bool(request.client and ip_address(request.client.host).is_loopback)
        except ValueError:
            local = False
        origin = request.headers.get('origin')
        if not local or (origin is not None and origin not in LOCAL_ORIGINS):
            return JSONResponse({'detail': 'Local access only'}, status_code=403)
        response = await call_next(request)
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        return response
