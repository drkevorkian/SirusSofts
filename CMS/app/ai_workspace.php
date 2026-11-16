<?php
declare(strict_types=1);
if (!defined('SIRUS_CMS_BOOTSTRAPPED')) { http_response_code(404); exit; }
require_once __DIR__.'/access.php';
require_once __DIR__.'/ai_training.php';

/** Private account data: never write documents or chat records beneath public_html. */
const CMS_AI_MEMORY_QUOTA = 2147483648; // 2 GiB, only messages and user memories
const CMS_AI_MAX_MEMORY = 16000;
const CMS_AI_MAX_MESSAGE = 262144;

function cms_ai_workspace_schema(PDO $db): void {
    $db->exec(<<<'SQL'
CREATE TABLE IF NOT EXISTS cms_ai_usage (
    account TEXT PRIMARY KEY, bytes_used INTEGER NOT NULL DEFAULT 0
      CHECK(bytes_used BETWEEN 0 AND 2147483648)
);
CREATE TABLE IF NOT EXISTS cms_ai_conversations (
    id TEXT PRIMARY KEY, account TEXT NOT NULL, title TEXT NOT NULL,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS cms_ai_conversation_owner ON cms_ai_conversations(account,updated_at);
CREATE TABLE IF NOT EXISTS cms_ai_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL REFERENCES cms_ai_conversations(id) ON DELETE CASCADE,
    account TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('user','assistant')),
    content TEXT NOT NULL, bytes INTEGER NOT NULL, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS cms_ai_message_owner ON cms_ai_messages(account,conversation_id,id);
CREATE TABLE IF NOT EXISTS cms_ai_memories (
    id TEXT PRIMARY KEY, account TEXT NOT NULL,
    conversation_id TEXT, content TEXT NOT NULL, source_url TEXT NOT NULL DEFAULT '',
    bytes INTEGER NOT NULL, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS cms_ai_memory_owner ON cms_ai_memories(account,created_at);
CREATE TABLE IF NOT EXISTS cms_ai_requests (
    job_id TEXT PRIMARY KEY, account TEXT NOT NULL, conversation_id TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('queued','running','completed','error')),
    last_seq INTEGER NOT NULL DEFAULT 0, output TEXT NOT NULL DEFAULT '',
    worker TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS cms_ai_request_owner ON cms_ai_requests(account,conversation_id);
CREATE TABLE IF NOT EXISTS cms_ai_document_objects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sha256 TEXT NOT NULL UNIQUE, bytes INTEGER NOT NULL,
    profile TEXT NOT NULL, status TEXT NOT NULL
      CHECK(status IN ('queued','processing','parsed','failed')),
    staging_key TEXT NOT NULL DEFAULT '',
    pages INTEGER NOT NULL DEFAULT 0, total_pages INTEGER NOT NULL DEFAULT 0,
    processed_pages INTEGER NOT NULL DEFAULT 0, error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL, claimed_at INTEGER NOT NULL DEFAULT 0,
    completed_at TEXT
);
CREATE TABLE IF NOT EXISTS cms_ai_document_access (
    object_id INTEGER NOT NULL REFERENCES cms_ai_document_objects(id) ON DELETE CASCADE,
    account TEXT NOT NULL, profile TEXT NOT NULL, title TEXT NOT NULL,
    created_at TEXT NOT NULL, PRIMARY KEY(object_id,account,profile)
);
CREATE INDEX IF NOT EXISTS cms_ai_doc_owner ON cms_ai_document_access(account);
CREATE TABLE IF NOT EXISTS cms_ai_document_chunks (
    object_id INTEGER NOT NULL REFERENCES cms_ai_document_objects(id) ON DELETE CASCADE,
    page INTEGER NOT NULL, part INTEGER NOT NULL, body TEXT NOT NULL,
    PRIMARY KEY(object_id,page,part)
);
CREATE TABLE IF NOT EXISTS cms_ai_api_rate (
    actor TEXT NOT NULL, period INTEGER NOT NULL, uses INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(actor,period)
);
CREATE TABLE IF NOT EXISTS cms_ai_category_public (
    profile TEXT PRIMARY KEY, enabled INTEGER NOT NULL DEFAULT 0
      CHECK(enabled IN (0,1))
);
CREATE TABLE IF NOT EXISTS cms_ai_category_grants (
    viewer_id INTEGER NOT NULL REFERENCES cms_viewers(id) ON DELETE CASCADE,
    profile TEXT NOT NULL, PRIMARY KEY(viewer_id,profile)
);
SQL);
    cms_ai_search_schema($db);
}
/** SQLite FTS5 covers the full account history, without sending data to any server. */
function cms_ai_search_schema(PDO $db):void {
    try {
        foreach(['memory'=>['cms_ai_memories','cms_ai_memory_fts'],
                 'message'=>['cms_ai_messages','cms_ai_message_fts']] as [$source,$index]){
            $st=$db->prepare("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?");
            $st->execute([$index]);$new=$st->fetchColumn()===false;
            $db->exec("CREATE VIRTUAL TABLE IF NOT EXISTS ".$index."
                       USING fts5(account UNINDEXED,content,tokenize='unicode61')");
            $db->exec("CREATE TRIGGER IF NOT EXISTS ".$index."_insert AFTER INSERT ON ".$source."
                BEGIN INSERT INTO ".$index."(rowid,account,content)
                VALUES(new.rowid,new.account,new.content); END");
            $db->exec("CREATE TRIGGER IF NOT EXISTS ".$index."_delete AFTER DELETE ON ".$source."
                BEGIN DELETE FROM ".$index." WHERE rowid=old.rowid; END");
            if($new)$db->exec("INSERT INTO ".$index."(rowid,account,content)
                SELECT rowid,account,content FROM ".$source);
        }
    }catch(PDOException $e){
        // Older SQLite builds may lack FTS5; limited lexical recall remains available.
        error_log('Sirus AI FTS5 initialization unavailable: '.get_class($e));
    }
}

/** CMS sessions are authoritative. Never trust a client-supplied account or role. */
function cms_ai_identity(PDO $db): array {
    if(cms_is_admin()) return ['role'=>'admin','account'=>'admin:'.(int)$_SESSION['cms_admin_id'],
                               'username'=>(string)($_SESSION['cms_username']??'')];
    $viewer=cms_current_viewer($db);
    if($viewer) return ['role'=>'user','account'=>'user:'.(int)$viewer['id'],
                        'username'=>(string)$viewer['username']];
    return ['role'=>'guest','account'=>null];
}
function cms_ai_actor(array $identity): string {
    if(!is_string($identity['account']??null)) throw new RuntimeException('Sign in to save account data.');
    return $identity['account'];
}
function cms_ai_id(): string { return bin2hex(random_bytes(16)); }
function cms_ai_quota_used(PDO $db,string $account): int {
    $st=$db->prepare('SELECT bytes_used FROM cms_ai_usage WHERE account=?');
    $st->execute([$account]); return (int)($st->fetchColumn()?:0);
}
/** Call inside a write transaction; SQLite serializes writers and prevents quota races. */
function cms_ai_charge(PDO $db,string $account,int $delta): void {
    if(abs($delta)>CMS_AI_MEMORY_QUOTA) throw new InvalidArgumentException('Invalid memory charge.');
    $db->prepare('INSERT OR IGNORE INTO cms_ai_usage(account,bytes_used) VALUES(?,0)')->execute([$account]);
    $check=$db->prepare('SELECT bytes_used FROM cms_ai_usage WHERE account=?');
    $check->execute([$account]);$current=(int)$check->fetchColumn();
    if($current+$delta<0 || $current+$delta>CMS_AI_MEMORY_QUOTA)
        throw new RuntimeException('2 GiB conversation/memory quota exceeded.');
    $st=$db->prepare('UPDATE cms_ai_usage SET bytes_used=bytes_used+?
       WHERE account=? AND bytes_used+? BETWEEN 0 AND ?');
    $st->execute([$delta,$account,$delta,CMS_AI_MEMORY_QUOTA]);
    if($st->rowCount()!==1) throw new RuntimeException('2 GiB conversation/memory quota exceeded.');
}
function cms_ai_require_conversation(PDO $db,string $account,string $id): void {
    if(!preg_match('/^[a-f0-9]{32}$/D',$id)) throw new InvalidArgumentException('Invalid conversation ID.');
    $st=$db->prepare('SELECT 1 FROM cms_ai_conversations WHERE id=? AND account=?');
    $st->execute([$id,$account]);
    if(!$st->fetchColumn()) throw new RuntimeException('Conversation not found.');
}
function cms_ai_conversation_new(PDO $db,string $account,string $title): string {
    if(strlen($title)>160 || trim($title)==='') throw new InvalidArgumentException('Invalid title.');
    $id=cms_ai_id(); $now=cms_now();
    $db->beginTransaction();
    try{
        cms_ai_charge($db,$account,strlen($title));
        $db->prepare('INSERT INTO cms_ai_conversations(id,account,title,created_at,updated_at)
            VALUES(?,?,?,?,?)')->execute([$id,$account,$title,$now,$now]);
        $db->commit();
    }catch(Throwable $e){if($db->inTransaction())$db->rollBack();throw $e;}
    return $id;
}
function cms_ai_message_add(PDO $db,string $account,string $conversation,string $role,string $content): void {
    if(!in_array($role,['user','assistant'],true) || trim($content)==='' ||
       strlen($content)>CMS_AI_MAX_MESSAGE) throw new InvalidArgumentException('Invalid message.');
    cms_ai_require_conversation($db,$account,$conversation);
    $bytes=strlen($content);
    $db->beginTransaction();
    try {
        cms_ai_charge($db,$account,$bytes);
        $db->prepare('INSERT INTO cms_ai_messages(conversation_id,account,role,content,bytes,created_at)
          VALUES(?,?,?,?,?,?)')->execute([$conversation,$account,$role,$content,$bytes,cms_now()]);
        $db->prepare('UPDATE cms_ai_conversations SET updated_at=? WHERE id=? AND account=?')
          ->execute([cms_now(),$conversation,$account]);
        $db->commit();
    } catch(Throwable $e) {if($db->inTransaction())$db->rollBack();throw $e;}
}
function cms_ai_message_history(PDO $db,string $account,string $conversation,int $limit=20): array {
    cms_ai_require_conversation($db,$account,$conversation);
    $st=$db->prepare('SELECT role,content FROM (
        SELECT id,role,content FROM cms_ai_messages
        WHERE account=? AND conversation_id=? ORDER BY id DESC LIMIT ?)
        ORDER BY id ASC');
    $st->bindValue(1,$account);$st->bindValue(2,$conversation);
    $st->bindValue(3,max(1,min(100,$limit)),PDO::PARAM_INT);$st->execute();
    return $st->fetchAll(PDO::FETCH_ASSOC);
}
function cms_ai_conversation_delete(PDO $db,string $account,string $conversation): void {
    cms_ai_require_conversation($db,$account,$conversation);
    $db->beginTransaction();
    try {
        $title=$db->prepare('SELECT title FROM cms_ai_conversations WHERE id=? AND account=?');
        $title->execute([$conversation,$account]);$titleBytes=strlen((string)$title->fetchColumn());
        $sum=$db->prepare('SELECT COALESCE(SUM(bytes),0) FROM cms_ai_messages WHERE account=? AND conversation_id=?');
        $sum->execute([$account,$conversation]);
        $bytes=(int)$sum->fetchColumn();
        $db->prepare('DELETE FROM cms_ai_messages WHERE account=? AND conversation_id=?')
          ->execute([$account,$conversation]);
        $db->prepare('DELETE FROM cms_ai_conversations WHERE account=? AND id=?')
          ->execute([$account,$conversation]);
        $db->prepare('UPDATE cms_ai_memories SET conversation_id=NULL WHERE account=? AND conversation_id=?')
          ->execute([$account,$conversation]); // saved memories survive conversation deletion
        cms_ai_charge($db,$account,-($bytes+$titleBytes));
        $db->commit();
    } catch(Throwable $e) {if($db->inTransaction())$db->rollBack();throw $e;}
}
function cms_ai_memory_save(PDO $db,string $account,string $content,?string $conversation=null,string $source=''): string {
    if(trim($content)===''||strlen($content)>CMS_AI_MAX_MEMORY||strlen($source)>500 ||
       ($source!=='' && (!str_starts_with($source,'https://') ||
        filter_var($source,FILTER_VALIDATE_URL)===false)))
        throw new InvalidArgumentException('Invalid memory content or source.');
    if($conversation!==null)cms_ai_require_conversation($db,$account,$conversation);
    $id=cms_ai_id(); $bytes=strlen($content)+strlen($source);
    $db->beginTransaction();
    try {
        cms_ai_charge($db,$account,$bytes);
        $db->prepare('INSERT INTO cms_ai_memories
            (id,account,conversation_id,content,source_url,bytes,created_at)
            VALUES(?,?,?,?,?,?,?)')->execute([$id,$account,$conversation,$content,$source,$bytes,cms_now()]);
        $db->commit();
    } catch(Throwable $e) {if($db->inTransaction())$db->rollBack();throw $e;}
    return $id;
}
function cms_ai_memory_delete(PDO $db,string $account,string $id): void {
    if(!preg_match('/^[a-f0-9]{32}$/D',$id))throw new InvalidArgumentException('Invalid memory ID.');
    $db->beginTransaction();
    try {
        $st=$db->prepare('SELECT bytes FROM cms_ai_memories WHERE id=? AND account=?');
        $st->execute([$id,$account]);$bytes=$st->fetchColumn();
        if($bytes===false)throw new RuntimeException('Memory not found.');
        $db->prepare('DELETE FROM cms_ai_memories WHERE id=? AND account=?')->execute([$id,$account]);
        cms_ai_charge($db,$account,-(int)$bytes);
        $db->commit();
    } catch(Throwable $e) {if($db->inTransaction())$db->rollBack();throw $e;}
}
/** Owner-scoped full-text recall; falls back to bounded lexical search without FTS5. */
function cms_ai_recall(PDO $db,string $account,string $query,?string $currentConversation=null): string {
    $words=preg_split('/[^a-z0-9]+/i',strtolower($query),-1,PREG_SPLIT_NO_EMPTY);
    $words=array_values(array_unique(array_filter($words,static fn($w)=>strlen($w)>=4)));
    if(!$words)return '';
    $matches=[];
    $fts=(bool)$db->query("SELECT 1 FROM sqlite_master
        WHERE type='table' AND name='cms_ai_memory_fts'")->fetchColumn();
    if($fts){
        $match=implode(' OR ',array_map(static fn($w)=>'"'.$w.'"',$words));
        $st=$db->prepare('SELECT m.content,m.source_url,bm25(cms_ai_memory_fts) AS rank
           FROM cms_ai_memory_fts
           JOIN cms_ai_memories m ON m.rowid=cms_ai_memory_fts.rowid
           WHERE cms_ai_memory_fts MATCH ? AND m.account=?
           ORDER BY rank LIMIT 15');
        $st->execute([$match,$account]);
        foreach($st->fetchAll(PDO::FETCH_ASSOC) as $row)
            $matches[]=['score'=>100-(float)$row['rank'],
                'text'=>$row['content'],'source'=>$row['source_url']];
        $st=$db->prepare('SELECT m.content,bm25(cms_ai_message_fts) AS rank
           FROM cms_ai_message_fts
           JOIN cms_ai_messages m ON m.id=cms_ai_message_fts.rowid
           WHERE cms_ai_message_fts MATCH ? AND m.account=? AND m.conversation_id!=?
           ORDER BY rank LIMIT 15');
        $st->execute([$match,$account,$currentConversation??'']);
        foreach($st->fetchAll(PDO::FETCH_ASSOC) as $row)
            $matches[]=['score'=>50-(float)$row['rank'],
                'text'=>$row['content'],'source'=>'previous conversation'];
    }else{
        $st=$db->prepare('SELECT content,source_url FROM cms_ai_memories
           WHERE account=? ORDER BY created_at DESC LIMIT 250');
        $st->execute([$account]);
        foreach($st->fetchAll(PDO::FETCH_ASSOC) as $row){
            $score=0;$low=strtolower($row['content']);
            foreach($words as $w)if(str_contains($low,$w))$score++;
            if($score)$matches[]=['score'=>$score+10,
                'text'=>$row['content'],'source'=>$row['source_url']];
        }
        $st=$db->prepare("SELECT content FROM cms_ai_messages
            WHERE account=? AND conversation_id!=? ORDER BY id DESC LIMIT 250");
        $st->execute([$account,$currentConversation??'']);
        foreach($st->fetchAll(PDO::FETCH_COLUMN) as $body){
            $score=0;$low=strtolower($body);
            foreach($words as $w)if(str_contains($low,$w))$score++;
            if($score)$matches[]=['score'=>$score,'text'=>$body,'source'=>'previous conversation'];
        }
    }
    usort($matches,static fn($a,$b)=>$b['score']<=>$a['score']);
    $out='';foreach(array_slice($matches,0,6) as $row){
        $fragment=substr($row['text'],0,550);
        $line="\n[Saved context / ".($row['source']?:'user memory')."]\n".
             "[BEGIN UNTRUSTED DATA]\n".$fragment."\n[END UNTRUSTED DATA]\n";
        if(strlen($out)+strlen($line)>2800)break;
        $out.=$line;
    }
    return $out;
}
function cms_ai_allowed_profiles(PDO $db,array $identity): array {
    $enabled=$db->query('SELECT slug,title FROM cms_ai_profiles WHERE enabled=1 ORDER BY title')->fetchAll(PDO::FETCH_ASSOC);
    if($identity['role']==='admin')return $enabled;
    $public=$db->query('SELECT profile FROM cms_ai_category_public WHERE enabled=1')->fetchAll(PDO::FETCH_COLUMN);
    $allowed=array_fill_keys($public,true);
    if($identity['role']==='user') {
        $st=$db->prepare('SELECT profile FROM cms_ai_category_grants WHERE viewer_id=?');
        $st->execute([(int)substr($identity['account'],5)]);
        foreach($st->fetchAll(PDO::FETCH_COLUMN) as $p)$allowed[$p]=true;
    }
    return array_values(array_filter($enabled,static fn($row)=>isset($allowed[$row['slug']])));
}
function cms_ai_require_profile(PDO $db,array $identity,string $profile): void {
    foreach(cms_ai_allowed_profiles($db,$identity) as $entry)
        if($entry['slug']===$profile)return;
    throw new RuntimeException('Knowledge category not available to this account.');
}
function cms_ai_document_directory(): string {
    $path=cms_private_directory().'/ai-document-staging';
    if(is_link($path))throw new RuntimeException('Unsafe document staging path.');
    if(!is_dir($path) && !mkdir($path,0700) && !is_dir($path))
        throw new RuntimeException('Could not initialize private document staging.');
    if(is_link($path))throw new RuntimeException('Unsafe document staging path.');
    return $path;
}
/** Original upload is kept only until verified parsing succeeds. Exact duplicates never restage. */
function cms_ai_document_enqueue(PDO $db,array $identity,array $file,string $profile): array {
    $account=cms_ai_actor($identity);
    cms_ai_require_profile($db,$identity,$profile);
    $name=cms_ai_pdf_filename($file['name']??null);
    $tmp=$file['tmp_name']??null;
    if(($file['error']??-1)!==UPLOAD_ERR_OK || !cms_ai_pdf_size_valid($file['size']??null) ||
       !is_string($tmp)||!is_uploaded_file($tmp)||!cms_ai_pdf_header($tmp))
        throw new InvalidArgumentException('Select a valid PDF (maximum 200 MiB).');
    if(!in_array((new finfo(FILEINFO_MIME_TYPE))->file($tmp),['application/pdf','application/octet-stream'],true))
        throw new InvalidArgumentException('Uploaded file is not PDF.');
    $sha=hash_file('sha256',$tmp);
    $st=$db->prepare('SELECT id,status FROM cms_ai_document_objects WHERE sha256=?');
    $st->execute([$sha]);$existing=$st->fetch(PDO::FETCH_ASSOC);
    if($existing) {
        $db->prepare('INSERT OR IGNORE INTO cms_ai_document_access(object_id,account,profile,title,created_at)
            VALUES(?,?,?,?,?)')->execute([(int)$existing['id'],$account,$profile,$name,cms_now()]);
        // PHP removes the untouched upload from its temporary directory after request.
        return ['id'=>(int)$existing['id'],'status'=>$existing['status'],'duplicate'=>true];
    }
    $st=$db->query("SELECT COALESCE(SUM(bytes),0) FROM cms_ai_document_objects");
    $reserved=(int)$st->fetchColumn();
    $bytes=(int)$file['size'];
    // This is the separate DOCUMENT quota, never a charge against 2 GiB chat memory.
    // Bound public PDF ingestion on the existing 60 GiB Ubuntu gateway until
    // dedicated document storage is attached and its capacity is verified.
    $documentCap=8*1024*1024*1024;
    if($reserved+$bytes>$documentCap)
        throw new RuntimeException('Shared document capacity reached; contact site administrator.');
    $privateDir=cms_ai_document_directory();
    $available=disk_free_space($privateDir);
    if($available===false || $available-$bytes<5*1024*1024*1024)
        throw new RuntimeException('Document upload temporarily disabled: insufficient free storage.');
    $key=bin2hex(random_bytes(20)).'.pdf';$destination=$privateDir.'/'.$key;
    if(!move_uploaded_file($tmp,$destination))throw new RuntimeException('Could not stage uploaded PDF.');
    chmod($destination,0600);
    try {
        $db->beginTransaction();
        $db->prepare("INSERT OR IGNORE INTO cms_ai_document_objects
            (sha256,bytes,profile,status,staging_key,created_at)
            VALUES(?,?,?,'queued',?,?)")
            ->execute([$sha,(int)$file['size'],$profile,$key,cms_now()]);
        $fresh=$db->prepare('SELECT id,status,staging_key FROM cms_ai_document_objects WHERE sha256=?');
        $fresh->execute([$sha]);$row=$fresh->fetch(PDO::FETCH_ASSOC);
        $db->prepare('INSERT OR IGNORE INTO cms_ai_document_access(object_id,account,profile,title,created_at)
            VALUES(?,?,?,?,?)')->execute([(int)$row['id'],$account,$profile,$name,cms_now()]);
        $db->commit();
        if($row['staging_key']!==$key)unlink($destination); // race: another upload won
        return ['id'=>(int)$row['id'],'status'=>$row['status'],
                'duplicate'=>$row['staging_key']!==$key];
    } catch(Throwable $e) {
        if($db->inTransaction())$db->rollBack();
        if(is_file($destination))unlink($destination);
        throw $e;
    }
}
function cms_ai_account_documents(PDO $db,string $account): array {
    $st=$db->prepare('SELECT o.id,o.sha256,a.profile,o.status,o.pages,o.total_pages,o.processed_pages,o.error,a.title
        FROM cms_ai_document_access a JOIN cms_ai_document_objects o ON o.id=a.object_id
        WHERE a.account=? ORDER BY a.created_at DESC LIMIT 200');
    $st->execute([$account]);return $st->fetchAll(PDO::FETCH_ASSOC);
}
