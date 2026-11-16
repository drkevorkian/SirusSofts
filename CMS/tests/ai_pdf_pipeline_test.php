<?php
declare(strict_types=1);
if(PHP_SAPI!=='cli'){http_response_code(404);exit;}
define('SIRUS_CMS_BOOTSTRAPPED',true);
require dirname(__DIR__).'/app/core.php';
// Regression for the live systemd worker's original duplicate-include crash.
$workerCode=file_get_contents(dirname(__DIR__).'/bin/ai-staged-pdf-worker.php');
if(!is_string($workerCode)||!str_contains($workerCode,"require_once dirname(__DIR__).'/app/ai_pdf_pipeline.php';")){
    fwrite(STDERR,"FAIL: PDF worker must require_once the pipeline (already loaded by core.php).\\n");
    exit(1);
}
require_once dirname(__DIR__).'/app/ai_pdf_pipeline.php';

function verify_pdf(bool $value,string $reason):void{
    if(!$value){fwrite(STDERR,"FAIL: ".$reason."\n");exit(1);}
}
$db=new PDO('sqlite::memory:',null,null,
    [PDO::ATTR_ERRMODE=>PDO::ERRMODE_EXCEPTION,PDO::ATTR_DEFAULT_FETCH_MODE=>PDO::FETCH_ASSOC]);
$db->exec('PRAGMA foreign_keys=ON');
cms_schema($db);
$db->prepare("INSERT INTO cms_ai_pdf_uploads
    (profile,title,original_name,file_key,sha256,bytes,status,created_at)
    VALUES('mechanics','Engine manual','Engine.pdf',?,? ,?, 'staged',?)")
    ->execute([str_repeat('a',40).'.pdf',str_repeat('b',64),350*1024*1024,cms_now()]);
$one=(int)$db->lastInsertId();
cms_ai_pdf_pipeline_schema($db);
cms_ai_pdf_pipeline_schema($db);
verify_pdf((int)$db->query('SELECT COUNT(*) FROM cms_ai_pdf_pipeline')->fetchColumn()===1,
    'Existing staged PDF is queued exactly once');
$claim=cms_ai_pdf_pipeline_claim($db);
verify_pdf((int)$claim['id']===$one,'Old staged PDF claimed');
verify_pdf(cms_ai_pdf_pipeline_claim($db)===null,'Claim cannot duplicate active job');
$batch=cms_ai_pdf_pipeline_commit_text($db,$claim,
    "First page: torque 42 Nm.\fSecond page: remove drive belt.");
verify_pdf($batch>0,'Automatic extraction makes a manual batch');
$meta=$db->query("SELECT j.status,j.pages,j.excerpts,p.status AS upload_status
    FROM cms_ai_pdf_pipeline j JOIN cms_ai_pdf_uploads p ON p.id=j.pdf_id")->fetch();
verify_pdf($meta['status']==='ready'&&$meta['upload_status']==='processed'&&
    (int)$meta['pages']===2&&(int)$meta['excerpts']===2,
    'Extraction records pages/excerpts and preserves original as processed');
verify_pdf((int)$db->query("SELECT COUNT(*) FROM cms_ai_documents WHERE reviewed=0")->fetchColumn()===2,
    'No auto-approval');
verify_pdf(count(iterator_to_array(cms_ai_export_records($db,'knowledge')))===0,
    'Knowledge export refuses staged-only extracts');
$db->prepare("UPDATE cms_ai_documents SET reviewed=1 WHERE id IN
    (SELECT document_id FROM cms_ai_manual_batch_docs WHERE batch_id=?)")->execute([$batch]);
$db->prepare("UPDATE cms_ai_manual_batches SET approved=1 WHERE id=?")->execute([$batch]);
verify_pdf(count(iterator_to_array(cms_ai_export_records($db,'knowledge')))===2,
    'Administrator approval allows reviewed knowledge export');
verify_pdf(cms_ai_pdf_pipeline_label(['batch_approved'=>1,'job_status'=>'ready'])==='Approved',
    'Approved pipeline visible in UI');
cms_ai_pdf_pipeline_schema($db);
verify_pdf(cms_ai_pdf_pipeline_claim($db)===null,'Approved PDFs never restage');
$db->prepare("INSERT INTO cms_ai_pdf_uploads
    (profile,title,original_name,file_key,sha256,bytes,status,created_at)
    VALUES('mechanics','No text','Image.pdf',?,? ,?, 'staged',?)")
    ->execute([str_repeat('c',40).'.pdf',str_repeat('d',64),22,cms_now()]);
$two=(int)$db->lastInsertId();
$claim2=cms_ai_pdf_pipeline_claim($db);
verify_pdf((int)$claim2['id']===$two,'Second uploaded PDF queued');
cms_ai_pdf_pipeline_fail($db,$two,'No extractable text');
verify_pdf(cms_ai_pdf_pipeline_label(['job_status'=>'failed'])==='Failed — retry available',
    'Failed status is visible');
cms_ai_pdf_pipeline_retry($db,$two);
$claim3=cms_ai_pdf_pipeline_claim($db);
verify_pdf((int)$claim3['id']===$two,'Failed PDF may be retried');
cms_ai_pdf_pipeline_fail($db,$two,'No extractable text');
echo "PASS: in-place PDF queue, idempotence, unreviewed extraction, approval, retries and 350MiB uploads.\n";
