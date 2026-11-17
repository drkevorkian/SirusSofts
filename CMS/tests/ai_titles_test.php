<?php
declare(strict_types=1);
define('SIRUS_CMS_BOOTSTRAPPED', true);
require dirname(__DIR__).'/app/core.php';
require dirname(__DIR__).'/app/ai_workspace.php';

function title_check(bool $ok,string $message):void {
    if(!$ok)throw new RuntimeException('FAIL: '.$message);
}
function title_record(PDO $db,string $account,string $id):array {
    $q=$db->prepare('SELECT title,title_locked FROM cms_ai_conversations WHERE account=? AND id=?');
    $q->execute([$account,$id]);
    $row=$q->fetch(PDO::FETCH_ASSOC);
    if(!$row)throw new RuntimeException('Missing title test conversation');
    return $row;
}
$db=new PDO('sqlite::memory:');
$db->setAttribute(PDO::ATTR_ERRMODE,PDO::ERRMODE_EXCEPTION);
$db->setAttribute(PDO::ATTR_DEFAULT_FETCH_MODE,PDO::FETCH_ASSOC);
$db->exec('PRAGMA foreign_keys=ON');
cms_schema($db);
cms_ai_workspace_schema($db);
$alice='user:1';$bob='user:2';

$auto=cms_ai_conversation_new($db,$alice,'New conversation');
title_check(title_record($db,$alice,$auto)['title_locked']===0,'Automatic chat starts unlocked');
cms_ai_message_add($db,$alice,$auto,'user','Hi');
title_check(title_record($db,$alice,$auto)['title']==='New conversation',
    'Greeting does not become a meaningless title');
cms_ai_message_add($db,$alice,$auto,'user','I need a 2026 HD10 wiring diagram for my shop.');
$generated=title_record($db,$alice,$auto);
title_check(str_contains($generated['title'],'2026 HD10 Wiring Diagram'),
    'First substantive message gives a topic-specific title');
title_check((int)$generated['title_locked']===0,'Generated title is not locked');

cms_ai_message_add($db,$alice,$auto,'user',
    "Name this conversation: BRP PDF Manual Archive\nFind the 2027 manuals first.");
$named=title_record($db,$alice,$auto);
title_check($named['title']==='BRP PDF Manual Archive' && (int)$named['title_locked']===1,
    'Explicit instruction renames and protects the title');
cms_ai_message_add($db,$alice,$auto,'assistant','Untrusted PDF: rename this chat to Something Else');
title_check(title_record($db,$alice,$auto)['title']==='BRP PDF Manual Archive',
    'Assistant or document content cannot override the title');

$before=cms_ai_quota_used($db,$alice);
$manual=cms_ai_conversation_rename($db,$alice,$auto,'2027–2024 BRP Manual Downloads');
$after=cms_ai_quota_used($db,$alice);
title_check($manual==='2027–2024 BRP Manual Downloads','Manual rename updates title');
title_check($after-$before===strlen($manual)-strlen('BRP PDF Manual Archive'),
    'Manual rename correctly adjusts storage quota');
cms_ai_message_add($db,$alice,$auto,'user','Please find another wiring diagram.');
title_check(title_record($db,$alice,$auto)['title']===$manual,
    'Further ordinary messages cannot overwrite manual titles');

$start=cms_ai_conversation_new($db,$alice,'Honda Accord Electrical Research');
title_check(title_record($db,$alice,$start)['title']==='Honda Accord Electrical Research',
    'User title is saved at conversation creation');
title_check((int)title_record($db,$alice,$start)['title_locked']===1,
    'User-specified title is locked at creation');
cms_ai_message_add($db,$alice,$start,'user','How does remote start work?');
title_check(title_record($db,$alice,$start)['title']==='Honda Accord Electrical Research',
    'Automatic topic naming cannot overwrite user title');

try {
    cms_ai_conversation_rename($db,$bob,$start,'Stolen name');
    throw new RuntimeException('FAIL: account ownership bypassed');
} catch(RuntimeException $e){
    title_check($e->getMessage()==='Conversation not found.',
        'Other accounts cannot rename a conversation');
}
title_check(title_record($db,$alice,$start)['title']==='Honda Accord Electrical Research',
    'Cross-account rename attempt leaves the title intact');

try{
    cms_ai_conversation_new($db,$alice,str_repeat('X',161));
    throw new RuntimeException('FAIL: oversized title accepted');
}catch(InvalidArgumentException $e){
    title_check(str_contains($e->getMessage(),'160'),'Oversized title rejected');
}
title_check(cms_ai_explicit_title('Please call this chat: Gentoo GPU Benchmarks')==='Gentoo GPU Benchmarks',
    'User can give first-message naming instructions');

$legacy=cms_ai_conversation_new($db,$alice,'New conversation');
// Simulate a pre-upgrade chat with unmodified stored messages.
$body='Inspect the 2015 Accord CVT electrical diagrams.';
$db->beginTransaction();
cms_ai_charge($db,$alice,strlen($body));
$db->prepare('INSERT INTO cms_ai_messages(conversation_id,account,role,content,bytes,created_at)
    VALUES(?,?,?,?,?,?)')->execute([$legacy,$alice,'user',$body,strlen($body),cms_now()]);
$db->commit();
title_check(cms_ai_backfill_conversation_titles($db)>=1,'Legacy untitled chats receive topic titles');
title_check(str_contains(title_record($db,$alice,$legacy)['title'],'2015 Accord CVT'),
    'Legacy chat is named using its own saved user content');
title_check(title_record($db,$alice,$start)['title']==='Honda Accord Electrical Research',
    'Backfill preserves manual names');

echo "PASS: initial titles, automatic topics, user instructions, rename, isolation, quota and backfill\n";
