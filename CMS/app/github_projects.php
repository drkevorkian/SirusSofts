<?php
declare(strict_types=1);
if (!defined('SIRUS_CMS_BOOTSTRAPPED')) { http_response_code(404); exit; }

/**
 * Read-only integration with the standalone Python GitHub catalog database.
 * Discovery/synchronization remain in deploy/github_catalog.py; PHP never
 * requires GitHub credentials, clones source, deploys, or publishes projects.
 */
function cms_github_project_identity(string $fullName): array
{
    if (!preg_match('#^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$#D', $fullName)) {
        throw new InvalidArgumentException('Invalid GitHub repository name');
    }
    [$owner, $name] = explode('/', $fullName, 2);
    $matches = [];
    $matched = preg_match('/(?:[-_.]?v[-_.]?)([1-9][0-9]{0,2})$/iD', $name, $matches, PREG_OFFSET_CAPTURE);
    if (!$matched) {
        $matched = preg_match('/[-_.]([1-9][0-9]{0,2})$/D', $name, $matches, PREG_OFFSET_CAPTURE);
    }
    $stem = $name;
    $version = null;
    if ($matched) {
        $prefix = substr($name, 0, $matches[0][1]);
        if (preg_replace('/[-_.]+/', '', $prefix) !== '') {
            $stem = $prefix;
            $version = (int)$matches[1][0];
        }
    }
    $normalized = strtolower(trim((string)preg_replace('/[-_.]+/', '-', $stem), '-'));
    if ($normalized === '') { throw new InvalidArgumentException('Empty repository family'); }
    return [
        'family_key' => strtolower($owner) . '/' . $normalized,
        'family_name' => ucwords(str_replace('-', ' ', $normalized)),
        'version_number' => $version,
        'version_label' => $version === null ? 'Original / unversioned' : 'v' . $version,
    ];
}

function cms_github_grouped_projects(array $repositories): array
{
    $groups = [];
    foreach ($repositories as $repo) {
        if (!is_array($repo) || !is_string($repo['full_name'] ?? null)) {
            throw new InvalidArgumentException('Invalid GitHub catalog entry');
        }
        $metadata = cms_github_project_identity($repo['full_name']);
        $key = $metadata['family_key'];
        if (!isset($groups[$key])) {
            $groups[$key] = ['key' => $key, 'label' => $metadata['family_name'], 'versions' => []];
        }
        $groups[$key]['versions'][] = array_merge($repo, [
            'family_key' => $metadata['family_key'],
            'version_number' => $metadata['version_number'],
            'version_label' => $metadata['version_label'],
        ]);
    }
    ksort($groups, SORT_STRING);
    foreach ($groups as &$group) {
        usort($group['versions'], static function (array $a, array $b): int {
            $aNumber = $a['version_number'] ?? 0;
            $bNumber = $b['version_number'] ?? 0;
            return ($aNumber <=> $bNumber) ?:
                strcasecmp((string)$a['full_name'], (string)$b['full_name']);
        });
        $group['grouped'] = count($group['versions']) > 1;
    }
    unset($group);
    return array_values($groups);
}

/**
 * The database must be provisioned outside the public CMS by a privileged
 * catalog sync service, readable by but not owned by the PHP-FPM user.
 */
function cms_github_catalog_records(): array
{
    cms_need_admin();
    $path = getenv('SIRUS_GITHUB_CATALOG_FILE');
    if (!is_string($path) || $path === '') {
        return ['state' => 'Catalog path not configured', 'groups' => []];
    }
    if (!str_starts_with($path, '/') || str_contains($path, "\0") ||
        str_contains($path, '?') || str_contains($path, '#') ||
        is_link($path) || !is_file($path)) {
        return ['state' => 'Catalog file unavailable', 'groups' => []];
    }
    $real = realpath($path);
    $site = cms_site_root();
    if ($real === false || $real === $site || str_starts_with($real, $site . '/')) {
        return ['state' => 'Catalog file cannot be publicly served', 'groups' => []];
    }
    try {
        // SQLite URI mode=ro prevents the web process from creating or
        // mutating this Python-owned catalog database.
        $db = new PDO('sqlite:file:' . $real . '?mode=ro', null, null, [
            PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION,
            PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
        ]);
        $rows = $db->query('SELECT full_name, github_private, site_visibility,
            description, branch, archived, html_url, sync_status, hosting_requested,
            checked FROM projects ORDER BY full_name LIMIT 501')->fetchAll();
        if (count($rows) > 500) {
            throw new RuntimeException('Catalog exceeds the 500-repository admin display limit');
        }
        foreach ($rows as $row) {
            if (!in_array($row['site_visibility'], ['private', 'public'], true)) {
                throw new RuntimeException('Invalid catalog visibility value');
            }
        }
        return ['state' => 'Available (read-only)', 'groups' => cms_github_grouped_projects($rows)];
    } catch (Throwable $error) {
        error_log('Sirus GitHub catalog read failure: ' . get_class($error));
        return ['state' => 'Catalog could not be read', 'groups' => []];
    }
}

function cms_github_projects_page(PDO $db): void
{
    cms_need_admin();
    $catalog = cms_github_catalog_records();
    cms_admin_shell_start($db, 'GitHub repositories');
    ?>
    <div class="admin-panel">
      <h2>Discovered project families</h2>
      <p>Repository metadata is read from the separate private catalog service.
      Versions are grouped by normalized repository names. This page is
      informational only: discovery never publishes projects, reveals
      private source code, changes visibility, or deploys software.</p>
      <p>Catalog: <strong><?=cms_esc($catalog['state'])?></strong></p>
      <?php if(!$catalog['groups']): ?><p>No cached GitHub projects available.</p><?php endif; ?>
    </div>
    <?php foreach($catalog['groups'] as $group): ?>
    <div class="admin-panel">
      <h2><?=cms_esc($group['label'])?>
        <?php if($group['grouped']): ?><small>Version family</small><?php endif; ?>
      </h2>
      <div class="table-scroll"><table>
        <thead><tr><th>Repository</th><th>Version</th><th>GitHub</th>
          <th>CMS listing</th><th>Sync</th><th>Hosting requested</th></tr></thead>
        <tbody>
        <?php foreach($group['versions'] as $entry): ?>
        <tr><td><?=cms_esc($entry['full_name'])?></td>
          <td><?=cms_esc($entry['version_label'])?></td>
          <td><?=((int)$entry['github_private'])===1?'Private':'Public'?></td>
          <td><?=cms_esc($entry['site_visibility'])?></td>
          <td><?=cms_esc($entry['sync_status'])?></td>
          <td><?=((int)$entry['hosting_requested'])===1?'Requested':'Not requested'?></td></tr>
        <?php endforeach; ?>
        </tbody>
      </table></div>
    </div>
    <?php endforeach; ?>
    <?php cms_admin_shell_end($db);
}
