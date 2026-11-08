<?php
declare(strict_types=1);
/** Subprocess test: exercises the same CLI an administrator would run. */
if (!extension_loaded('pdo_sqlite') || !extension_loaded('sodium')) {
    fwrite(STDERR, "pdo_sqlite and sodium required\n");
    exit(2);
}
function cliCheck(bool $value, string $message): void
{
    if (!$value) { throw new RuntimeException($message); }
}
function cliCall(array $args): array
{
    $command = [PHP_BINARY, dirname(__DIR__) . '/bin/storage-tool.php', ...$args];
    $process = proc_open($command, [1 => ['pipe', 'w'], 2 => ['pipe', 'w']], $pipes);
    if (!is_resource($process)) { throw new RuntimeException('Cannot launch CLI'); }
    $out = stream_get_contents($pipes[1]);
    $err = stream_get_contents($pipes[2]);
    fclose($pipes[1]);
    fclose($pipes[2]);
    return [proc_close($process), (string)$out, (string)$err];
}
$dir = sys_get_temp_dir() . '/sirus-cli-' . bin2hex(random_bytes(8));
if (!mkdir($dir, 0700)) { throw new RuntimeException('mkdir failed'); }
try {
    $source = $dir . '/source.sqlite';
    $database = new PDO('sqlite:' . $source, null, null, [PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION]);
    $database->exec('PRAGMA journal_mode=WAL');
    $database->exec('CREATE TABLE records (id INTEGER PRIMARY KEY, data TEXT NOT NULL)');
    $database->prepare('INSERT INTO records(data) VALUES (?)')->execute(['original']);
    $archive = $dir . '/snapshot.ssb';
    $manifest = $dir . '/snapshot.json';
    $restored = $dir . '/restored.sqlite';
    $keyFile = $dir . '/backup.key';
    file_put_contents($keyFile, random_bytes(32));
    chmod($keyFile, 0600);
    $common = ['--project=example', '--archive=' . $archive,
        '--manifest=' . $manifest, '--key-file=' . $keyFile];
    [$status, $output, $error] = cliCall([
        'backup', ...$common, '--db=' . $source, '--schema-version=2'
    ]);
    cliCheck($status === 0, 'backup CLI failed: ' . $error);
    $result = json_decode($output, true, 20, JSON_THROW_ON_ERROR);
    cliCheck($result['result']['schema_version'] === 2, 'backup metadata');
    cliCheck(is_file($archive) && is_file($manifest), 'encrypted archive created');
    [$status, $output] = cliCall(['verify', ...$common]);
    cliCheck($status === 0, 'CLI verify');
    $database->prepare('INSERT INTO records(data) VALUES (?)')->execute(['later record']);
    [$status, $output, $error] = cliCall(['restore', ...$common, '--target=' . $restored]);
    cliCheck($status === 0, 'restore CLI failed: ' . $error);
    $restoredDb = new PDO('sqlite:' . $restored);
    cliCheck($restoredDb->query('SELECT COUNT(*) FROM records')->fetchColumn() == 1,
        'restore should only contain snapshot records');
    [$status, $output] = cliCall(['preflight', '--db=' . $restored, '--target-driver=mariadb']);
    cliCheck($status === 0, 'preflight must return blocker report successfully');
    cliCheck(json_decode($output, true)['result']['can_migrate_automatically'] === false,
        'cross-engine migration remains blocked');
    [$status, $output, $error] = cliCall(['compare', '--db=' . $source, '--other-db=' . $restored]);
    cliCheck($status !== 0 && str_contains($error, 'fingerprints differ'),
        'migration comparison must fail after source changes');
    [$status, $output] = cliCall(['restore', ...$common, '--target=' . $restored]);
    cliCheck($status !== 0, 'CLI restore must refuse overwrite');
    chmod($keyFile, 0644);
    [$status, $output] = cliCall(['verify', ...$common]);
    cliCheck($status !== 0, 'world-readable key must be rejected');
    $restoredDb = null;
    $database = null;
    echo "PASS: real backup/verify/restore/preflight/compare CLI, key mode and non-overwrite checks\n";
} finally {
    foreach (glob($dir . '/*') ?: [] as $file) { @unlink($file); }
    @rmdir($dir);
}
