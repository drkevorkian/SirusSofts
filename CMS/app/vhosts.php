<?php
declare(strict_types=1);
if(!defined('SIRUS_CMS_BOOTSTRAPPED')){http_response_code(404);exit;}
require_once __DIR__.'/hosting_config.php';
/**
 * Administrator-only project virtual-host editor.
 * This file does not call a shell or accept configuration snippets or file paths.
 * A separate restricted root-owned AF_UNIX agent handles Nginx page-only vhosts.
 */
function cms_vhost_request(string $action,string $slug,string $kind): array {
    $socket='/run/sirus-nginx/agent.sock';
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
    if($action==='vhost_settings'){
        $saved=cms_hosting_save($db,$slug,[
            'assigned_node'=>$_POST['assigned_node']??null,
            'database_backend'=>$_POST['database_backend']??null,
            'source_repository'=>$_POST['source_repository']??null,
            'source_ref'=>$_POST['source_ref']??null,
            'release_policy'=>$_POST['release_policy']??null,
        ]);
        cms_audit($db,'hosting-config',$slug);
        cms_flash('Planned hosting preferences saved; no deployment or database migration performed.');
        cms_go('/admin/hosting');
    }

    // Hestia owns Nginx/Apache vhosts. The legacy standalone Nginx agent is
    // incompatible even if an old socket is accidentally present.
    if(is_file('/etc/hestiacp/hestia.conf')){
        throw new InvalidArgumentException(
            'On Hestia, use the hosting panel for DNS, TLS and vhost deployment. This CMS only stores planning metadata.');
    }
    if($action==='vhost_apply'){
        if($project['state']!=='published')
            throw new InvalidArgumentException('Publish the project listing before provisioning its virtual host.');
        $confirmation=(string)($_POST['confirmation']??'');
        if(!hash_equals('APPLY '.$slug,$confirmation))
            throw new InvalidArgumentException('Enter APPLY '.$slug.' to confirm this server change.');
    }
    if($action==='vhost_rollback'){
        $confirmation=(string)($_POST['confirmation']??'');
        if(!hash_equals('ROLLBACK '.$slug,$confirmation))
            throw new InvalidArgumentException('Enter ROLLBACK '.$slug.' to confirm removing only the managed vhost.');
    }
    $operation=match($action){
        'vhost_apply'=>'apply',
        'vhost_rollback'=>'rollback',
        default=>'preview'
    };
    $result=cms_vhost_request($operation,$slug,(string)$project['kind']);
    $_SESSION['cms_vhost_result']=$result;
    if($action==='vhost_apply'||$action==='vhost_rollback'){
        cms_audit($db,'vhost-'.$result['status'],$slug);
        cms_flash('Restricted Nginx vhost operation: '.$result['status'].' for '.$result['hostname'].'.');
    }else cms_flash('Nginx vhost preview generated. No server changes made.');
    cms_go('/admin/hosting');
}
function cms_vhost_page(PDO $db,?string $error): void {
    $hestia=is_file('/etc/hestiacp/hestia.conf');
    cms_admin_shell_start($db,'Virtual hosts');
    $flash=cms_take_flash();
    if($flash)echo '<div class="flash-message">'.cms_esc($flash['message']??'').'</div>';
    if($error)echo '<div class="form-error">'.cms_esc($error).'</div>';
    $preview=$_SESSION['cms_vhost_result']??null;
    unset($_SESSION['cms_vhost_result']);
    if(is_array($preview)){
        echo '<div class="admin-panel"><h2>Latest routing result</h2><p><strong>'.cms_esc($preview['status']??'').'</strong> — '.cms_esc($preview['hostname']??'').'</p>';
        echo '<p>Subdomain: <code>'.cms_esc($preview['subdomain_url']??'').'</code><br>';
        echo 'Alternate subdomain: <code>'.cms_esc($preview['secondary_subdomain_url']??'').'</code><br>';
        echo 'Directory URL: <code>'.cms_esc($preview['directory_url']??'').'</code><br>';
        echo 'Alternate directory: <code>'.cms_esc($preview['secondary_directory_url']??'').'</code><br>';
        echo 'Nginx document root: <code>'.cms_esc($preview['www_root']??'').'</code></p>';
        if(isset($preview['note']))echo '<p>'.cms_esc($preview['note']).'</p>';
        echo '</div>';
    }
    ?><?php if($hestia): ?>
      <div class="admin-panel"><h2>HestiaCP controls live hosting</h2>
      <p>This page stores project hosting plans, not live virtual hosts.
      The old standalone Nginx agent is disabled on Hestia; enabling it would
      conflict with Hestia-owned Nginx and Apache configurations.</p>
      <p>For a future project domain, create a separate Hestia-managed website
      or reviewed reverse-proxy template, configure DNS and TLS, and verify
      application health before declaring it live.</p></div>
    <?php endif; ?>
    <div class="admin-panel"><h2>Project routing</h2>
      <p>CMS pages can use both <code>project.sirussofts.org</code> and
      <code>project.sirussofts.com</code>, plus <code>/project</code> on either root.
      Applying a content-page vhost requires an already published project and
      a valid TLS certificate covering both project hostnames.</p>
      <p>The legacy restricted Nginx service is for non-Hestia hosts only.
      Hestia-native application routing must be reviewed and deployed separately.
      The CMS cannot execute shell commands or install server configuration.</p>
      <p><strong>Application vhosts require an explicitly approved, healthy loopback HTTP runtime.</strong>
      cPoint, WorldCom and Sentinel Trader remain protected. Planned hosting settings only record intent and do not deploy apps, change node assignments, switch databases or publish projects. DNS and TLS
      provisioning are external administrator tasks; this screen never publishes
      source code, databases or credentials.</p></div>
      <div class="admin-panel table-scroll"><table><thead><tr>
      <th>Project</th><th>Directory route</th><th>Subdomain</th><th>Deployment settings</th><th>Provisioning</th>
      </tr></thead><tbody>
    <?php foreach(cms_projects($db,false) as $project):
        $slug=(string)$project['slug'];
        $configuration=cms_hosting_config($db,$slug); ?>
      <tr><td><strong><?=cms_esc($project['title'])?></strong><br><small><?=cms_esc($project['kind'])?> / <?=cms_esc($project['state'])?></small></td>
      <td><code>/<?=cms_esc($slug)?></code></td>
      <td><code><?=cms_esc($slug)?>.sirussofts.org</code><br><code><?=cms_esc($slug)?>.sirussofts.com</code></td>
      <td>
        <?php if(in_array($slug,['cpoint','worldcom','sentinel-trader','sentinal-trader'],true)):?>
          <small>Protected — managed independently</small>
        <?php else:?>
        <details><summary>Configuration (planned)</summary>
          <form method="post" action="/admin/hosting">
            <input type="hidden" name="csrf" value="<?=cms_esc(cms_csrf())?>">
            <input type="hidden" name="slug" value="<?=cms_esc($slug)?>">
            <input type="hidden" name="action" value="vhost_settings">
            <label>Assigned node
              <input name="assigned_node" maxlength="64" required
                value="<?=cms_esc($configuration['assigned_node'])?>">
            </label>
            <label>Database engine (intent only)
              <select name="database_backend">
                <?php foreach(['sqlite'=>'SQLite','mariadb'=>'MariaDB','mysql'=>'MySQL'] as $driver=>$label):?>
                  <option value="<?=$driver?>" <?=$configuration['database_backend']===$driver?'selected':''?>><?=$label?></option>
                <?php endforeach;?>
              </select>
            </label>
            <label>GitHub owner/repository
              <input name="source_repository" maxlength="130"
                value="<?=cms_esc($configuration['source_repository'])?>" placeholder="owner/repository">
            </label>
            <label>Git ref/branch
              <input name="source_ref" maxlength="120"
                value="<?=cms_esc($configuration['source_ref'])?>" placeholder="main">
            </label>
            <label>Release policy
              <select name="release_policy">
                <?php foreach(['hold'=>'On hold','manual-review'=>'Manual approval required'] as $mode=>$label):?>
                  <option value="<?=$mode?>" <?=$configuration['release_policy']===$mode?'selected':''?>><?=$label?></option>
                <?php endforeach;?>
              </select>
            </label>
            <button class="btn small" type="submit">Save preferences</button>
          </form>
        </details>
        <?php endif;?>
      </td>
      <td><?php if($hestia): ?>
          <small>Manual Hestia setup required. No live vhost changes available here.</small>
        <?php else: ?>
          <form method="post" action="/admin/hosting">
          <input type="hidden" name="csrf" value="<?=cms_esc(cms_csrf())?>">
          <input type="hidden" name="slug" value="<?=cms_esc($slug)?>">
          <button class="btn small" name="action" value="vhost_preview" type="submit">Preview</button>
          <?php if($project['state']==='published'):?>
          <label>Type <code>APPLY <?=cms_esc($slug)?></code> or <code>ROLLBACK <?=cms_esc($slug)?></code>
          <input name="confirmation" maxlength="50" autocomplete="off" placeholder="APPLY <?=cms_esc($slug)?>"></label>
          <button class="btn small primary" name="action" value="vhost_apply" type="submit">Apply page vhost</button>
          <button class="btn small" name="action" value="vhost_rollback" type="submit">Rollback managed vhost</button>
          <?php else:?><label>Type <code>ROLLBACK <?=cms_esc($slug)?></code> if previously provisioned
          <input name="confirmation" maxlength="50" autocomplete="off" placeholder="ROLLBACK <?=cms_esc($slug)?>"></label>
          <button class="btn small" name="action" value="vhost_rollback" type="submit">Rollback managed vhost</button><small>Publish first to enable Apply</small><?php endif;?>
        </form><?php endif;?></td></tr>
    <?php endforeach;?></tbody></table></div>
    <?php cms_admin_shell_end($db);
}
