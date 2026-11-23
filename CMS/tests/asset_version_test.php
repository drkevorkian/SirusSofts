<?php
declare(strict_types=1);
define('SIRUS_CMS_BOOTSTRAPPED', true);
require dirname(__DIR__).'/app/core.php';
require dirname(__DIR__).'/app/site.php';
foreach(['cms.css','cms.js','live.js','ai-panels.js','gentoo-pdf-admin.js'] as $name){
    $actual=cms_asset_url($name);
    $expected='/assets/'.$name.'?v='.substr(
        hash_file('sha256',dirname(__DIR__).'/assets/'.$name),0,16);
    if($actual!==$expected)throw new RuntimeException('Asset fingerprint mismatch: '.$name);
}
try{
    cms_asset_url('../app/core.php');
    throw new RuntimeException('Unsafe asset accepted');
}catch(InvalidArgumentException $expected){}
echo "PASS: content-versioned CMS assets and restricted asset names\n";
