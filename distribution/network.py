"""Public GitHub downloads only; no inherited proxy or local credentials."""
import hashlib,json,ssl,urllib.error,urllib.parse,urllib.request
import certifi
from . import package

class ReleaseUnavailable(Exception):pass
class PublicNetworkError(Exception):pass

def allowed_url(url):
    parsed=urllib.parse.urlsplit(url)
    if parsed.scheme!='https' or parsed.hostname not in ('api.github.com','github.com','release-assets.githubusercontent.com','objects.githubusercontent.com') or parsed.username or parsed.password or parsed.port not in (None,443):raise ValueError('Only official HTTPS GitHub release URLs are allowed')
    return True

class Redirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        try:allowed_url(newurl)
        except BaseException:fp.close();raise
        return super().redirect_request(req,fp,code,msg,headers,newurl)

def open_public(url):
    allowed_url(url)
    context=ssl.create_default_context(cafile=certifi.where())
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),Redirects(),urllib.request.HTTPSHandler(context=context))
    request=urllib.request.Request(url,headers={'User-Agent':'OpenGloss-Windows-Updater','Accept':'application/vnd.github+json' if url==package.API else 'application/octet-stream'})
    try:return opener.open(request,timeout=30)
    except urllib.error.HTTPError as error:
        error.close()
        if url==package.API and error.code==404:raise ReleaseUnavailable('暂无可用正式版本') from None
        if error.code==429:raise PublicNetworkError('GitHub 请求受限，请稍后重试') from None
        raise PublicNetworkError('GitHub 请求失败，请稍后重试') from None
    except ssl.SSLError:raise PublicNetworkError('安全连接证书校验失败') from None
    except urllib.error.URLError as error:
        if isinstance(error.reason,ssl.SSLError):raise PublicNetworkError('安全连接证书校验失败') from None
        raise PublicNetworkError('网络连接失败，请检查连接后重试') from None
    except OSError:raise PublicNetworkError('网络连接失败，请检查连接后重试') from None

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
