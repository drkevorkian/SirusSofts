<?php
declare(strict_types=1);
if(PHP_SAPI!=='cli'){http_response_code(404);exit;}
define('SIRUS_CMS_BOOTSTRAPPED',true);
require dirname(__DIR__).'/app/gpt_portal.php';
function gpt_check(bool $ok,string $message):void {
    if(!$ok){fwrite(STDERR,"FAIL: ".$message."\n");exit(1);}
}
gpt_check(cms_gpt_route('/GPT'),'Exact uppercase route is recognized');
gpt_check(cms_gpt_route('/GPT/api/version'),'Nested uppercase route is recognized');
foreach(['/gpt','/Gpt','/GPTevil','/projects','/admin/GPT'] as $path)
    gpt_check(!cms_gpt_route($path),'Unrelated and mixed-case paths rejected: '.$path);
$html=cms_gpt_public_html();
gpt_check(str_contains($html,'GPT workspace'),'Holding page rendered');
gpt_check(str_contains($html,'Remote file operations are not enabled'),
    'No misleading claim of remote access');
gpt_check(!str_contains($html,'type="password"') && !str_contains($html,'source=chatGPT'),
    'No credential or query-string based authentication suggested');
$index=file_get_contents(dirname(__DIR__).'/index.php');
gpt_check(is_string($index) &&
    strpos($index,'cms_gpt_run($gptPath)') < strpos($index,'try {$db=cms_db();}'),
    'GPT runs before database bootstrap');
gpt_check(strpos((string)$index,"require \$private.'/gpt_portal.php';")!==false,
    'GPT handler is wired to CMS entrypoint');
echo "PASS: isolated uppercase GPT holding page; API remains disabled\n";
