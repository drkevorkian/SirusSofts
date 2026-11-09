<?php
declare(strict_types=1);
define('SIRUS_CMS_BOOTSTRAPPED',true);
require dirname(__DIR__).'/app/work_broker.php';
function must(bool $test,string $msg):void {if(!$test)throw new RuntimeException($msg);}
$dir=sys_get_temp_dir().'/work-test-'.bin2hex(random_bytes(8));
mkdir($dir,0700);
try{
    $broker=new SirusWorkBroker($dir.'/work.sqlite3');
    $key=random_bytes(32);$now=1794168000;
    $requestId=bin2hex(random_bytes(16));
    $body=json_encode([
        'node_id'=>'gentoo-home','sent_at'=>$now,'nonce'=>bin2hex(random_bytes(16)),
        'op'=>'submit','request_id'=>$requestId,'kind'=>'health_snapshot','payload'=>new stdClass()
    ],JSON_THROW_ON_ERROR);
    $sig=hash_hmac('sha256',$body,$key);
    $resolve=fn(string $id)=>$id==='gentoo-home'?$key:'';
    $new=$broker->request($body,$sig,$resolve,$now);
    must($new['state']==='queued','New request queued');
    must(count($broker->recent())===1,'Only one queued task');
    try{$broker->request($body,$sig,$resolve,$now);must(false,'Replay must fail');}
    catch(PDOException $e){} // duplicate (node_id, nonce)
    $repeat=json_decode($body,true);
    $repeat['nonce']=bin2hex(random_bytes(16));
    $again=json_encode($repeat,JSON_THROW_ON_ERROR);
    must($broker->request($again,hash_hmac('sha256',$again,$key),$resolve,$now)['request_id']===$requestId,
         'Idempotent resubmit');
    must(count($broker->recent())===1,'No duplicate jobs');
    $status=$repeat;$status['op']='status';unset($status['kind'],$status['payload']);
    $status['nonce']=bin2hex(random_bytes(16));
    $statusBody=json_encode($status,JSON_THROW_ON_ERROR);
    must($broker->request($statusBody,hash_hmac('sha256',$statusBody,$key),$resolve,$now)['state']==='queued',
         'Authenticated status');
    $forbidden=$repeat;$forbidden['kind']='shell';$forbidden['nonce']=bin2hex(random_bytes(16));
    $forbiddenBody=json_encode($forbidden,JSON_THROW_ON_ERROR);
    try{$broker->request($forbiddenBody,hash_hmac('sha256',$forbiddenBody,$key),$resolve,$now);
        must(false,'Arbitrary code jobs must be rejected');}
    catch(InvalidArgumentException $e){}
    try{$broker->request($statusBody,str_repeat('0',64),$resolve,$now);
        must(false,'Invalid signature must fail');}
    catch(RuntimeException $e){}
    $other=$status;$other['node_id']='gentoo-other';$other['nonce']=bin2hex(random_bytes(16));
    $otherBody=json_encode($other,JSON_THROW_ON_ERROR);
    try{$broker->request($otherBody,hash_hmac('sha256',$otherBody,$key),fn($id)=>$key,$now);
        must(false,'Cross-node job status must fail');}
    catch(InvalidArgumentException $e){}
    echo "PASS: HMAC job broker, replay protection, idempotence and per-node isolation.\n";
}finally{
    foreach(glob($dir.'/*')?:[] as $file)@unlink($file);
    @rmdir($dir);
}
