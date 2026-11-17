<?php
declare(strict_types=1);
define('SIRUS_CMS_BOOTSTRAPPED',true);
require_once __DIR__.'/../app/ai_gentoo.php';

function check_gentoo(bool $condition,string $reason):void {
    if(!$condition)throw new RuntimeException($reason);
}
$_GET=['pdf_state'=>'ready','pdf_search'=>'BRP HD11','pdf_page'=>'12'];
[$state,$q,$page]=cms_gentoo_pdf_filters();
check_gentoo($state==='ready'&&$q==='BRP HD11'&&$page===12,'Valid filters');
check_gentoo(str_contains(cms_gentoo_pdf_query_url($state,$q,$page),
                          'pdf_search=BRP%20HD11'),'Query is URL encoded');
$_GET=['pdf_state'=>'../../bad','pdf_search'=>"test\ninjected",'pdf_page'=>'-1'];
[$state,$q,$page]=cms_gentoo_pdf_filters();
check_gentoo($state===''&&$q===''&&$page===1,'Invalid filters rejected');
check_gentoo(cms_gentoo_pdf_format_bytes(1073741824)==='1.00 GiB',
             'Bytes formatted');
try {
    cms_gentoo_pdf_call('execute',['command'=>'rm -rf /']);
    throw new RuntimeException('Arbitrary operation incorrectly accepted');
} catch(InvalidArgumentException $expected) {}
check_gentoo(cms_gentoo_pdf_bulk_confirmation('APPROVE ALL READY','yes')==='APPROVE ALL READY',
             'Bulk confirmation accepted');
foreach([['APPROVE ALL READY',null],['NO','yes'],[null,'yes']] as $values){
    try {
        cms_gentoo_pdf_bulk_confirmation($values[0],$values[1]);
        throw new RuntimeException('Missing bulk confirmation accepted');
    }catch(InvalidArgumentException $expected){}
}
echo "PASS: Gentoo PDF CMS admin input routing helpers\n";
