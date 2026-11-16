<?php
declare(strict_types=1);
/** Sandboxed systemd-only worker. No PDF parsing in HTTP/PHP-FPM. */
if(PHP_SAPI!=='cli'){http_response_code(404);exit;}
define('SIRUS_CMS_BOOTSTRAPPED',true);
require dirname(__DIR__).'/app/core.php';
require dirname(__DIR__).'/app/ai_pdf_pipeline.php';
if(get_current_user()==='root' || (function_exists('posix_geteuid')&&posix_geteuid()===0))
    throw new RuntimeException('Refusing to parse PDFs as root.');
$db=cms_db();
$row=cms_ai_pdf_pipeline_claim($db);
if(!$row){echo "PASS: No staged PDFs waiting for extraction.\n";exit(0);}
$err=null;$tmp=null;
try {
    $folder=cms_ai_pdf_directory();
    $key=(string)$row['file_key'];
    if(!preg_match('/^[0-9a-f]{40}\.pdf$/D',$key))
        throw new RuntimeException('Invalid stored PDF key.');
    $source=$folder.'/'.$key;
    if(is_link($source)||!is_file($source)||!cms_ai_pdf_header($source)
       ||(int)filesize($source)!==(int)$row['bytes']
       ||!hash_equals((string)$row['sha256'],hash_file('sha256',$source)))
        throw new RuntimeException('Stored PDF failed integrity verification.');
    if(!is_executable('/usr/bin/pdftotext')||!is_executable('/usr/bin/timeout'))
        throw new RuntimeException('Poppler PDF extraction tools unavailable.');
    $tmp=tempnam($folder,'.sirus-extract-');
    $err=tempnam($folder,'.sirus-error-');
    if($tmp===false||$err===false)
        throw new RuntimeException('Cannot allocate private extraction temporary files.');
    chmod($tmp,0600);chmod($err,0600);
    $argv=['/usr/bin/timeout','-k','5s','240s','/usr/bin/pdftotext',
        '-layout','-enc','UTF-8',$source,$tmp];
    $fds=[0=>['file','/dev/null','r'],1=>['file','/dev/null','w'],2=>['file',$err,'w']];
    $proc=proc_open($argv,$fds,$pipes,null,
        ['LC_ALL'=>'C','PATH'=>'/usr/bin:/bin'],['bypass_shell'=>true]);
    if(!is_resource($proc))throw new RuntimeException('PDF parser could not start.');
    $code=proc_close($proc);
    if($code!==0)throw new RuntimeException('PDF text extraction failed or timed out; try again or review source PDF.');
    if(!is_file($tmp)||filesize($tmp)>64*1024*1024)
        throw new RuntimeException('Extracted text exceeds 64 MiB; split the document.');
    $raw=file_get_contents($tmp);
    if(!is_string($raw)||trim($raw)==='')
        throw new RuntimeException('No selectable PDF text; scanned pages may need OCR.');
    $batch=cms_ai_pdf_pipeline_commit_text($db,$row,$raw);
    echo "PASS: Extracted PDF #".(int)$row['id']." into unreviewed batch #$batch.\n";
} catch(Throwable $e) {
    cms_ai_pdf_pipeline_fail($db,(int)$row['id'],$e->getMessage());
    fwrite(STDERR,"FAIL: PDF #".(int)$row['id']." requires operator review; details in CMS.\n");
    exit(1);
} finally {
    foreach([$tmp,$err] as $file)if(is_string($file)&&is_file($file))unlink($file);
}
