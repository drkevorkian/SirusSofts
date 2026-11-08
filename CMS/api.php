<?php
declare(strict_types=1);
/**
 * Backward compatible sensor upload endpoint.
 * Older Android clients post to https://sirussofts.com/api.php?r=...
 * Keep that route alive even though cPoint's website now lives under /cpoint.
 */
$app=__DIR__.'/cpoint/api.php';
if(!is_file($app)){
    http_response_code(503);
    header('Content-Type: application/json; charset=utf-8');
    exit('{"ok":false,"error":"cPoint service is being installed"}');
}
require $app;
