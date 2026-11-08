<?php
declare(strict_types=1);
if (!defined('SIRUS_CMS_BOOTSTRAPPED')) { http_response_code(404); exit; }
require_once __DIR__ . '/storage_registry.php';
require_once __DIR__ . '/node_registry.php';

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
    ];
    $storageFile = getenv('SIRUS_STORAGE_REGISTRY_FILE');
    $nodeFile = getenv('SIRUS_NODE_REGISTRY_FILE');
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
    return $snapshot;
}

function cms_operations_page(PDO $db): void
{
    cms_need_admin();
    $snapshot = cms_operations_snapshot();
    cms_admin_shell_start($db, 'Infrastructure');
    ?>
    <div class="admin-panel">
      <h2>Registered compute nodes</h2>
      <p>Nodes send authenticated heartbeats to the public coordinator. Offline
      means no accepted heartbeat within the last three minutes, not an
      automatically failed-over workload. No remote command execution is enabled.</p>
      <p>Registry: <strong><?=cms_esc($snapshot['nodes_state'])?></strong></p>
      <div class="table-scroll"><table><thead><tr>
        <th>Node</th><th>OS / init</th><th>CPU threads</th>
        <th>RAM (GiB)</th><th>GPU vendors</th><th>Last heartbeat (UTC)</th><th>Health</th>
      </tr></thead><tbody>
      <?php foreach($snapshot['nodes'] as $node): ?>
      <tr><td><code><?=cms_esc($node['node_id'])?></code></td>
        <td><?=cms_esc($node['os'])?> / <?=cms_esc($node['init_system'])?></td>
        <td><?=cms_esc($node['cpu_threads'])?></td>
        <td><?=cms_esc(number_format((int)$node['ram_bytes']/1073741824,1))?></td>
        <td><?=cms_esc(implode(', ', $node['gpu_vendors']??[]))?></td>
        <td><?=cms_esc(gmdate('Y-m-d H:i:s', (int)$node['last_seen']))?></td>
        <td><?=cms_esc($node['health'])?></td></tr>
      <?php endforeach; ?>
      </tbody></table></div>
      <?php if(!$snapshot['nodes']): ?><p>No registered nodes available.</p><?php endif; ?>
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
