<?php
declare(strict_types=1);
if (!defined('SIRUS_CMS_BOOTSTRAPPED')) { http_response_code(404); exit; }
require_once __DIR__ . '/storage_registry.php';
require_once __DIR__ . '/node_registry.php';
require_once __DIR__ . '/work_broker.php';

/**
 * Read-only CMS infrastructure overview. Registry paths are chosen by the
 * trusted PHP-FPM pool environment, never from browser requests.
 */
function cms_operations_snapshot(): array
{
    cms_need_admin();
    $snapshot = [
        'storage_state' => 'Not configured',
        'storage' => [],
        'nodes_state' => 'Not configured',
        'nodes' => [],
        'work_state' => 'Not configured',
        'work_jobs' => [],
    ];
    $storageFile = getenv('SIRUS_STORAGE_REGISTRY_FILE');
    $nodeDir='';
    try {$nodeDir=cms_private_directory().'/nodes';}
    catch(Throwable $error){$nodeDir='';} // Unit tests and non-Hestia staging environments.
    $nodeFile = getenv('SIRUS_NODE_REGISTRY_FILE') ?: ($nodeDir!==''?$nodeDir.'/registry.sqlite3':'');
    foreach (['storage' => $storageFile, 'nodes' => $nodeFile] as $name => $path) {
        if (!is_string($path) || $path === '') { continue; }
        if (!str_starts_with($path, '/') || str_contains($path, "\0") ||
            is_link($path) || !is_file($path)) {
            $snapshot[$name . '_state'] = 'Configured registry file unavailable';
            continue;
        }
        $site = cms_site_root();
        $real = realpath($path);
        if ($real === false || $real === $site || str_starts_with($real, $site . '/')) {
            $snapshot[$name . '_state'] = 'Registry path is not private';
            continue;
        }
        try {
            if ($name === 'storage') {
                $registry = new SirusStorageRegistry($real);
                $snapshot['storage'] = $registry->all();
            } else {
                $registry = new SirusNodeRegistry($real);
                $snapshot['nodes'] = $registry->allNodes();
            }
            $snapshot[$name . '_state'] = 'Available';
        } catch (Throwable $error) {
            error_log('Sirus infrastructure registry read failure: ' . get_class($error));
            $snapshot[$name . '_state'] = 'Registry could not be read';
        }
    }
    $workFile=$nodeDir!==''?$nodeDir.'/work.sqlite3':'';
    if(is_file($workFile) && !is_link($workFile)){
        try{
            $broker=new SirusWorkBroker($workFile);
            $snapshot['work_jobs']=$broker->recent(30);
            $snapshot['work_state']='Available';
        }catch(Throwable $error){
            error_log('Sirus work queue admin read failed: '.get_class($error));
            $snapshot['work_state']='Unavailable';
        }
    }
    return $snapshot;
}

/** Enqueue fixed-type work on a connected Gentoo or macOS node, never PHP. */
function cms_operations_post(PDO $db,string $action): never {
    cms_need_admin();
    if(!in_array($action,['enqueue_gentoo_job','enqueue_node_job'],true))
        throw new InvalidArgumentException('Unsupported infrastructure operation.');
    $nodeId=(string)($_POST['node_id']??'');
    $kind=(string)($_POST['kind']??'');
    if(!preg_match('/^[a-z][a-z0-9-]{1,63}$/D',$nodeId) ||
       !in_array($kind,['health_snapshot','compute_selftest'],true))
        throw new InvalidArgumentException('Choose a valid connected node and approved task.');
    $nodeDir=cms_private_directory().'/nodes';
    $nodeDb=$nodeDir.'/registry.sqlite3';
    if(!is_file($nodeDb)||is_link($nodeDb))
        throw new InvalidArgumentException('No registered compute node has connected.');
    $registry=new SirusNodeRegistry($nodeDb);
    $node=$registry->getNode($nodeId);
    if(!$node || $node['health']!=='online')
        throw new InvalidArgumentException('The selected compute node is offline. Wait for a fresh signed heartbeat.');
    $broker=new SirusWorkBroker($nodeDir.'/work.sqlite3');
    $id=$broker->enqueue($nodeId,$kind);
    cms_audit($db,'enqueue-node-work',$nodeId.':'.$kind.':'.$id);
    if(str_contains((string)($_SERVER['HTTP_ACCEPT']??''),'application/json')) {
        header('Content-Type: application/json; charset=utf-8');
        header('Cache-Control: no-store');
        http_response_code(202);
        echo json_encode(['ok'=>true,'request_id'=>$id,'node_id'=>$nodeId],JSON_THROW_ON_ERROR);
        exit;
    }
    cms_flash('Work queued for node: '.$id.'. The selected node will claim it on its next authenticated poll.');
    cms_go('/admin/infrastructure');
}

function cms_node_size(int $bytes): string {
    return number_format(max(0,$bytes)/1073741824,1).' GiB';
}
function cms_node_uptime(int $seconds): string {
    $days=intdiv(max(0,$seconds),86400);
    $hours=intdiv($seconds%86400,3600);
    return $days.'d '.$hours.'h';
}

function cms_operations_page(PDO $db): void
{
    cms_need_admin();
    $snapshot = cms_operations_snapshot();
    cms_admin_shell_start($db, 'Infrastructure');
    ?>
    <div class="admin-panel">
      <h2>Compute cluster health</h2>
      <p>Gentoo/OpenRC and macOS/launchd nodes send independently signed, outbound
      HTTPS heartbeats to the Ubuntu coordinator. Last-seen data is considered online
      only within three minutes; readings from disconnected nodes are historical.
      Ubuntu only brokers work; approved task execution happens on each selected worker.</p>
      <p>Private node registry: <strong><?=cms_esc($snapshot['nodes_state'])?></strong></p>
      <?php if(!$snapshot['nodes']):?>
        <p class="empty-state">No compute node has reported yet. Install an outbound
        agent on Gentoo and enroll its heartbeat key on this Ubuntu VPS.</p>
      <?php endif;?>
    </div>
    <?php foreach($snapshot['nodes'] as $node):
      $m=is_array($node['metrics']??null)?$node['metrics']:[];
      $online=$node['health']==='online';
      $memoryUsed=(int)($m['memory_used_bytes']??0);
      $memoryTotal=(int)($node['ram_bytes']??0);
      $diskUsed=(int)($m['disk_used_bytes']??0);
      $diskTotal=(int)($m['disk_total_bytes']??0);
      $cpu=$m['cpu_pct']??null;
      $cpuTemp=$m['cpu_temp_c']??null;
      $gpu=is_array($m['gpus']??null)?$m['gpus']:[];
      $lastSeen=(int)$node['last_seen'];
      $age=max(0,time()-$lastSeen);
      $memoryPct=$memoryTotal>0?round(100*$memoryUsed/$memoryTotal,1):0;
      $diskPct=$diskTotal>0?round(100*$diskUsed/$diskTotal,1):0;
    ?>
    <section class="admin-panel node-stats" aria-label="<?=cms_esc($node['node_id'])?> compute node">
      <div class="node-heading">
        <div><h2><?=cms_esc($node['node_id'])?></h2>
          <p><?=cms_esc($node['os'])?> / <?=cms_esc($node['init_system'])?>
             · <?=(int)$node['cpu_threads']?> CPU threads
             · agent <?=cms_esc($node['agent_version'])?></p>
        </div>
        <div data-live-node-id="<?=cms_esc($node['node_id'])?>" class="node-state <?=($online?'is-online':'is-offline')?>">
          <?=($online?'Online':'Offline — stale metrics')?>
        </div>
      </div>
      <p class="muted small">Last authenticated heartbeat:
        <?=cms_esc(gmdate('Y-m-d H:i:s',$lastSeen))?> UTC
        (<?=cms_esc(number_format($age))?> seconds ago).
        All metrics shown below are <?=($online?'recent':'from the last successful heartbeat')?>.</p>
      <div class="node-metric-grid">
        <div class="node-metric">
          <span>CPU utilization</span>
          <strong data-live-metric="cpu_pct" data-live-metric-node="<?=cms_esc($node['node_id'])?>"><?=$cpu===null?'Unavailable':cms_esc(number_format((float)$cpu,1)).'%'?></strong>
          <?php if($cpu!==null):?><progress max="100" value="<?=cms_esc((string)$cpu)?>">CPU load</progress><?php endif;?>
          <small>1-minute load: <?=isset($m['load_1'])?cms_esc((string)$m['load_1']):'N/A'?></small>
        </div>
        <div class="node-metric">
          <span>Memory</span>
          <strong data-live-metric="memory" data-live-metric-node="<?=cms_esc($node['node_id'])?>"><?=isset($m['memory_used_bytes'])?cms_esc(cms_node_size($memoryUsed)):'Unavailable'?> / <?=cms_esc(cms_node_size($memoryTotal))?></strong>
          <?php if(isset($m['memory_used_bytes'])):?><progress max="100" value="<?=cms_esc((string)$memoryPct)?>">Memory used</progress><?php endif;?>
          <small><?=isset($m['memory_used_bytes'])?cms_esc((string)$memoryPct).'% used':'Awaiting updated agent'?></small>
        </div>
        <div class="node-metric">
          <span>Root filesystem</span>
          <strong data-live-metric="disk" data-live-metric-node="<?=cms_esc($node['node_id'])?>"><?=$diskTotal>0?cms_esc(cms_node_size($diskUsed)).' / '.cms_esc(cms_node_size($diskTotal)):'Unavailable'?></strong>
          <?php if($diskTotal>0):?><progress max="100" value="<?=cms_esc((string)$diskPct)?>">Disk used</progress><?php endif;?>
          <small><?=$diskTotal>0?cms_esc((string)$diskPct).'% used':'Awaiting updated agent'?></small>
        </div>
        <div class="node-metric">
          <span>CPU temperature</span>
          <strong data-live-metric="cpu_temp_c" data-live-metric-node="<?=cms_esc($node['node_id'])?>"><?=$cpuTemp===null?'Unavailable':cms_esc(number_format((float)$cpuTemp,1)).' °C'?></strong>
          <small>Uptime:
            <?=isset($m['uptime_seconds'])?cms_esc(cms_node_uptime((int)$m['uptime_seconds'])):'N/A'?></small>
        </div>
      </div>
      <?php if($node['os']==='macos'):?>
      <p><a href="/mac?node=<?=rawurlencode((string)$node['node_id'])?>">Open Mac hardware and research dashboard</a>.
      Operating system and hardware capabilities are reported by the signed Mac agent.</p>
      <?php endif;?>
      <?php if($node['os']==='gentoo'):?>
      <p><a href="https://gentoo.sirussofts.com/" rel="noopener noreferrer"
        target="_blank">Open private Gentoo process, GPU and CPU-affinity dashboard</a>.
        This separate authenticated Gentoo page shows local process ownership
        without exporting PID details to the Ubuntu node registry. Its controls
        require the Gentoo administrator password and are limited to the
        Gentoo dashboard service account. Driver resets are not enabled.</p>
      <?php endif;?>
      <h3>Running services and application health</h3>
      <div data-live-node-services="<?=cms_esc($node['node_id'])?>">
        <p>OpenRC services:
          <?=!isset($m['services'])?'Not reported by this installed agent':
             cms_esc(implode(', ',(array)$m['services'])?:'No services reported')?></p>
        <p>Local applications:
          <?php if(!isset($m['applications'])):?>Not reported by this installed agent<?php endif;?>
          <?php foreach((array)($m['applications']??[]) as $app):?>
            <?=cms_esc($app['name']??'app')?>:
            <?=!empty($app['healthy'])?'Healthy':'Unavailable'?>
          <?php endforeach;?>
          <?php if(isset($m['applications'])&&!$m['applications']):?>No applications reported<?php endif;?>
        </p>
      </div>
      <h3>Graphics processing units</h3>
      <?php if(!$gpu):?>
        <p>No GPU temperature/utilization data yet.
        Detected PCI vendors: <?=cms_esc(implode(', ',$node['gpu_vendors']??[]))?></p>
      <?php else:?>
      <div class="node-metric-grid" data-live-gpus="<?=cms_esc($node['node_id'])?>">
        <?php foreach($gpu as $device):?>
          <div class="node-metric">
            <span><?=cms_esc($device['name']??'GPU')?> · <?=cms_esc($device['vendor']??'')?></span>
            <strong><?=isset($device['usage_pct'])?cms_esc(number_format((float)$device['usage_pct'],1)).'% GPU':'GPU load unavailable'?></strong>
            <?php if(isset($device['usage_pct'])):?>
              <progress max="100" value="<?=cms_esc((string)$device['usage_pct'])?>">GPU load</progress>
            <?php endif;?>
            <small>Temperature: <?=isset($device['temp_c'])?cms_esc(number_format((float)$device['temp_c'],1)).' °C':'Unavailable'?></small>
            <?php if(($device['vendor']??'')==='0x10de' &&
              ($device['usage_pct']??null)===null && ($device['temp_c']??null)===null):?>
              <small class="muted">NVIDIA card detected; driver telemetry is unavailable to the Gentoo node. Check nvidia-smi or NVML.</small>
            <?php endif;?>
            <small>VRAM: <?=isset($device['vram_used_bytes'])?cms_esc(cms_node_size((int)$device['vram_used_bytes'])):'N/A'?>
              / <?=isset($device['vram_total_bytes'])?cms_esc(cms_node_size((int)$device['vram_total_bytes'])):'N/A'?></small>
          </div>
        <?php endforeach;?>
      </div>
      <?php endif;?>
    </section>
    <?php endforeach;?>
        <div class="admin-panel">
      <h2>Delegated compute work — Gentoo and macOS</h2>
      <p>Ubuntu queues work; registered Gentoo and macOS workers poll over authenticated HTTPS,
      execute approved fixed-type tasks locally, and return results. PHP/Ubuntu does not execute these jobs.</p>
      <?php $flash=cms_take_flash();if($flash):?>
        <p class="flash-message"><?=cms_esc($flash['message'])?></p>
      <?php endif;?>
      <form method="post" action="/admin/infrastructure" data-live-submit>
        <p class="muted small" data-live-form-message role="status" aria-live="polite">Select an online node and a task, then submit.</p>
        <input type="hidden" name="csrf" value="<?=cms_esc(cms_csrf())?>">
        <input type="hidden" name="action" value="enqueue_node_job">
        <label>Worker node
          <select name="node_id" required>
            <?php foreach($snapshot['nodes'] as $node):
              if($node['health']!=='online')continue;?>
              <option value="<?=cms_esc($node['node_id'])?>"><?=cms_esc($node['node_id'])?></option>
            <?php endforeach;?>
          </select>
        </label>
        <label>Approved worker task
          <select name="kind">
            <option value="health_snapshot">Hardware health snapshot</option>
            <option value="compute_selftest">Bounded CPU computation self-test</option>
          </select>
        </label>
        <button type="submit" class="btn primary"
           <?=!array_filter($snapshot['nodes'],static fn($n)=>$n['health']==='online')?'disabled':''?>>
           Delegate work to selected node
        </button>
      </form>
      <p class="muted small">Only authenticated administrators may enqueue.
      A node must have reported within the last three minutes.</p>
      <p>Private work queue: <strong><?=cms_esc($snapshot['work_state'])?></strong></p>
      <div class="table-scroll"><table><thead><tr>
        <th>Request ID</th><th>Node</th><th>Kind</th>
        <th>Status</th><th>Attempts</th><th>Submitted (UTC)</th><th>Result</th>
      </tr></thead><tbody data-live-infrastructure-jobs>
        <?php foreach($snapshot['work_jobs'] as $job):?>
        <tr><td><code><?=cms_esc($job['request_id'])?></code></td>
            <td><?=cms_esc($job['node_id'])?></td>
            <td><?=cms_esc($job['kind'])?></td>
            <td><?=cms_esc($job['state'])?></td>
            <td><?=(int)$job['attempts']?></td>
            <td><?=cms_esc(gmdate('Y-m-d H:i:s',(int)$job['created_at']))?></td>
            <td><?php if(in_array($job['state'],['done','failed'],true)):?>
              <details><summary>View <?=cms_esc($job['state'])?></summary>
                <pre class="source-content"><code><?=cms_esc((string)$job['result_json'])?></code></pre>
                <?php if($job['error']):?><p><?=cms_esc($job['error'])?></p><?php endif;?>
              </details>
            <?php else:?>Awaiting worker<?php endif;?></td>
        </tr>
        <?php endforeach;?>
      </tbody></table></div>
      <?php if(!$snapshot['work_jobs']):?><p>No delegated work recorded yet.</p><?php endif;?>
    </div>
    <div class="admin-panel">
      <h2>Project database registry</h2>
      <p>Database credentials are never stored in this registry. Changing a
      backend or migrating data is not yet exposed through the website;
      administrator validation and rollback are still required.</p>
      <p>Registry: <strong><?=cms_esc($snapshot['storage_state'])?></strong></p>
      <div class="table-scroll"><table><thead><tr>
        <th>Project</th><th>Backend</th><th>Primary node</th>
        <th>Schema</th><th>Migration state</th><th>Last update (UTC)</th>
      </tr></thead><tbody>
      <?php foreach($snapshot['storage'] as $storage): ?>
      <tr><td><code><?=cms_esc($storage['project_id'])?></code></td>
        <td><?=cms_esc($storage['driver'])?></td>
        <td><?=cms_esc($storage['primary_node'])?></td>
        <td><?=cms_esc($storage['schema_version'])?></td>
        <td><?=cms_esc($storage['migration_state'])?></td>
        <td><?=cms_esc($storage['updated_at'])?></td></tr>
      <?php endforeach; ?>
      </tbody></table></div>
      <?php if(!$snapshot['storage']): ?><p>No project database records available.</p><?php endif; ?>
    </div>
    <?php
    cms_admin_shell_end($db);
}

/**
 * Update Center phase 1: signed, fixed-command, read-only package previews.
 * The web process never runs apt/emerge and cannot install packages.
 */
function cms_updates_post(PDO $db, string $action): never {
    cms_need_admin();
    if($action==='check_source_updates'){
        $dir=cms_private_directory().'/nodes';
        if(!is_dir($dir) || is_link($dir))
            throw new InvalidArgumentException('Private node directory unavailable.');
        $marker=$dir.'/source_refresh.request';
        if(is_link($marker))
            throw new InvalidArgumentException('Unsafe source refresh path.');
        if(!is_file($marker)){
            $handle=@fopen($marker,'x');
            if($handle===false)throw new InvalidArgumentException('Cannot request GitHub refresh.');
            fclose($handle);
            chmod($marker,0600);
        }
        cms_audit($db,'request-github-source-check','approved-sirussofts-branch');
        if(str_contains((string)($_SERVER['HTTP_ACCEPT']??''),'application/json')){
            header('Content-Type: application/json; charset=utf-8');
            header('Cache-Control: no-store');
            http_response_code(202);
            echo json_encode(['ok'=>true,'request_id'=>'github-source-check'],JSON_THROW_ON_ERROR);
            exit;
        }
        cms_flash('GitHub source check requested; the restricted indexer will process it.');
        cms_go('/admin/updates');
    }
    if($action!=='check_package_updates')
        throw new InvalidArgumentException('Unsupported Update Center operation.');
    $nodeId=(string)($_POST['node_id']??'');
    if(!in_array($nodeId,['ubuntu-primary','gentoo-home'],true))
        throw new InvalidArgumentException('Select an approved host.');
    $dir=cms_private_directory().'/nodes';
    if(!is_dir($dir) || !is_file($dir.'/work.sqlite3') ||
       is_link($dir.'/work.sqlite3'))
        throw new InvalidArgumentException('Private work broker is not configured.');
    if($nodeId==='gentoo-home'){
        $registryPath=$dir.'/registry.sqlite3';
        if(!is_file($registryPath) || is_link($registryPath))
            throw new InvalidArgumentException('Gentoo registry unavailable.');
        $node=(new SirusNodeRegistry($registryPath))->getNode($nodeId);
        if(!$node || $node['health']!=='online' || $node['os']!=='gentoo')
            throw new InvalidArgumentException('Gentoo must have a fresh authenticated heartbeat.');
    }
    $broker=new SirusWorkBroker($dir.'/work.sqlite3');
    $id=$broker->enqueue($nodeId,'package_update_check');
    cms_audit($db,'preview-host-updates',$nodeId.':'.$id);
    if(str_contains((string)($_SERVER['HTTP_ACCEPT']??''),'application/json')) {
        header('Content-Type: application/json; charset=utf-8');
        header('Cache-Control: no-store');
        http_response_code(202);
        echo json_encode(['ok'=>true,'request_id'=>$id,'node_id'=>$nodeId],JSON_THROW_ON_ERROR);
        exit;
    }
    cms_flash('Read-only update check queued for '.$nodeId.'. No packages were changed.');
    cms_go('/admin/updates');
}

function cms_updates_page(PDO $db): void {
    cms_need_admin();
    $snapshot=cms_operations_snapshot();
    $gentooOnline=false;
    foreach($snapshot['nodes'] as $node) {
        if(($node['node_id']??'')==='gentoo-home' && ($node['os']??'')==='gentoo' &&
           ($node['health']??'')==='online')$gentooOnline=true;
    }
    cms_admin_shell_start($db,'Update Center');
    ?>
    <div class="admin-panel">
      <h2>Ubuntu and Gentoo updates</h2>
      <p>Phase 1: read-only update previews. These jobs are queued through the
      existing private broker. Ubuntu performs an APT simulation; Gentoo
      performs an emerge pretend run. No package installations, syncs,
      reboots or service restarts can be initiated here.</p>
      <p>Work broker: <strong><?=cms_esc($snapshot['work_state'])?></strong></p>
      <?php $flash=cms_take_flash();if($flash):?>
        <p class="flash-message"><?=cms_esc($flash['message'])?></p>
      <?php endif;?>
      <?php foreach(['ubuntu-primary'=>'Ubuntu public gateway',
                     'gentoo-home'=>'Gentoo compute node'] as $id=>$label):?>
        <form method="post" action="/admin/updates" data-live-submit>
          <input type="hidden" name="csrf" value="<?=cms_esc(cms_csrf())?>">
          <input type="hidden" name="action" value="check_package_updates">
          <input type="hidden" name="node_id" value="<?=cms_esc($id)?>">
          <p><strong><?=cms_esc($label)?></strong>
            <?php if($id==='gentoo-home'&&!$gentooOnline):?>
              <span class="muted">— no recent authenticated heartbeat</span>
            <?php endif;?>
          </p>
          <button type="submit" class="btn primary"
            <?=($snapshot['work_state']!=='Available' ||
              ($id==='gentoo-home'&&!$gentooOnline))?'disabled':''?>>
            Check available updates
          </button>
        </form>
      <?php endforeach;?>
    </div>
    <div class="admin-panel">
      <h2>SirusSofts GitHub source updates</h2>
      <p>Compare the approved GitHub deployment branch against actual installed
      SirusSofts files on Ubuntu and the signed Gentoo node. Different code
      is flagged for review, not automatically installed.</p>
      <form method="post" action="/admin/updates" data-live-submit>
        <input type="hidden" name="csrf" value="<?=cms_esc(cms_csrf())?>">
        <input type="hidden" name="action" value="check_source_updates">
        <button type="submit" class="btn primary">Check GitHub source revisions</button>
      </form>
      <p data-live-source-status class="muted">Awaiting authenticated live source inventory.</p>
      <div data-live-source-list role="status" aria-live="polite"></div>
    </div>
    <?php cms_release_planner($db); ?>
    <div class="admin-panel">
      <h2>Recent update checks</h2>
      <div class="table-scroll"><table><thead><tr>
        <th>Host</th><th>Status</th><th>Time (UTC)</th><th>Preview</th>
      </tr></thead><tbody data-live-update-jobs>
        <?php foreach($snapshot['work_jobs'] as $job):
          if(($job['kind']??'')!=='package_update_check')continue;?>
          <tr><td><?=cms_esc($job['node_id'])?></td>
            <td><?=cms_esc($job['state'])?></td>
            <td><?=cms_esc(gmdate('Y-m-d H:i:s',(int)$job['updated_at']))?></td>
            <td><details><summary>View result</summary>
              <pre class="source-content"><code><?=cms_esc((string)$job['result_json'])?></code></pre>
              <?php if($job['error']):?><p><?=cms_esc($job['error'])?></p><?php endif;?>
            </details></td>
          </tr>
        <?php endforeach;?>
      </tbody></table></div>
    </div>
    <div class="admin-panel">
      <h2>Applying updates</h2>
      <p>Not enabled yet. Installation will require a separate, restricted
      privileged executor per host, rollback/backup checks, disk-space gates,
      an explicit confirmation, and a post-update health check. Web PHP
      must never receive unrestricted sudo or shell access.</p>
    </div>
    <?php cms_admin_shell_end($db);
}

/** Authenticated, read-only Mac view. No process control or remote shell. */
function cms_mac_page(PDO $db): void {
    cms_need_admin();
    $snapshot=cms_operations_snapshot();
    $macs=array_values(array_filter($snapshot['nodes'],static fn($node)=>($node['os']??'')==='macos'));
    $selected=(string)($_GET['node']??'');
    if($selected!=='' && !preg_match('/^[a-z][a-z0-9-]{1,63}$/D',$selected))
        { http_response_code(400); exit('Invalid node'); }
    if($selected!=='')$macs=array_values(array_filter($macs,static fn($node)=>$node['node_id']===$selected));
    cms_admin_shell_start($db,'Mac infrastructure');
    ?>
    <div class="admin-panel"><h1>Mac research nodes</h1>
      <p>Signed outbound telemetry from Intel and Apple Silicon Macs. Offline values are historical.
      The worker is locally supervised by launchd; this page does not grant root access.</p>
      <p><a href="/admin/infrastructure">Infrastructure overview</a></p>
    </div>
    <?php if(!$macs):?><div class="admin-panel"><p>No enrolled Mac node has reported.</p></div><?php endif;?>
    <?php foreach($macs as $node):
      $m=is_array($node['metrics']??null)?$node['metrics']:[];
      $mac=is_array($m['mac']??null)?$m['mac']:[];
      $online=($node['health']??'')==='online'; ?>
      <section class="admin-panel node-stats">
      <h2><?=cms_esc((string)$node['node_id'])?> — <?=$online?'Online':'Offline'?></h2>
      <p>Model: <?=cms_esc((string)($mac['model']??'Unknown'))?> ·
      Architecture: <?=cms_esc((string)($mac['arch']??'Unknown'))?> ·
      macOS: <?=cms_esc((string)($mac['os_version']??'Unknown'))?></p>
      <div class="node-metric-grid">
        <div class="node-metric"><span>CPU</span><strong><?=isset($m['cpu_pct'])?cms_esc((string)$m['cpu_pct']).'%':'Unavailable'?></strong>
        <small>Logical threads: <?=(int)$node['cpu_threads']?> · Target: <?=cms_esc((string)($mac['worker_cpu_target']??'Unknown'))?>%</small></div>
        <div class="node-metric"><span>Memory</span><strong><?=isset($m['memory_used_bytes'])?cms_esc(cms_node_size((int)$m['memory_used_bytes'])):'Unknown'?> / <?=cms_esc(cms_node_size((int)$node['ram_bytes']))?></strong>
        <small>Pressure: <?=cms_esc((string)($mac['memory_pressure']??'Unknown'))?></small></div>
        <div class="node-metric"><span>Power</span><strong><?=cms_esc((string)($mac['power_source']??'Unknown'))?></strong><small>Battery: <?=isset($mac['battery_pct'])?cms_esc((string)$mac['battery_pct']).'%':'Unknown'?></small></div>
        <div class="node-metric"><span>Storage</span><strong><?=isset($m['disk_used_bytes'],$m['disk_total_bytes'])?cms_esc(cms_node_size((int)$m['disk_used_bytes']).' / '.cms_node_size((int)$m['disk_total_bytes'])):'Unavailable'?></strong>
        <small>Uptime: <?=isset($m['uptime_seconds'])?cms_esc(cms_node_uptime((int)$m['uptime_seconds'])):'Unknown'?></small></div>
      </div>
      <p>Sleep prevention requested: <?=!empty($mac['sleep_assertion'])?'Yes':'No'?>.
      Last authenticated heartbeat: <?=cms_esc(gmdate('Y-m-d H:i:s',(int)$node['last_seen']))?> UTC.</p>
      <h3>GPU hardware</h3>
      <?php foreach((array)($m['gpus']??[]) as $gpu):?>
        <p><?=cms_esc((string)($gpu['name']??'GPU'))?> · vendor <?=cms_esc((string)($gpu['vendor']??'Unknown'))?>
        (utilization and dedicated VRAM may be unavailable).</p>
      <?php endforeach;?>
      </section>
    <?php endforeach;?>
    <?php cms_admin_shell_end($db);
}
