<?php
declare(strict_types=1);
if(PHP_SAPI!=='cli'){http_response_code(404);exit;}
define('SIRUS_CMS_BOOTSTRAPPED',true);
require dirname(__DIR__).'/app/core.php';
require dirname(__DIR__).'/app/site.php';
require dirname(__DIR__).'/app/admin.php';
function ai_assert(bool $ok,string $why):void{if(!$ok){fwrite(STDERR,"FAIL $why\n");exit(1);}}
try{cms_ai_profile('../bad');ai_assert(false,'profile validation');}
catch(InvalidArgumentException $expected){}
try{cms_ai_valid_document(['profile'=>'coding','title'=>'bad','content'=>'text','source_url'=>'javascript:alert(1)']);ai_assert(false,'URL validation');}
catch(InvalidArgumentException $expected){}
try{cms_ai_valid_example(['profile'=>'time','prompt'=>'a','response'=>'b','split'=>'invalid']);ai_assert(false,'split validation');}
catch(InvalidArgumentException $expected){}
if(!extension_loaded('pdo_sqlite')){echo "PASS: validation; SQLite unavailable\n";exit(0);}
$db=new PDO('sqlite::memory:',null,null,[PDO::ATTR_ERRMODE=>PDO::ERRMODE_EXCEPTION,PDO::ATTR_DEFAULT_FETCH_MODE=>PDO::FETCH_ASSOC]);
$db->exec('PRAGMA foreign_keys=ON');
cms_schema($db);cms_schema($db);
ai_assert((int)$db->query('SELECT count(*) FROM cms_ai_profiles')->fetchColumn()===3,'idempotent profiles');
$db->prepare('INSERT INTO cms_ai_documents(profile,title,source_url,content,reviewed,created_at) VALUES(?,?,?,?,?,?)')
   ->execute(['physics','Trusted','https://example.org/paper','Lorentz factor gamma',1,cms_now()]);
$db->prepare('INSERT INTO cms_ai_documents(profile,title,source_url,content,reviewed,created_at) VALUES(?,?,?,?,?,?)')
   ->execute(['coding','Unreviewed','','SECRET',0,cms_now()]);
$db->prepare('INSERT INTO cms_ai_examples(profile,prompt,response,split,reviewed,created_at) VALUES(?,?,?,?,?,?)')
   ->execute(['time','Compute gamma','5/3','train',1,cms_now()]);
ai_assert(count(iterator_to_array(cms_ai_export_records($db,'knowledge')))===1,'reviewed RAG export only');
ai_assert(count(iterator_to_array(cms_ai_export_records($db,'train')))===1,'training export only');
ai_assert(count(iterator_to_array(cms_ai_export_records($db,'eval')))===0,'no train/eval leakage');
$db->exec("UPDATE cms_ai_profiles SET enabled=0 WHERE slug='physics'");
ai_assert(count(iterator_to_array(cms_ai_export_records($db,'knowledge')))===0,'disabled source excluded');
$_SESSION=['cms_admin_id'=>1,'cms_username'=>'tester'];
ob_start();cms_ai_page($db);$html=(string)ob_get_clean();
ai_assert(str_contains($html,'AI Knowledge &amp; Training'),'admin renders');
ai_assert(!str_contains($html,'SECRET'),'body content not exposed on listing');
echo "PASS: AI schema, validation, review gating, separated exports and admin UI.\n";
