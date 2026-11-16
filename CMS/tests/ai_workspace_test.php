<?php
declare(strict_types=1);
define('SIRUS_CMS_BOOTSTRAPPED',true);
require dirname(__DIR__).'/app/core.php';
require dirname(__DIR__).'/app/ai_workspace.php';
require dirname(__DIR__).'/app/ai_api.php';
function check(bool $condition,string $message):void {
    if(!$condition)throw new RuntimeException('FAIL: '.$message);
}
$db=new PDO('sqlite::memory:');
$db->setAttribute(PDO::ATTR_ERRMODE,PDO::ERRMODE_EXCEPTION);
$db->setAttribute(PDO::ATTR_DEFAULT_FETCH_MODE,PDO::FETCH_ASSOC);
$db->exec('PRAGMA foreign_keys=ON');
cms_schema($db);
cms_ai_workspace_schema($db);
$now=cms_now();
$db->prepare('INSERT INTO cms_viewers(id,username,password_hash,created_at) VALUES(?,?,?,?)')
   ->execute([1,'alice','unused',$now]);
$db->prepare('INSERT INTO cms_viewers(id,username,password_hash,created_at) VALUES(?,?,?,?)')
   ->execute([2,'bob','unused',$now]);
$alice='user:1';$bob='user:2';
$id=cms_ai_conversation_new($db,$alice,'Engine diagnostics');
cms_ai_message_add($db,$alice,$id,'user','Check the timing chain on my engine.');
check(count(cms_ai_message_history($db,$alice,$id))===1,'Owner can read conversation');
try {cms_ai_message_history($db,$bob,$id);throw new Exception('Leaked conversation');}
catch(RuntimeException $e) {check($e->getMessage()==='Conversation not found.','Cross-account denied');}
$mem=cms_ai_memory_save($db,$alice,'Check camshaft wear before proceeding.',$id);
check(cms_ai_quota_used($db,$alice)>0,'Quota counts conversation and memory');
check(str_contains(cms_ai_recall($db,$alice,'camshaft wear'), 'camshaft'),
    'Saved global or conversation memory retrievable');
check(cms_ai_recall($db,$bob,'camshaft wear')==='','Bob cannot recall Alice memory');
try {cms_ai_memory_delete($db,$bob,$mem);throw new Exception('Deleted other user memory');}
catch(RuntimeException $e){check($e->getMessage()==='Memory not found.','Cross-account delete denied');}
cms_ai_memory_delete($db,$alice,$mem);
$usage=cms_ai_quota_used($db,$alice);
$db->prepare('UPDATE cms_ai_usage SET bytes_used=? WHERE account=?')
   ->execute([CMS_AI_MEMORY_QUOTA-5,$alice]);
try {cms_ai_memory_save($db,$alice,'This cannot fit in five bytes.');
      throw new Exception('Exceeded quota');}
catch(RuntimeException $e){check(str_contains($e->getMessage(),'quota'),'2 GiB enforced');}
check(cms_ai_quota_used($db,$alice)===CMS_AI_MEMORY_QUOTA-5,'Failed charge is atomic');
$db->prepare('UPDATE cms_ai_usage SET bytes_used=? WHERE account=?')->execute([$usage,$alice]);
cms_ai_conversation_delete($db,$alice,$id);
check(cms_ai_quota_used($db,$alice)===0,'Deletion refunds message storage');
$guest=['role'=>'guest','account'=>null];
$member=['role'=>'user','account'=>$alice];
$admin=['role'=>'admin','account'=>'admin:1'];
check(cms_ai_allowed_profiles($db,$guest)===[],'All categories private by default');
$db->exec("INSERT INTO cms_ai_category_public(profile,enabled) VALUES('mechanics',1)");
check(count(cms_ai_allowed_profiles($db,$guest))===1,'Admin publication gates guest categories');
$db->exec("INSERT INTO cms_ai_category_grants(viewer_id,profile) VALUES(1,'physics')");
check(count(cms_ai_allowed_profiles($db,$member))===2,'User grants and public categories');
check(count(cms_ai_allowed_profiles($db,$admin))===7,'Admin sees all categories');
$db->prepare("INSERT INTO cms_ai_document_objects(sha256,bytes,profile,status,created_at)
   VALUES(?,100,'mechanics','parsed',?)")->execute([str_repeat('a',64),$now]);
$object=(int)$db->lastInsertId();
$db->prepare('INSERT INTO cms_ai_document_chunks(object_id,page,part,body) VALUES(?,?,?,?)')
   ->execute([$object,1,1,'Authorized test torque specification.']);
$db->prepare('INSERT INTO cms_ai_document_access(object_id,account,title,created_at) VALUES(?,?,?,?)')
   ->execute([$object,$alice,'Personal service manual',$now]);
check(count(cms_ai_account_documents($db,$alice))===1,'Owner document visible');
check(cms_ai_account_documents($db,$bob)===[],'Private document not visible to other user');
check(count(cms_ai_api_knowledge($db,$member,'mechanics'))===1,'Authorized retrieval');
check(cms_ai_api_knowledge($db,$guest,'mechanics')===[],'Public category does not leak personal documents');
check(str_contains(cms_ai_api_context($db,$member,'mechanics','torque specification'),
    'Authorized test torque'),'Owned document included in authorized context');
check(!str_contains(cms_ai_api_context($db,$guest,'mechanics','torque specification'),
    'Authorized test torque'),'Guest never sees personal document');
echo "PASS: account isolation, quota, category ACL, private document and recall tests\n";
