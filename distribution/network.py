"""Public GitHub downloads only; no inherited proxy or local credentials."""
import hashlib,json,urllib.parse,urllib.request
from . import package

def allowed_url(url):
    parsed=urllib.parse.urlsplit(url)
    if parsed.scheme!='https' or parsed.hostname not in ('api.github.com','github.com','release-assets.githubusercontent.com','objects.githubusercontent.com') or parsed.username or parsed.password or parsed.port not in (None,443):raise ValueError('Only official HTTPS GitHub release URLs are allowed')
    return True

class Redirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        allowed_url(newurl)
        return super().redirect_request(req,fp,code,msg,headers,newurl)

def open_public(url):
    allowed_url(url)
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),Redirects())
    request=urllib.request.Request(url,headers={'User-Agent':'OpenGloss-Windows-Updater','Accept':'application/vnd.github+json' if url==package.API else 'application/octet-stream'})
    return opener.open(request,timeout=30)

def latest(current):
    with open_public(package.API) as response:
        raw=response.read(1024*1024+1)
        if len(raw)>1024*1024:raise ValueError('Release metadata too large')
    release=json.loads(raw)
    return package.select_release(release,current)

def download(offer,destination):
    allowed_url(offer['url']);digest=hashlib.sha256();size=0
    try:
        with open_public(offer['url']) as response,open(destination,'xb') as output:
            while True:
                block=response.read(1024*1024)
                if not block:break
                size+=len(block)
                if size>offer['size'] or size>package.MAX_BYTES:raise ValueError('Download exceeds expected size')
                digest.update(block);output.write(block)
        if size!=offer['size'] or digest.hexdigest()!=offer['sha256']:raise ValueError('Downloaded release checksum mismatch')
    except BaseException:
        from pathlib import Path
        if Path(destination).exists():Path(destination).unlink()
        raise
    return destination
