<?php
declare(strict_types=1);
/**
 * Operator-only Plesk subdomain helper.
 *
 * Dry-run: php bin/provision-subdomain.php project-slug
 * Apply:   sudo php bin/provision-subdomain.php project-slug --apply
 *
 * Never call this tool from an HTTP request or from the public CMS.
 * DNS A/AAAA records and TLS certificates are separate operator tasks.
 */
if(PHP_SAPI!=='cli'){http_response_code(404);exit;}
define('SIRUS_CMS_BOOTSTRAPPED',true);
require __DIR__.'/../app/core.php';
$slug=(string)($argv[1]??'');
$apply=in_array('--apply',$argv,true);
if(!cms_safe_slug($slug)){
    fwrite(STDERR,"Usage: php provision-subdomain.php <safe-project-slug> [--apply]\n");
    exit(2);
}
$db=cms_db();
$project=cms_project($db,$slug,false);
if(!$project){fwrite(STDERR,"Project is not registered in the CMS.\n");exit(2);}
$docroot=cms_site_root();
$vhost=dirname(cms_private_directory(),2);
if(!str_starts_with($docroot,rtrim($vhost,'/').'/')){
    fwrite(STDERR,"CMS document root is not inside this Plesk vhost.\n");exit(2);
}
$relative='/'.trim(substr($docroot,strlen($vhost)),'/');
if($project['kind']==='app')$relative.='/'.$slug;
if($project['kind']==='app'&&!is_file($docroot.'/'.$slug.'/index.php')){
    fwrite(STDERR,"Application entry file missing under CMS root: ".$slug."/index.php\n");
    fwrite(STDERR,"Deploy the trusted application before provisioning its subdomain.\n");
    exit(2);
}
$hostname=$slug.'.'.SIRUS_CMS_DOMAIN;
echo "Subdomain: ".$hostname."\n";
echo "Document root: ".$relative."\n";
echo "Directory URL: https://".SIRUS_CMS_DOMAIN."/".$slug."/\n";
echo "This does not create DNS records, enable HTTPS or install an application.\n";
$cmd=['/usr/sbin/plesk','bin','subdomain','--create',$slug,
      '-domain',SIRUS_CMS_DOMAIN,'-www-root',$relative];
if(!$apply){
    echo "DRY RUN: ".implode(' ',array_map('escapeshellarg',$cmd))."\n";
    echo "Re-run with --apply as root after backups and DNS/Plesk review.\n";
    exit(0);
}
if(function_exists('posix_geteuid') && posix_geteuid()!==0){
    fwrite(STDERR,"--apply requires root via SSH, not the PHP website process.\n");exit(2);
}
if(!is_executable('/usr/sbin/plesk')){
    fwrite(STDERR,"Plesk CLI is unavailable at /usr/sbin/plesk; provision manually in Plesk.\n");
    exit(2);
}
echo "Type CREATE ".$hostname." to apply: ";
$confirmation=trim((string)fgets(STDIN));
if(!hash_equals('CREATE '.$hostname,$confirmation)){echo "Cancelled.\n";exit(2);}
$descriptors=[0=>['pipe','r'],1=>['pipe','w'],2=>['pipe','w']];
$process=proc_open($cmd,$descriptors,$pipes,null,null,['bypass_shell'=>true]);
if(!is_resource($process)){fwrite(STDERR,"Plesk process could not start.\n");exit(1);}
fclose($pipes[0]);
$out=stream_get_contents($pipes[1],8192);fclose($pipes[1]);
$err=stream_get_contents($pipes[2],8192);fclose($pipes[2]);
$status=proc_close($process);
echo (string)$out;
if($status!==0){fwrite(STDERR,(string)$err);exit($status);}
echo "\nPlesk host created. NEXT: configure its DNS and TLS certificate manually.\n";
echo "Verify both URLs serve the same application and shared private data.\n";
