<?php
declare(strict_types=1);
if(!defined('SIRUS_CMS_BOOTSTRAPPED')){http_response_code(404);exit;}

/**
 * Ubuntu-hosted /GPT holding route. This intentionally has NO CMS database,
 * shell, filesystem editor, SSH, or credential handling. Access is disabled
 * until the separate short-lived, least-privilege API passes security review.
 */
function cms_gpt_route(string $path):bool {
    return $path==='/GPT'||str_starts_with($path,'/GPT/');
}
function cms_gpt_public_html():string {
    return '<!doctype html><html lang="en"><head><meta charset="utf-8">'.
        '<meta name="viewport" content="width=device-width,initial-scale=1">'.
        '<meta name="robots" content="noindex,nofollow">'.
        '<title>GPT · SirusSofts</title>'.
        '<link rel="stylesheet" href="/assets/cms.css"></head><body>'.
        '<main class="shell page-main"><section class="page-intro">'.
        '<div class="eyebrow">SIRUSSOFTS / GPT</div>'.
        '<h1>GPT workspace</h1>'.
        '<p>This reserved workspace is not open for public access.</p>'.
        '<p>Remote file operations are not enabled.</p>'.
        '<p><a href="/">Return to SirusSofts</a></p>'.
        '</section></main></body></html>';
}
function cms_gpt_run(string $path):never {
    $method=strtoupper((string)($_SERVER['REQUEST_METHOD']??'GET'));
    header('Cache-Control: no-store, private');
    header('Referrer-Policy: no-referrer');
    if($path==='/GPT') {
        if($method!=='GET' && $method!=='HEAD') {
            header('Allow: GET, HEAD');
            http_response_code(405);
            exit;
        }
        header('Content-Type: text/html; charset=utf-8');
        if($method==='GET')echo cms_gpt_public_html();
        exit;
    }
    // Fail closed for /GPT/api/* until token issuance, authorization,
    // per-request HMAC, nonces, revocation, audit and file sandbox exist.
    header('Content-Type: application/json; charset=utf-8');
    if($method==='GET'||$method==='HEAD'||$method==='POST'||
       $method==='PUT'||$method==='PATCH'||$method==='DELETE') {
        http_response_code(403);
        if($method!=='HEAD')echo '{"ok":false,"error":"GPT API is not enabled"}';
        exit;
    }
    http_response_code(405);
    header('Allow: GET, HEAD, POST, PUT, PATCH, DELETE');
    exit;
}
