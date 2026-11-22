<?php
declare(strict_types=1);
define('SIRUS_CMS_BOOTSTRAPPED',true);
require dirname(__DIR__).'/app/core.php';
require_once dirname(__DIR__).'/app/ai_continuous.php';

function continuous_check(bool $condition,string $reason):void {
    if(!$condition)throw new RuntimeException('FAIL: '.$reason);
}
$base=['operation'=>'configure','passes'=>'100','revisit_seconds'=>'3600',
       'max_chat_jobs'=>'0','max_cpu_load'=>'3.5',
       'target'=>'auto','model_id'=>'sirussofts-coder'];
$parsed=cms_ai_continuous_payload($base);
continuous_check($parsed['value']['background_concurrency']===1,'Safe default background concurrency');
$parallel=cms_ai_continuous_payload($base+['background_concurrency'=>'4']);
continuous_check($parallel['value']['background_concurrency']===4,'Bounded background concurrency');
continuous_check($parsed['local_only']===true,'Local-only controller');
continuous_check($parsed['value']['passes']===100,'100 bounded analysis passes');
continuous_check($parsed['value']['max_cpu_load']===3.5,'Load limit parsed');
continuous_check($parsed['value']['max_chat_jobs']===0,'Chat admission enforced');
continuous_check($parsed['value']['min_gpu_free_mib']===0,
    'Default VRAM guard remains optional and disabled');
continuous_check($parsed['value']['max_database_mib']===0,
    'Private input database budget disabled by default');
continuous_check(!array_key_exists('research_watts',$parsed['value']),'Older forms preserve energy settings');
foreach(['0.129999'=>129999,'0'=>0,''=>-1] as $input=>$micros){
    $estimate=cms_ai_continuous_payload($base+['research_watts'=>'250','usd_kwh'=>(string)$input]);
    continuous_check($estimate['value']['research_watts']===250&&
        $estimate['value']['rate_microusd_kwh']===$micros,'Exact rate and power assumptions');
}
$budget=cms_ai_continuous_payload($base+['daily_energy_wh'=>'500']);
continuous_check($budget['value']['daily_energy_wh']===500,'Energy admission budget');
$override=cms_ai_continuous_payload(['operation'=>'power_override','minutes'=>'15','actor'=>'guest:1'],'cms-admin:7');
continuous_check($override['actor']==='cms-admin:7'&&$override['value']===15,'Bounded session-attributed override');
$bounded=cms_ai_continuous_payload(array_replace($base,[
    'max_database_mib'=>'512']));
continuous_check($bounded['value']['max_database_mib']===512,
    'Valid private database growth ceiling');
$withGpu=cms_ai_continuous_payload(array_replace($base,[
    'min_gpu_free_mib'=>'2048','target'=>'amd']));
continuous_check($withGpu['value']['min_gpu_free_mib']===2048&&
    $withGpu['value']['target']==='amd','Explicit GPU VRAM threshold parsed');
$source='pdf:'.str_repeat('a',64);
$jobId=str_repeat('1',32);
$cancel=cms_ai_continuous_payload([
    'operation'=>'cancel','job_id'=>$jobId,
    'confirmation'=>'CANCEL JOB','actor'=>'cms-admin:999'
],'cms-admin:12');
continuous_check($cancel['job_id']===$jobId&&
    $cancel['actor']==='cms-admin:12'&&$cancel['action']==='cancel',
    'Targeted cancellation binds current admin actor rather than untrusted form input');
foreach([
    [['operation'=>'cancel','job_id'=>$jobId,'confirmation'=>'CANCEL JOB'],''],
    [['operation'=>'cancel','job_id'=>$jobId,'confirmation'=>'YES'],'cms-admin:12'],
    [['operation'=>'cancel','job_id'=>'../secret',
      'confirmation'=>'CANCEL JOB'],'cms-admin:12'],
    [['operation'=>'cancel','job_id'=>$jobId,
      'confirmation'=>'CANCEL JOB'],'model:fake'],
] as [$badCancel,$badActor]){
    try{
        cms_ai_continuous_payload($badCancel,$badActor);
        throw new LogicException('Invalid job cancellation accepted');
    }catch(InvalidArgumentException $e){}
}

$history=cms_ai_continuous_history_payload($source,'8');
continuous_check($history['source_id']===$source&&$history['offset']===8,
    'Authenticated history source and offset normalized');
continuous_check($history['local_only']===true,'Private history stays local');
continuous_check($history['audit_offset']===0,'Audit page defaults to zero');
$historyPaged=cms_ai_continuous_history_payload($source,'8','20');
continuous_check($historyPaged['offset']===8&&
    $historyPaged['audit_offset']===20,'Independent bounded audit offset');
foreach(['-1','1000001','unknown'] as $badAudit){
    try{
        cms_ai_continuous_history_payload($source,'0',$badAudit);
        throw new LogicException('Invalid audit offset accepted');
    }catch(InvalidArgumentException $e){}
}
foreach([
    [$source,'-1'],
    [$source,'1000001'],
    ['../etc/passwd','0'],
    ['pdf:'.str_repeat('z',64),'0'],
    ['research:'.str_repeat('f',31),'0'],
    [$source,8],
] as [$badSource,$badOffset]){
    try{
        cms_ai_continuous_history_payload($badSource,$badOffset);
        throw new LogicException('Unsafe history input accepted');
    }catch(InvalidArgumentException $e){}
}

continuous_check(cms_ai_continuous_payload([
  'operation'=>'priority','source_id'=>$source,'priority'=>'-10'
])['value']===-10,'Negative source priority');
continuous_check(cms_ai_continuous_payload([
  'operation'=>'exclude','source_id'=>$source
])['source_id']===$source,'Source exclusion');
foreach(['enable','pause','resume','stop','disable','scan'] as $op)
    continuous_check(cms_ai_continuous_payload(['operation'=>$op])['action']===$op,
                     'Known operation '.$op);
foreach([
  ['operation'=>'shell','command'=>'whoami'],
  ['operation'=>'priority','source_id'=>'../../etc/passwd','priority'=>'1'],
  ['operation'=>'priority','source_id'=>$source,'priority'=>'11'],
  ['operation'=>'configure','passes'=>'101']+$base,
  array_replace($base,['model_id'=>'http://private-endpoint']),
  array_replace($base,['target'=>'localhost']),
  array_replace($base,['max_chat_jobs'=>'999']),
  array_replace($base,['revisit_seconds'=>'-1']),
  array_replace($base,['max_cpu_load'=>'not-number']),
  array_replace($base,['min_gpu_free_mib'=>'-1']),
  array_replace($base,['min_gpu_free_mib'=>'1048577']),
  array_replace($base,['min_gpu_free_mib'=>'not-a-number']),
  array_replace($base,['usd_kwh'=>'10.000001']),
  array_replace($base,['background_concurrency'=>'0']),
  array_replace($base,['background_concurrency'=>'5']),
  array_replace($base,['usd_kwh'=>'1e5']),
  array_replace($base,['daily_energy_wh'=>'-1']),
  ['operation'=>'power_override','minutes'=>'15'],
  array_replace($base,['research_watts'=>'-1']),
  array_replace($base,['max_database_mib'=>'-1']),
  array_replace($base,['max_database_mib'=>'1048577']),
  array_replace($base,['max_database_mib'=>'not-a-number']),
] as $bad){
    try {
        cms_ai_continuous_payload($bad);
        throw new LogicException('Accepted invalid continuous control input');
    } catch(InvalidArgumentException $e) {
        continuous_check(true,'Invalid input blocked');
    }
}
$sourceRequest=cms_ai_continuous_history_payload($source,'0');
$reportReply=[
    'source'=>['id'=>$source,'title'=>'Approved manual metadata',
               'revision'=>str_repeat('b',64),'extraction_status'=>'approved'],
    'pass_results_total'=>1,
    'offset'=>0,'audit_offset'=>0,'audit_total'=>1,
    'audit_events'=>[['id'=>1,'happened'=>123,'kind'=>'job-complete',
                       'subject'=>str_repeat('c',32),'detail'=>'']],
    'passes'=>[[
        'job_id'=>str_repeat('c',32),'cycle'=>1,'page'=>5,'part'=>1,
        'pass_number'=>2,'revision'=>str_repeat('d',64),
        'source_revision'=>str_repeat('e',64),
        'sha256'=>str_repeat('f',64),
        'notes'=>'Private source draft <not verified>',
        'duration_ms'=>15,
        'review_state'=>'unreviewed'
    ]]
];
$report=cms_ai_continuous_report_data($sourceRequest,$reportReply);
continuous_check($report['training_export']===false&&
    $report['verified_knowledge']===false&&
    $report['contains_private_unreviewed_drafts']===true&&
    $report['audit_total']===1&&count($report['audit_events'])===1,
    'Report may not masquerade as verified knowledge or training');
continuous_check($report['passes'][0]['page']===5&&
    $report['passes'][0]['draft_notes']==='Private source draft <not verified>',
    'Private report preserves source/page/pass and original draft');
foreach([
    array_replace($reportReply,['source'=>['id'=>'pdf:'.str_repeat('1',64)]]),
    array_replace($reportReply,['offset'=>10]),
    array_replace($reportReply,['audit_offset'=>10]),
    array_replace($reportReply,['audit_events'=>array_fill(0,21,[
        'id'=>1,'happened'=>1,'kind'=>'job-complete','subject'=>'x','detail'=>''])]),
    array_replace($reportReply,['passes'=>[
        array_replace($reportReply['passes'][0],['review_state'=>'supported'])]]),
    array_replace($reportReply,['passes'=>[
        array_replace($reportReply['passes'][0],['sha256'=>'invalid'])]]),
] as $invalid){
    try{
        cms_ai_continuous_report_data($sourceRequest,$invalid);
        throw new LogicException('Invalid research report accepted');
    }catch(RuntimeException $e){}
}
$db=new PDO('sqlite::memory:',null,null,[PDO::ATTR_ERRMODE=>PDO::ERRMODE_EXCEPTION]);
$_SESSION=[];
try {
    cms_ai_continuous_post($db,['operation'=>'enable']);
    throw new LogicException('Unauthorized mutation accepted');
} catch(RuntimeException $e) {
    continuous_check(str_contains($e->getMessage(),'Sign in required'),
                     'Unauthenticated research mutation rejected');
}
try{
    cms_ai_continuous_export($db,['source_id'=>$source,'offset'=>'0']);
    throw new LogicException('Unauthenticated report download accepted');
}catch(RuntimeException $e){
    continuous_check(str_contains($e->getMessage(),'Sign in required'),
                     'Report export requires CMS administrator');
}
echo "PASS: continuous research admin-only operations, validation, local-only policy.\n";
