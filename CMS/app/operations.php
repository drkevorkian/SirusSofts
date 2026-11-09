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

/** Enqueue Gentoo-only work from an authenticated administrator, never PHP. */
function cms_operations_post(PDO $db,string $action): never {
    cms_need_admin();
    if($action!=='enqueue_gentoo_job')
        throw new InvalidArgumentException('Unsupported infrastructure operation.');
    $nodeId=(string)($_POST['node_id']??'');
    $kind=(string)($_POST['kind']??'');
    if(!preg_match('/^[a-z][a-z0-9-]{1,63}$/D',$nodeId) ||
       !in_array($kind,['health_snapshot','compute_selftest'],true))
        throw new InvalidArgumentException('Choose a valid connected node and approved task.');
    $nodeDir=cms_private_directory().'/nodes';
    $nodeDb=$nodeDir.'/registry.sqlite3';
    if(!is_file($nodeDb)||is_link($nodeDb))
        throw new InvalidArgumentException('No registered Gentoo node has connected.');
    $registry=new SirusNodeRegistry($nodeDb);
    $node=$registry->getNode($nodeId);
    if(!$node || $node['health']!=='online')
        throw new InvalidArgumentException('The selected Gentoo node is offline. Wait for a fresh signed heartbeat.');
    $broker=new SirusWorkBroker($nodeDir.'/work.sqlite3');
    $id=$broker->enqueue($nodeId,$kind);
    cms_audit($db,'enqueue-gentoo-work',$nodeId.':'.$kind.':'.$id);
    cms_flash('Work queued for Gentoo: '.$id.'. Gentoo will claim it on its next authenticated poll.');
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
      <p>Gentoo/OpenRC and future compute nodes send independently signed, outbound
      HTTPS heartbeats to the Ubuntu coordinator. Last-seen data is considered online
      only within three minutes; readings from disconnected nodes are historical.
      This panel does not expose remote shell or execute workloads.</p>
      <p>Private node registry: <strong><?=cms_esc($snapshot['nodes_state'])?></strong></p>
      <?php if(!$snapshot['nodes']):?>
        <p class="empty-state">No Gentoo node has reported yet. Install the outbound
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
        <div class="node-state <?=($online?'is-online':'is-offline')?>">
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
          <strong><?=$cpu===null?'Unavailable':cms_esc(number_format((float)$cpu,1)).'%'?></strong>
          <?php if($cpu!==null):?><progress max="100" value="<?=cms_esc((string)$cpu)?>">CPU load</progress><?php endif;?>
          <small>1-minute load: <?=isset($m['load_1'])?cms_esc((string)$m['load_1']):'N/A'?></small>
        </div>
        <div class="node-metric">
          <span>Memory</span>
          <strong><?=isset($m['memory_used_bytes'])?cms_esc(cms_node_size($memoryUsed)):'Unavailable'?> / <?=cms_esc(cms_node_size($memoryTotal))?></strong>
          <?php if(isset($m['memory_used_bytes'])):?><progress max="100" value="<?=cms_esc((string)$memoryPct)?>">Memory used</progress><?php endif;?>
          <small><?=isset($m['memory_used_bytes'])?cms_esc((string)$memoryPct).'% used':'Awaiting updated agent'?></small>
        </div>
        <div class="node-metric">
          <span>Root filesystem</span>
          <strong><?=$diskTotal>0?cms_esc(cms_node_size($diskUsed)).' / '.cms_esc(cms_node_size($diskTotal)):'Unavailable'?></strong>
          <?php if($diskTotal>0):?><progress max="100" value="<?=cms_esc((string)$diskPct)?>">Disk used</progress><?php endif;?>
          <small><?=$diskTotal>0?cms_esc((string)$diskPct).'% used':'Awaiting updated agent'?></small>
        </div>
        <div class="node-metric">
          <span>CPU temperature</span>
          <strong><?=$cpuTemp===null?'Unavailable':cms_esc(number_format((float)$cpuTemp,1)).' °C'?></strong>
          <small>Uptime:
            <?=isset($m['uptime_seconds'])?cms_esc(cms_node_uptime((int)$m['uptime_seconds'])):'N/A'?></small>
        </div>
      </div>
      <h3>Graphics processing units</h3>
      <?php if(!$gpu):?>
        <p>No GPU temperature/utilization data yet.
        Detected PCI vendors: <?=cms_esc(implode(', ',$node['gpu_vendors']??[]))?></p>
      <?php else:?>
      <div class="node-metric-grid">
        <?php foreach($gpu as $device):?>
          <div class="node-metric">
            <span><?=cms_esc($device['name']??'GPU')?> · <?=cms_esc($device['vendor']??'')?></span>
            <strong><?=isset($device['usage_pct'])?cms_esc(number_format((float)$device['usage_pct'],1)).'% GPU':'GPU load unavailable'?></strong>
            <?php if(isset($device['usage_pct'])):?>
              <progress max="100" value="<?=cms_esc((string)$device['usage_pct'])?>">GPU load</progress>
            <?php endif;?>
            <small>Temperature: <?=isset($device['temp_c'])?cms_esc(number_format((float)$device['temp_c'],1)).' °C':'Unavailable'?></small>
            <small>VRAM: <?=isset($device['vram_used_bytes'])?cms_esc(cms_node_size((int)$device['vram_used_bytes'])):'N/A'?>
              / <?=isset($device['vram_total_bytes'])?cms_esc(cms_node_size((int)$device['vram_total_bytes'])):'N/A'?></small>
          </div>
        <?php endforeach;?>
      </div>
      <?php endif;?>
    </section>
    <?php endforeach;?>
        <div class="admin-panel">
      <h2>Gentoo → Ubuntu delegated work</h2>
      <p>Ubuntu hosts the website and queues work. The separate Gentoo Python
      node polls the CMS over authenticated HTTPS, executes approved tasks
      on Gentoo, and returns results. PHP/Ubuntu does not execute these jobs.</p>
      <?php $flash=cms_take_flash();if($flash):?>
        <p class="flash-message"><?=cms_esc($flash['message'])?></p>
      <?php endif;?>
      <form method="post" action="/admin/infrastructure">
        <input type="hidden" name="csrf" value="<?=cms_esc(cms_csrf())?>">
        <input type="hidden" name="action" value="enqueue_gentoo_job">
        <label>Worker node
          <select name="node_id" required>
            <?php foreach($snapshot['nodes'] as $node):
              if($node['health']!=='online')continue;?>
              <option value="<?=cms_esc($node['node_id'])?>"><?=cms_esc($node['node_id'])?></option>
            <?php endforeach;?>
          </select>
        </label>
        <label>Approved Gentoo task
          <select name="kind">
            <option value="health_snapshot">Gentoo hardware health snapshot</option>
            <option value="compute_selftest">Gentoo bounded CPU computation self-test</option>
          </select>
        </label>
        <button type="submit" class="btn primary"
           <?=!array_filter($snapshot['nodes'],static fn($n)=>$n['health']==='online')?'disabled':''?>>
           Delegate work to Gentoo
        </button>
      </form>
      <p class="muted small">Only authenticated administrators may enqueue.
      A node must have reported within the last three minutes.</p>
      <p>Private work queue: <strong><?=cms_esc($snapshot['work_state'])?></strong></p>
      <div class="table-scroll"><table><thead><tr>
        <th>Request ID</th><th>Node</th><th>Kind</th>
        <th>Status</th><th>Attempts</th><th>Submitted (UTC)</th><th>Result</th>
      </tr></thead><tbody>
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
            <?php else:?>Awaiting Gentoo<?php endif;?></td>
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
