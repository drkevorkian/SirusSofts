<?php
declare(strict_types=1);
define('SIRUS_CMS_BOOTSTRAPPED',true);
require_once __DIR__.'/../app/core.php';
require_once __DIR__.'/../app/ai_manual_view.php';
function valid_manual(bool $pass,string $note):void {
    if(!$pass)throw new RuntimeException('Manual image viewer test: '.$note);
}
valid_manual(cms_ai_manual_sha(str_repeat('a',64))===str_repeat('a',64),'SHA 64 accepted');
valid_manual(cms_ai_manual_sha(str_repeat('a',12))===str_repeat('a',12),'legacy SHA prefix accepted');
valid_manual(cms_ai_manual_page_number('298')===298,'valid page');
valid_manual(cms_ai_manual_size('preview')==='preview','preview');
valid_manual(cms_ai_manual_size('full')==='full','full');
foreach(['../secret','f','A',str_repeat('a',13),str_repeat('x',64)] as $digest){
    try{cms_ai_manual_sha($digest);throw new RuntimeException('Invalid SHA accepted');}
    catch(InvalidArgumentException $expected){}
}
foreach(['0','-5','01','20001','0x10','../../secret'] as $number){
    try{cms_ai_manual_page_number($number);throw new RuntimeException('Invalid page accepted');}
    catch(InvalidArgumentException $expected){}
}
foreach(['pdf','../full','inline','raw'] as $size){
    try{cms_ai_manual_size($size);throw new RuntimeException('Unsupported size accepted');}
    catch(InvalidArgumentException $expected){}
}
try{
    cms_gentoo_pdf_call('download_pdf',['sha256'=>str_repeat('a',64)]);
    throw new RuntimeException('Raw PDF command accepted');
}catch(InvalidArgumentException $expected){}
echo "PASS: admin-only image viewer input guards; no PDF download operation\n";
