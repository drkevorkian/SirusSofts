#!/usr/bin/env python3
"""Private GitHub project catalog for SirusSofts. No public source publication."""
import argparse, hashlib, hmac, json, os, re, shutil, sqlite3, subprocess, threading
from contextlib import contextmanager
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote
import urllib.request, urllib.error
from version_groups import grouped_projects

DATA=Path('/var/lib/sirusdeploy')
TOKEN=Path('/etc/sirusdeploy/github.token')
ADMIN=Path('/etc/sirusdeploy/catalog-admin-token')
PATTERN=re.compile(r'^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$')

def valid_repo(repo):
    return (isinstance(repo,str) and bool(PATTERN.fullmatch(repo))
            and all(p not in ('.','..') and not p.startswith('-') for p in repo.split('/')))

def stamp():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')

@contextmanager
def dbconn(path):
    c=sqlite3.connect(path)
    try:
        with c: yield c
    finally: c.close()

def initialize(path):
    path.parent.mkdir(parents=True,exist_ok=True)
    with dbconn(path) as c:
        c.execute("""CREATE TABLE IF NOT EXISTS projects(
          full_name TEXT PRIMARY KEY, github_private INTEGER NOT NULL,
          site_visibility TEXT NOT NULL DEFAULT 'private'
            CHECK(site_visibility IN ('private','public')),
          description TEXT NOT NULL DEFAULT '', branch TEXT NOT NULL,
          archived INTEGER NOT NULL DEFAULT 0, html_url TEXT NOT NULL DEFAULT '',
          sync_status TEXT NOT NULL DEFAULT 'pending',
          sync_error TEXT NOT NULL DEFAULT '', commit_sha TEXT NOT NULL DEFAULT '',
          checked TEXT NOT NULL DEFAULT '', hosting_requested INTEGER NOT NULL DEFAULT 0)""")

def secret(path):
    p=Path(path)
    if not p.is_file() or p.stat().st_mode & 0o077:
        raise RuntimeError('Secret missing or permissions too broad: '+str(p))
    v=p.read_text().strip()
    if len(v)<20: raise RuntimeError('Secret is missing/too short')
    return v

def all_repos(access_token):
    page=1
    while page<=500:
        url=('https://api.github.com/user/repos?per_page=100&page='+str(page)
             +'&affiliation=owner,collaborator,organization_member')
        req=urllib.request.Request(url,headers={'Authorization':'Bearer '+access_token,
          'Accept':'application/vnd.github+json','X-GitHub-Api-Version':'2022-11-28',
          'User-Agent':'SirusSofts-Catalog/0.2'})
        try:
            with urllib.request.urlopen(req,timeout=30) as response: items=json.load(response)
        except urllib.error.HTTPError as e:
            raise RuntimeError('GitHub API HTTP '+str(e.code)+' (check token scope)') from None
        if not isinstance(items,list):raise RuntimeError('Unexpected GitHub API response')
        for item in items:
            name=item.get('full_name','')
            branch=item.get('default_branch') or 'main'
            if not valid_repo(name) or not re.fullmatch(r'[A-Za-z0-9._/-]+',branch) or '..' in branch or branch.startswith(('-', '/', '.')):continue
            yield {'full_name':name, 'github_private':bool(item.get('private',True)),
                   'description':(item.get('description') or '')[:400],
                   'branch':branch,'archived':bool(item.get('archived',False)),
                   'html_url':'https://github.com/'+name,
                   'size_kb':max(0,int(item.get('size') or 0))}
        if len(items)<100:return
        page+=1
    raise RuntimeError('Too many repository pages')

def run_git(args,access_token):
    env=dict(os.environ, GITHUB_TOKEN=access_token,
        GIT_ASKPASS=str(Path(__file__).with_name('git_askpass.sh')),
        GIT_TERMINAL_PROMPT='0',GIT_LFS_SKIP_SMUDGE='1',
        GIT_CONFIG_NOSYSTEM='1',GIT_CONFIG_GLOBAL='/dev/null')
    p=subprocess.run(['git','-c','core.hooksPath=/dev/null',
                      '-c','credential.helper=',*args],env=env,
                      stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,
                      timeout=600)
    if p.returncode:raise RuntimeError('Git operation failed ('+str(p.returncode)+')')
    return p.stdout.strip()

def clone_or_update(root,item,access_token):
    name=item['full_name']
    if not valid_repo(name):raise ValueError('Invalid repository name')
    target=root/'sources'/name
    target.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    os.chmod(root/'sources',0o700);os.chmod(target.parent,0o700)
    if target.is_symlink():raise RuntimeError('Destination is symlink')
    size=item['size_kb']*1024
    if shutil.disk_usage(target.parent).free < size+512*1024*1024:
        raise RuntimeError('Insufficient disk space; repository skipped')
    url='https://github.com/'+name+'.git'
    if target.exists():
        if not (target/'.git').is_dir():raise RuntimeError('Existing path is not a repository')
        if run_git(['-C',str(target),'remote','get-url','origin'],access_token)!=url:
            raise RuntimeError('Unexpected Git remote')
        run_git(['-C',str(target),'fetch','--quiet','--depth','1','--no-tags',
                 'origin','refs/heads/'+item['branch']],access_token)
        run_git(['-C',str(target),'reset','--hard','--quiet','FETCH_HEAD'],access_token)
    else:
        run_git(['clone','--quiet','--depth','1','--single-branch','--no-tags',
                 '--branch',item['branch'],url,str(target)],access_token)
    return run_git(['-C',str(target),'rev-parse','HEAD'],access_token)

def synchronize(db,root,access_token):
    initialize(db)
    items=list(all_repos(access_token))
    result={'discovered':len(items),'synced':0,'failed':0,'errors':[]}
    for item in items:
        name=item['full_name']
        with dbconn(db) as c:
            c.execute("""INSERT INTO projects(full_name,github_private,description,branch,
                       archived,html_url,checked) VALUES(?,?,?,?,?,?,?)
                       ON CONFLICT(full_name) DO UPDATE SET
                       github_private=excluded.github_private,
                       description=excluded.description,branch=excluded.branch,
                       archived=excluded.archived,html_url=excluded.html_url,
                       checked=excluded.checked""",
                      (name,int(item['github_private']),item['description'],
                       item['branch'],int(item['archived']),item['html_url'],stamp()))
        try:
            sha=clone_or_update(root,item,access_token)
            if not re.fullmatch(r'[0-9a-f]{40}',sha):raise RuntimeError('Invalid checkout revision')
            state,error='synced','';result['synced']+=1
        except Exception as e:
            sha='';state,error='failed',str(e)[:200];result['failed']+=1
            result['errors'].append({'repo':name,'message':error})
        with dbconn(db) as c:
            c.execute("""UPDATE projects SET sync_status=?,sync_error=?,
                       commit_sha=CASE WHEN ?<>'' THEN ? ELSE commit_sha END,checked=?
                       WHERE full_name=?""",
                      (state,error,sha,sha,stamp(),name))
    return result

def list_projects(db,public=False):
    with dbconn(db) as c:
        c.row_factory=sqlite3.Row
        if public:
            sql="""SELECT full_name,description,html_url FROM projects
                  WHERE site_visibility='public' ORDER BY full_name"""
        else:
            sql='SELECT * FROM projects ORDER BY full_name'
        return [dict(x) for x in c.execute(sql)]

def update_project(db,name,property,value):
    if not valid_repo(name):raise ValueError('Invalid repository')
    if property=='site_visibility':
        if value not in ('private','public'):raise ValueError('Invalid visibility')
    elif property=='hosting_requested':
        if not isinstance(value,bool):raise ValueError('Invalid hosting request')
        value=int(value)
    else:raise ValueError('Invalid setting')
    with dbconn(db) as c:
        r=c.execute('UPDATE projects SET '+property+'=? WHERE full_name=?',(value,name))
        if r.rowcount!=1:raise KeyError('Repository not discovered')

HTML="""<!doctype html><html lang="en"><meta charset="utf-8">
<title>SirusSofts Project Admin</title><style>
body{font:15px system-ui;max-width:1100px;margin:25px auto;background:#17212e;color:#eaf1ff;padding:15px}
input,button{padding:8px;margin:4px}button{cursor:pointer}
table{width:100%;border-collapse:collapse}td,th{border-bottom:1px solid #506075;padding:12px;text-align:left}
</style><h1>SirusSofts — GitHub projects</h1>
<p>Everything imports privately. Public visibility means a project listing, NOT public source code or live hosting.</p>
<label>Admin token <input type="password" id="token" autocomplete="off"></label>
<button onclick="load()">Load</button><button onclick="sync()">Sync all GitHub repositories</button>
<p id="status"></p><table><thead><tr><th>Project / version</th><th>GitHub</th><th>Visibility</th><th>Source sync</th><th>Hosting</th></tr></thead><tbody id="projects"></tbody></table>
<script>
async function req(url,method='GET',data=null){
const r=await fetch(url,{method,headers:{'Authorization':'Bearer '+document.getElementById('token').value,
'Content-Type':'application/json'},body:data?JSON.stringify(data):null,cache:'no-store'});
const x=await r.json();if(!r.ok)throw Error(x.error||'Request failed');return x;
}
async function load(){try{
let x=await req('/api/projects');let box=document.getElementById('projects');box.replaceChildren();
for(let family of x.groups){
  let heading=document.createElement('tr');let headingCell=heading.insertCell();
  headingCell.colSpan=5;
  headingCell.style.cssText='font-weight:700;background:#263347;padding:14px';
  headingCell.textContent=(family.grouped?'Version family: ':'Project: ')+family.label+
    (family.grouped?' ('+family.versions.length+' repositories)':'');
  box.append(heading);
  for(let p of family.versions){
    let row=document.createElement('tr');
    let label=p.full_name+(family.grouped?' — '+p.version_label:'');
    let fields=[label,p.github_private?'private':'public',p.site_visibility,
                p.sync_status,p.hosting_requested?'requested':'not hosted'];
    for(let s of fields){let td=row.insertCell();td.textContent=s;}
    let v=document.createElement('button');v.textContent='Toggle visibility';
    v.onclick=async()=>{
      if(p.site_visibility==='private'&&!confirm('Show project listing publicly? (Source remains private.)'))return;
      await req('/api/projects/'+encodeURIComponent(p.full_name),'POST',
                {site_visibility:p.site_visibility==='private'?'public':'private'});
      load();};
    row.cells[2].append(v);
    let h=document.createElement('button');h.textContent='Request / cancel hosting';
    h.onclick=async()=>{
      await req('/api/projects/'+encodeURIComponent(p.full_name),'POST',
                {hosting_requested:!p.hosting_requested});
      load();};
    row.cells[4].append(h);box.append(row);
  }
}
document.getElementById('status').textContent=x.projects.length+' repositories in '+x.groups.length+' families';
}catch(e){document.getElementById('status').textContent=e.message;}}
async function sync(){try{let r=await req('/api/sync','POST',{});document.getElementById('status').textContent=r.message;}
catch(e){document.getElementById('status').textContent=e.message;}}
</script></html>"""

class Service:
    def __init__(self,db,root,token_path,admin_path):
        self.db,self.root=db,root;self.token_path=token_path
        self.admin_secret=secret(admin_path)
        self.lock=threading.Lock();self.status={'state':'not_started'}
    def sync(self):
        try:
            self.status={'state':'done',**synchronize(self.db,self.root,secret(self.token_path))}
        except Exception as e:
            self.status={'state':'failed','message':str(e)[:200]}
        finally:self.lock.release()

def handler(service):
    class H(BaseHTTPRequestHandler):
        def reply(self,status,obj):
            raw=json.dumps(obj).encode()
            self.send_response(status);self.send_header('Content-Type','application/json')
            self.send_header('Cache-Control','no-store')
            self.send_header('Content-Length',str(len(raw)))
            self.end_headers();self.wfile.write(raw)
        def authorized(self):
            h=self.headers.get('Authorization','')
            if not h.startswith('Bearer ') or not hmac.compare_digest(h[7:],service.admin_secret):
                self.reply(401,{'error':'Admin token required'});return False
            return True
        def do_GET(self):
            if self.path=='/admin':
                raw=HTML.encode()
                self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8')
                self.send_header('Cache-Control','no-store')
                self.send_header('X-Frame-Options','DENY')
                self.send_header('Content-Security-Policy',"default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'")
                self.send_header('Content-Length',str(len(raw)))
                self.end_headers();self.wfile.write(raw);return
            if not self.authorized():return
            if self.path=='/api/projects':
                projects=list_projects(service.db)
                return self.reply(200,{'projects':projects,'groups':grouped_projects(projects)})
            if self.path=='/api/public':
                projects=list_projects(service.db,True)
                return self.reply(200,{'projects':projects,'groups':grouped_projects(projects)})
            if self.path=='/api/status':return self.reply(200,service.status)
            self.reply(404,{'error':'not found'})
        def do_POST(self):
            if not self.authorized():return
            try:
                n=int(self.headers.get('Content-Length','-1'))
                if n<0 or n>4096:return self.reply(413,{'error':'Invalid payload size'})
                body=json.loads(self.rfile.read(n))
                if not isinstance(body,dict):raise ValueError('JSON object required')
            except (ValueError,UnicodeDecodeError):
                return self.reply(400,{'error':'Invalid JSON'})
            if self.path=='/api/sync':
                if not service.lock.acquire(False):return self.reply(409,{'error':'Sync running'})
                service.status={'state':'running'}
                threading.Thread(target=service.sync,daemon=True).start()
                return self.reply(202,{'message':'Synchronization started'})
            if self.path.startswith('/api/projects/'):
                name=unquote(self.path[len('/api/projects/'):])
                if len(body)!=1:return self.reply(400,{'error':'One setting at a time'})
                try:
                    key,value=next(iter(body.items()))
                    update_project(service.db,name,key,value)
                except KeyError:return self.reply(404,{'error':'Unknown repository'})
                except ValueError as e:return self.reply(400,{'error':str(e)})
                return self.reply(200,{'updated':name})
            self.reply(404,{'error':'not found'})
        def log_message(self,*args):pass
    return H

def main():
    p=argparse.ArgumentParser()
    p.add_argument('command',choices=['sync','list','public-feed','serve','check'])
    p.add_argument('--data',default=str(DATA))
    a=p.parse_args()
    root=Path(a.data);db=root/'catalog.sqlite3';initialize(db)
    if a.command=='check':print('Catalog database initialized');return
    if a.command=='list':print(json.dumps(list_projects(db),indent=2));return
    if a.command=='public-feed':print(json.dumps(list_projects(db,True),indent=2));return
    if a.command=='sync':print(json.dumps(synchronize(db,root,secret(TOKEN)),indent=2));return
    service=Service(db,root,TOKEN,ADMIN)
    ThreadingHTTPServer(('127.0.0.1',8788),handler(service)).serve_forever()

if __name__=='__main__':main()
