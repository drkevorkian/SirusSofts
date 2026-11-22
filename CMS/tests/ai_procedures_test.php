<?php
declare(strict_types=1);
define('SIRUS_CMS_BOOTSTRAPPED',true);
require dirname(__DIR__).'/app/core.php';
require_once dirname(__DIR__).'/app/ai_procedures.php';
require_once dirname(__DIR__).'/app/ai_api.php';

function procedure_check(bool $yes,string $message):void {
    if(!$yes)throw new RuntimeException('FAIL: '.$message);
}
$actor='cms-admin:42';
$source='pdf:'.str_repeat('a',64);
$hash=str_repeat('b',64);
$id=str_repeat('c',64);
$seed=str_repeat('d',64);
$seedPayload=cms_ai_procedures_payload(['operation'=>'seed',
    'source_id'=>$source,'heading'=>'Removal of primary clutch'],$actor);
procedure_check($seedPayload['local_only']===true,'Local-only guard');
procedure_check($seedPayload['actor']===$actor,'Actor comes from session argument');
procedure_check($seedPayload['source_id']===$source,'Exact PDF only');

$accepted=cms_ai_procedures_payload([
    'operation'=>'accept','id'=>$id,'expected_hash'=>$hash,
    'confirmation'=>'ACCEPT FOR REVIEW','reason'=>'Original source pages reviewed'
],$actor);
procedure_check($accepted['action']==='accept','Accept only into review queue');
procedure_check($accepted['reason']==='Original source pages reviewed','Audit reason');

$rejected=cms_ai_procedures_payload([
    'operation'=>'reject','id'=>$id,'expected_hash'=>$hash,
    'confirmation'=>'REJECT SUGGESTION','reason'=>'Different service revision'
],$actor);
procedure_check($rejected['action']==='reject','Reject candidate operation');
procedure_check(cms_ai_procedures_payload([
    'operation'=>'pause','seed_id'=>$seed],$actor)['seed_id']===$seed,
    'Pause known seed only');
procedure_check(cms_ai_procedures_payload([
    'operation'=>'resume','seed_id'=>$seed],$actor)['seed_id']===$seed,
    'Resume known seed only');

$proposal=cms_ai_procedures_payload(['operation'=>'propose_scope','source_id'=>$source,
    'page'=>'3','part'=>'2','expected_scope'=>'brp/defender/2026/hd10/clutch/rev-a',
    'quote'=>'BRP Defender 2026 HD10 clutch Rev-A','actor'=>'cms-admin:999'],$actor);
procedure_check($proposal['page']===3&&$proposal['actor']===$actor,'Scope citation and session identity');
$vote=cms_ai_procedures_payload(['operation'=>'review_scope','id'=>$id,'decision'=>'support',
    'reason'=>'Reviewed source context','confirmation'=>'SUPPORT SCOPE'],$actor);
procedure_check($vote['actor']===$actor&&$vote['id']===$id,'Vote bound to evidence identifier');
$packet=cms_ai_procedures_payload(['operation'=>'vote_procedure','id'=>$id,
    'left_scope'=>$seed,'right_scope'=>$hash,'expected_hash'=>$hash,'decision'=>'reject',
    'reason'=>'Originals disagree','confirmation'=>'REJECT PRINTED EVIDENCE'],$actor);
procedure_check($packet['left_scope']===$seed&&$packet['actor']===$actor,'Procedure packet vote');
$wrong=[
    [['operation'=>'vote_procedure','id'=>$id,'decision'=>'support',
      'reason'=>'Reviewed evidence','confirmation'=>'SUPPORT PRINTED EVIDENCE'],$actor],
    [['operation'=>'review_scope','id'=>$id,'decision'=>'support',
      'reason'=>'Reviewed source','confirmation'=>'APPROVE REPAIR'],$actor],
    [['operation'=>'seed','source_id'=>'../../etc/passwd','heading'=>'Removal of primary clutch'],$actor],
    [['operation'=>'seed','source_id'=>$source,'heading'=>"<script>alert('x')</script>"],$actor],
    [['operation'=>'accept','id'=>$id,'expected_hash'=>$hash,
      'confirmation'=>'APPROVE REPAIR','reason'=>'Original source pages reviewed'],$actor],
    [['operation'=>'reject','id'=>$id,'expected_hash'=>$hash,
      'confirmation'=>'REJECT SUGGESTION','reason'=>'x'],$actor],
    [['operation'=>'accept','id'=>$id,'expected_hash'=>'deadbeef',
      'confirmation'=>'ACCEPT FOR REVIEW','reason'=>'Human review'],$actor],
    [['operation'=>'accept','id'=>$id,'expected_hash'=>$hash,
      'confirmation'=>'ACCEPT FOR REVIEW','reason'=>str_repeat('a',201)],$actor],
    [['operation'=>'pause','seed_id'=>'incorrect'],$actor],
    [['operation'=>'seed','source_id'=>$source,'heading'=>'Removal of primary clutch'],'guest:1'],
    [['operation'=>'debug','id'=>$id],$actor],
];
foreach($wrong as [$payload,$identity]){
    $thrown=false;
    try{cms_ai_procedures_payload($payload,$identity);}
    catch(InvalidArgumentException){$thrown=true;}
    procedure_check($thrown,'Unsafe operation rejected');
}
foreach(['/api/in/research/procedures',
         '/api/in/research/procedures/control'] as $path){
    $signature=cms_ai_admin_signature(str_repeat('z',32),'1760000000',
        str_repeat('1',32),$path,'{"local_only":true}');
    procedure_check(preg_match('/^[a-f0-9]{64}$/D',$signature)===1,
                    'Signed administrative bridge route required');
}
$path=dirname(__DIR__).'/app/ai_procedures.php';
$ui=file_get_contents($path);
procedure_check(is_string($ui)&&str_contains($ui,'cms_need_admin()'),
    'Admin only');
procedure_check(str_contains($ui,'cms_ai_api_rate('),'Rate limiting');
procedure_check(str_contains($ui,'cms_esc('),'HTML escaping');
procedure_check(str_contains($ui,'cms_csrf()'),'Session CSRF in review forms');
procedure_check(str_contains($ui,'cms_audit('),'CMS activity audit');
echo "PASS: procedure review CMS payload and signed route tests\n";
