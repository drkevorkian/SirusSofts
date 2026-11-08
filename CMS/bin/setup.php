<?php
declare(strict_types=1);

/**
 * Private, single-run CLI initializer.
 * This file is part of the complete deployable CMS folder.
 * Run from an interactive SSH/Plesk terminal: php bin/setup.php
 * The CMS database is created outside the web root automatically.
 * Never pass passwords on shell command lines.
 */
if(PHP_SAPI!=='cli'){http_response_code(404);exit;}
define('SIRUS_CMS_BOOTSTRAPPED',true);
require __DIR__.'/../app/core.php';
if(!extension_loaded('pdo_sqlite'))exit("PHP PDO_SQLITE is required.\n");

/**
 * Root SSH access is supported. Before creating SQLite, identify the Plesk
 * hosting account, prepare ONLY this CMS's private storage, and permanently
 * drop UID/GID privileges. No root-owned SQLite or root-running web process.
 * Use --web-user=USERNAME if the Plesk httpdocs owner cannot be determined.
 */
function cms_setup_web_account(array $args): array {
    if(!function_exists('posix_getpwnam') || !function_exists('posix_getpwuid'))
        throw new RuntimeException('Root setup requires the PHP POSIX extension.');

    $explicit=null;
    foreach(array_slice($args,1) as $arg){
        if(!str_starts_with((string)$arg,'--web-user='))
            throw new InvalidArgumentException('Usage: php bin/setup.php [--web-user=WEB_USER]');
        if($explicit!==null)
            throw new InvalidArgumentException('Provide --web-user only once.');
        $explicit=substr((string)$arg,11);
        if(!preg_match('/^[a-z_][a-z0-9_-]{0,31}$/D',$explicit))
            throw new InvalidArgumentException('Invalid Linux website username.');
    }
    if($explicit!==null){
        $account=posix_getpwnam($explicit);
        if(!is_array($account))
            throw new RuntimeException('Requested website user does not exist.');
    }else{
        $root=cms_site_root();
        $httpdocs=null;
        for($p=$root;;$p=dirname($p)){
            if(basename($p)==='httpdocs'){$httpdocs=$p;break;}
            if(dirname($p)===$p)break;
        }
        if($httpdocs===null)
            throw new RuntimeException('Cannot locate Plesk httpdocs. Specify --web-user=WEB_USER.');
        $uid=fileowner($httpdocs);
        if($uid===false || (int)$uid===0)
            throw new RuntimeException('Plesk httpdocs is root-owned or has unknown ownership. Specify --web-user=WEB_USER.');
        $account=posix_getpwuid((int)$uid);
        if(!is_array($account))
            throw new RuntimeException('Could not resolve httpdocs owner. Specify --web-user=WEB_USER.');
    }
    if((int)$account['uid']===0 || (int)$account['gid']===0)
        throw new RuntimeException('Refusing root or root-group as the PHP website identity.');
    return $account;
}
function cms_setup_prepare_storage(array $account): void {
    $private=cms_private_directory();
    $parent=dirname($private);
    if(is_link($parent))
        throw new RuntimeException('Refusing a symlinked Plesk private/ directory.');
    if(!is_dir($parent)){
        // A fresh Plesk subscription may not contain private/ yet.
        if(basename($parent)!=='private' || !is_dir(dirname($parent)) ||
           !@mkdir($parent,0700) ||
           !@chown($parent,(string)$account['name']) ||
           !@chgrp($parent,(int)$account['gid']))
            throw new RuntimeException('Unable to create a private/ folder for the Plesk hosting user.');
    }
    foreach([$private,$private.'/data'] as $directory){
        if(is_link($directory))
            throw new RuntimeException('Refusing a symlink in CMS private storage.');
        if(!is_dir($directory) && !@mkdir($directory,0700))
            throw new RuntimeException('Cannot create private CMS folder.');
        $owner=fileowner($directory);
        if($owner===false || ((int)$owner!==0 && (int)$owner!==(int)$account['uid']))
            throw new RuntimeException('Private CMS folder is already owned by another non-root account.');
        if(!@chown($directory,(string)$account['name']) ||
           !@chgrp($directory,(int)$account['gid']) || !@chmod($directory,0700))
            throw new RuntimeException('Unable to secure CMS private directory ownership.');
    }
}
function cms_setup_drop_root(array $args): void {
    if(!function_exists('posix_geteuid'))
        throw new RuntimeException('PHP POSIX extension is needed for root-safe CMS setup.');
    if(posix_geteuid()!==0)return;
    $account=cms_setup_web_account($args);
    echo "Root terminal detected. CMS will initialize as Plesk account: ".
        $account['name']." (uid ".(int)$account['uid'].").\n";
    cms_setup_prepare_storage($account);
    if(!function_exists('posix_initgroups') ||
       !posix_initgroups((string)$account['name'],(int)$account['gid']))
        throw new RuntimeException('Unable to reset website supplementary groups; POSIX initgroups is required.');
    if(!posix_setgid((int)$account['gid']) || !posix_setuid((int)$account['uid']))
        throw new RuntimeException('Could not drop root privileges safely.');
    if(posix_geteuid()!==(int)$account['uid'] || posix_getegid()!==(int)$account['gid'])
        throw new RuntimeException('Website UID/GID verification failed.');
    if(!is_writable(dirname(cms_database_file())))
        throw new RuntimeException('Plesk user cannot write CMS private storage. Check private/ parent access.');
    echo "Privileges dropped successfully. No SQLite file will be owned by root.\n";
}
try{cms_setup_drop_root($argv);}
catch(Throwable $problem){
    fwrite(STDERR,"CMS setup cannot continue: ".$problem->getMessage()."\n");
    exit(1);
}
if(is_file(cms_database_file()))exit("CMS database already exists. Refusing to reinstall or reset.\n");
echo "SirusSofts CMS ".SIRUS_CMS_VERSION." installer\n";
echo "This initializer does not modify cPoint or publish any project.\n";
echo "Admin username [admin]: ";
$username=trim((string)fgets(STDIN));
if($username==='')$username='admin';
if(!preg_match('/^[a-zA-Z0-9_.-]{3,40}$/D',$username))exit("Username must have 3–40 safe characters.\n");
function cms_cli_password(string $question): string {
    echo $question;
    $hidden=false;
    if(function_exists('shell_exec') && function_exists('posix_isatty') &&
       posix_isatty(STDIN) && DIRECTORY_SEPARATOR==='/') {
        @shell_exec('stty -echo');
        $hidden=true;
    }
    try{$value=rtrim((string)fgets(STDIN),"\r\n");}
    finally{if($hidden){@shell_exec('stty echo');echo "\n";}}
    if(!$hidden)echo "\nWarning: terminal input could not be hidden; clear terminal history.\n";
    return $value;
}
$password=cms_cli_password("New CMS admin password (14+ characters): ");
$again=cms_cli_password("Repeat password: ");
if(strlen($password)<14||strlen($password)>512||!hash_equals($password,$again))
    exit("Password mismatch or outside required 14–512 characters. No database created.\n");
unset($again);
$directory=dirname(cms_database_file());
if(!is_dir($directory) && !mkdir($directory,0700,true) && !is_dir($directory))
    exit("Unable to create private CMS database folder.\n");
@chmod($directory,0700);
$db=new PDO('sqlite:'.cms_database_file(),null,null,[
    PDO::ATTR_ERRMODE=>PDO::ERRMODE_EXCEPTION,
    PDO::ATTR_DEFAULT_FETCH_MODE=>PDO::FETCH_ASSOC
]);
$db->exec('PRAGMA busy_timeout=5000');
$db->exec('PRAGMA foreign_keys=ON');
$db->exec('PRAGMA journal_mode=WAL');
$db->beginTransaction();
try{
    cms_schema($db);
    $stmt=$db->prepare('INSERT INTO cms_settings(name,value) VALUES(?,?)');
    foreach(cms_defaults() as $key=>$value)$stmt->execute([$key,$value]);
    $db->prepare('INSERT INTO cms_admins(username,password_hash,created_at) VALUES(?,?,?)')
       ->execute([$username,password_hash($password,PASSWORD_DEFAULT),cms_now()]);
    unset($password);
    $db->commit();
    @chmod(cms_database_file(),0600);
    echo "Initialized CMS database. All projects remain unpublished by default.\n";
    echo "Project applications remain independent from the CMS.\n";
    echo "Next: configure Nginx routing and valid DNS/TLS.\n";
}catch(Throwable $error){
    if($db->inTransaction())$db->rollBack();
    error_log('CMS setup failure: '.get_class($error));
    exit("Setup failed. Inspect PHP and PDO_SQLITE; do not expose private data.\n");
}
