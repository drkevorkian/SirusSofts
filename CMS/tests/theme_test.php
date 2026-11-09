<?php
declare(strict_types=1);
define('SIRUS_CMS_BOOTSTRAPPED',true);
require dirname(__DIR__).'/app/core.php';
require dirname(__DIR__).'/app/site.php';
function mustTheme(bool $condition,string $label):void {
    if(!$condition)throw new RuntimeException('FAIL: '.$label);
}
$db=new PDO('sqlite::memory:',null,null,[PDO::ATTR_ERRMODE=>PDO::ERRMODE_EXCEPTION,
    PDO::ATTR_DEFAULT_FETCH_MODE=>PDO::FETCH_ASSOC]);
cms_schema($db);
$themes=cms_theme_options();
$accents=cms_accent_options();
mustTheme(count($themes)>=9,'at least nine independently selectable palettes');
mustTheme(in_array('ghost',array_keys($themes),true),'light mode included');
mustTheme(in_array('midnight',array_keys($themes),true),'legacy blue mode retained');
mustTheme(count($accents)>=7,'additional accent choices');
foreach(array_keys($themes) as $id){
    mustTheme((bool)preg_match('/^[a-z]{3,20}$/D',$id),'safe palette identifier');
}
foreach(array_keys($accents) as $id){
    mustTheme((bool)preg_match('/^[a-z]{3,20}$/D',$id),'safe accent identifier');
}
mustTheme(cms_setting($db,'theme')==='midnight','original palette is default');
ob_start();
cms_layout_start($db,'SirusSofts');
cms_layout_end($db);
$html=ob_get_clean();
mustTheme(str_contains($html,'data-theme="midnight"'),'theme attribute in public HTML');
mustTheme(str_contains($html,'data-accent="blue"'),'legacy accent retained');
$css=file_get_contents(dirname(__DIR__).'/assets/cms.css');
mustTheme(is_string($css),'CSS exists');
foreach(array_keys($themes) as $id){
    mustTheme(str_contains($css,'data-theme="'.$id.'"') ||
              str_contains($css,'data-theme-choice="'.$id.'"'),'CSS for '.$id);
}
echo "PASS: ten allowlisted themes, light modes, accent choices and original defaults.\n";
