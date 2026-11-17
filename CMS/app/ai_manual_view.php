<?php
declare(strict_types=1);
if(!defined('SIRUS_CMS_BOOTSTRAPPED')){http_response_code(404);exit;}
require_once __DIR__.'/ai_gentoo.php';

/** Approved Gentoo manuals are private administrator-only reference assets. */
function cms_ai_manual_sha(string $sha):string {
    if(!preg_match('/^(?:[a-f0-9]{12}|[a-f0-9]{64})$/D',$sha))
        throw new InvalidArgumentException('Invalid approved manual identifier.');
    return $sha;
}
function cms_ai_manual_page_number(string $page):int {
    if(!preg_match('/^[1-9][0-9]{0,4}$/D',$page) || (int)$page>20000)
        throw new InvalidArgumentException('Invalid manual page number.');
    return (int)$page;
}
function cms_ai_manual_size(mixed $size):string {
    if(!is_string($size)||!in_array($size,['preview','full'],true))
        throw new InvalidArgumentException('Invalid manual page size.');
    return $size;
}
function cms_ai_manual_metadata(string $sha):array {
    $sha=cms_ai_manual_sha($sha);
    $result=cms_gentoo_pdf_call('manual_meta',['sha256'=>$sha]);
    if(!is_string($result['sha256']??null)
       || !preg_match('/^[a-f0-9]{64}$/D',$result['sha256'])
       || !str_starts_with($result['sha256'],$sha)
       || !is_string($result['title']??null)
       || strlen($result['title'])>180
       || !is_int($result['pages']??null)
       || $result['pages']<1||$result['pages']>20000)
        throw new RuntimeException('Invalid approved Gentoo manual metadata.');
    return ['sha256'=>$result['sha256'],'title'=>$result['title'],
            'pages'=>$result['pages'],'view_only'=>true];
}
function cms_ai_manual_image_response(string $sha,int $page,string $size):never {
    $sha=cms_ai_manual_sha($sha);
    if($page<1||$page>20000)throw new InvalidArgumentException('Invalid manual page.');
    $size=cms_ai_manual_size($size);
    $result=cms_gentoo_pdf_call('manual_page',[
        'sha256'=>$sha,'page'=>$page,'size'=>$size
    ]);
    $digest=$result['sha256']??null;
    $encoded=$result['base64']??null;
    if(!is_string($digest)||!preg_match('/^[a-f0-9]{64}$/D',$digest)
       || !str_starts_with($digest,$sha)
       || ($result['mime']??null)!=='image/jpeg'
       || ($result['page']??null)!==$page
       || !is_string($encoded)||strlen($encoded)>3300000)
        throw new RuntimeException('Invalid Gentoo manual preview response.');
    $image=base64_decode($encoded,true);
    if(!is_string($image)||strlen($image)<200||strlen($image)>2400000
       || !str_starts_with($image,"\xff\xd8\xff")||!str_ends_with($image,"\xff\xd9"))
        throw new RuntimeException('Invalid Gentoo page image.');
    http_response_code(200);
    header('Content-Type: image/jpeg');
    header('Content-Length: '.strlen($image));
    header('Cache-Control: private, no-store, max-age=0');
    header('Pragma: no-cache');
    header('X-Content-Type-Options: nosniff');
    header('X-Robots-Tag: noindex, noimageindex, noarchive');
    header('Referrer-Policy: no-referrer');
    header('Content-Disposition: inline; filename="manual-page.jpg"');
    header('Vary: Cookie');
    echo $image;
    exit;
}
