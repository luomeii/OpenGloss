"""Read-only source health check, using a public instance marker, no credential."""
import json,os,sys,urllib.error,urllib.request
from pae_core.http_security import instance_identity

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        raise urllib.error.HTTPError('',code,'redirect refused',headers,fp)

def probe():
    expected=instance_identity()
    try:
        port=int(os.environ.get('PAE_ENGINE_PORT','4815'))
        if not 1<=port<=65535:raise ValueError('invalid port')
        request=urllib.request.Request('http://127.0.0.1:%d/v1/health'%port)
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
        with opener.open(request,timeout=60) as response:raw=response.read(65537)
        if len(raw)>65536:raise ValueError('oversized health')
        body=json.loads(raw.decode('utf-8'))
        if not isinstance(body,dict) or body.get('instance_id')!=expected:raise ValueError('foreign health')
        return {'ok':body.get('ok') is True,'instance_id':expected,'expected_instance_id':expected,'status':200}
    except urllib.error.HTTPError as error:
        error.close();return {'ok':False,'error':'engine_unavailable','status':error.code}
    except (urllib.error.URLError,OSError):return {'ok':False,'error':'engine_connection_error','status':0}
    except (ValueError,TypeError):return {'ok':False,'error':'instance_or_response_invalid','status':0}

def main():
    if sys.argv[1:]==['--prepare']:instance_identity();return 0
    print(json.dumps(probe(),ensure_ascii=True));return 0

if __name__=='__main__':sys.exit(main())
