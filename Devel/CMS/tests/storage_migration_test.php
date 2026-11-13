<?php
declare(strict_types=1);
require dirname(__DIR__) . '/app/storage_migration.php';
if (!extension_loaded('pdo_sqlite')) { fwrite(STDERR, "pdo_sqlite required\n"); exit(2); }
function migrationCheck(bool $value, string $message): void {
    if (!$value) { throw new RuntimeException($message); }
}
$dir = sys_get_temp_dir() . '/sirus-migration-' . bin2hex(random_bytes(8));
if (!mkdir($dir, 0700)) { throw new RuntimeException('mkdir failed'); }
try {
    $make = function () {
        $pdo = new PDO('sqlite::memory:', null, null, [
            PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION,
            PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
        ]);
        $pdo->exec('PRAGMA foreign_keys=ON');
        $pdo->exec('CREATE TABLE records (id INTEGER PRIMARY KEY, text TEXT NOT NULL)');
        $pdo->exec("INSERT INTO records VALUES (1,'x'),(2,'y')");
        return $pdo;
    };
    $source = $make();
    $target = $make();
    migrationCheck(SirusSqliteMigration::preflight($source, 'sqlite')['can_migrate_automatically'],
                   'SQLite preflight should pass');
    migrationCheck(!SirusSqliteMigration::preflight($source, 'mysql')['can_migrate_automatically'],
                   'Cross-engine migration must be blocked');
    migrationCheck(SirusSqliteMigration::verify($source, $target)['verified'], 'Matching database verified');
    $target->exec("INSERT INTO records VALUES (3,'z')");
    try {
        SirusSqliteMigration::verify($source, $target);
        throw new RuntimeException('Data mismatch accepted');
    } catch (RuntimeException $expected) {
        migrationCheck(str_contains($expected->getMessage(), 'fingerprints'), 'Unexpected failure');
    }
    $journal = new SirusMigrationJournal($dir . '/journal.sqlite');
    $id = $journal->begin('example', 'original_sqlite', 'new_sqlite');
    foreach (['preflight_passed', 'snapshot_verified', 'copying', 'target_verified'] as $step) {
        $journal->advance($id, $step);
    }
    migrationCheck($journal->get($id)['state'] === 'target_verified', 'Journal persisted verified stage');
    try {
        $journal->advance($id, 'rolled_back');
        throw new RuntimeException('Skipped activation');
    } catch (InvalidArgumentException $expected) {}
    $journal->advance($id, 'activated');
    $journal->advance($id, 'rolled_back');
    migrationCheck($journal->get($id)['state'] === 'rolled_back', 'Rollback state persisted');
    echo "PASS: SQLite preflight, fingerprints, cross-engine block and migration journal\n";
} finally {
    foreach (glob($dir . '/*') ?: [] as $file) { @unlink($file); }
    @rmdir($dir);
}
