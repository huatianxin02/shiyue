"""Local authenticated proxy. Agent output stays separate from the search index."""
import json
import os
import re
import subprocess
import urllib.parse
import urllib.request
import urllib.error
from pathlib import Path

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args):
        raise ValueError('本机服务不能重定向')

def call(route, data=None, q=None, binary=False):
    allowed={'info','requests','artifacts','publish','accept','cancel','metadata','download','knowledge','scope','read','notifications','artifact-final','artifact-versions','references/resolve','open-console'}
    if route not in allowed:raise ValueError('不支持该协作操作')
    if route=='open-console':
        if data is None:raise ValueError('打开控制台需要 POST 请求')
        # This fixed local launcher must also work before Console has a runtime descriptor.
        # No request value may choose an application, executable, or command argument.
        try:
            subprocess.run(['/usr/bin/open',str(Path.home()/'Desktop'/'小鲸控制台.app')],
                check=True,timeout=8,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        except (OSError,subprocess.SubprocessError):
            raise ValueError('小鲸控制台未能打开，请检查桌面上的小鲸控制台应用') from None
        return {'ok':True,'opened':True}
    root=Path(os.environ.get('SHIYUE_CONSOLE_DATA',str(Path.home()/'Library/Application Support/小鲸控制台')))
    try: session=json.loads((root/'runtime.json').read_text())
    except (OSError,ValueError):raise ValueError('请先打开小鲸控制台，再刷新 Agent 成果')
    if not re.fullmatch(r'http://127\.0\.0\.1:\d{1,5}',session.get('origin','')):raise ValueError('本机连接地址无效')
    request=urllib.request.Request(session['origin']+'/api/bridge/'+route+('?' + urllib.parse.urlencode(q) if q else ''),
        data=None if data is None else json.dumps(data).encode(),headers={'X-Console-Token':session['token'],'Content-Type':'application/json'})
    try:
        with urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect()).open(request,timeout=40) as response:
            raw=response.read(64*1024*1024+1 if binary else 4*1024*1024)
        if binary and len(raw)>64*1024*1024:raise ValueError('文件超过 64 MB')
        return raw if binary else json.loads(raw)
    except urllib.error.HTTPError as e:
        try: message=json.load(e).get('error','协作操作未完成')
        except (ValueError,OSError):message='协作操作未完成'
        raise ValueError(message)
    except urllib.error.URLError:raise ValueError('小鲸控制台未连接，请打开后重试')

def ensure_scope(v):
    """Create a stable, initially empty collection/profile, preserving user edits."""
    from vault import now
    cid='6b37ea879a805839b0552b508c0305a1'
    profile='bf302ef10be950d290e1d7320844a4a9'
    with v.db() as c:
        c.execute('INSERT OR IGNORE INTO collections VALUES(?,?,?,?)',(cid,'Agent 已发布成果',now(),now()))
        c.execute('INSERT OR IGNORE INTO knowledge_profiles VALUES(?,?,?,?,?,?,?)',
            (profile,'小鲸可检索',1,0,'[]',json.dumps([cid]),now()))
    return {'profile':profile,'collection':cid}
