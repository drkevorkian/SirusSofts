<?php
declare(strict_types=1);
/**
 * SirusSofts public homepage / project router.
 * This file belongs at CMS/index.php, alongside assets/, app/, bin/.
 * On Ubuntu 24.04 with Hestia: deploy CONTENTS of CMS/ into the Hestia
 * site's public_html using deploy/ubuntu24/06-deploy-cms-hestia.sh.
 * Hestia-owned Nginx security includes block access to app/, bin/, tests/.
 * Only the SQLite database lives outside the public web root.
 */
$private=__DIR__.'/app';
if(!is_file($private.'/core.php')){
    http_response_code(503);
    header('Content-Type: text/plain; charset=utf-8');
    exit('SirusSofts CMS files are incomplete. Keep index.php and app/ together.');
}
define('SIRUS_CMS_BOOTSTRAPPED',true);
require $private.'/core.php';
require $private.'/site.php';
cms_headers();
try {$db=cms_db();}
catch(Throwable $error){
    error_log('SirusSofts CMS unavailable: '.get_class($error));
    http_response_code(503);
    header('Content-Type: text/plain; charset=utf-8');
    exit('SirusSofts CMS setup is required or its database is unavailable. See deploy/ubuntu24/README.md and initialize as the unprivileged Hestia website user.');
}
$host=cms_request_host();
$subdomain=cms_project_host_slug($host);
if($subdomain===''){
    http_response_code(421);
    exit('Unknown SirusSofts hostname.');
}
$path=cms_request_path();
if($subdomain!==null) {
    // A Hestia-managed content subdomain may map to this shared CMS docroot.
    // Full PHP projects (such as cPoint) get their own document root, so their
    // API and assets work natively without a URL redirect or duplicate DB.
    if($path!=='/'&&$path!=='/index.php'){cms_public_not_found($db);exit;}
    $project=cms_public_directory_project($db,$subdomain);
    if(!$project){cms_public_not_found($db);exit;}
    cms_public_project($db,$project);exit;
}
// Public root website routes; only /admin is protected.
if($path==='/'){cms_public_home($db);exit;}
if($path==='/projects'){cms_public_catalog($db);exit;}
if($path==='/about'){cms_public_about($db);exit;}
if($path==='/admin'||str_starts_with($path,'/admin/')){
    require $private.'/admin.php';
    cms_admin_run($db,$path);
}
if(preg_match('#^/pages/([a-z][a-z0-9-]{1,39})$#D',$path,$match)){
    $page=cms_page($db,$match[1],true);
    if($page){cms_public_page($db,$page);exit;}
}
if(preg_match('#^/projects/([a-z][a-z0-9-]{1,39})$#D',$path,$match)||
   preg_match('#^/([a-z][a-z0-9-]{1,39})$#D',$path,$match)){
    $project=cms_public_directory_project($db,$match[1]);
    if($project){cms_public_project($db,$project);exit;}
}
cms_public_not_found($db);
