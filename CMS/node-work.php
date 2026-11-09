<?php
declare(strict_types=1);
header('Content-Type: application/json; charset=utf-8');
header('Cache-Control: no-store');
header('X-Content-Type-Options: nosniff');
$https=(!empty($_SERVER['HTTPS']) && strtolower((string)$_SERVER['HTTPS'])!=='off')
    || (int)($_SERVER['SERVER_PORT']??0)===443;
if(($_SERVER['REQUEST_METHOD']??'')!=='POST' || !$https){
    http_response_code(405);exit('{"ok":false,"error":"HTTPS POST required"}');
}
define('SIRUS_CMS_BOOTSTRAPPED',true);
require __DIR__.'/app/core.php';
require __DIR__.'/app/work_broker.php';
$base=cms_private_directory().'/nodes';
$keyDir=$base.'/keys';
$database=$base.'/work.sqlite3';
if(!is_dir($keyDir) || !is_dir($base) || !is_writable($base)){
    http_response_code(503);exit('{"ok":false,"error":"Work broker not configured"}');
}
$raw=file_get_contents('php://input',false,null,0,4097);
try {
    if(!is_string($raw) || strlen($raw)>4096)
        throw new InvalidArgumentException('Invalid job envelope size.');
    $broker=new SirusWorkBroker($database);
    $result=$broker->request(
        $raw,strtolower((string)($_SERVER['HTTP_X_SIRUS_NODE_SIGNATURE']??'')),
        static function(string $id)use($keyDir):string{
            $path=$keyDir.'/'.$id.'.key';
            if(is_link($path) || !is_file($path) || (fileperms($path)&0077)!==0)
                return '';
            return (string)file_get_contents($path);
        });
    http_response_code($result['state']==='queued'?202:200);
    echo json_encode($result,JSON_THROW_ON_ERROR);
}catch(Throwable $e){
    error_log('Sirus work request rejected: '.get_class($e));
    http_response_code(401);
    echo '{"ok":false,"error":"Work request rejected"}';
}
