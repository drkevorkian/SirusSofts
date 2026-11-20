<?php
declare(strict_types=1);
define('SIRUS_CMS_BOOTSTRAPPED',true);
require dirname(__DIR__).'/app/core.php';
require_once dirname(__DIR__).'/app/ai_continuous.php';

function evidence_check(bool $ok,string $message):void {
    if(!$ok)throw new RuntimeException('FAIL: '.$message);
}
$reviewer='cms-admin:12';
$id=str_repeat('a',64);
$source='pdf:'.$id;
$base=[
    'operation'=>'review',
    'evidence_id'=>$id,
    'quote_sha'=>str_repeat('b',64),
    'revision'=>str_repeat('c',64),
    'review_action'=>'support',
    'confirmation'=>'I VERIFIED ORIGINAL',
    'reason'=>'Compared with original approved PDF page.'
];
$p=cms_ai_evidence_payload($base,$reviewer);
evidence_check($p['action']==='review'&&$p['reviewer']===$reviewer,
    'Human actor bound to authenticated administrative session');
evidence_check($p['review_action']==='support'&&
    !isset($p['confirmation'])&&$p['local_only']===true,
    'Signed review request is fixed and local-only');
evidence_check(cms_ai_evidence_payload([
    'operation'=>'scope','source_id'=>$source,
    'applicability'=>'brp/defender/2026/hd11/clutch'
],$reviewer)['applicability']==='brp/defender/2026/hd11/clutch',
    'Explicit exact applicability accepted');
evidence_check(cms_ai_evidence_payload([
    'operation'=>'scope','source_id'=>$source,'applicability'=>''
],$reviewer)['applicability']==='','Comparison scopes can be cleared');
evidence_check(cms_ai_evidence_payload([
    'operation'=>'index'
],$reviewer)['action']==='index','Bounded private index operation accepted');
evidence_check(cms_ai_evidence_payload([
    'operation'=>'dismiss','evidence_id'=>$id
],$reviewer)['reviewer']===$reviewer,'Human conflict dismissal');

foreach([
    ['operation'=>'shell','cmd'=>'id'],
    ['operation'=>'scope','source_id'=>'../etc/passwd','applicability'=>'brp/a/2026'],
    ['operation'=>'scope','source_id'=>$source,'applicability'=>'http://private'],
    ['operation'=>'scope','source_id'=>$source,'applicability'=>'brp//2026'],
    ['operation'=>'scope','source_id'=>$source,'applicability'=>'A/B/2026'],
    ['operation'=>'review','evidence_id'=>$id,'quote_sha'=>str_repeat('b',64),
      'revision'=>str_repeat('c',64),'review_action'=>'support'],
    array_replace($base,['confirmation'=>'I DID NOT CHECK']),
    array_replace($base,['evidence_id'=>'missing']),
    array_replace($base,['review_action'=>'auto_promote']),
    array_replace($base,['revision'=>str_repeat('e',63)]),
    array_replace($base,['reason'=>str_repeat('x',201)]),
] as $bad){
    try{
        cms_ai_evidence_payload($bad,$reviewer);
        throw new LogicException('Unvalidated evidence mutation accepted');
    }catch(InvalidArgumentException $e){
        evidence_check(true,'Unsafe input blocked');
    }
}
try{
    cms_ai_evidence_payload($base,'model:auto');
    throw new LogicException('Model actor may impersonate human');
}catch(InvalidArgumentException $e){}
$_SESSION=[];
$db=new PDO('sqlite::memory:',null,null,[PDO::ATTR_ERRMODE=>PDO::ERRMODE_EXCEPTION]);
try{
    cms_ai_evidence_post($db,$base);
    throw new LogicException('Unauthenticated evidence review accepted');
}catch(RuntimeException $e){
    evidence_check(str_contains($e->getMessage(),'Sign in required'),
        'Only CMS admin session can issue review actions');
}
$_POST=[];$_SESSION=['cms_admin_id'=>12,'cms_csrf'=>'session-proof'];
try{
    cms_check_csrf();
    throw new LogicException('Missing CSRF accepted');
}catch(InvalidArgumentException $e){}
require_once dirname(__DIR__).'/app/ai_api.php';
$key=str_repeat('k',32);
$timestamp='1760000000';
$nonce=str_repeat('a',32);
$path='/api/in/research/evidence/control';
$raw='{"local_only":true,"action":"review"}';
$expected=hash_hmac('sha256',"sirus-research-admin-v1\n".$timestamp."\n".
    $nonce."\n".$path."\n".$raw,$key);
evidence_check(cms_ai_admin_signature($key,$timestamp,$nonce,$path,$raw)===$expected,
    'Signed internal control requests match Python HMAC format');
evidence_check(strlen(cms_ai_admin_signature($key,$timestamp,$nonce,
    '/api/in/research/continuous/history',$raw))===64,
    'History route is allowlisted for signed private reads');
try{cms_ai_admin_signature($key,$timestamp,$nonce,'/api/in/jobs',$raw);
    throw new LogicException('Arbitrary path signed');}
catch(InvalidArgumentException $e){}
echo "PASS: private source review, admin and CSRF gating, explicit human confirmation, strict scope validation.\n";
