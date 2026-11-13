<?php
declare(strict_types=1);
require dirname(__DIR__) . '/app/storage_backup.php';
function backupCheck(bool $value, string $message): void {
    if (!$value) { throw new RuntimeException($message); }
}
if (!extension_loaded('pdo_sqlite') || !extension_loaded('sodium')) {
    fwrite(STDERR, "pdo_sqlite and sodium required for backup test\n");
    exit(2);
}
$dir = sys_get_temp_dir() . '/sirus-backup-' . bin2hex(random_bytes(8));
if (!mkdir($dir, 0700)) { throw new RuntimeException('mkdir failed'); }
try {
    $source = $dir . '/live.sqlite';
    $pdo = new PDO('sqlite:' . $source, null, null, [PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION]);
    $pdo->exec('PRAGMA journal_mode=WAL');
    $pdo->exec('CREATE TABLE examples (id INTEGER PRIMARY KEY, sample TEXT NOT NULL)');
    $insert = $pdo->prepare('INSERT INTO examples(sample) VALUES (?)');
    $insert->execute([str_repeat('original', 22000)]);
    $archive = $dir . '/snapshot.ssb';
    $manifestFile = $dir . '/snapshot.json';
    $key = random_bytes(SODIUM_CRYPTO_SECRETSTREAM_XCHACHA20POLY1305_KEYBYTES);
    $manifest = SirusEncryptedSqliteBackup::create('example', $source, $archive, $manifestFile, $key, 3);
    backupCheck($manifest['schema_version'] === 3, 'schema version');
    backupCheck($manifest['database_bytes'] > 65536, 'multi-block snapshot');
    backupCheck(SirusEncryptedSqliteBackup::verify($archive, $manifestFile, $key, 'example')['format_version'] === 1, 'round-trip verification');
    $insert->execute(['new write after snapshot']);
    $restored = $dir . '/restored.sqlite';
    SirusEncryptedSqliteBackup::restore($archive, $manifestFile, $restored, $key, 'example');
    $restoredDb = new PDO('sqlite:' . $restored);
    backupCheck((int)$restoredDb->query('SELECT COUNT(*) FROM examples')->fetchColumn() === 1, 'snapshot excludes later writes');
    $restoredDb = null;
    try {
        SirusEncryptedSqliteBackup::restore($archive, $manifestFile, $restored, $key, 'example');
        throw new RuntimeException('Restore overwrote existing target');
    } catch (InvalidArgumentException $expected) {}
    try {
        SirusEncryptedSqliteBackup::verify($archive, $manifestFile, random_bytes(32), 'example');
        throw new RuntimeException('Wrong key accepted');
    } catch (RuntimeException $expected) {}
    try {
        SirusEncryptedSqliteBackup::verify($archive, $manifestFile, $key, 'other');
        throw new RuntimeException('Wrong project accepted');
    } catch (RuntimeException $expected) {}
    $handle = fopen($archive, 'r+b');
    fseek($handle, -5, SEEK_END);
    fwrite($handle, 'XXXXX');
    fclose($handle);
    try {
        SirusEncryptedSqliteBackup::verify($archive, $manifestFile, $key, 'example');
        throw new RuntimeException('Tampered ciphertext accepted');
    } catch (RuntimeException $expected) {}
    echo "PASS: WAL-consistent encrypted backup, restore, integrity and tamper checks\n";
    $pdo = null;
} finally {
    foreach (glob($dir . '/*') ?: [] as $path) { @unlink($path); }
    @rmdir($dir);
}
