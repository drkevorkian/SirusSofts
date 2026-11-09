<?php
declare(strict_types=1);
if(!defined('SIRUS_CMS_BOOTSTRAPPED')){http_response_code(404);exit;}

/** Signed, replay-resistant, metadata-only broker; NEVER executes commands. */
final class SirusWorkBroker {
    private PDO $db;

    public function __construct(string $path) {
        if(!str_starts_with($path,'/') || str_contains($path,"\0") ||
           is_link($path) || !is_dir(dirname($path)) || !is_writable(dirname($path)))
            throw new RuntimeException('Work broker private database path unavailable.');
        $public=realpath(dirname(__DIR__));
        $dir=realpath(dirname($path));
        if($dir===false || ($public!==false &&
           ($dir===$public || str_starts_with($dir,$public.'/'))))
            throw new RuntimeException('Work broker database must not be public.');
        $this->db=new PDO('sqlite:'.$path,null,null,[
            PDO::ATTR_ERRMODE=>PDO::ERRMODE_EXCEPTION,
            PDO::ATTR_DEFAULT_FETCH_MODE=>PDO::FETCH_ASSOC
        ]);
        $this->db->exec('PRAGMA busy_timeout=5000');
        $this->db->exec('PRAGMA journal_mode=WAL');
        $this->db->exec(<<<'SQL'
CREATE TABLE IF NOT EXISTS work_jobs (
    request_id TEXT PRIMARY KEY,
    node_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('queued','running','done','failed')),
    result_json TEXT NOT NULL DEFAULT '{}',
    error TEXT NOT NULL DEFAULT '',
    attempts INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    claimed_at INTEGER NOT NULL DEFAULT 0
)
SQL);
        $this->db->exec('CREATE INDEX IF NOT EXISTS work_pending ON work_jobs(state,created_at)');
        $this->db->exec(<<<'SQL'
CREATE TABLE IF NOT EXISTS work_nonces (
    node_id TEXT NOT NULL,
    nonce TEXT NOT NULL,
    seen_at INTEGER NOT NULL,
    PRIMARY KEY(node_id,nonce)
)
SQL);
    }

    /** Call ONLY after verifying a node's signature and timestamp. */
    public function request(string $body,string $signature,callable $resolveKey,?int $now=null): array {
        $now??=time();
        if(strlen($body)>4096 || $body==='' || !preg_match('/^[a-f0-9]{64}$/D',$signature))
            throw new InvalidArgumentException('Invalid work request envelope.');
        $message=json_decode($body,true,12,JSON_THROW_ON_ERROR);
        if(!is_array($message) || array_is_list($message) ||
           array_diff(array_keys($message),['node_id','sent_at','nonce','op','request_id','kind','payload']))
            throw new InvalidArgumentException('Invalid work request attributes.');
        $id=$message['node_id']??null;
        $ts=$message['sent_at']??null;
        $nonce=$message['nonce']??null;
        $op=$message['op']??null;
        $rid=$message['request_id']??null;
        if(!is_string($id) || !preg_match('/^[a-z][a-z0-9-]{1,63}$/D',$id) ||
           !is_int($ts) || abs($now-$ts)>120 ||
           !is_string($nonce) || !preg_match('/^[a-f0-9]{32}$/D',$nonce) ||
           !is_string($rid) || !preg_match('/^[a-f0-9]{32}$/D',$rid) ||
           !in_array($op,['submit','status'],true))
            throw new InvalidArgumentException('Invalid work request identity.');
        $key=$resolveKey($id);
        if(!is_string($key) || strlen($key)!==32 ||
           !hash_equals(hash_hmac('sha256',$body,$key),$signature))
            throw new RuntimeException('Node authentication failed.');
        $this->db->exec('BEGIN IMMEDIATE');
        try {
            $this->db->prepare('DELETE FROM work_nonces WHERE seen_at < ?')->execute([$now-86400]);
            $st=$this->db->prepare('INSERT INTO work_nonces(node_id,nonce,seen_at) VALUES(?,?,?)');
            $st->execute([$id,$nonce,$now]);
            if($op==='submit'){
                $kind=$message['kind']??null;
                $payload=$message['payload']??null;
                if(!in_array($kind,['health_snapshot','site_health'],true) ||
                   !is_array($payload) || $payload!==[])
                    throw new InvalidArgumentException('Only approved, parameter-free jobs can be submitted.');
                $lookup=$this->db->prepare('SELECT * FROM work_jobs WHERE request_id=?');
                $lookup->execute([$rid]);$prior=$lookup->fetch();
                if($prior){
                    if($prior['node_id']!==$id || $prior['kind']!==$kind)
                        throw new InvalidArgumentException('Request ID already belongs to another job.');
                    $row=$prior;
                }else{
                    $limit=$this->db->prepare("SELECT COUNT(*) FROM work_jobs
                        WHERE node_id=? AND state IN ('queued','running')");
                    $limit->execute([$id]);
                    if((int)$limit->fetchColumn()>=32)
                        throw new RuntimeException('Too many outstanding jobs for this node.');
                    $this->db->prepare("INSERT INTO work_jobs(request_id,node_id,kind,state,created_at,updated_at)
                        VALUES(?,?,?,'queued',?,?)")->execute([$rid,$id,$kind,$now,$now]);
                    $row=['request_id'=>$rid,'node_id'=>$id,'kind'=>$kind,
                          'state'=>'queued','result_json'=>'{}','error'=>''];
                }
            }else{
                if(array_key_exists('kind',$message) || array_key_exists('payload',$message))
                    throw new InvalidArgumentException('Status cannot submit a job.');
                $lookup=$this->db->prepare('SELECT * FROM work_jobs WHERE request_id=? AND node_id=?');
                $lookup->execute([$rid,$id]);$row=$lookup->fetch();
                if(!$row)throw new InvalidArgumentException('Job not found.');
            }
            $this->db->commit();
            $result=json_decode($row['result_json'],true);
            return [
                'ok'=>true,'request_id'=>$row['request_id'],'kind'=>$row['kind'],
                'state'=>$row['state'],'result'=>is_array($result)?$result:[],
                'error'=>$row['error']
            ];
        }catch(Throwable $e){
            if($this->db->inTransaction())$this->db->rollBack();
            throw $e;
        }
    }

    /** Administrator-only view: IDs, status and timestamps, no secrets. */
    public function recent(int $limit=30): array {
        $limit=max(1,min(100,$limit));
        return $this->db->query("SELECT request_id,node_id,kind,state,created_at,updated_at,
                                 attempts FROM work_jobs ORDER BY created_at DESC LIMIT ".$limit)
                        ->fetchAll();
    }
}
