<?php
declare(strict_types=1);
/** Run the real offline migration on an existing private CMS database. */
if(PHP_SAPI!=='cli')exit(1);
define('SIRUS_CMS_BOOTSTRAPPED',true);
require dirname(__DIR__).'/app/core.php';
$private=sys_get_temp_dir().'/sirus-release-migrate-'.bin2hex(random_bytes(8));
$data=$private.'/data';
if(!mkdir($private,0700) || !mkdir($data,0700))
    throw new RuntimeException('Could not prepare private fixture');
putenv('SIRUS_CMS_PRIVATE_ROOT='.$private);
$database=$data.'/cms.sqlite3';
try{
    $pdo=new PDO('sqlite:'.$database,null,null,[
        PDO::ATTR_ERRMODE=>PDO::ERRMODE_EXCEPTION,
        PDO::ATTR_DEFAULT_FETCH_MODE=>PDO::FETCH_ASSOC
    ]);
    cms_schema($pdo);
    $pdo->exec("INSERT INTO cms_settings(name,value) VALUES('fixture','preserve-me')");
    $pdo->exec("INSERT INTO cms_admins(username,password_hash,created_at)
                VALUES('existing-admin','existing-hash','2026-01-01')");
    $pdo=null;
    $script=dirname(__DIR__).'/bin/migrate-schema.php';
    for($run=0;$run<2;$run++){
        $pipes=[];
        $process=proc_open([PHP_BINARY,$script],[
            0=>['pipe','r'],1=>['pipe','w'],2=>['pipe','w']
        ],$pipes,null,array_merge($_ENV,[
            'SIRUS_CMS_PRIVATE_ROOT'=>$private
        ]));
        if(!is_resource($process))
            throw new RuntimeException('Failed to start offline migration');
        fclose($pipes[0]);
        $output=stream_get_contents($pipes[1]);fclose($pipes[1]);
        $errors=stream_get_contents($pipes[2]);fclose($pipes[2]);
        if(proc_close($process)!==0)
            throw new RuntimeException('Offline migration failed: '.$errors);
        if(!str_contains($output,'Release Planner schemas are current'))
            throw new RuntimeException('Offline migration did not report Release Planner');
    }
    $pdo=new PDO('sqlite:'.$database);
    foreach(['cms_source_release_plans','cms_source_deploy_requests',
             'cms_source_controller_health'] as $table){
        $statement=$pdo->prepare("SELECT count(*) FROM sqlite_master
            WHERE type='table' AND name=?");
        $statement->execute([$table]);
        if((int)$statement->fetchColumn()!==1)
            throw new RuntimeException('Missing release controller table: '.$table);
    }
    if($pdo->query("SELECT value FROM cms_settings WHERE name='fixture'")->fetchColumn()
       !=='preserve-me')
        throw new RuntimeException('Original CMS setting was overwritten');
    if($pdo->query("SELECT username FROM cms_admins WHERE username='existing-admin'")
       ->fetchColumn()!=='existing-admin')
        throw new RuntimeException('Original administrator was overwritten');
    $pdo=null;
    echo "PASS offline migration installs release queue tables idempotently and preserves CMS data\n";
}finally{
    putenv('SIRUS_CMS_PRIVATE_ROOT');
    foreach(glob($data.'/*')?:[] as $name)@unlink($name);
    @rmdir($data);@rmdir($private);
}
