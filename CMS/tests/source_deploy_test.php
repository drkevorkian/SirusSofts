<?php
declare(strict_types=1);
define('SIRUS_CMS_BOOTSTRAPPED',true);
require dirname(__DIR__).'/app/core.php';
require dirname(__DIR__).'/app/site.php';
require dirname(__DIR__).'/app/admin.php';

function deployAssert(bool $condition,string $message): void {
    if(!$condition)throw new RuntimeException($message);
}
function deployRejected(callable $call,string $message): void {
    try{$call();}catch(InvalidArgumentException $expected){return;}
    throw new RuntimeException($message);
}
$base=sys_get_temp_dir().'/sirus-deploy-'.bin2hex(random_bytes(8));
$nodes=$base.'/nodes';
$stage=$nodes.'/source_artifacts';
if(!mkdir($base,0700)||!mkdir($nodes,0700)||!mkdir($stage,0700))
    throw new RuntimeException('Test directory setup failed');
putenv('SIRUS_CMS_PRIVATE_ROOT='.$base);
$now=time();
$id=str_repeat('a',32);
$commit=str_repeat('b',40);
$baseline=str_repeat('1',64);
$source="print('verified source')\n";
$target=hash('sha256',$source);
try {
    $_SESSION=['cms_admin_id'=>1,'cms_username'=>'admin'];
    $db=new PDO('sqlite::memory:',null,null,[
        PDO::ATTR_ERRMODE=>PDO::ERRMODE_EXCEPTION,
        PDO::ATTR_DEFAULT_FETCH_MODE=>PDO::FETCH_ASSOC
    ]);
    cms_schema($db);
    cms_release_schema($db);
    $db->prepare("INSERT INTO cms_source_controller_health(host,last_seen,state)
                  VALUES('ubuntu-primary',?,'ready')")->execute([$now]);
    $password='strong-password-for-release';
    $db->prepare("INSERT INTO cms_admins(username,password_hash,created_at)
                  VALUES('admin',?,?)")
      ->execute([password_hash($password,PASSWORD_DEFAULT),date('c')]);
    $db->prepare("INSERT INTO cms_source_release_plans(
            id,host,source_path,commit_sha,target_sha256,baseline_sha256,state,
            created_by,created_at,approved_at)
            VALUES(?,?,?,?,?,?,'approved',1,?,?)")
       ->execute([$id,'ubuntu-primary','ubuntu-worker/worker.py',
                  $commit,$target,$baseline,$now-120,$now-30]);
    file_put_contents($stage.'/'.$id.'.source',$source);
    file_put_contents($stage.'/'.$id.'.json',json_encode([
        'id'=>$id,'host'=>'ubuntu-primary','path'=>'ubuntu-worker/worker.py',
        'commit'=>$commit,'target_sha256'=>$target,
        'baseline_sha256'=>$baseline,'staged_at'=>$now,
        'ready_for_manual_review'=>true
    ]));
    $installed=static fn(string $host,string $path):string=>$baseline;
    $request=static fn(string $action,string $pass,string $phrase,?callable $getter=null)=>
       cms_release_request_deployment($db,$action,$id,$pass,$phrase,
           $now,$getter??$installed,$stage);
    deployRejected(fn()=>$request('apply','wrong','DEPLOY '.substr($commit,0,12)),
        'Password bypassed');
    deployRejected(fn()=>$request('apply',$password,'DEPLOY wrong'),
        'Confirmation bypassed');
    deployRejected(fn()=>$request('apply',$password,'DEPLOY '.substr($commit,0,12),
        static fn() => str_repeat('e',64)),'Changed installed baseline accepted');
    $job=$request('apply',$password,'DEPLOY '.substr($commit,0,12));
    deployAssert((bool)preg_match('/^[a-f0-9]{32}$/D',$job),'Invalid request ID');
    deployAssert($db->query("SELECT state FROM cms_source_deploy_requests")->fetchColumn()
        ==='queued','Apply request was not queued');
    deployRejected(fn()=>$request('apply',$password,'DEPLOY '.substr($commit,0,12)),
        'Duplicate deployment request accepted');
    deployRejected(fn()=>$request('rollback',$password,'ROLLBACK '.substr($commit,0,12)),
        'Rollback before successful apply was accepted');
    $db->exec("UPDATE cms_source_deploy_requests SET state='done'");
    $rollback=$request('rollback',$password,'ROLLBACK '.substr($commit,0,12));
    deployAssert($rollback!==$job,'Rollback needs its own audited request');
    deployRejected(fn()=>$request('rollback',$password,'ROLLBACK '.substr($commit,0,12)),
        'Duplicate rollback accepted');
    echo "PASS password-confirmed source apply/rollback queue authorization and duplicate guards\n";
} finally {
    putenv('SIRUS_CMS_PRIVATE_ROOT');
    foreach(glob($stage.'/*')?:[] as $f)@unlink($f);
    @rmdir($stage);@rmdir($nodes);@rmdir($base);
}
