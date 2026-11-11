<?php
declare(strict_types=1);
define('SIRUS_CMS_BOOTSTRAPPED',true);
require dirname(__DIR__).'/app/core.php';
require dirname(__DIR__).'/app/live_events.php';

$base=sys_get_temp_dir().'/sirus-live-'.bin2hex(random_bytes(8));
if(!mkdir($base,0700)||!mkdir($base.'/nodes',0700))
    throw new RuntimeException('Private test setup failed');
putenv('SIRUS_CMS_PRIVATE_ROOT='.$base);
try{
    $first=cms_live_publish('message.received',['message_id'=>'msg-1','unread'=>1]);
    $second=cms_live_publish('mail.received',['unread'=>3]);
    if($second<=$first)throw new RuntimeException('Event IDs must increase');
    $db=new PDO('sqlite:'.$base.'/nodes/live_events.sqlite3');
    if((int)$db->query('SELECT COUNT(*) FROM live_events')->fetchColumn()!==2)
        throw new RuntimeException('Notification journal missing events');
    try{
        cms_live_publish('execute.command',['shell'=>'rm -rf /']);
        throw new RuntimeException('Unsupported notification accepted');
    }catch(InvalidArgumentException $expected){}
    try{
        cms_live_publish('mail.received',['full_body'=>str_repeat('x',4096)]);
        throw new RuntimeException('Oversized notification accepted');
    }catch(InvalidArgumentException $expected){}
    echo "PASS private, allowlisted notification journal\n";
}finally{
    putenv('SIRUS_CMS_PRIVATE_ROOT');
    foreach(glob($base.'/nodes/*')?:[] as $file)@unlink($file);
    @rmdir($base.'/nodes');@rmdir($base);
}
