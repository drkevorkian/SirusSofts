<?php
declare(strict_types=1);
if(!defined('SIRUS_CMS_BOOTSTRAPPED')){http_response_code(404);exit;}
require_once __DIR__.'/access.php';

function cms_source_path_ok(string $path): bool {
    if(strlen($path)>400 || str_contains($path,"\0") || str_contains($path,'\\') ||
       str_contains($path,'//') || str_starts_with($path,'/') ||
       str_ends_with($path,'/'))return false;
    if($path==='')return true;
    foreach(explode('/',$path) as $segment){
        if($segment==='.' || $segment==='..' || $segment==='' ||
           !preg_match('/^[A-Za-z0-9._@+ -]{1,120}$/D',$segment))return false;
    }
    return true;
}
function cms_source_fetch(int $repoId,string $path): array {
    if($repoId<1 || !cms_source_path_ok($path))
        throw new InvalidArgumentException('Invalid source path.');
    $socket='/run/sirussofts-source/bridge.sock';
    $stream=@stream_socket_client('unix://'.$socket,$errno,$message,3,STREAM_CLIENT_CONNECT);
    if(!is_resource($stream))
        throw new RuntimeException('Source browser service has not been enabled by the server operator.');
    try {
        stream_set_timeout($stream,20);
        fwrite($stream,json_encode(['repo_id'=>$repoId,'path'=>$path],JSON_THROW_ON_ERROR)."\n");
        $data='';
        while(!feof($stream)){
            $chunk=fread($stream,32768);
            if($chunk===false || $chunk==='')break;
            $data.=$chunk;
            if(strlen($data)>850000)throw new RuntimeException('Source response exceeded limit.');
            if(str_contains($data,"\n"))break;
        }
        if(!str_contains($data,"\n"))throw new RuntimeException('Incomplete source browser response.');
        $result=json_decode(trim($data),true,16,JSON_THROW_ON_ERROR);
        if(!is_array($result)||($result['ok']??false)!==true)
            throw new InvalidArgumentException((string)($result['error']??'Source not available.'));
        return $result;
    }finally{fclose($stream);}
}
function cms_source_page(PDO $db,?string $error): void {
    if(!cms_authenticated($db)){http_response_code(403);exit('Sign in required.');}
    $records=cms_granted_repositories($db);
    $requested=$_GET['repo']??null;
    $repoId=null;
    if($requested!==null){
        if(!is_string($requested) || !preg_match('/^[1-9][0-9]{0,15}$/D',$requested))
            {http_response_code(404);exit('Repository not found.');}
        $repoId=(int)$requested;
        if(!cms_can_view_source($db,$repoId)){
            http_response_code(403);exit('Source access denied.');
        }
    }
    $path=$_GET['path']??'';
    if(!is_string($path)||!cms_source_path_ok($path)){
        http_response_code(404);exit('Invalid source path.');
    }
    $repoName='';
    foreach($records as $record){
        if((int)$record['github_id']===$repoId)$repoName=(string)$record['full_name'];
    }
    $result=null;
    if($repoId!==null){
        try{$result=cms_source_fetch($repoId,$path);}
        catch(InvalidArgumentException|RuntimeException $e){$error=$e->getMessage();}
    }
    cms_layout_start($db,'Source browser');
    ?><section class="page-intro"><div class="eyebrow">RESTRICTED SOURCE ACCESS</div>
      <h1>Source browser</h1><p>Read-only repository files. Downloading or executing code is not enabled.</p>
      <p><?php if(cms_is_admin()):?><a href="/admin" class="inline-link">Admin dashboard</a>
      <?php else:?><a href="/admin/account" class="inline-link">My account</a><?php endif;?></p>
    </section>
    <div class="admin-panel">
      <h2>Repositories available to you</h2>
      <?php if(!$records):?><p>No repository permissions have been assigned.</p><?php endif;?>
      <div class="source-repos">
      <?php foreach($records as $record):?>
        <a class="btn small" href="/admin/source?repo=<?=(int)$record['github_id']?>"><?=cms_esc($record['full_name'])?></a>
      <?php endforeach;?>
      </div>
    </div>
    <?php if($error):?><div class="form-error"><?=cms_esc($error)?></div><?php endif;?>
    <?php if($repoId!==null):?>
      <section class="admin-panel">
        <h2><?=cms_esc($repoName)?> <small><?=cms_esc($path?:'/')?></small></h2>
        <?php if($path!==''):
          $parts=explode('/',$path);array_pop($parts);
          $parent=implode('/',$parts);?>
          <a class="inline-link" href="/admin/source?repo=<?=$repoId?>&amp;path=<?=rawurlencode($parent)?>">← Parent directory</a>
        <?php endif;?>
        <?php if($result && ($result['kind']??'')==='directory'):?>
          <div class="table-scroll"><table><thead><tr><th>Name</th><th>Type</th><th>Bytes</th></tr></thead><tbody>
          <?php foreach($result['entries']??[] as $item):?>
            <tr><td><a href="/admin/source?repo=<?=$repoId?>&amp;path=<?=rawurlencode((string)$item['path'])?>">
                <?=cms_esc($item['name'])?></a></td>
                <td><?=cms_esc($item['type'])?></td><td><?=(int)$item['size']?></td></tr>
          <?php endforeach;?>
          </tbody></table></div>
        <?php elseif($result && ($result['kind']??'')==='file'):?>
          <p class="muted small">Text preview limited to 192 KiB. HTML and scripts are escaped, not executed.</p>
          <pre class="source-content"><code><?=cms_esc((string)$result['content'])?></code></pre>
        <?php endif;?>
      </section>
    <?php endif;?>
    <?php cms_layout_end($db);
}
