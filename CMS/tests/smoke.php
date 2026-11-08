<?php
declare(strict_types=1);
/** Run locally from repo root: php CMS/tests/smoke.php (PHP >=8.1). */
if(PHP_SAPI!=='cli'){http_response_code(404);exit;}
define('SIRUS_CMS_BOOTSTRAPPED',true);
require __DIR__.'/../app/core.php';
require __DIR__.'/../app/site.php';
require __DIR__.'/../app/admin.php';
function check(bool $condition,string $message): void {
    if(!$condition){fwrite(STDERR,'FAIL: '.$message."\n");exit(1);}
}
check(cms_safe_slug('cpoint')&&cms_safe_slug('my-next-project'),'Normal slugs allowed');
check(!cms_safe_slug('../private')&&!cms_safe_slug('admin')&&!cms_safe_slug('bad/slug'),
      'Reserved/traversal slugs rejected');
$_SERVER['HTTP_HOST']='sirussofts.com';
check(cms_project_host_slug(cms_request_host())===null,'Public root host');
check(cms_project_host_slug('cpoint.sirussofts.com')==='cpoint','COM project subdomain');
check(cms_project_host_slug('cpoint.sirussofts.org')==='cpoint','ORG project subdomain');
check(cms_project_host_slug('sirussofts.org')===null,'ORG public root');
check(cms_root_domain()==='sirussofts.com','COM root host selection');
$_SERVER['HTTP_HOST']='sirussofts.org';
check(cms_root_domain()==='sirussofts.org','ORG root host selection');
check(cms_project_host_slug('evil.example.com')==='','Foreign domain denied');
check(cms_project_host_slug('a.b.sirussofts.com')==='','Nested subdomains denied');
check(!str_contains(cms_body('<script>alert(1)</script>'),'<script>'),'Content escaped');
check(str_contains(cms_body('## Heading'),'&gt;')===false,'Safe headings');
check(str_contains(cms_body('## Heading'),'<h2>Heading</h2>'),'Heading support');
$_SERVER['REQUEST_URI']='/../private';
check(cms_request_path()==='/404','Traversal routing rejected');
$_SERVER['REQUEST_URI']='/project%2Fadmin';
check(cms_request_path()==='/404','Encoded slash rejected');
$_SERVER['REQUEST_URI']='/projects';
check(cms_request_path()==='/projects','Normal route');
if(!extension_loaded('pdo_sqlite')){
    fwrite(STDOUT,"PASS: routing/escaping checks. PDO_SQLITE unavailable; skipped database smoke checks.\n");
    exit(0);
}
$db=new PDO('sqlite::memory:',null,null,[PDO::ATTR_ERRMODE=>PDO::ERRMODE_EXCEPTION,
    PDO::ATTR_DEFAULT_FETCH_MODE=>PDO::FETCH_ASSOC]);
cms_schema($db);
cms_schema($db); // idempotent migration
$setting=$db->prepare('INSERT INTO cms_settings(name,value) VALUES (?,?)');
foreach(cms_defaults() as $k=>$v)$setting->execute([$k,$v]);
$now=cms_now();
$db->prepare('INSERT INTO cms_projects
    (slug,title,summary,body,category,icon,kind,state,sort_order,created_at,updated_at)
    VALUES(?,?,?,?,?,?,?,?,?,?,?)')
   ->execute(['hello','A public project','The sample project','Content','Software','◈',
              'page','published',10,$now,$now]);
$db->prepare('INSERT INTO cms_projects
    (slug,title,summary,body,category,icon,kind,state,sort_order,created_at,updated_at)
    VALUES(?,?,?,?,?,?,?,?,?,?,?)')
   ->execute(['draft-only','Hidden','Hidden sample','Content','Software','◈',
              'page','draft',20,$now,$now]);
check(count(cms_projects($db,true))===1,'Only published projects visible');
check(cms_project($db,'draft-only',true)===null,'Unpublished project not public');
check(cms_project($db,'hello',true)!==null,'Published project route');
$ids=$db->query('SELECT id FROM cms_projects ORDER BY sort_order ASC')->fetchAll(PDO::FETCH_COLUMN);
cms_reorder_project_ids($db,$ids[1].','.$ids[0]);
check(cms_projects($db,false)[0]['slug']==='draft-only','Reordering changes admin project order');
try {
    cms_reorder_project_ids($db,$ids[0].','.$ids[0]);
    check(false,'Duplicate project IDs must fail');
} catch(InvalidArgumentException $expected){}
try {
    cms_reorder_project_ids($db,(string)$ids[0]);
    check(false,'Missing project IDs must fail');
} catch(InvalidArgumentException $expected){}
check(cms_projects($db,true)[0]['slug']==='hello','Published project still visible after reorder');

ob_start();cms_public_home($db);$home=ob_get_clean();
check(str_contains((string)$home,'A public project'),'Public homepage renders a project');
check(!str_contains((string)$home,'Hidden sample'),'Unpublished project not leaked on homepage');
check(!str_contains((string)$home,'name="password"'),'Public homepage has no login form');
$db->prepare('INSERT INTO cms_pages(slug,title,body,state,created_at,updated_at) VALUES(?,?,?,?,?,?)')
    ->execute(['readme','Read Me','Page text','published',$now,$now]);
check(cms_page($db,'readme',true)!==null,'Public CMS page query');
fwrite(STDOUT,"PASS: CMS routing, escaping, schema, unpublished privacy and public homepage.\n");
