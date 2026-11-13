<?php
declare(strict_types=1);
define('SIRUS_CMS_BOOTSTRAPPED',true);
require dirname(__DIR__).'/app/core.php';
require dirname(__DIR__).'/app/site.php';
require dirname(__DIR__).'/app/admin.php';
function expect(bool $ok,string $msg): void {
    if(!$ok){fwrite(STDERR,"FAIL: ".$msg."\n");exit(1);}
}
$db=new PDO('sqlite::memory:',null,null,[
    PDO::ATTR_ERRMODE=>PDO::ERRMODE_EXCEPTION,
    PDO::ATTR_DEFAULT_FETCH_MODE=>PDO::FETCH_ASSOC
]);
$db->exec('PRAGMA foreign_keys=ON');
cms_schema($db);
cms_schema($db);
$_SESSION=[];
$now=cms_now();
$db->prepare('INSERT INTO cms_projects
    (slug,title,summary,body,category,icon,kind,state,sort_order,created_at,updated_at)
    VALUES (?,?,?,?,?,?,?,?,?,?,?)')->execute([
        'secretrepo','Not approved yet','HIDDEN SUMMARY 123','HIDDEN SOURCE TEXT ABC',
        'GitHub','⌘','page','draft',1000,$now,$now]);
$pid=(int)$db->lastInsertId();
$db->prepare('INSERT INTO cms_github_repositories
    (github_id,full_name,project_id,github_private,description,default_branch,
     archived,fork,html_url,pushed_at,seen_at,last_seen_run)
    VALUES (?,?,?,?,?,?,?,?,?,?,?,?)')->execute([
        4242,'owner/SecretRepository',$pid,1,'PRIVATE GITHUB DESCRIPTION','main',0,0,
        'https://github.com/owner/SecretRepository','',$now,1]);
$public=cms_public_directory_projects($db);
expect(count($public)===1,'Private GitHub repo must have a PUBLIC listing');
expect($public[0]['title']==='owner/SecretRepository','Draft title replaced with repo identifier');
ob_start();cms_public_catalog($db);$html=(string)ob_get_clean();
expect(str_contains($html,'owner/SecretRepository'),'Public page must list imported repo');
expect(!str_contains($html,'HIDDEN SUMMARY 123') &&
       !str_contains($html,'HIDDEN SOURCE TEXT ABC') &&
       !str_contains($html,'PRIVATE GITHUB DESCRIPTION'),
       'Draft descriptions and source are never public');
ob_start();cms_public_project($db,$public[0]);$detail=(string)ob_get_clean();
expect(str_contains($detail,'Sign in to view source'),'Project provides authorized source login');
expect(!str_contains($detail,'HIDDEN SOURCE TEXT ABC'),'Public detail cannot contain draft body');
expect(cms_public_directory_project($db,'secretrepo')!==null,'Imported draft route is listed');
expect(!cms_can_view_source($db,4242),'Anonymous source access must fail');
$db->prepare('INSERT INTO cms_viewers(username,password_hash,created_at)
    VALUES(?,?,?)')->execute(['viewer',password_hash('VeryLongPassword1234',PASSWORD_DEFAULT),$now]);
$vid=(int)$db->lastInsertId();
$_SESSION=['cms_member_id'=>$vid,'cms_member_version'=>1,'cms_username'=>'viewer'];
expect(cms_current_viewer($db)!==null,'Viewer session must validate');
expect(!cms_is_admin(),'Viewer must not be admin');
expect(!cms_can_view_source($db,4242),'Viewer defaults to no source permission');
$db->prepare('INSERT INTO cms_source_permissions(viewer_id,github_id) VALUES(?,?)')
   ->execute([$vid,4242]);
expect(cms_can_view_source($db,4242),'Explicit grant allows source viewing');
expect(count(cms_granted_repositories($db))===1,'Granted listing only');
$db->prepare('DELETE FROM cms_source_permissions WHERE viewer_id=?')->execute([$vid]);
expect(!cms_can_view_source($db,4242),'Revoking grant works immediately');
$db->prepare('UPDATE cms_viewers SET enabled=0,auth_version=auth_version+1 WHERE id=?')
   ->execute([$vid]);
expect(cms_current_viewer($db)===null,'Disabled account session invalid');
$_SESSION=['cms_admin_id'=>1,'cms_username'=>'admin'];
expect(cms_can_view_source($db,4242),'Admin may view every imported repository');
expect(count(cms_granted_repositories($db))===1,'Admin repository list');
foreach(['','src/test.php','.github/workflows/build.yml','some file.txt'] as $path){
    expect(cms_source_path_ok($path),'Valid source path '.$path);
}
foreach(['/etc/passwd','../secret','a/../secret','a//b','a\\b',str_repeat('a',401)] as $path){
    expect(!cms_source_path_ok($path),'Path must be denied');
}
echo "PASS: public private-repository listings, concealed draft details, permission checks and revocation.\n";
