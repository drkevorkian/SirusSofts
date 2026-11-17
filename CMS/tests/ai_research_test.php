<?php
declare(strict_types=1);
define('SIRUS_CMS_BOOTSTRAPPED',true);
require dirname(__DIR__).'/app/core.php';
require dirname(__DIR__).'/app/ai_workspace.php';
require dirname(__DIR__).'/app/ai_research.php';

function research_check(bool $yes,string $why):void {
    if(!$yes)throw new RuntimeException('FAIL: '.$why);
}
$db=new PDO('sqlite::memory:');
$db->setAttribute(PDO::ATTR_ERRMODE,PDO::ERRMODE_EXCEPTION);
$db->setAttribute(PDO::ATTR_DEFAULT_FETCH_MODE,PDO::FETCH_ASSOC);
$db->exec('PRAGMA foreign_keys=ON');
cms_schema($db);
cms_ai_workspace_schema($db);
cms_ai_workspace_schema($db);

$who=['role'=>'admin','account'=>'admin:1'];
$other=['role'=>'admin','account'=>'admin:2'];
$user=['role'=>'user','account'=>'user:1'];
$pdf=[
 'title'=>'Defender HD11 service manual',
 'source_type'=>'pdf','url'=>'','text'=>'',
 'pdf_sha256'=>str_repeat('a',64),
 'page_start'=>1,'page_end'=>0,'passes'=>100,
 'target'=>'auto','model_id'=>'sirussofts-coder',
 'focus'=>'Clutch mechanism',
];
$valid=cms_ai_research_validate($pdf);
research_check($valid['source']['source_type']==='pdf','Existing Gentoo PDF accepted');
research_check($valid['source']['passes']===100,'100 passes supported');
research_check($valid['source']['page_end']===0,'Whole-document sentinel retained');
research_check($valid['local_only']===true,'Local-only policy explicit');
foreach([0,-1,101,true,'10',2.5] as $bad){
    $payload=$pdf;$payload['passes']=$bad;
    try{cms_ai_research_validate($payload);throw new LogicException('Invalid pass count accepted');}
    catch(InvalidArgumentException $e){}
}
foreach(['../../private',str_repeat('A',64),'invalid'] as $bad){
    $payload=$pdf;$payload['pdf_sha256']=$bad;
    try{cms_ai_research_validate($payload);throw new LogicException('Invalid PDF digest accepted');}
    catch(InvalidArgumentException $e){}
}
$payload=$pdf;$payload['page_start']=12;$payload['page_end']=3;
try{cms_ai_research_validate($payload);throw new LogicException('Invalid PDF pages accepted');}
catch(InvalidArgumentException $e){}
research_check(cms_ai_research_authorized($who)==='admin:1','Admin accepted');
try{cms_ai_research_authorized($user);throw new LogicException('Ordinary user accepted');}
catch(RuntimeException $e){research_check(str_contains($e->getMessage(),'Administrator'),'User refused');}

$id=str_repeat('b',32);
$db->prepare('INSERT INTO cms_ai_research_jobs
  (id,account,title,spec,target,model_id,passes,created_at)
  VALUES(?,?,?,?,?,?,?,?)')->execute([
  $id,'admin:1','Private PDF Research',
  json_encode($valid['source'],JSON_THROW_ON_ERROR),'auto',
  'sirussofts-coder',100,cms_now()
]);
research_check(cms_ai_research_owned($db,$who,$id)['title']==='Private PDF Research',
    'Owner can find private research');
research_check(count(cms_ai_research_list($db,$who))===1,'Owner can list jobs');
research_check(count(cms_ai_research_list($db,$other))===0,'Other admin sees no private jobs');
try{cms_ai_research_owned($db,$other,$id);throw new LogicException('Cross-account access');}
catch(RuntimeException $e){
    research_check($e->getMessage()==='Research job not found.','Ownership enforced');
}
echo "PASS: iterative research validation, existing PDF selection and admin job ownership.\n";
