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
continuous_check($parsed['local_only']===true,'Local-only controller');
continuous_check($parsed['value']['passes']===100,'100 bounded analysis passes');
continuous_check($parsed['value']['max_cpu_load']===3.5,'Load limit parsed');
continuous_check($parsed['value']['max_chat_jobs']===0,'Chat admission enforced');
$source='pdf:'.str_repeat('a',64);
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
] as $bad){
    try {
        cms_ai_continuous_payload($bad);
        throw new LogicException('Accepted invalid continuous control input');
    } catch(InvalidArgumentException $e) {
        continuous_check(true,'Invalid input blocked');
    }
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
echo "PASS: continuous research admin-only operations, validation, local-only policy.\n";
