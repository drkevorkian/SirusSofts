<?php
declare(strict_types=1);
if (!defined('SIRUS_CMS_BOOTSTRAPPED')) { http_response_code(404); exit; }

/**
 * Planned hosting metadata, not live deployment configuration. No shell access,
 * credentials, application activation or storage cutover is permitted here.
 */
function cms_hosting_defaults(string $slug): array
{
    return [
        'slug' => $slug,
        'assigned_node' => 'vps-primary',
        'database_backend' => 'sqlite',
        'source_repository' => '',
        'source_ref' => '',
        'release_policy' => 'hold',
        'updated_at' => '',
    ];
}

function cms_hosting_config(PDO $db, string $slug): array
{
    if (!cms_safe_slug($slug)) {
        throw new InvalidArgumentException('Invalid project slug');
    }
    $stmt = $db->prepare('SELECT * FROM cms_hosting_preferences WHERE slug=?');
    $stmt->execute([$slug]);
    return $stmt->fetch(PDO::FETCH_ASSOC) ?: cms_hosting_defaults($slug);
}

function cms_hosting_validate(array $input): array
{
    $node = $input['assigned_node'] ?? null;
    $backend = $input['database_backend'] ?? null;
    $repository = $input['source_repository'] ?? null;
    $ref = $input['source_ref'] ?? null;
    $policy = $input['release_policy'] ?? null;
    if (!is_string($node) || !preg_match('/^[a-z][a-z0-9-]{1,63}$/D', $node) ||
        !in_array($backend, ['sqlite', 'mysql', 'mariadb'], true) ||
        !is_string($repository) || strlen($repository) > 130 ||
        !is_string($ref) || strlen($ref) > 120 ||
        !in_array($policy, ['hold', 'manual-review'], true)) {
        throw new InvalidArgumentException('Invalid hosting metadata');
    }
    if ($repository !== '' &&
        (!preg_match('#^[a-zA-Z0-9_.-]+/[a-zA-Z0-9_.-]+$#D', $repository) ||
         str_contains($repository, '..'))) {
        throw new InvalidArgumentException('Invalid source repository name');
    }
    if (($repository === '') !== ($ref === '')) {
        throw new InvalidArgumentException('Repository and ref must be provided together');
    }
    if ($ref !== '' &&
        (!preg_match('#^[A-Za-z0-9._/-]+$#D', $ref) ||
         str_contains($ref, '..') || str_contains($ref, '//') ||
         str_starts_with($ref, '/') || str_starts_with($ref, '-') ||
         str_ends_with($ref, '/') || str_ends_with($ref, '.lock'))) {
        throw new InvalidArgumentException('Invalid Git ref');
    }
    return [
        'assigned_node' => $node, 'database_backend' => $backend,
        'source_repository' => $repository, 'source_ref' => $ref,
        'release_policy' => $policy,
    ];
}

function cms_hosting_save(PDO $db, string $slug, array $input): array
{
    cms_need_admin();
    if (!cms_safe_slug($slug) ||
        in_array($slug, ['cpoint', 'worldcom', 'sentinel-trader', 'sentinal-trader'], true)) {
        throw new InvalidArgumentException('Protected or invalid project route');
    }
    $exists = $db->prepare('SELECT 1 FROM cms_projects WHERE slug=?');
    $exists->execute([$slug]);
    if (!$exists->fetchColumn()) {
        throw new InvalidArgumentException('Project must exist before saving hosting preferences');
    }
    $validated = cms_hosting_validate($input);
    $now = cms_now();
    $db->prepare('INSERT INTO cms_hosting_preferences
      (slug,assigned_node,database_backend,source_repository,source_ref,release_policy,updated_at)
      VALUES (?,?,?,?,?,?,?) ON CONFLICT(slug) DO UPDATE SET
      assigned_node=excluded.assigned_node,database_backend=excluded.database_backend,
      source_repository=excluded.source_repository,source_ref=excluded.source_ref,
      release_policy=excluded.release_policy,updated_at=excluded.updated_at')
      ->execute([$slug, $validated['assigned_node'], $validated['database_backend'],
          $validated['source_repository'], $validated['source_ref'],
          $validated['release_policy'], $now]);
    return cms_hosting_config($db, $slug);
}
