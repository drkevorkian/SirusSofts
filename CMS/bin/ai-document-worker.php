<?php
declare(strict_types=1);
/** Private batch parser. Run only as unprivileged CMS site user, never via HTTP. */
if(PHP_SAPI!=='cli'){http_response_code(404);exit;}
define('SIRUS_CMS_BOOTSTRAPPED',true);
require dirname(__DIR__).'/app/core.php';
require dirname(__DIR__).'/app/ai_workspace.php';
function cms_ai_ingest_one(PDO $db):bool {
    cms_ai_workspace_schema($db);
    $now=time();$db->beginTransaction();
    try {
        $db->prepare("UPDATE cms_ai_document_objects SET status='queued',claimed_at=0
           WHERE status='processing' AND claimed_at < ?")
           ->execute([$now-3600]); // interrupted job can safely resume
        $row=$db->query("SELECT id,sha256,staging_key FROM cms_ai_document_objects
           WHERE status='queued' ORDER BY id LIMIT 1")->fetch(PDO::FETCH_ASSOC);
        if(!$row){$db->commit();return false;}
        $db->prepare("UPDATE cms_ai_document_objects
           SET status='processing',claimed_at=? WHERE id=?")->execute([$now,$row['id']]);
        $db->commit();
    } catch(Throwable $e){if($db->inTransaction())$db->rollBack();throw $e;}
    $id=(int)$row['id'];$key=(string)$row['staging_key'];
    $tmp=null;
    try {
        if(!preg_match('/^[a-f0-9]{40}\.pdf$/D',$key))
            throw new RuntimeException('Invalid staged document key.');
        $source=cms_ai_document_directory().'/'.$key;
        if(is_link($source)||!is_file($source)||!hash_equals($row['sha256'],hash_file('sha256',$source)))
            throw new RuntimeException('Staged document missing or hash mismatch.');
        $binary='/usr/bin/pdftotext';
        $timeout='/usr/bin/timeout';
        if(!is_executable($binary)||!is_executable($timeout))
            throw new RuntimeException('Local Poppler and timeout commands are required.');
        $tmp=tempnam(cms_ai_document_directory(),'extract-');
        if($tmp===false)throw new RuntimeException('Could not allocate temporary extraction.');
        chmod($tmp,0600);
        // Fixed executable/flags, argv array, never shell interpolation or user commands.
        $argv=[$timeout,'150s',$binary,'-layout','-enc','UTF-8',$source,$tmp];
        $streams=[0=>['pipe','r'],1=>['pipe','w'],2=>['pipe','w']];
        $proc=proc_open($argv,$streams,$pipes,null,['LC_ALL'=>'C','PATH'=>'/usr/bin:/bin'],['bypass_shell'=>true]);
        if(!is_resource($proc))throw new RuntimeException('PDF parser failed to start.');
        fclose($pipes[0]);
        $stdout=stream_get_contents($pipes[1],8192);fclose($pipes[1]);
        $stderr=stream_get_contents($pipes[2],8192);fclose($pipes[2]);
        $exit=proc_close($proc);
        if($exit!==0)throw new RuntimeException('PDF text extraction failed or timed out.');
        if(!is_file($tmp)||filesize($tmp)>32*1024*1024)
            throw new RuntimeException('Extracted text exceeds 32 MiB limit.');
        $raw=file_get_contents($tmp);
        if(!is_string($raw)||$raw==='')throw new RuntimeException('PDF contains no extractable text.');
        if(!mb_check_encoding($raw,'UTF-8'))$raw=mb_convert_encoding($raw,'UTF-8','UTF-8');
        $pages=explode("\f",$raw);
        if(count($pages)>5001)throw new RuntimeException('Document exceeds 5000 pages.');
        $db->beginTransaction();
        try{
            $db->prepare('DELETE FROM cms_ai_document_chunks WHERE object_id=?')->execute([$id]);
            $insert=$db->prepare('INSERT INTO cms_ai_document_chunks(object_id,page,part,body) VALUES(?,?,?,?)');
            $pageCount=0;$nonempty=0;
            foreach($pages as $index=>$page){
                $page=trim($page);
                if($page==='')continue;
                if(strlen($page)>400000)throw new RuntimeException('Individual PDF page is too large.');
                $pageCount=max($pageCount,$index+1);
                foreach(str_split($page,8000) as $part=>$chunk) {
                    $insert->execute([$id,$index+1,$part+1,$chunk]);$nonempty++;
                }
            }
            if($nonempty<1)throw new RuntimeException('PDF has no readable page text; manual review/OCR needed.');
            $db->prepare("UPDATE cms_ai_document_objects SET
                status='parsed',pages=?,error='',staging_key='',completed_at=?
                WHERE id=? AND status='processing'")->execute([$pageCount,cms_now(),$id]);
            $db->commit();
        }catch(Throwable $e){if($db->inTransaction())$db->rollBack();throw $e;}
        if(is_file($source))unlink($source); // only after complete DB commit
        return true;
    }catch(Throwable $e){
        if($db->inTransaction())$db->rollBack();
        $db->prepare("UPDATE cms_ai_document_objects
            SET status='failed',error=? WHERE id=? AND status='processing'")
            ->execute([substr($e->getMessage(),0,240),$id]);
        throw $e;
    }finally{if($tmp!==null&&is_file($tmp))unlink($tmp);}
}
try {
    $db=cms_db();
    $found=cms_ai_ingest_one($db);
    echo $found?"PASS: Document parsed, indexed and temporary upload removed.\n":
                "PASS: No documents waiting for processing.\n";
} catch(Throwable $e){
    error_log('Sirus AI document processing failed: '.get_class($e));
    fwrite(STDERR,"FAIL: Document processing failed; inspect private job status.\n");
    exit(1);
}
