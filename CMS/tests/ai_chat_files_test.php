<?php
declare(strict_types=1);
if(PHP_SAPI!=='cli')exit;
define('SIRUS_CMS_BOOTSTRAPPED',true);
require __DIR__.'/../app/core.php';
require __DIR__.'/../app/ai_chat_files.php';
function expect_attachment(bool $ok,string $label):void {
    if(!$ok)throw new RuntimeException('Attachment test: '.$label);
}
expect_attachment(cms_ai_chat_file_type('manual.PDF')==='pdf','PDF type');
expect_attachment(cms_ai_chat_file_type('notes.md')==='text','Markdown type');
expect_attachment(cms_ai_chat_file_type('source.rs')==='text','Rust source type');
foreach(['passwd.exe','../../secret.txt',"bad\nline.csv"] as $name){
    try {
        cms_ai_chat_file_name($name);
        if(str_ends_with($name,'.exe'))cms_ai_chat_file_type($name);
        throw new RuntimeException('Unexpected unsafe attachment accepted');
    }catch(InvalidArgumentException $expected){}
}
if(!extension_loaded('pdo_sqlite')){echo "PASS attachment validation; SQLite missing\n";exit;}
$db=new PDO('sqlite::memory:',null,null,[
    PDO::ATTR_ERRMODE=>PDO::ERRMODE_EXCEPTION,
    PDO::ATTR_DEFAULT_FETCH_MODE=>PDO::FETCH_ASSOC]);
$db->exec('PRAGMA foreign_keys=ON');
cms_schema($db);
cms_ai_chat_schema($db);
cms_ai_chat_schema($db);
$owner='admin:1';
$conv=cms_ai_conversation_new($db,$owner,'PDF conversation');
$other=cms_ai_conversation_new($db,'user:2','Private conversation');
$id=str_repeat('a',32);
$db->prepare("INSERT INTO cms_ai_chat_files(id,conversation_id,account,profile,name,kind,bytes,created_at)
  VALUES (?,?,?,'mechanics','HD11 notes.txt','text',100,?)")
   ->execute([$id,$conv,$owner,cms_now()]);
$db->prepare('INSERT INTO cms_ai_chat_text_chunks(file_id,page,part,body) VALUES (?,0,1,?)')
   ->execute([$id,'HD11 primary clutch bolt torque 75 Nm from workshop notes']);
$context=cms_ai_chat_context($db,$owner,$conv,'HD11 clutch torque');
expect_attachment(str_contains($context['context'],'75 Nm'),'Current conversation attachment retrieval');
expect_attachment(str_contains($context['context'],'[A1]'),'Source citation');
expect_attachment($context['pending']===[],'No false pending');
expect_attachment(count(cms_ai_chat_list($db,$owner,$conv))===1,'Owner list');
expect_attachment(cms_ai_chat_list($db,'user:2',$other)===[],'Cross-account isolation');
try {cms_ai_chat_list($db,'user:2',$conv);throw new RuntimeException('Isolation failed');}
catch(RuntimeException $expected){}
cms_ai_chat_remove($db,$owner,$conv,$id);
expect_attachment(cms_ai_chat_list($db,$owner,$conv)===[],'Explicit removal');
echo "PASS: chat attachment type gates, owner isolation, retrieval and removal\n";
