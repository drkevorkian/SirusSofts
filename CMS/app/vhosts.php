<?php
declare(strict_types=1);
if(!defined('SIRUS_CMS_BOOTSTRAPPED')){http_response_code(404);exit;}
/**
 * Administrator-only project virtual-host editor.
 * This file does not call a shell or accept configuration snippets or file paths.
 * A separate root-owned, restricted AF_UNIX agent handles Plesk provisioning.
 */
function cms_vhost_request(string $action,string $slug,string $kind): array {
    $socket='/run/sirus-vhost/agent.sock';
    $connection=@stream_socket_client('unix://'.$socket,$errno,$error,5,STREAM_CLIENT_CONNECT);
    if(!is_resource($connection))
        throw new InvalidArgumentException('The Ubuntu virtual-host agent is unavailable or the website user lacks socket permission.');
    try {
        stream_set_timeout($connection,12);
        $payload=json_encode(['action'=>$action,'slug'=>$slug,'kind'=>$kind],JSON_THROW_ON_ERROR)."\n";
        $written=0;
        while($written<strlen($payload)){
            $count=fwrite($connection,substr($payload,$written));
            if($count===false||$count===0)throw new RuntimeException('Unable to submit virtual-host request.');
            $written+=$count;
        }
        $line=fgets($connection,8192);
        if($line===false||!str_ends_with($line,"\n"))
            throw new RuntimeException('Virtual-host agent did not return a complete response.');
        $response=json_decode($line,true,16,JSON_THROW_ON_ERROR);
        if(!is_array($response)||!array_key_exists('ok',$response))
            throw new RuntimeException('Virtual-host agent returned an invalid response.');
        if($response['ok']!==true)
            throw new InvalidArgumentException((string)($response['error']??'Virtual-host operation refused.'));
        if(!isset($response['result'])||!is_array($response['result']))
            throw new RuntimeException('Virtual-host operation did not return a result.');
        return $response['result'];
    }finally{fclose($connection);}
}
function cms_vhost_post(PDO $db,string $action): never {
    $slug=(string)($_POST['slug']??'');
    if(!cms_safe_slug($slug))throw new InvalidArgumentException('Invalid project slug.');
    $st=$db->prepare('SELECT slug,kind,state FROM cms_projects WHERE slug=?');
    $st->execute([$slug]);
    $project=$st->fetch(PDO::FETCH_ASSOC);
    if(!$project)throw new InvalidArgumentException('The selected project is not in the CMS.');
    if($action==='vhost_apply'){
        if($project['state']!=='published')
            throw new InvalidArgumentException('Publish the project listing before provisioning its virtual host.');
        $confirmation=(string)($_POST['confirmation']??'');
        if(!hash_equals('APPLY '.$slug,$confirmation))
            throw new InvalidArgumentException('Enter APPLY '.$slug.' to confirm this server change.');
    }
    $result=cms_vhost_request($action==='vhost_apply'?'apply':'preview',
                              $slug,(string)$project['kind']);
    $_SESSION['cms_vhost_result']=$result;
    if($action==='vhost_apply'){
        cms_audit($db,'vhost-'.$result['status'],$slug);
        cms_flash('Plesk operation: '.$result['status'].' for '.$result['hostname'].'. Check DNS and HTTPS certificates.');
    }else cms_flash('Virtual-host preview generated. No server changes made.');
    cms_go('/admin/hosting');
}
function cms_vhost_page(PDO $db,?string $error): void {
    cms_admin_shell_start($db,'Virtual hosts');
    $flash=cms_take_flash();
    if($flash)echo '<div class="flash-message">'.cms_esc($flash['message']??'').'</div>';
    if($error)echo '<div class="form-error">'.cms_esc($error).'</div>';
    $preview=$_SESSION['cms_vhost_result']??null;
    unset($_SESSION['cms_vhost_result']);
    if(is_array($preview)){
        echo '<div class="admin-panel"><h2>Latest routing result</h2><p><strong>'.cms_esc($preview['status']??'').'</strong> — '.cms_esc($preview['hostname']??'').'</p>';
        echo '<p>Subdomain: <code>'.cms_esc($preview['subdomain_url']??'').'</code><br>';
        echo 'Directory URL: <code>'.cms_esc($preview['directory_url']??'').'</code><br>';
        echo 'Plesk document root: <code>'.cms_esc($preview['www_root']??'').'</code></p>';
        if(isset($preview['note']))echo '<p>'.cms_esc($preview['note']).'</p>';
        echo '</div>';
    }
    ?><div class="admin-panel"><h2>Project routing</h2>
      <p>Every published project can use both <code>https://slug.sirussofts.com</code> and
      <code>https://sirussofts.com/slug</code>. A content project uses the shared CMS;
      an application uses its own existing <code>httpdocs/slug/</code> directory.</p>
      <p>Preview checks target paths. Apply uses a limited Ubuntu service and Plesk's
      native subdomain commands. No raw nginx rules, arbitrary document-root paths,
      server restart, or GitHub source publication is allowed here.</p>
      <p>DNS records and HTTPS certificates still require validation. This editor
      never overwrites an unmanaged existing subdomain.</p></div>
      <div class="admin-panel table-scroll"><table><thead><tr>
      <th>Project</th><th>Directory route</th><th>Subdomain</th><th>Provisioning</th>
      </tr></thead><tbody>
    <?php foreach(cms_projects($db,false) as $project):
        $slug=(string)$project['slug']; ?>
      <tr><td><strong><?=cms_esc($project['title'])?></strong><br><small><?=cms_esc($project['kind'])?> / <?=cms_esc($project['state'])?></small></td>
      <td><code>/<?=cms_esc($slug)?></code></td>
      <td><code><?=cms_esc($slug)?>.sirussofts.com</code></td>
      <td><form method="post" action="/admin/hosting">
          <input type="hidden" name="csrf" value="<?=cms_esc(cms_csrf())?>">
          <input type="hidden" name="slug" value="<?=cms_esc($slug)?>">
          <button class="btn small" name="action" value="vhost_preview" type="submit">Preview</button>
          <?php if($project['state']==='published'):?>
          <label>Type <code>APPLY <?=cms_esc($slug)?></code> to provision
          <input name="confirmation" maxlength="50" autocomplete="off" placeholder="APPLY <?=cms_esc($slug)?>"></label>
          <button class="btn small primary" name="action" value="vhost_apply" type="submit">Apply</button>
          <?php else:?><small>Publish first to enable Apply</small><?php endif;?>
        </form></td></tr>
    <?php endforeach;?></tbody></table></div>
    <?php cms_admin_shell_end($db);
}
