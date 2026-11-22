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
// The server must not soft-delete a conversation that still has an active job.
$job=str_repeat('a',32);
$db->prepare("INSERT INTO cms_ai_requests
(job_id,account,conversation_id,state,created_at,updated_at)
VALUES(?,?,?,'queued',?,?)")->execute([$job,$alice,$id,$now,$now]);
try {cms_ai_conversation_delete($db,$alice,$id);throw new Exception('Deleted an active job');}
catch(RuntimeException $e) {check(str_contains($e->getMessage(),'generation'),
    'Active inference blocks chat deletion');}
$db->prepare("UPDATE cms_ai_requests SET state='completed' WHERE job_id=?")->execute([$job]);
cms_ai_conversation_delete($db,$alice,$id);
check(cms_ai_quota_used($db,$alice)===$usage,'Soft deletion retains charged storage');
$st=$db->prepare('SELECT deleted_at FROM cms_ai_conversations WHERE id=? AND account=?');
$st->execute([$id,$alice]);
check(is_string($st->fetchColumn()),'Soft deletion preserves the conversation row');
try {cms_ai_message_history($db,$alice,$id);throw new Exception('Deleted chat accessible');}
catch(RuntimeException $e){check($e->getMessage()==='Conversation not found.','Deleted chat excluded from active access');}
check(cms_ai_recall($db,$alice,'timing chain')==='','Deleted chats excluded from cross-chat recall');
try {cms_ai_conversation_restore($db,$bob,$id);throw new Exception('Other user restored chat');}
catch(RuntimeException $e){check($e->getMessage()==='Deleted conversation not found.',
    'Another account cannot restore a private chat');}
try {cms_ai_conversation_delete($db,$alice,$id);throw new Exception('Double delete succeeded');}
catch(RuntimeException $e){check($e->getMessage()==='Conversation not found.',
    'Already-deleted conversations cannot be deleted twice');}
cms_ai_conversation_restore($db,$alice,$id);
check(count(cms_ai_message_history($db,$alice,$id))===1,'Restore preserves messages');
check(str_contains(cms_ai_recall($db,$alice,'timing chain'),'timing chain'),
    'Restored chats are again eligible for normal recall');
check(cms_ai_quota_used($db,$alice)===$usage,'Restore does not charge twice');
$guest=['role'=>'guest','account'=>null];
$member=['role'=>'user','account'=>$alice];
$admin=['role'=>'admin','account'=>'admin:1'];
check(cms_ai_allowed_profiles($db,$guest)===[],'All categories private by default');
$db->exec("INSERT INTO cms_ai_category_public(profile,enabled) VALUES('mechanics',1)");
check(count(cms_ai_allowed_profiles($db,$guest))===1,'Admin publication gates guest categories');
$db->exec("INSERT INTO cms_ai_category_grants(viewer_id,profile) VALUES(1,'physics')");
check(count(cms_ai_allowed_profiles($db,$member))===2,'User grants and public categories');
check(count(cms_ai_allowed_profiles($db,$admin))===count(cms_ai_expertise()),
    'Admin sees all configured categories');
$db->prepare("INSERT INTO cms_ai_document_objects(sha256,bytes,profile,status,created_at)
   VALUES(?,100,'mechanics','parsed',?)")->execute([str_repeat('a',64),$now]);
$object=(int)$db->lastInsertId();
$db->prepare('INSERT INTO cms_ai_document_chunks(object_id,page,part,body) VALUES(?,?,?,?)')
   ->execute([$object,1,1,'Authorized test torque specification.']);
$db->prepare('INSERT INTO cms_ai_document_access(object_id,account,profile,title,created_at) VALUES(?,?,?,?,?)')
   ->execute([$object,$alice,'mechanics','Personal service manual',$now]);
check(count(cms_ai_account_documents($db,$alice))===1,'Owner document visible');
check(cms_ai_account_documents($db,$bob)===[],'Private document not visible to other user');
check(count(cms_ai_api_knowledge($db,$member,'mechanics'))===1,'Authorized retrieval');
check(cms_ai_api_knowledge($db,$guest,'mechanics')===[],'Public category does not leak personal documents');
check(str_contains(cms_ai_api_context($db,$member,'mechanics','torque specification'),
    'Authorized test torque'),'Owned document included in authorized context');
check(!str_contains(cms_ai_api_context($db,$guest,'mechanics','torque specification'),
    'Authorized test torque'),'Guest never sees personal document');
$db->prepare('INSERT INTO cms_ai_document_access(object_id,account,profile,title,created_at) VALUES(?,?,?,?,?)')
   ->execute([$object,$bob,'science','Same PDF, different authorized category',$now]);
check(cms_ai_account_documents($db,$bob)[0]['profile']==='science',
    'Canonical document is reused without inheriting first uploader category');
check(count(cms_ai_api_knowledge($db,$member,'mechanics'))===1,
    'Other accounts cannot override owner document category');
// On FTS5-enabled builds, old conversations remain searchable beyond the latest 250 rows.
$legacy=cms_ai_conversation_new($db,$alice,'Long-term history');
cms_ai_message_add($db,$alice,$legacy,'user','Unique 1940s carburetor research finding.');
for($i=0;$i<275;$i++)
    cms_ai_message_add($db,$alice,$legacy,'user','Other maintenance note number '.$i);
$fresh=cms_ai_conversation_new($db,$alice,'New recall session');
$hasFts=(bool)$db->query("SELECT 1 FROM sqlite_master
    WHERE type='table' AND name='cms_ai_message_fts'")->fetchColumn();
if($hasFts) {
    check(str_contains(cms_ai_recall($db,$alice,'carburetor research',$fresh),'carburetor'),
          'FTS5 finds context from beyond 250 older messages');
    check(cms_ai_recall($db,$bob,'carburetor research')==='',
          'FTS5 retrieval remains account-isolated');
}
echo "PASS: account isolation, quota, category ACL, private document and recall tests\n";
