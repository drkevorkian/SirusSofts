<?php
declare(strict_types=1);
require_once __DIR__ . '/storage_backup.php';

/**
 * Operator-only encrypted backup upload to a verified PRIVATE GitHub repo.
 * No plaintext database records or decryption keys are transmitted.
 * Manifest is uploaded LAST as the marker of a complete backup set.
 * Incomplete remote ciphertext is deliberately left untouched on failure.
 */
final class SirusPrivateGithubBackup
{
    private const MAX_ARCHIVE = 5 * 1024 * 1024;
    private const MAX_MANIFEST = 65536;

    public static function secret(string $path): string
    {
        if (!str_starts_with($path, '/') || str_contains($path, "\0") ||
            is_link($path) || !is_file($path) ||
            (fileperms($path) & 0077) !== 0 ||
            filesize($path) > 4096) {
            throw new InvalidArgumentException('GitHub token must be in a small private regular file');
        }
        $token = trim((string)file_get_contents($path));
        if (!preg_match('/^[A-Za-z0-9_]{20,200}$/D', $token)) {
            throw new InvalidArgumentException('Invalid GitHub token file format');
        }
        return $token;
    }

    private static function privateFile(string $file, int $maxBytes): string
    {
        if (!str_starts_with($file, '/') || str_contains($file, "\0") ||
            is_link($file) || !is_file($file)) {
            throw new InvalidArgumentException('Backup file must exist outside public document root');
        }
        $mode = fileperms($file);
        $size = filesize($file);
        $public = realpath(dirname(__DIR__));
        $real = realpath($file);
        if ($mode === false || ($mode & 0077) !== 0 || $size === false ||
            $size < 1 || $size > $maxBytes || $real === false ||
            ($public !== false && ($real === $public ||
            str_starts_with($real, $public . '/')))) {
            throw new InvalidArgumentException('Backup path, file permission or snapshot size is unsafe');
        }
        return $real;
    }

    private static function request(string $method, string $url, ?array $payload, string $token): array
    {
        if (!extension_loaded('curl')) {
            throw new RuntimeException('PHP cURL extension is required for GitHub upload');
        }
        $handle = curl_init($url);
        if ($handle === false) { throw new RuntimeException('GitHub connection initialization failed'); }
        try {
            $headers = [
                'Authorization: Bearer ' . $token,
                'Accept: application/vnd.github+json',
                'X-GitHub-Api-Version: 2022-11-28',
                'User-Agent: SirusSofts-Encrypted-Backup/0.1',
                'Content-Type: application/json',
            ];
            curl_setopt_array($handle, [
                CURLOPT_CUSTOMREQUEST => $method,
                CURLOPT_HTTPHEADER => $headers,
                CURLOPT_RETURNTRANSFER => true,
                CURLOPT_FOLLOWLOCATION => false,
                CURLOPT_SSL_VERIFYPEER => true,
                CURLOPT_SSL_VERIFYHOST => 2,
                CURLOPT_CONNECTTIMEOUT => 8,
                CURLOPT_TIMEOUT => 35,
            ]);
            if ($payload !== null) {
                curl_setopt($handle, CURLOPT_POSTFIELDS, json_encode($payload, JSON_THROW_ON_ERROR));
            }
            $response = curl_exec($handle);
            $status = curl_getinfo($handle, CURLINFO_HTTP_CODE);
            if (!is_string($response) || strlen($response) > 100000) {
                throw new RuntimeException('GitHub response could not be read safely');
            }
            $data = json_decode($response, true);
            if (!is_array($data)) {
                throw new RuntimeException('GitHub returned an invalid JSON response');
            }
            return ['status' => $status, 'data' => $data];
        } finally {
            curl_close($handle);
        }
    }

    /**
     * The optional transport is test-only dependency injection. Production CLI
     * always uses verified cURL HTTPS and cannot inject arbitrary URLs.
     */
    public static function publish(
        string $repository, string $project, string $archiveFile,
        string $manifestFile, string $key, string $tokenFile,
        ?callable $transport = null
    ): array {
        if (!preg_match('#^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$#D', $repository) ||
            str_contains($repository, '..') ||
            !preg_match('/^[a-z][a-z0-9-]{1,63}$/D', $project)) {
            throw new InvalidArgumentException('Invalid target repository or project');
        }
        $archiveFile = self::privateFile($archiveFile, self::MAX_ARCHIVE);
        $manifestFile = self::privateFile($manifestFile, self::MAX_MANIFEST);
        $archiveName = basename($archiveFile);
        if (!preg_match('/^[A-Za-z0-9][A-Za-z0-9_.-]{0,100}\.ssb$/D', $archiveName) ||
            basename($manifestFile) === $archiveName) {
            throw new InvalidArgumentException('Encrypted archive requires a safe .ssb filename');
        }
        // Fail before making any HTTP request when source is tampered, wrong-key,
        // inconsistent or not a complete authenticated secretstream archive.
        SirusEncryptedSqliteBackup::verify($archiveFile, $manifestFile, $key, $project);
        $manifest = file_get_contents($manifestFile);
        $ciphertext = file_get_contents($archiveFile);
        if (!is_string($manifest) || !is_string($ciphertext)) {
            throw new RuntimeException('Failed to read locally validated backup');
        }
        $token = self::secret($tokenFile);
        $transport ??= [self::class, 'request'];
        $root = 'https://api.github.com/repos/' . $repository;
        $metadata = $transport('GET', $root, null, $token);
        if (!is_array($metadata) || ($metadata['status'] ?? null) !== 200 ||
            ($metadata['data']['private'] ?? null) !== true ||
            strcasecmp((string)($metadata['data']['full_name'] ?? ''), $repository) !== 0) {
            throw new RuntimeException('Destination repository was not verified as private');
        }
        // Randomized prefix avoids overwrites. No update SHA is ever sent.
        $prefix = 'backups/' . $project . '/' . gmdate('Ymd\THis\Z') . '-' . bin2hex(random_bytes(6));
        $files = [
            $archiveName => $ciphertext,
            'manifest.json' => $manifest,
        ];
        $uploaded = [];
        foreach ($files as $name => $data) {
            $relative = $prefix . '/' . $name;
            $body = [
                'message' => 'Store encrypted SirusSofts recovery file for ' . $project,
                'content' => base64_encode($data),
            ];
            $response = $transport('PUT', $root . '/contents/' . $relative, $body, $token);
            if (!is_array($response) || ($response['status'] ?? null) !== 201 ||
                !preg_match('/^[a-f0-9]{40}$/D', (string)($response['data']['commit']['sha'] ?? ''))) {
                throw new RuntimeException('Encrypted backup upload incomplete; inspect remote orphaned ciphertext manually');
            }
            $uploaded[] = $relative;
        }
        return [
            'repository' => $repository, 'project' => $project, 'prefix' => $prefix,
            'files' => $uploaded, 'manifest_uploaded_last' => true,
            'note' => 'Snapshot remains encrypted; original local database is untouched',
        ];
    }
}
