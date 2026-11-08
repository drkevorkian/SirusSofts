<?php
declare(strict_types=1);

/**
 * Offline, operator-controlled storage recovery CLI.
 * Never pass encryption keys via CLI args; only read a protected key file.
 * No automatic migration/cutover, deletion, or HTTP execution.
 */
if (PHP_SAPI !== 'cli') {
    http_response_code(404);
    exit;
}
require dirname(__DIR__) . '/app/storage_adapters.php';
require dirname(__DIR__) . '/app/storage_backup.php';
require dirname(__DIR__) . '/app/storage_migration.php';
require dirname(__DIR__) . '/app/storage_github.php';

function sirusStorageUsage(): never
{
    fwrite(STDERR, "Usage:\n"
        . " php CMS/bin/storage-tool.php backup --project=ID --db=/private/source.sqlite --archive=/private/file.ssb --manifest=/private/file.json --key-file=/private/key [--schema-version=N]\n"
        . " php CMS/bin/storage-tool.php verify --project=ID --archive=... --manifest=... --key-file=...\n"
        . " php CMS/bin/storage-tool.php restore --project=ID --archive=... --manifest=... --key-file=... --target=/private/new.sqlite\n"
        . " php CMS/bin/storage-tool.php preflight --db=/private/source.sqlite [--target-driver=sqlite|mysql|mariadb]\n"
        . " php CMS/bin/storage-tool.php compare --db=/private/source.sqlite --other-db=/private/target.sqlite\n"
        . " php CMS/bin/storage-tool.php publish-github --project=ID --repo=owner/PRIVATE-repo --archive=... --manifest=... --key-file=... --token-file=/private/github.token\n"
        . " php CMS/bin/storage-tool.php fetch-github --project=ID --repo=owner/PRIVATE-repo --prefix=backups/ID/YYYYMMDDTHHMMSSZ-HEX --directory=/private/destination --key-file=... --token-file=...\n");
    exit(2);
}

function sirusStorageOptions(array $argv): array
{
    if (count($argv) < 3) { sirusStorageUsage(); }
    $command = $argv[1];
    $allowed = match ($command) {
        'backup' => ['project', 'db', 'archive', 'manifest', 'key-file', 'schema-version'],
        'verify' => ['project', 'archive', 'manifest', 'key-file'],
        'restore' => ['project', 'archive', 'manifest', 'key-file', 'target'],
        'preflight' => ['db', 'target-driver'],
        'compare' => ['db', 'other-db'],
        'publish-github' => ['project', 'repo', 'archive', 'manifest', 'key-file', 'token-file'],
        'fetch-github' => ['project', 'repo', 'prefix', 'directory', 'key-file', 'token-file'],
        default => sirusStorageUsage(),
    };
    $options = [];
    foreach (array_slice($argv, 2) as $arg) {
        if (!is_string($arg) || !preg_match('/^--([a-z][a-z-]*)=(.+)$/D', $arg, $matches)) {
            sirusStorageUsage();
        }
        if (!in_array($matches[1], $allowed, true) || isset($options[$matches[1]])) {
            sirusStorageUsage();
        }
        $options[$matches[1]] = $matches[2];
    }
    $required = match ($command) {
        'backup' => ['project', 'db', 'archive', 'manifest', 'key-file'],
        'verify' => ['project', 'archive', 'manifest', 'key-file'],
        'restore' => ['project', 'archive', 'manifest', 'key-file', 'target'],
        'preflight' => ['db'],
        'compare' => ['db', 'other-db'],
        'publish-github' => ['project', 'repo', 'archive', 'manifest', 'key-file', 'token-file'],
        'fetch-github' => ['project', 'repo', 'prefix', 'directory', 'key-file', 'token-file'],
    };
    foreach ($required as $name) {
        if (!isset($options[$name])) { sirusStorageUsage(); }
    }
    return [$command, $options];
}

function sirusStorageKey(string $path): string
{
    if (!str_starts_with($path, '/') || str_contains($path, "\0") || is_link($path) ||
        !is_file($path)) {
        throw new InvalidArgumentException('Encryption key file must be a private regular file');
    }
    $mode = fileperms($path);
    if ($mode === false || ($mode & 0077) !== 0) {
        throw new InvalidArgumentException('Key file must not be accessible to group or others');
    }
    $key = file_get_contents($path);
    if ($key === false || strlen($key) !== SODIUM_CRYPTO_SECRETSTREAM_XCHACHA20POLY1305_KEYBYTES) {
        throw new InvalidArgumentException('Encryption key file must contain exactly 32 binary bytes');
    }
    return $key;
}

function sirusStorageDb(string $file): PDO
{
    return (new SirusSqliteAdapter())->connect(['path' => $file], static fn(): string => '');
}

try {
    [$action, $o] = sirusStorageOptions($argv);
    switch ($action) {
        case 'backup':
            $schema = $o['schema-version'] ?? '0';
            if (!preg_match('/^(0|[1-9][0-9]{0,8})$/D', $schema)) {
                throw new InvalidArgumentException('Invalid schema version');
            }
            // Validate original DB path before a backup can be created.
            $source = sirusStorageDb($o['db']);
            $source = null;
            $result = SirusEncryptedSqliteBackup::create(
                $o['project'], $o['db'], $o['archive'], $o['manifest'],
                sirusStorageKey($o['key-file']), (int)$schema
            );
            break;
        case 'verify':
            $result = SirusEncryptedSqliteBackup::verify(
                $o['archive'], $o['manifest'], sirusStorageKey($o['key-file']), $o['project']
            );
            break;
        case 'restore':
            $result = SirusEncryptedSqliteBackup::restore(
                $o['archive'], $o['manifest'], $o['target'],
                sirusStorageKey($o['key-file']), $o['project']
            );
            break;
        case 'preflight':
            $result = SirusSqliteMigration::preflight(
                sirusStorageDb($o['db']), $o['target-driver'] ?? 'sqlite'
            );
            break;
        case 'fetch-github':
            $result = SirusPrivateGithubBackup::fetch(
                $o['repo'], $o['project'], $o['prefix'], $o['directory'],
                sirusStorageKey($o['key-file']), $o['token-file']
            );
            break;
        case 'publish-github':
            $result = SirusPrivateGithubBackup::publish(
                $o['repo'], $o['project'], $o['archive'], $o['manifest'],
                sirusStorageKey($o['key-file']), $o['token-file']
            );
            break;
        case 'compare':
            $result = SirusSqliteMigration::verify(
                sirusStorageDb($o['db']), sirusStorageDb($o['other-db'])
            );
            break;
    }
    // No secrets, raw record data, database paths or private connection strings printed.
    fwrite(STDOUT, json_encode(['ok' => true, 'action' => $action, 'result' => $result],
        JSON_THROW_ON_ERROR | JSON_PRETTY_PRINT | JSON_UNESCAPED_SLASHES) . "\n");
} catch (Throwable $error) {
    fwrite(STDERR, 'FAILED ' . $action . ': ' . get_class($error) . ' — ' . $error->getMessage() . "\n");
    exit(1);
}
