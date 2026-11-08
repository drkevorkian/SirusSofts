<?php
declare(strict_types=1);
/**
 * Explicit HTTPS heartbeat receiver. Disabled until both environment settings
 * are provisioned by the administrator; do not expose node keys via HTTP.
 */
header('Content-Type: application/json; charset=utf-8');
header('Cache-Control: no-store');
header('X-Content-Type-Options: nosniff');
$https = (!empty($_SERVER['HTTPS']) && strtolower((string)$_SERVER['HTTPS']) !== 'off')
    || (int)($_SERVER['SERVER_PORT'] ?? 0) === 443;
if (($_SERVER['REQUEST_METHOD'] ?? '') !== 'POST' || !$https) {
    http_response_code(405);
    exit('{"ok":false,"error":"HTTPS POST required"}');
}
$keyDir = getenv('SIRUS_NODE_KEY_DIR') ?: '';
$registryPath = getenv('SIRUS_NODE_REGISTRY_FILE') ?: '';
if (!str_starts_with($keyDir, '/') || !str_starts_with($registryPath, '/') ||
    !is_dir($keyDir) || !is_dir(dirname($registryPath))) {
    http_response_code(503);
    exit('{"ok":false,"error":"Node coordinator not configured"}');
}
require __DIR__ . '/app/node_registry.php';
$raw = file_get_contents('php://input', false, null, 0, 8193);
try {
    if (!is_string($raw) || strlen($raw) > 8192) {
        throw new InvalidArgumentException('Heartbeat too large');
    }
    $registry = new SirusNodeRegistry($registryPath);
    $result = $registry->acceptHeartbeat(
        $raw, strtolower((string)($_SERVER['HTTP_X_SIRUS_NODE_SIGNATURE'] ?? '')),
        static function (string $id) use ($keyDir): string {
            $file = $keyDir . '/' . $id . '.key';
            if (is_link($file) || !is_file($file) || (fileperms($file) & 0007) !== 0) {
                return '';
            }
            return (string)file_get_contents($file);
        }
    );
    echo json_encode(['ok' => true] + $result, JSON_THROW_ON_ERROR);
} catch (Throwable $error) {
    error_log('Sirus node heartbeat rejected: ' . get_class($error));
    http_response_code(401);
    echo '{"ok":false,"error":"Heartbeat rejected"}';
}
