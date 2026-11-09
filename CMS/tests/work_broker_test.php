<?php
declare(strict_types=1);
define('SIRUS_CMS_BOOTSTRAPPED',true);
require dirname(__DIR__).'/app/work_broker.php';
function must(bool $test,string $msg):void {if(!$test)throw new RuntimeException($msg);}
$dir=sys_get_temp_dir().'/gentoo-work-test-'.bin2hex(random_bytes(8));
mkdir($dir,0700);
try{
    $broker=new SirusWorkBroker($dir.'/work.sqlite3');
    $key=random_bytes(32);$now=1794168000;
    $resolve=fn(string $node)=>$node==='gentoo-home'?$key:'';
    $make=function(string $op,array $values=[])use($key,$now,$broker,$resolve):array{
        $message=array_merge(['node_id'=>'gentoo-home','sent_at'=>$now,
                              'nonce'=>bin2hex(random_bytes(16)),'op'=>$op],$values);
        $body=json_encode($message,JSON_THROW_ON_ERROR);
        return $broker->request($body,hash_hmac('sha256',$body,$key),$resolve,$now);
    };
    $requestId=$broker->enqueue('gentoo-home','compute_selftest',$now);
    must(strlen($requestId)===32,'Ubuntu issues a secure job ID');
    must(count($broker->recent())===1,'Ubuntu queues exactly one Gentoo task');
    $job=$make('claim');
    must($job['request_id']===$requestId && $job['state']==='running',
         'Gentoo claims the queued task');
    $lease=$job['lease_token'];
    must(strlen($lease)===32,'Claim must return secure lease');
    must($make('claim')['state']==='idle','Claiming again does not duplicate running work');
    $done=['request_id'=>$requestId,'lease_token'=>$lease,'state'=>'done',
           'result'=>['executor'=>'gentoo','iterations'=>50000],'error'=>''];
    must($make('complete',$done)['state']==='done','Gentoo returns the actual result');
    must($make('status',['request_id'=>$requestId])['result']['executor']==='gentoo',
         'Ubuntu CMS persists Gentoo result');
    must($make('complete',$done)['state']==='done','Lost ACK can be retried idempotently');
    try{
        $wrong=$done;$wrong['result']=['executor'=>'ubuntu'];
        $make('complete',$wrong);
        must(false,'Conflicting completion must not be accepted');
    }catch(InvalidArgumentException $expected){}
    must(count($broker->recent())===1,'No duplicate job created on retries');
    $history=$make('history');
    must($history['node_id']==='gentoo-home','History is scoped to the authenticated Gentoo node');
    must(count($history['jobs'])===1,'History returns the node job');
    must($history['jobs'][0]['result']['executor']==='gentoo','History contains completed result');
    must(!array_key_exists('lease_token',$history['jobs'][0]),'History never exposes lease secrets');
    try{
        $make('submit',['request_id'=>bin2hex(random_bytes(16)),
                        'kind'=>'site_health','payload'=>new stdClass()]);
        must(false,'Gentoo must NOT assign work to Ubuntu');
    }catch(InvalidArgumentException $expected){}
    try{$broker->enqueue('gentoo-home','shell',$now);must(false,'Cannot enqueue shell');}
    catch(InvalidArgumentException $expected){}
    $otherId=$broker->enqueue('gentoo-other','health_snapshot',$now);
    must(count($make('history')['jobs'])===1,'History does not leak other node assignments');
    try{$make('status',['request_id'=>$otherId]);must(false,'Cross-node result leak');}
    catch(InvalidArgumentException $expected){}
    $second=$make('claim');
    must($second['state']==='idle','Gentoo cannot claim other nodes jobs');
    // Stale lease recovered with a new token. Old token can't override new work.
    $waiting=$broker->enqueue('gentoo-home','health_snapshot',$now);
    $first=$make('claim');
    $later=function($op,$payload,$when)use($broker,$key,$resolve){
        $body=json_encode(array_merge(['node_id'=>'gentoo-home','sent_at'=>$when,
              'nonce'=>bin2hex(random_bytes(16)),'op'=>$op],$payload),JSON_THROW_ON_ERROR);
        return $broker->request($body,hash_hmac('sha256',$body,$key),$resolve,$when);
    };
    $reissued=$later('claim',[],$now+121);
    must($reissued['request_id']===$waiting && $reissued['lease_token']!==$first['lease_token'],
        'Expired Gentoo lease reissued safely');
    try{
       $later('complete',['request_id'=>$waiting,'lease_token'=>$first['lease_token'],
             'state'=>'done','result'=>[],'error'=>''],$now+121);
       must(false,'Expired lease must fail');
    }catch(InvalidArgumentException $expected){}
    must($later('complete',['request_id'=>$waiting,'lease_token'=>$reissued['lease_token'],
           'state'=>'done','result'=>['executor'=>'gentoo'],'error'=>''],$now+121)['state']==='done',
           'Current Gentoo lease can complete');
    $bad=json_encode(['node_id'=>'gentoo-home','sent_at'=>$now,
        'nonce'=>bin2hex(random_bytes(16)),'op'=>'claim'],JSON_THROW_ON_ERROR);
    try{$broker->request($bad,str_repeat('0',64),$resolve,$now);
        must(false,'Invalid signature must fail');}catch(RuntimeException $expected){}
    must($broker->request($bad,hash_hmac('sha256',$bad,$key),$resolve,$now)['state']==='idle',
        'First valid signed nonce accepted');
    try{$broker->request($bad,hash_hmac('sha256',$bad,$key),$resolve,$now);
        must(false,'Replay must fail');}catch(PDOException $expected){}
    echo "PASS: Ubuntu-queued Gentoo jobs, signed claim, execution reporting, retry, lease and node isolation.\n";
}finally{
    foreach(glob($dir.'/*')?:[] as $file)@unlink($file);
    @rmdir($dir);
}
