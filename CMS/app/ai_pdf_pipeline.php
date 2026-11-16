<?php
declare(strict_types=1);
if(!defined('SIRUS_CMS_BOOTSTRAPPED')){http_response_code(404);exit;}

/** In-place, admin-only PDF extraction queue. Uploaded originals remain private. */
function cms_ai_pdf_pipeline_schema(PDO $db):void {
    $db->exec("CREATE TABLE IF NOT EXISTS cms_ai_pdf_pipeline (
        pdf_id INTEGER PRIMARY KEY REFERENCES cms_ai_pdf_uploads(id) ON DELETE CASCADE,
        status TEXT NOT NULL CHECK(status IN ('queued','processing','ready','failed')),
        batch_id INTEGER REFERENCES cms_ai_manual_batches(id) ON DELETE SET NULL,
        pages INTEGER NOT NULL DEFAULT 0,
        excerpts INTEGER NOT NULL DEFAULT 0,
        error TEXT NOT NULL DEFAULT '',
        claimed_at INTEGER NOT NULL DEFAULT 0,
        updated_at TEXT NOT NULL
    )");
    // The migration picks up PDFs uploaded BEFORE this functionality existed.
    $st=$db->prepare("INSERT OR IGNORE INTO cms_ai_pdf_pipeline(pdf_id,status,updated_at)
        SELECT id,'queued',? FROM cms_ai_pdf_uploads WHERE status='staged'");
    $st->execute([cms_now()]);
}
function cms_ai_pdf_pipeline_rows(PDO $db):array {
    cms_ai_pdf_pipeline_schema($db);
    return $db->query("SELECT p.id,p.original_name,p.profile,p.bytes,p.status AS upload_status,
            j.status AS job_status,j.pages,j.excerpts,j.error,j.batch_id,
            b.approved AS batch_approved
        FROM cms_ai_pdf_uploads p
        LEFT JOIN cms_ai_pdf_pipeline j ON j.pdf_id=p.id
        LEFT JOIN cms_ai_manual_batches b ON b.id=j.batch_id
        ORDER BY p.id DESC LIMIT 100")->fetchAll(PDO::FETCH_ASSOC);
}
function cms_ai_pdf_pipeline_label(array $row):string {
    if((int)($row['batch_approved']??0)===1)return 'Approved';
    return match($row['job_status']??null) {
        null,'queued' => ($row['upload_status']??'staged')==='staged'?'Queued for extraction':'Previously processed',
        'processing' => 'Extracting',
        'ready' => 'Ready for review',
        'failed' => 'Failed — retry available',
        default => 'Unknown'
    };
}
function cms_ai_pdf_pipeline_retry(PDO $db,int $id):void {
    $st=$db->prepare("UPDATE cms_ai_pdf_pipeline SET status='queued',error='',claimed_at=0,
        updated_at=? WHERE pdf_id=? AND status='failed'");
    $st->execute([cms_now(),$id]);
    if($st->rowCount()!==1)throw new InvalidArgumentException('PDF is not in failed state.');
}
function cms_ai_pdf_pipeline_claim(PDO $db):?array {
    cms_ai_pdf_pipeline_schema($db);
    $db->beginTransaction();
    try {
        // Recover an interrupted parser after the systemd timeout/host restart.
        $db->prepare("UPDATE cms_ai_pdf_pipeline SET status='queued',claimed_at=0
            WHERE status='processing' AND claimed_at<?")->execute([time()-900]);
        $row=$db->query("SELECT p.id,p.original_name,p.file_key,p.sha256,p.profile,p.bytes
            FROM cms_ai_pdf_uploads p JOIN cms_ai_pdf_pipeline j ON j.pdf_id=p.id
            WHERE j.status='queued' AND p.status='staged'
            ORDER BY p.id LIMIT 1")->fetch(PDO::FETCH_ASSOC);
        if($row){
            $st=$db->prepare("UPDATE cms_ai_pdf_pipeline SET status='processing',claimed_at=?,
                updated_at=? WHERE pdf_id=? AND status='queued'");
            $st->execute([time(),cms_now(),$row['id']]);
            if($st->rowCount()!==1)throw new RuntimeException('PDF queue claim conflicted.');
        }
        $db->commit();
        return $row?:null;
    }catch(Throwable $e){
        if($db->inTransaction())$db->rollBack();
        throw $e;
    }
}
function cms_ai_pdf_pipeline_fail(PDO $db,int $id,string $reason):void {
    $db->prepare("UPDATE cms_ai_pdf_pipeline SET status='failed',error=?,
        claimed_at=0,updated_at=? WHERE pdf_id=? AND status='processing'")
        ->execute([substr($reason,0,240),cms_now(),$id]);
}
/** Convert already-extracted UTF-8 text to an unreviewed batch atomically. */
function cms_ai_pdf_pipeline_commit_text(PDO $db,array $pdf,string $raw):int {
    if($raw===''||strlen($raw)>64*1024*1024)
        throw new RuntimeException('Extracted text is empty or exceeds 64 MiB.');
    if(!mb_check_encoding($raw,'UTF-8'))$raw=mb_convert_encoding($raw,'UTF-8','UTF-8');
    $pages=explode("\f",$raw);
    if($pages&&trim((string)end($pages))==='')array_pop($pages);
    $total=count($pages);
    if($total<1||$total>10000)throw new RuntimeException('PDF page count is unsupported.');
    $count=0;
    $db->beginTransaction();
    try {
        $st=$db->prepare("SELECT status FROM cms_ai_pdf_pipeline WHERE pdf_id=?");
        $st->execute([(int)$pdf['id']]);
        if($st->fetchColumn()!=='processing')throw new RuntimeException('PDF is no longer claimed.');
        $db->prepare('INSERT INTO cms_ai_manual_batches(title,document_count,created_at)
            VALUES(?,0,?)')->execute([substr((string)$pdf['original_name'],0,160),cms_now()]);
        $batch=(int)$db->lastInsertId();
        $doc=$db->prepare('INSERT INTO cms_ai_documents
            (profile,title,source_url,content,reviewed,created_at)
            VALUES(?,?,?,?,0,?)');
        $link=$db->prepare('INSERT INTO cms_ai_manual_batch_docs(batch_id,document_id) VALUES(?,?)');
        foreach($pages as $n=>$page) {
            $page=trim($page);
            $offset=0;$part=0;
            while($offset<strlen($page)){
                // Byte-limited, UTF-8-safe excerpts fit the existing 60 kB record bound.
                $chunk=mb_strcut($page,$offset,4800,'UTF-8');
                if($chunk==='')throw new RuntimeException('Unprocessable PDF text.');
                $offset+=strlen($chunk);
                $chunk=trim($chunk);
                if($chunk==='')continue;
                if(++$count>20000)throw new RuntimeException('Too many excerpts; split the manual.');
                $title=substr((string)$pdf['original_name'],0,100).' | page '.($n+1).' | part '.(++$part);
                $doc->execute([(string)$pdf['profile'],$title,'',$chunk,cms_now()]);
                $link->execute([$batch,(int)$db->lastInsertId()]);
            }
        }
        if(!$count)throw new RuntimeException('No selectable text; PDF may need OCR.');
        $db->prepare('UPDATE cms_ai_manual_batches SET document_count=? WHERE id=?')
            ->execute([$count,$batch]);
        $st=$db->prepare("UPDATE cms_ai_pdf_pipeline SET status='ready',batch_id=?,
            pages=?,excerpts=?,error='',claimed_at=0,updated_at=?
            WHERE pdf_id=? AND status='processing'");
        $st->execute([$batch,$total,$count,cms_now(),(int)$pdf['id']]);
        if($st->rowCount()!==1)throw new RuntimeException('PDF status changed during extraction.');
        $db->prepare("UPDATE cms_ai_pdf_uploads SET status='processed' WHERE id=?")
            ->execute([(int)$pdf['id']]);
        $db->commit();
        return $batch;
    }catch(Throwable $e){
        if($db->inTransaction())$db->rollBack();
        throw $e;
    }
}
