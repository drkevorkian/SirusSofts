<?php
declare(strict_types=1);
if(PHP_SAPI!=='cli'){http_response_code(404);exit;}
define('SIRUS_CMS_BOOTSTRAPPED',true);
require dirname(__DIR__).'/app/core.php';
require dirname(__DIR__).'/app/site.php';
require dirname(__DIR__).'/app/admin.php';
require dirname(__DIR__).'/app/ai_workspace.php';
function auth_check(bool $okay,string $message):void {
    if(!$okay){fwrite(STDERR,"FAIL: ".$message."\n");exit(1);}
}
$_SERVER['HTTPS']='on';
$_SERVER['HTTP_HOST']='sirussofts.com';
$_SERVER['REMOTE_ADDR']='127.0.0.1';
$_COOKIE['sirus_cms_admin']='legacy-cookie';
cms_session();
auth_check(session_name()==='sirus_cms_session','New CMS cookie name');
$p=session_get_cookie_params();
auth_check($p['path']==='/' && $p['secure']===true && $p['httponly']===true &&
    $p['samesite']==='Strict','Cookie shared by /admin and /ai/api, secure and host-only');
auth_check(!isset($_COOKIE['sirus_cms_admin']),'Legacy cookie no longer trusted');
auth_check(cms_login_return_path('/Gentoo/AI/')==='/Gentoo/AI/','AI return allowed');
foreach(['https://evil.test','//evil.test','/admin/ai','/Gentoo/AI/%2e%2e'] as $bad)
    auth_check(cms_login_return_path($bad)===null,'Unsafe return rejected');
auth_check(cms_login_destination(null,true)==='/admin','Admin default landing');
auth_check(cms_login_destination(null,false)==='/admin/account','Viewer default landing');
auth_check(cms_login_destination('/Gentoo/AI/',false)==='/Gentoo/AI/','Viewer returns to AI');
auth_check(cms_auth_canonical_location('sirussofts.org','/admin/login')===
    'https://sirussofts.com/admin/login','ORG login canonicalized');
auth_check(cms_auth_canonical_location('www.sirussofts.com','/admin')===
    'https://sirussofts.com/admin','WWW login canonicalized');
auth_check(cms_auth_canonical_location('sirussofts.com','/admin')===null,
    'Canonical login stays on apex');
auth_check(cms_auth_canonical_location('sirussofts.org','/projects')===null,
    'Public pages unaffected');
$db=new PDO('sqlite::memory:',null,null,[
    PDO::ATTR_ERRMODE=>PDO::ERRMODE_EXCEPTION,
    PDO::ATTR_DEFAULT_FETCH_MODE=>PDO::FETCH_ASSOC
]);
cms_schema($db);
$db->prepare('INSERT INTO cms_admins(username,password_hash,created_at) VALUES(?,?,?)')
    ->execute(['admin-test',password_hash('AdminLongPass12345',PASSWORD_DEFAULT),cms_now()]);
$db->prepare('INSERT INTO cms_viewers(username,password_hash,created_at) VALUES(?,?,?)')
    ->execute(['viewer-test',password_hash('ViewerLongPass12345',PASSWORD_DEFAULT),cms_now()]);
auth_check(cms_login($db,'admin-test','AdminLongPass12345'),'Admin sign-in accepted');
$a=cms_ai_identity($db);
auth_check($a['role']==='admin'&&$a['username']==='admin-test'&&
    str_starts_with($a['account'],'admin:'),'AI recognizes signed-in admin');
$_SERVER['REQUEST_URI']='/ai/api/state';
auth_check(cms_ai_identity($db)['role']==='admin','Identity survives /ai/api route');
$_SESSION=[];
auth_check(cms_login($db,'viewer-test','ViewerLongPass12345'),'Viewer sign-in accepted');
$v=cms_ai_identity($db);
auth_check($v['role']==='user'&&$v['username']==='viewer-test'&&
    str_starts_with($v['account'],'user:'),'AI recognizes signed-in viewer');
$db->exec('UPDATE cms_viewers SET auth_version=auth_version+1');
auth_check(cms_ai_identity($db)['role']==='guest','Revoked viewer becomes guest');
echo "PASS: site-wide cookie migration, canonical sign-in and shared AI account identity\n";
