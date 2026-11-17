<?php
declare(strict_types=1);
define('SIRUS_CMS_BOOTSTRAPPED',true);
require dirname(__DIR__).'/app/core.php';
require dirname(__DIR__).'/app/ai_workspace.php';

function title_check(bool $condition,string $what):void {
    if(!$condition)throw new RuntimeException('FAIL: '.$what);
}
function title_of(PDO $db,string $id):array {
    $stmt=$db->prepare('SELECT title,title_locked,updated_at FROM cms_ai_conversations WHERE id=?');
    $stmt->execute([$id]);
    return $stmt->fetch(PDO::FETCH_ASSOC);
}
$db=new PDO('sqlite::memory:');
$db->setAttribute(PDO::ATTR_ERRMODE,PDO::ERRMODE_EXCEPTION);
$db->setAttribute(PDO::ATTR_DEFAULT_FETCH_MODE,PDO::FETCH_ASSOC);
$db->exec('PRAGMA foreign_keys=ON');
cms_schema($db);
cms_ai_workspace_schema($db);
cms_ai_workspace_schema($db); // additive migration is idempotent
$cols=array_column($db->query('PRAGMA table_info(cms_ai_conversations)')->fetchAll(PDO::FETCH_ASSOC),'name');
title_check(in_array('title_locked',$cols,true),'Title lock column migrated');

title_check(cms_ai_topic_title('2026 HD10 wire diagram')==='2026 HD10 Wire Diagram',
    'Topic shorthand becomes a useful title');
title_check(cms_ai_topic_title('Hello!')===null,'Greeting alone is not a topic');
title_check(cms_ai_explicit_title('Name this conversation: 2026 HD10 Wiring')==='2026 HD10 Wiring',
    'User can title a conversation');
title_check(cms_ai_explicit_title('Call this chat "Clutch Research"')==='Clutch Research',
    'Quoted first-line title accepted');
title_check(cms_ai_explicit_title("Set the chat title to Electrical Systems\nWhat is relay K2?")==='Electrical Systems',
    'Initial title instruction with an actual task accepted');
title_check(cms_ai_explicit_title('How can I rename a chat?')===null,
    'Ordinary question not misread as a command');

$alice='user:1';$bob='user:2';
$id=cms_ai_conversation_new($db,$alice,'New conversation');
title_check(title_of($db,$id)['title']==='New conversation','Untitled initial state');
cms_ai_message_add($db,$alice,$id,'assistant','Name this conversation: This is untrusted AI output.');
title_check(title_of($db,$id)['title']==='New conversation',
    'Assistant cannot rename a conversation');
cms_ai_message_add($db,$alice,$id,'user','Hi');
title_check(title_of($db,$id)['title']==='New conversation',
    'Greeting does not lock a placeholder title');
cms_ai_message_add($db,$alice,$id,'user','2026 HD10 wire diagram');
title_check(title_of($db,$id)['title']==='2026 HD10 Wire Diagram',
    'First substantive user question titles the chat');
title_check((int)title_of($db,$id)['title_locked']===0,'Auto title stays editable');

$before=cms_ai_quota_used($db,$alice);
$title='2026 HD10 Electrical Diagrams';
cms_ai_conversation_rename($db,$alice,$id,$title);
title_check(title_of($db,$id)['title']===$title,'Manual rename persists');
title_check((int)title_of($db,$id)['title_locked']===1,
    'Manual title protected from auto-rename');
title_check(cms_ai_quota_used($db,$alice)===$before+strlen($title)-strlen('2026 HD10 Wire Diagram'),
    'Manual rename charges only delta in account quota');
cms_ai_message_add($db,$alice,$id,'user','Find wiring for a different model, too.');
title_check(title_of($db,$id)['title']===$title,
    'Ordinary later questions never overwrite a chosen name');
$before=cms_ai_quota_used($db,$alice);
cms_ai_conversation_rename($db,$alice,$id,$title);
title_check(cms_ai_quota_used($db,$alice)===$before,
    'Saving unchanged title does not change storage accounting');

cms_ai_message_add($db,$alice,$id,'user',"Name this conversation: HD10 Shop Wiring\nFind page 10.");
title_check(title_of($db,$id)['title']==='HD10 Shop Wiring',
    'User can explicitly rename in chat at any time');
title_check((int)title_of($db,$id)['title_locked']===1,
    'Chat-directed title remains locked');
try {
    cms_ai_conversation_rename($db,$bob,$id,'Steal private title');
    throw new RuntimeException('Cross-account edit was permitted');
} catch(RuntimeException $e){
    title_check($e->getMessage()==='Conversation not found.',
        'Cross-account rename rejected without leaking the title');
}
foreach(['',"bad\nname",str_repeat('x',161)] as $bad){
    try{
        cms_ai_conversation_rename($db,$alice,$id,$bad);
        throw new RuntimeException('Invalid title accepted');
    }catch(InvalidArgumentException $e){
        title_check(str_contains($e->getMessage(),'title'),
            'Bad title rejected');
    }
}
title_check(title_of($db,$id)['title']==='HD10 Shop Wiring',
    'Invalid edits do not alter the title');

$second=cms_ai_conversation_new($db,$alice,'New conversation');
cms_ai_conversation_rename($db,$alice,$second,'New conversation');
cms_ai_message_add($db,$alice,$second,'user','A meaningful first message');
title_check(title_of($db,$second)['title']==='New conversation',
    'Manually chosen placeholder is not auto-renamed');

$firstLine=cms_ai_conversation_new($db,$alice,'New conversation');
cms_ai_message_add($db,$alice,$firstLine,'user',
    "Name this conversation: Defender HD10 Circuit Research\nFind the diagram.");
title_check(title_of($db,$firstLine)['title']==='Defender HD10 Circuit Research',
    'New conversation accepts first-line explicit title before inference');

// Emulate a pre-upgrade legacy chat: its old messages exist, but the title
// was never generated. Backfill must use the earliest substantive user turn.
$old=cms_ai_conversation_new($db,$alice,'New conversation');
$oldTimestamp=title_of($db,$old)['updated_at'];
foreach(['Hello','2026 Can-Am Defender HD10 wiring and fuse box'] as $message){
    $db->beginTransaction();
    cms_ai_charge($db,$alice,strlen($message));
    $db->prepare("INSERT INTO cms_ai_messages(conversation_id,account,role,content,bytes,created_at)
                  VALUES(?,?,'user',?,?,?)")
       ->execute([$old,$alice,$message,strlen($message),cms_now()]);
    $db->commit();
}
$custom=cms_ai_conversation_new($db,$alice,'My Manual Research');
title_check(title_of($db,$custom)['title']==='My Manual Research',
    'Optional new-chat title is saved immediately');
title_check((int)title_of($db,$custom)['title_locked']===1,
    'Initial user-selected title is locked before the first prompt');
cms_ai_message_add($db,$alice,$custom,'user','Ask something else');
title_check(title_of($db,$custom)['title']==='My Manual Research',
    'Automatic naming never overwrites a title supplied at creation');
$changes=cms_ai_backfill_conversation_titles($db);
title_check($changes===1,'Only one historic placeholder backfilled');
title_check(str_contains(title_of($db,$old)['title'],'Defender HD10'),
    'Backfill uses existing user-message content');
title_check(title_of($db,$old)['updated_at']===$oldTimestamp,
    'Backfill preserves original chronological order');
title_check(title_of($db,$custom)['title']==='My Manual Research',
    'Backfill preserves all custom titles');
title_check(cms_ai_backfill_conversation_titles($db)===0,
    'Backfill is idempotent');

// Simulate a real legacy database without title_locked before migration.
$legacy=new PDO('sqlite::memory:');
$legacy->setAttribute(PDO::ATTR_ERRMODE,PDO::ERRMODE_EXCEPTION);
$legacy->setAttribute(PDO::ATTR_DEFAULT_FETCH_MODE,PDO::FETCH_ASSOC);
$legacy->exec('CREATE TABLE cms_ai_conversations(
    id TEXT PRIMARY KEY,account TEXT NOT NULL,title TEXT NOT NULL,
    created_at TEXT NOT NULL,updated_at TEXT NOT NULL)');
cms_ai_workspace_schema($legacy);
$columns=array_column($legacy->query('PRAGMA table_info(cms_ai_conversations)')->fetchAll(PDO::FETCH_ASSOC),'name');
title_check(count(array_filter($columns,fn($c)=>$c==='title_locked'))===1,
    'Existing database upgraded by adding a single lock column');
title_check(count(array_filter($columns,fn($c)=>$c==='deleted_at'))===1,
    'Existing database upgraded with reversible deletion timestamp');
cms_ai_workspace_schema($legacy);
$columnsAgain=array_column($legacy->query('PRAGMA table_info(cms_ai_conversations)')->fetchAll(PDO::FETCH_ASSOC),'name');
title_check(count(array_filter($columnsAgain,fn($c)=>$c==='deleted_at'))===1,
    'Deletion migration stays idempotent');
echo "PASS: private conversation naming, manual override, first-message topic, ownership, quota and legacy migration.\n";
