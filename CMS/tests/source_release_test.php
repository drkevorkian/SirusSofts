<?php
declare(strict_types=1);
define('SIRUS_CMS_BOOTSTRAPPED',true);
require dirname(__DIR__).'/app/core.php';
require dirname(__DIR__).'/app/site.php';
require dirname(__DIR__).'/app/admin.php';

function checkRelease(bool $condition,string $why): void {
    if(!$condition)throw new RuntimeException($why);
}
$base=sys_get_temp_dir().'/sirus-release-'.bin2hex(random_bytes(8));
if(!mkdir($base,0700)||!mkdir($base.'/nodes',0700))
    throw new RuntimeException('Test private directory creation failed');
putenv('SIRUS_CMS_PRIVATE_ROOT='.$base);
$now=time();
$head=str_repeat('a',40);
$previous=str_repeat('b',40);
$installed=str_repeat('1',64);
$target=str_repeat('2',64);
$oldTarget=str_repeat('3',64);
$component='gentoo-node/client.py';
$index=[
    'repository'=>'drkevorkian/SirusSofts',
    'branch'=>'main',
    'commit'=>$head,
    'checked_at'=>$now,
    'candidates'=>[
        ['commit'=>$head,'message'=>'Latest', 'files'=>[
            ['platform'=>'gentoo','path'=>$component,'sha256'=>$target]]],
        ['commit'=>$previous,'message'=>'Previous', 'files'=>[
            ['platform'=>'gentoo','path'=>$component,'sha256'=>$oldTarget]]],
    ],
];
try {
    file_put_contents($base.'/nodes/source_index.json',
        json_encode($index,JSON_THROW_ON_ERROR));
    $db=new PDO('sqlite::memory:',null,null,[
       PDO::ATTR_ERRMODE=>PDO::ERRMODE_EXCEPTION,
       PDO::ATTR_DEFAULT_FETCH_MODE=>PDO::FETCH_ASSOC
    ]);
    cms_schema($db);
    $hash=password_hash('this is a very secure admin password',PASSWORD_DEFAULT);
    $db->prepare("INSERT INTO cms_admins(username,password_hash,created_at)
                  VALUES('admin',?,?)")->execute([$hash,date('c')]);
    $_SESSION=['cms_admin_id'=>1,'cms_username'=>'admin'];
    $current=static fn(string $host,string $path):string=>$installed;
    $id=cms_release_create($db,'gentoo-home',$component,$previous,$current,$now);
    checkRelease((bool)preg_match('/^[a-f0-9]{32}$/D',$id),'Invalid plan ID');
    $plans=cms_release_recent($db);
    checkRelease($plans[0]['state']==='planned','Plan must start planned');
    checkRelease($plans[0]['target_sha256']===$oldTarget,'Old immutable revision not selected');
    try{
        cms_release_approve($db,$id,'incorrect','APPROVE '.substr($previous,0,12),$current,$now);
        throw new RuntimeException('Invalid admin password was accepted');
    }catch(InvalidArgumentException $expected){}
    try{
        cms_release_approve($db,$id,'this is a very secure admin password',
            'APPROVE wrong-prefix',$current,$now);
        throw new RuntimeException('Incorrect confirmation was accepted');
    }catch(InvalidArgumentException $expected){}
    $changed=static fn(string $host,string $path):string=>str_repeat('f',64);
    try{
        cms_release_approve($db,$id,'this is a very secure admin password',
            'APPROVE '.substr($previous,0,12),$changed,$now);
        throw new RuntimeException('Installed baseline change was not detected');
    }catch(InvalidArgumentException $expected){}
    cms_release_approve($db,$id,'this is a very secure admin password',
        'APPROVE '.substr($previous,0,12),$current,$now);
    checkRelease(cms_release_recent($db)[0]['state']==='approved','Plan was not approved');
    try{
        cms_release_approve($db,$id,'this is a very secure admin password',
            'APPROVE '.substr($previous,0,12),$current,$now);
        throw new RuntimeException('Double approval was accepted');
    }catch(InvalidArgumentException $expected){}
    cms_release_cancel($db,$id);
    checkRelease(cms_release_recent($db)[0]['state']==='canceled','Approval cancellation failed');
    try{
        cms_release_create($db,'ubuntu-primary',$component,$head,$current,$now);
        throw new RuntimeException('Wrong-host component accepted');
    }catch(InvalidArgumentException $expected){}
    try{
        cms_release_create($db,'gentoo-home',$component,str_repeat('0',40),$current,$now);
        throw new RuntimeException('Non-indexed commit accepted');
    }catch(InvalidArgumentException $expected){}
    $other=cms_release_create($db,'gentoo-home',$component,$head,$current,$now);
    try{
        cms_release_approve($db,$other,'this is a very secure admin password',
            'APPROVE '.substr($head,0,12),$current,$now+3601);
        throw new RuntimeException('Expired plan approved');
    }catch(InvalidArgumentException $expected){}
    $expiryState=$db->prepare('SELECT state FROM cms_source_release_plans WHERE id=?');
    $expiryState->execute([$other]);
    checkRelease($expiryState->fetchColumn()==='planned','Expiry mutated the plan');
    echo "PASS source release approval, pinned historical revisions, invalid password,
stale installed hash, expiry, and cancellation\n";
}finally{
    putenv('SIRUS_CMS_PRIVATE_ROOT');
    foreach(glob($base.'/nodes/*')?:[] as $file)@unlink($file);
    @rmdir($base.'/nodes');@rmdir($base);
}
