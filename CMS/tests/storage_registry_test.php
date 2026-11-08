<?php
declare(strict_types=1);
require dirname(__DIR__) . '/app/storage_registry.php';
if (!extension_loaded('pdo_sqlite')) { fwrite(STDERR, "pdo_sqlite required\n"); exit(2); }
$dir = sys_get_temp_dir() . '/sirus-registry-' . bin2hex(random_bytes(8));
if (!mkdir($dir, 0700)) { throw new RuntimeException('mkdir failed'); }
try {
    $registry = new SirusStorageRegistry($dir . '/registry.sqlite');
    $registry->put('cpoint', 'sqlite', 'cpoint_primary', 'gentoo-home', 1);
    assert($registry->get('cpoint')['driver'] === 'sqlite');
    $registry->put('cpoint', 'mariadb', 'cpoint_mariadb', 'vps-east', 2);
    if ($registry->get('cpoint')['schema_version'] !== 2) { throw new RuntimeException('update failed'); }
    if (count($registry->all()) !== 1) { throw new RuntimeException('duplicate record'); }
    try {
        $registry->put('invalid/path', 'sqlite', 'x', 'vps');
        throw new RuntimeException('invalid ID accepted');
    } catch (InvalidArgumentException $expected) {}
    echo "Storage registry smoke tests passed\n";
} finally {
    @unlink($dir . '/registry.sqlite');
    @rmdir($dir);
}
