<?php
declare(strict_types=1);
if(!defined('SIRUS_CMS_BOOTSTRAPPED')){http_response_code(404);exit;}

/** SirusSofts CMS 0.1 — private application code. Never serve this file directly. */
const SIRUS_CMS_VERSION = '0.1.0-alpha';
const SIRUS_CMS_DOMAIN = 'sirussofts.org';

/**
 * The CMS directory IS the deployable website directory. Keep all mutable
 * state outside it; this also works if Plesk's document root is a nested CMS
 * folder under httpdocs.
 */
function cms_site_root(): string {
    $site=realpath(dirname(__DIR__));
    if(!$site||!is_dir($site))throw new RuntimeException('CMS website root is unavailable.');
    return $site;
}
function cms_private_directory(): string {
    $configured=getenv('SIRUS_CMS_PRIVATE_ROOT');
    if(is_string($configured)&&$configured!==''){
        if(!str_starts_with($configured,'/')||str_contains($configured,"\0")||
           is_link($configured)||!is_dir($configured))
            throw new RuntimeException('Private root must be an existing absolute directory.');
        $private=realpath($configured);
        $site=cms_site_root();
        $document=realpath((string)($_SERVER['DOCUMENT_ROOT']??''));
        if($private===false||$private===$site||str_starts_with($private,$site.'/')||
           ($document&&($private===$document||str_starts_with($private,$document.'/'))))
            throw new RuntimeException('Private root cannot be publicly served.');
        return $private;
    }
    $site=cms_site_root();
    $parent=dirname($site);
    for($cur=$site;;$cur=dirname($cur)){
        if(basename($cur)==='httpdocs'){
            $parent=dirname($cur);
            break;
        }
        if($cur===dirname($cur))break;
    }
    $private=$parent.'/private/sirus-cms';
    $document=realpath((string)($_SERVER['DOCUMENT_ROOT']??''));
    if(str_starts_with($private,rtrim($site,'/').'/') ||
       ($document && str_starts_with($private,rtrim($document,'/').'/')))
        throw new RuntimeException('CMS private storage cannot be inside a public document root.');
    return $private;
}
function cms_database_file(): string {
    return cms_private_directory().'/data/cms.sqlite3';
}
function cms_db(): PDO {
    static $db=null;
    if($db instanceof PDO)return $db;
    if(!extension_loaded('pdo_sqlite'))throw new RuntimeException('The CMS requires PHP PDO_SQLITE.');
    if(!is_file(cms_database_file()))throw new RuntimeException('The CMS database has not been initialized.');
    $db=new PDO('sqlite:'.cms_database_file(),null,null,[
        PDO::ATTR_ERRMODE=>PDO::ERRMODE_EXCEPTION,
        PDO::ATTR_DEFAULT_FETCH_MODE=>PDO::FETCH_ASSOC,
        PDO::ATTR_EMULATE_PREPARES=>false
    ]);
    $db->exec('PRAGMA busy_timeout=5000');
    $db->exec('PRAGMA foreign_keys=ON');
    return $db;
}
function cms_schema(PDO $db): void {
    $db->exec('CREATE TABLE IF NOT EXISTS cms_settings (name TEXT PRIMARY KEY,value TEXT NOT NULL)');
    $db->exec('CREATE TABLE IF NOT EXISTS cms_admins (
        id INTEGER PRIMARY KEY AUTOINCREMENT,username TEXT NOT NULL UNIQUE,
        password_hash TEXT NOT NULL,created_at TEXT NOT NULL,last_login TEXT
    )');
    $db->exec(<<<'SQL'
CREATE TABLE IF NOT EXISTS cms_projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,slug TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,summary TEXT NOT NULL,body TEXT NOT NULL,
    category TEXT NOT NULL,icon TEXT NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('page','app')),
    state TEXT NOT NULL CHECK(state IN ('draft','published')),
    sort_order INTEGER NOT NULL DEFAULT 100,
    created_at TEXT NOT NULL,updated_at TEXT NOT NULL
)
SQL);
    $db->exec(<<<'SQL'
CREATE TABLE IF NOT EXISTS cms_pages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,slug TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,body TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('draft','published')),
    created_at TEXT NOT NULL,updated_at TEXT NOT NULL
)
SQL);
    $db->exec('CREATE TABLE IF NOT EXISTS cms_logins (
        ip TEXT NOT NULL,username TEXT NOT NULL,count INTEGER NOT NULL,
        started INTEGER NOT NULL,PRIMARY KEY(ip,username)
    )');
    $db->exec('CREATE TABLE IF NOT EXISTS cms_audit (
        id INTEGER PRIMARY KEY AUTOINCREMENT,at TEXT NOT NULL,username TEXT NOT NULL,
        action TEXT NOT NULL,reference TEXT NOT NULL
    )');
    $db->exec('CREATE INDEX IF NOT EXISTS cms_projects_status ON cms_projects(state,sort_order)');
}
function cms_defaults(): array {
    return [
        'site_name'=>'SirusSofts',
        'tagline'=>'Software built to go further.',
        'hero_title'=>'Ideas deserve more than a prototype.',
        'hero_body'=>'Explore independent software, connected systems and original projects from SirusSofts.',
        'about'=>'SirusSofts is a home for independently developed software, connected systems, tools and experiments.',
        'footer'=>'SirusSofts · Built with curiosity.',
        'accent'=>'blue'
    ];
}
function cms_setting(PDO $db,string $key): string {
    static $cache=[];
    if(isset($cache[$key]))return $cache[$key];
    $st=$db->prepare('SELECT value FROM cms_settings WHERE name=?');$st->execute([$key]);
    $v=$st->fetchColumn();
    return $cache[$key]=($v===false?(cms_defaults()[$key]??''):(string)$v);
}
function cms_esc(mixed $value): string {
    return htmlspecialchars((string)$value,ENT_QUOTES|ENT_SUBSTITUTE,'UTF-8');
}
function cms_now(): string {return gmdate('Y-m-d\TH:i:s\Z');}
function cms_safe_slug(string $slug): bool {
    return (bool)preg_match('/^[a-z][a-z0-9-]{1,39}$/D',$slug) &&
        !in_array($slug,[
            'www','admin','api','assets','projects','pages','settings','about',
            'login','logout','private','install','setup','health','favicon','robots',
            'system','feed','cms','static','media','app','lib','dashboard'
        ],true);
}
function cms_request_host(): string {
    $raw=strtolower((string)($_SERVER['HTTP_HOST']??''));
    if(!preg_match('/^[a-z0-9.-]+(?::[0-9]{1,5})?$/D',$raw))return '';
    return preg_replace('/:[0-9]{1,5}$/','',$raw);
}
function cms_root_domain(): string {
    $host=cms_request_host();
    foreach (['sirussofts.org','sirussofts.com'] as $domain) {
        if ($host===$domain||$host==='www.'.$domain||str_ends_with($host,'.'.$domain))
            return $domain;
    }
    return SIRUS_CMS_DOMAIN;
}
function cms_project_host_slug(string $host): ?string {
    foreach (['sirussofts.org','sirussofts.com'] as $domain) {
        if ($host===$domain || $host==='www.'.$domain) return null;
        if (str_ends_with($host,'.'.$domain)) {
            $slug=substr($host,0,-strlen('.'.$domain));
            return cms_safe_slug($slug)?$slug:'';
        }
    }
    if(cms_local() && in_array($host,['localhost','127.0.0.1','::1'],true))return null;
    return '';
}
function cms_request_path(): string {
    $uri=(string)($_SERVER['REQUEST_URI']??'/');
    $path=parse_url($uri,PHP_URL_PATH);
    if(!is_string($path)||$path===''||strlen($path)>250||
        preg_match('/%(?:2f|5c|00)/i',$path))return '/404';
    $decoded=rawurldecode($path);
    if(str_contains($decoded,'//')||str_contains($decoded,'\\')||
        preg_match('#(^|/)\.\.?(/|$)#',$decoded))return '/404';
    return $decoded==='/'?'/':rtrim($decoded,'/');
}
function cms_https(): bool {
    return (!empty($_SERVER['HTTPS'])&&strtolower((string)$_SERVER['HTTPS'])!=='off')
        || (int)($_SERVER['SERVER_PORT']??0)===443;
}
function cms_local(): bool {
    return in_array($_SERVER['REMOTE_ADDR']??'',['127.0.0.1','::1'],true);
}
function cms_headers(): void {
    header('X-Content-Type-Options: nosniff');
    header('Referrer-Policy: strict-origin-when-cross-origin');
    header('X-Frame-Options: DENY');
    header("Content-Security-Policy: default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; font-src 'self'; connect-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'; object-src 'none'");
    header('Permissions-Policy: camera=(), microphone=(), geolocation=()');
    if(cms_https())header('Strict-Transport-Security: max-age=15552000');
}
function cms_session(): void {
    if(session_status()===PHP_SESSION_ACTIVE)return;
    ini_set('session.use_strict_mode','1');
    ini_set('session.use_only_cookies','1');
    ini_set('session.use_trans_sid','0');
    session_name('sirus_cms_admin');
    session_set_cookie_params([
        'lifetime'=>0,'path'=>'/admin','secure'=>cms_https(),
        'httponly'=>true,'samesite'=>'Strict'
    ]);
    session_start();
    if(!isset($_SESSION['cms_csrf']))$_SESSION['cms_csrf']=bin2hex(random_bytes(32));
}
function cms_csrf(): string {return (string)($_SESSION['cms_csrf']??'');}
function cms_check_csrf(): void {
    if(!isset($_POST['csrf'])||!is_string($_POST['csrf'])||
       !hash_equals(cms_csrf(),$_POST['csrf']))
        throw new InvalidArgumentException('Invalid form session. Reload and try again.');
}
function cms_is_admin(): bool {return !empty($_SESSION['cms_admin_id']);}
function cms_need_admin(): void {
    if(!cms_is_admin()){http_response_code(403);throw new RuntimeException('Sign in required.');}
}
function cms_login(PDO $db,string $username,string $password): bool {
    $ip=substr((string)($_SERVER['REMOTE_ADDR']??'unknown'),0,60);
    $username=trim($username);
    if(strlen($username)>64||strlen($password)>2048)return false;
    $st=$db->prepare('SELECT count,started FROM cms_logins WHERE ip=? AND username=?');
    $st->execute([$ip,$username]);$rec=$st->fetch();
    if($rec && (int)$rec['count']>=6 && time()-(int)$rec['started']<900)
        throw new InvalidArgumentException('Too many attempts. Try again in 15 minutes.');
    $st=$db->prepare('SELECT id,password_hash FROM cms_admins WHERE username=?');
    $st->execute([$username]);$admin=$st->fetch();
    $okay=$admin && password_verify($password,(string)$admin['password_hash']);
    if(!$okay){
        $count=$rec&&time()-(int)$rec['started']<900?(int)$rec['count']+1:1;
        $beg=$count===1?time():(int)$rec['started'];
        $st=$db->prepare('INSERT INTO cms_logins(ip,username,count,started)
            VALUES(?,?,?,?) ON CONFLICT(ip,username) DO UPDATE SET
            count=excluded.count,started=excluded.started');
        $st->execute([$ip,$username,$count,$beg]);return false;
    }
    $db->prepare('DELETE FROM cms_logins WHERE ip=? AND username=?')->execute([$ip,$username]);
    session_regenerate_id(true);
    $_SESSION['cms_admin_id']=(int)$admin['id'];
    $_SESSION['cms_username']=$username;
    $_SESSION['cms_csrf']=bin2hex(random_bytes(32));
    $db->prepare('UPDATE cms_admins SET last_login=? WHERE id=?')->execute([cms_now(),$admin['id']]);
    cms_audit($db,'sign-in','admin');
    return true;
}
function cms_audit(PDO $db,string $action,string $reference): void {
    $st=$db->prepare('INSERT INTO cms_audit(at,username,action,reference) VALUES(?,?,?,?)');
    $st->execute([cms_now(),substr((string)($_SESSION['cms_username']??'system'),0,64),
                  substr($action,0,60),substr($reference,0,120)]);
}
function cms_project(PDO $db,string $slug,bool $public=true): ?array {
    $st=$db->prepare('SELECT * FROM cms_projects WHERE slug=?'.($public?" AND state='published'":''));
    $st->execute([$slug]);$data=$st->fetch();
    return $data?:null;
}
function cms_projects(PDO $db,bool $public=true): array {
    return $db->query('SELECT * FROM cms_projects'.($public?" WHERE state='published'":'').
        ' ORDER BY sort_order ASC,title COLLATE NOCASE ASC')->fetchAll();
}
function cms_page(PDO $db,string $slug,bool $public=true): ?array {
    $st=$db->prepare('SELECT * FROM cms_pages WHERE slug=?'.($public?" AND state='published'":''));
    $st->execute([$slug]);return $st->fetch()?:null;
}
function cms_body(string $plain): string {
    $parts=preg_split('/\R\s*\R/u',trim($plain));
    if(!is_array($parts))return '';
    $html='';
    foreach($parts as $part){
        $part=trim($part);
        if($part==='')continue;
        if(str_starts_with($part,'## ')){
            $html.='<h2>'.cms_esc(substr($part,3)).'</h2>';
        }elseif(str_starts_with($part,'### ')){
            $html.='<h3>'.cms_esc(substr($part,4)).'</h3>';
        }else{$html.='<p>'.nl2br(cms_esc($part),false).'</p>';}
    }
    return $html;
}
function cms_go(string $path): never {
    if(!str_starts_with($path,'/')||str_starts_with($path,'//'))$path='/';
    header('Location: '.$path,true,303);exit;
}
function cms_flash(string $message,string $type='success'): void {
    $_SESSION['cms_flash']=['message'=>$message,'type'=>$type];
}
function cms_take_flash(): ?array {
    $flash=$_SESSION['cms_flash']??null;unset($_SESSION['cms_flash']);return $flash;
}
function cms_get_int(mixed $value): int {
    if(!is_scalar($value)||!preg_match('/^[1-9][0-9]{0,8}$/D',(string)$value))
        throw new InvalidArgumentException('Invalid record identifier.');
    return (int)$value;
}
function cms_text(mixed $value,int $limit,string $label,bool $required=false): string {
    if(!is_string($value))throw new InvalidArgumentException('Invalid '.$label.'.');
    $value=trim($value);
    if(strlen($value)>$limit || ($required && $value==='') ||
       preg_match('/[\x00-\x08\x0B\x0C\x0E-\x1F]/',$value))
        throw new InvalidArgumentException($label.' must be printable and under '.$limit.' bytes.');
    return $value;
}
