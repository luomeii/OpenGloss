"""Do not acknowledge business operations before a new release is committed."""
from pathlib import Path
class Admission:
    def __init__(self,app,root):self.app=app;self.pending=Path(root)/'pending-update.json'
    async def __call__(self,scope,receive,send):
        if scope['type']=='http' and scope.get('path','').startswith('/v1/') and scope.get('path')!='/v1/health' and self.pending.exists():
            body=b'{"ok":false,"error":"update_in_progress"}'
            await send({'type':'http.response.start','status':503,'headers':[(b'content-type',b'application/json'),(b'content-length',str(len(body)).encode('ascii')),(b'retry-after',b'2')]})
            await send({'type':'http.response.body','body':body});return
        await self.app(scope,receive,send)
