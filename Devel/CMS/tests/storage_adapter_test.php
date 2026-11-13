<?php
declare(strict_types=1);
require dirname(__DIR__) . '/app/storage_adapters.php';
function adapterCheck(bool $ok, string $message): void {
    if (!$ok) { throw new RuntimeException($message); }
}
adapterCheck(SirusStorageAdapters::forDriver('sqlite')->driver() === 'sqlite', 'sqlite factory');
adapterCheck(SirusStorageAdapters::forDriver('mariadb')->driver() === 'mariadb', 'mariadb factory');
adapterCheck(SirusStorageAdapters::forDriver('mysql')->driver() === 'mysql', 'mysql factory');
try {
    SirusStorageAdapters::forDriver('other');
    throw new RuntimeException('Unknown driver accepted');
} catch (InvalidArgumentException $expected) {}
try {
    (new SirusSqliteAdapter())->connect(['path' => '../relative.sqlite'], fn () => '');
    throw new RuntimeException('Relative SQLite path accepted');
} catch (InvalidArgumentException $expected) {}
if (!extension_loaded('pdo_sqlite')) {
    fwrite(STDERR, "pdo_sqlite required for SQLite adapter integration test\n");
    exit(2);
}
$dir = sys_get_temp_dir() . '/sirus-adapter-' . bin2hex(random_bytes(8));
if (!mkdir($dir, 0700)) { throw new RuntimeException('mkdir failed'); }
try {
    $dbPath = $dir . '/project.sqlite';
    $db = new PDO('sqlite:' . $dbPath);
    $db->exec('CREATE TABLE example (id INTEGER PRIMARY KEY, text TEXT NOT NULL)');
    $db->exec("INSERT INTO example(text) VALUES ('hello')");
    $db = null;
    $connection = SirusStorageAdapters::forDriver('sqlite')->connect(['path' => $dbPath], fn () => '');
    adapterCheck($connection->query('SELECT text FROM example')->fetchColumn() === 'hello', 'sqlite data');
    adapterCheck($connection->query('PRAGMA foreign_keys')->fetchColumn() == 1, 'foreign keys enabled');
    $connection = null;
    echo "PASS: storage adapter factory, validation, and SQLite connection\n";
} finally {
    @unlink($dir . '/project.sqlite');
    @rmdir($dir);
}
