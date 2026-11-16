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
ai_assert(cms_ai_depth('50')===50,'default depth');
ai_assert(cms_ai_depth('5000')===5000,'maximum depth');
foreach(['0','5001','-1','abc'] as $invalid){
    try{cms_ai_depth($invalid);ai_assert(false,'depth should fail');}
    catch(InvalidArgumentException $expected){}
}
ai_assert(cms_ai_engine_selection(['google','bing','duckduckgo'])===['google','bing','duckduckgo'],'engine selection');
try{cms_ai_engine_selection(['google','google']);ai_assert(false,'duplicate engine');}
catch(InvalidArgumentException $expected){}
try{cms_ai_pdf_filename('../evil.php');ai_assert(false,'invalid PDF name');}
catch(InvalidArgumentException $expected){}
ai_assert(cms_ai_pdf_filename('BRP HD11 Shop Manual.pdf')==='BRP HD11 Shop Manual.pdf','manual filename');
ai_assert(cms_ai_profile('finance')==='finance','finance expertise');
ai_assert(cms_ai_profile('mechanics')==='mechanics','mechanics expertise');
ai_assert(cms_ai_profile('relativity')==='relativity','relativity expertise');
if(!extension_loaded('pdo_sqlite')){echo "PASS: validation; SQLite unavailable\n";exit(0);}
$db=new PDO('sqlite::memory:',null,null,[PDO::ATTR_ERRMODE=>PDO::ERRMODE_EXCEPTION,PDO::ATTR_DEFAULT_FETCH_MODE=>PDO::FETCH_ASSOC]);
$db->exec('PRAGMA foreign_keys=ON');
cms_schema($db);cms_schema($db);
ai_assert((int)$db->query('SELECT count(*) FROM cms_ai_profiles')->fetchColumn()===7,'idempotent profiles');
ai_assert((int)$db->query('SELECT depth FROM cms_ai_research_config')->fetchColumn()===50,'persisted default search depth');
ai_assert((int)$db->query("SELECT count(*) FROM sqlite_master WHERE name='cms_ai_pdf_uploads'")->fetchColumn()===1,'PDF staging schema');
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
// Simulate a populated 2026 three-profile installation and verify preservation.
$legacy=new PDO('sqlite::memory:',null,null,[PDO::ATTR_ERRMODE=>PDO::ERRMODE_EXCEPTION]);
$legacy->exec('PRAGMA foreign_keys=ON');
$legacy->exec("CREATE TABLE cms_ai_profiles (slug TEXT PRIMARY KEY CHECK(slug IN ('coding','physics','time')),title TEXT NOT NULL,enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0,1)))");
$legacy->exec("CREATE TABLE cms_ai_documents (id INTEGER PRIMARY KEY,profile TEXT REFERENCES cms_ai_profiles(slug),title TEXT,source_url TEXT,content TEXT,reviewed INTEGER,created_at TEXT)");
$legacy->exec("INSERT INTO cms_ai_profiles(slug,title) VALUES('physics','Science & physics')");
$legacy->exec("INSERT INTO cms_ai_documents VALUES(1,'physics','Legacy','','Lorentz',1,'2026')");
cms_ai_schema($legacy);
ai_assert((int)$legacy->query("SELECT COUNT(*) FROM cms_ai_profiles")->fetchColumn()===7,'legacy upgraded');
ai_assert((string)$legacy->query("SELECT profile FROM cms_ai_documents WHERE id=1")->fetchColumn()==='physics','legacy documents retained');
ai_assert($legacy->query('PRAGMA foreign_key_check')->fetchColumn()===false,'legacy foreign keys intact');
echo "PASS: AI schema, validation, review gating, separated exports and admin UI.\n";
