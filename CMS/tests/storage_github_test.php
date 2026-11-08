<?php
declare(strict_types=1);
require dirname(__DIR__) . '/app/storage_github.php';
function githubCheck(bool $ok, string $message): void {
    if (!$ok) { throw new RuntimeException($message); }
}
if (!extension_loaded('pdo_sqlite') || !extension_loaded('sodium')) {
    fwrite(STDERR, "PDO_SQLITE and sodium required\n");
    exit(2);
}
$dir = sys_get_temp_dir() . '/sirus-github-' . bin2hex(random_bytes(8));
if (!mkdir($dir, 0700)) { throw new RuntimeException('mkdir failed'); }
try {
    $dbPath = $dir . '/example.sqlite';
    $db = new PDO('sqlite:' . $dbPath);
    $db->exec('CREATE TABLE private_data (value TEXT)');
    $db->exec("INSERT INTO private_data VALUES ('SECRET-DB-RECORD')");
    $db = null;
    $key = random_bytes(32);
    $archive = $dir . '/example.ssb';
    $manifest = $dir . '/example.json';
    SirusEncryptedSqliteBackup::create('example', $dbPath, $archive, $manifest, $key, 1);
    githubCheck(!str_contains((string)file_get_contents($archive), 'SECRET-DB-RECORD'),
        'Plaintext private data appeared in encrypted snapshot');
    $tokenFile = $dir . '/github.token';
    file_put_contents($tokenFile, 'github_pat_TEST_ONLY_123456789abcdef');
    chmod($tokenFile, 0600);
    $uploads = [];
    $mock = static function ($method, $url, $payload, $token) use (&$uploads): array {
        githubCheck($token === 'github_pat_TEST_ONLY_123456789abcdef', 'Wrong private token');
        githubCheck(str_starts_with($url, 'https://api.github.com/repos/owner/private-backups'),
            'Unexpected GitHub endpoint');
        if ($method === 'GET') {
            return ['status' => 200, 'data' => ['full_name' => 'owner/private-backups', 'private' => true]];
        }
        githubCheck($method === 'PUT' && !isset($payload['sha']), 'Upload must be create-only');
        githubCheck(!str_contains($payload['content'], 'SECRET-DB-RECORD'), 'Plaintext leaked');
        $uploads[] = ['url' => $url, 'data' => base64_decode($payload['content'], true)];
        return ['status' => 201, 'data' => ['commit' => ['sha' => str_repeat('a', 40)]]];
    };
    $result = SirusPrivateGithubBackup::publish(
        'owner/private-backups', 'example', $archive, $manifest, $key, $tokenFile, $mock
    );
    githubCheck(count($uploads) === 2, 'Both encrypted archive and manifest uploaded');
    githubCheck($uploads[0]['data'] === file_get_contents($archive), 'Ciphertext differs');
    githubCheck($uploads[1]['data'] === file_get_contents($manifest), 'Manifest differs');
    githubCheck(str_ends_with($uploads[1]['url'], '/manifest.json'), 'Manifest must be uploaded last');
    githubCheck($result['manifest_uploaded_last'] === true, 'Completion marker missing');
    $publicCalls = 0;
    $publicMock = static function ($method) use (&$publicCalls): array {
        $publicCalls++;
        if ($method === 'GET') {
            return ['status' => 200, 'data' => ['full_name' => 'owner/private-backups', 'private' => false]];
        }
        throw new RuntimeException('Public destination PUT must not occur');
    };
    try {
        SirusPrivateGithubBackup::publish('owner/private-backups', 'example',
            $archive, $manifest, $key, $tokenFile, $publicMock);
        throw new RuntimeException('Public repository accepted');
    } catch (RuntimeException $expected) {
        githubCheck(str_contains($expected->getMessage(), 'not verified as private'), 'Wrong public refusal');
    }
    githubCheck($publicCalls === 1, 'Publisher sent files to public repository');
    chmod($tokenFile, 0644);
    try {
        SirusPrivateGithubBackup::publish('owner/private-backups', 'example',
            $archive, $manifest, $key, $tokenFile, $mock);
        throw new RuntimeException('World-readable token accepted');
    } catch (InvalidArgumentException $expected) {}
    chmod($tokenFile, 0600);
    $prior = count($uploads);
    try {
        SirusPrivateGithubBackup::publish('owner/private-backups', 'example',
            $archive, $manifest, random_bytes(32), $tokenFile, $mock);
        throw new RuntimeException('Wrong backup encryption key accepted');
    } catch (RuntimeException $expected) {}
    githubCheck(count($uploads) === $prior, 'Wrong key triggered network upload');
    try {
        SirusPrivateGithubBackup::publish('owner/public;danger', 'example',
            $archive, $manifest, $key, $tokenFile, $mock);
        throw new RuntimeException('Repository path injection accepted');
    } catch (InvalidArgumentException $expected) {}
    echo "PASS: encrypted private GitHub backup publication, validation and refusal paths (mock transport)\n";
} finally {
    foreach (glob($dir . '/*') ?: [] as $file) { @unlink($file); }
    @rmdir($dir);
}
