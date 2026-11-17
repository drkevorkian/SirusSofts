<?php
declare(strict_types=1);
if(!defined('SIRUS_CMS_BOOTSTRAPPED')){http_response_code(404);exit;}

/**
 * Conversation titles are private, owner-controlled CMS metadata.
 * These routines make no inference/network calls and never inspect
 * assistant messages, uploaded PDFs, or retrieved manual instructions.
 */
function cms_ai_title_validate(string $title):string {
    $title=trim($title);
    if($title===''||strlen($title)>160||preg_match('//u',$title)!==1||
       preg_match('/[\x00-\x1f\x7f]/u',$title))
        throw new InvalidArgumentException('Conversation title must be 1–160 UTF-8 bytes on one line.');
    return $title;
}

/** User may begin a message with "Name this conversation: 2026 HD10 Wiring". */
function cms_ai_explicit_title(string $message):?string {
    $line=trim(explode("\n",str_replace("\r\n","\n",$message),2)[0]);
    if(!preg_match('/^(?:(?:please\s+)?(?:name|call|title)\s+(?:this\s+)?(?:conversation|chat|thread)|set\s+(?:the\s+)?(?:conversation|chat|thread)\s+(?:name|title))\s*(?:(?:to|as)\b\s*|[:=]\s*)?(.+)$/iu',
                   $line,$match))return null;
    $name=trim($match[1]," \t\"'“”‘’");
    if($name==='')return null;
    return cms_ai_title_validate($name);
}

/** Short, deterministic default title using the first substantive user line. */
function cms_ai_topic_title(string $message):?string {
    $explicit=cms_ai_explicit_title($message);
    if($explicit!==null)return $explicit;
    $line=trim(preg_split('/[\r\n]+/u',$message,2)[0]??'');
    if($line===''||preg_match('/^(?:hello|hi|hey|thanks|thank you|ok|okay)[.!? ]*$/iu',$line))
        return null;
    $line=preg_replace('/\s+/u',' ',$line);
    if(!is_string($line))return null;
    for($i=0;$i<3;$i++){
        $next=preg_replace('/^(?:(?:can|could|would)\s+you\s+|i\s+(?:want|need)\s+(?:you\s+)?(?:to\s+)?|please\s+|help\s+me\s+|show\s+me\s+|find\s+me\s+|tell\s+me\s+|what\s+(?:is|are)\s+|how\s+(?:do|can)\s+i\s+|in\s+the\s+)/iu','',$line,1);
        if(!is_string($next)||$next===$line)break;
        $line=trim($next);
    }
    $line=trim($line," \t\n\r\0\x0B\"'“”‘’?!.,:;#");
    $parts=preg_split('/\s+/u',$line);
    if(!is_array($parts)||!count($parts))return null;
    $result=implode(' ',array_slice($parts,0,9));
    if(strlen($result)>110)$result=mb_strcut($result,0,110,'UTF-8');
    $result=trim($result," \t\n\r\0\x0B?!.,:;#-");
    if($result==='')return null;
    $result=preg_replace_callback('/\b[a-z][a-z0-9_-]*\b/u',
        static fn(array $m)=>ucfirst($m[0]),$result);
    $result=preg_replace_callback('/\b(?:hd[-_]?\d{1,2}|pdf|brp|llm|gpu|ai|ssh)\b/iu',
        static fn(array $m)=>strtoupper($m[0]),$result);
    return cms_ai_title_validate($result);
}

/** Must be called inside a write transaction. Includes the 2 GiB quota delta. */
function cms_ai_title_write(PDO $db,string $account,string $id,
                            string $old,string $title,bool $locked,
                            bool $moveToTop):void {
    $title=cms_ai_title_validate($title);
    $delta=strlen($title)-strlen($old);
    if($delta!==0)cms_ai_charge($db,$account,$delta);
    $sql='UPDATE cms_ai_conversations SET title=?,title_locked=?';
    if($moveToTop)$sql.=',updated_at=?';
    $sql.=' WHERE id=? AND account=?';
    $args=[$title,$locked?1:0];
    if($moveToTop)$args[]=cms_now();
    $args[]=$id;$args[]=$account;
    $db->prepare($sql)->execute($args);
}

/** Explicit manual rename is always permitted for the conversation owner. */
function cms_ai_conversation_rename(PDO $db,string $account,
                                    string $id,string $title):string {
    $title=cms_ai_title_validate($title);
    cms_ai_require_conversation($db,$account,$id);
    $db->beginTransaction();
    try {
        $st=$db->prepare('SELECT title FROM cms_ai_conversations WHERE id=? AND account=?');
        $st->execute([$id,$account]);
        $old=$st->fetchColumn();
        if(!is_string($old))throw new RuntimeException('Conversation not found.');
        cms_ai_title_write($db,$account,$id,$old,$title,true,true);
        $db->commit();
    }catch(Throwable $e){if($db->inTransaction())$db->rollBack();throw $e;}
    return $title;
}

/** One-time upgrade backfill. Never touch manually named conversations. */
function cms_ai_backfill_conversation_titles(PDO $db,int $max=5000):int {
    $st=$db->prepare("SELECT c.id,c.account FROM cms_ai_conversations AS c
       WHERE c.title='New conversation' AND c.title_locked=0 AND c.deleted_at IS NULL
       AND EXISTS(SELECT 1 FROM cms_ai_messages AS m
                  WHERE m.account=c.account AND m.conversation_id=c.id
                    AND m.role='user')
       ORDER BY c.updated_at DESC LIMIT ?");
    $st->bindValue(1,max(1,min(10000,$max)),PDO::PARAM_INT);$st->execute();
    $updated=0;
    foreach($st->fetchAll(PDO::FETCH_ASSOC) as $row){
        $msg=$db->prepare("SELECT content FROM cms_ai_messages
                 WHERE account=? AND conversation_id=? AND role='user'
                 ORDER BY id ASC LIMIT 12");
        $msg->execute([$row['account'],$row['id']]);
        $title=null;
        foreach($msg->fetchAll(PDO::FETCH_COLUMN) as $message){
            $title=cms_ai_topic_title($message);
            if($title!==null)break;
        }
        if($title===null||$title==='New conversation')continue;
        $db->beginTransaction();
        try {
            $check=$db->prepare("SELECT title FROM cms_ai_conversations
                                 WHERE id=? AND account=? AND title_locked=0");
            $check->execute([$row['id'],$row['account']]);
            if($check->fetchColumn()==='New conversation'){
                cms_ai_title_write($db,$row['account'],$row['id'],
                                   'New conversation',$title,false,false);
                $updated++;
            }
            $db->commit();
        }catch(Throwable $e){
            if($db->inTransaction())$db->rollBack();
            // An account near its quota cannot block an otherwise safe
            // CMS upgrade or another account's title backfill.
            if(!($e instanceof RuntimeException &&
                 str_contains($e->getMessage(),'quota')))throw $e;
        }
    }
    return $updated;
}
