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
    claimed_at INTEGER NOT NULL DEFAULT 0,
    lease_token TEXT NOT NULL DEFAULT ''
)
SQL);
        $cols=$this->db->query('PRAGMA table_info(work_jobs)')->fetchAll(PDO::FETCH_ASSOC);
        if(!in_array('lease_token',array_column($cols,'name'),true)){
            $this->db->exec("ALTER TABLE work_jobs ADD COLUMN lease_token TEXT NOT NULL DEFAULT ''");
        }
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

    private static function nodeId(string $id): bool {
        return (bool)preg_match('/^[a-z][a-z0-9-]{1,63}$/D',$id);
    }
    private static function jobId(string $id): bool {
        return (bool)preg_match('/^[a-f0-9]{32}$/D',$id);
    }
    /** Called only after the authenticated CMS administrator authorizes work. */
    public function enqueue(string $nodeId,string $kind,?int $now=null): string {
        if(!self::nodeId($nodeId)||!in_array($kind,['health_snapshot','compute_selftest'],true))
            throw new InvalidArgumentException('Unsupported node or Gentoo work type.');
        $now??=time();
        $this->db->beginTransaction();
        try{
            $st=$this->db->prepare("SELECT COUNT(*) FROM work_jobs WHERE node_id=?
                                   AND state IN ('queued','running')");
            $st->execute([$nodeId]);
            if((int)$st->fetchColumn()>=32)
                throw new InvalidArgumentException('Too many outstanding tasks for this node.');
            $requestId=bin2hex(random_bytes(16));
            $this->db->prepare("INSERT INTO work_jobs
                (request_id,node_id,kind,state,created_at,updated_at)
                VALUES(?,?,?,'queued',?,?)")
                ->execute([$requestId,$nodeId,$kind,$now,$now]);
            $this->db->commit();
            return $requestId;
        }catch(Throwable $e){
            if($this->db->inTransaction())$this->db->rollBack();
            throw $e;
        }
    }

    /** Node-authenticated pull/ack only. Ubuntu never runs delegated work. */
    public function request(string $body,string $signature,callable $resolveKey,?int $now=null): array {
        $now??=time();
        if(strlen($body)>12000 || $body==='' || !preg_match('/^[a-f0-9]{64}$/D',$signature))
            throw new InvalidArgumentException('Invalid work request envelope.');
        $message=json_decode($body,true,12,JSON_THROW_ON_ERROR);
        if(!is_array($message) || array_is_list($message) ||
           array_diff(array_keys($message),[
               'node_id','sent_at','nonce','op','request_id','lease_token','state','result','error']))
            throw new InvalidArgumentException('Invalid work request attributes.');
        $id=$message['node_id']??null;
        $ts=$message['sent_at']??null;
        $nonce=$message['nonce']??null;
        $op=$message['op']??null;
        if(!is_string($id)||!self::nodeId($id) ||
           !is_int($ts)||abs($now-$ts)>120 ||
           !is_string($nonce)||!self::jobId($nonce) ||
           !in_array($op,['claim','complete','status','history'],true))
            throw new InvalidArgumentException('Invalid signed worker identity.');
        $key=$resolveKey($id);
        if(!is_string($key)||strlen($key)!==32 ||
           !hash_equals(hash_hmac('sha256',$body,$key),$signature))
            throw new RuntimeException('Node authentication failed.');
        $requestId=$message['request_id']??null;
        if(in_array($op,['claim','history'],true) && count($message)!==4)
            throw new InvalidArgumentException('Claim must not include task arguments.');
        if($op==='status' && (count($message)!==5 ||
            !is_string($requestId)||!self::jobId($requestId)))
            throw new InvalidArgumentException('Invalid status request.');
        if($op==='complete'){
            $lease=$message['lease_token']??null;
            $state=$message['state']??null;
            $result=$message['result']??null;
            $error=$message['error']??null;
            if(count($message)!==9 || !is_string($requestId)||!self::jobId($requestId) ||
               !is_string($lease)||!self::jobId($lease) ||
               !in_array($state,['done','failed'],true) ||
               !is_array($result)||array_is_list($result)&&$result!==[] ||
               !is_string($error)||strlen($error)>200 ||
               preg_match('/[\x00-\x08\x0B\x0C\x0E-\x1F]/',$error) ||
               strlen(json_encode($result,JSON_THROW_ON_ERROR))>8192 ||
               ($state==='done' && $error!==''))
                throw new InvalidArgumentException('Invalid completion payload.');
        }
        $this->db->beginTransaction();
        try{
            $this->db->prepare('DELETE FROM work_nonces WHERE seen_at<?')->execute([$now-86400]);
            $this->db->prepare('INSERT INTO work_nonces(node_id,nonce,seen_at) VALUES(?,?,?)')
                     ->execute([$id,$nonce,$now]); // A duplicate nonce aborts and rolls back.
            if($op==='history'){
                $query=$this->db->prepare("SELECT request_id,kind,state,attempts,created_at,
                    updated_at,result_json,error FROM work_jobs WHERE node_id=?
                    ORDER BY created_at DESC,request_id DESC LIMIT 40");
                $query->execute([$id]);
                $jobs=[];
                foreach($query->fetchAll() as $job){
                    $decoded=json_decode((string)$job['result_json'],true);
                    unset($job['result_json']);
                    $job['result']=is_array($decoded)?$decoded:[];
                    $job['created_at']=(int)$job['created_at'];
                    $job['updated_at']=(int)$job['updated_at'];
                    $job['attempts']=(int)$job['attempts'];
                    $jobs[]=$job;
                }
                $this->db->commit();
                return ['ok'=>true,'node_id'=>$id,'jobs'=>$jobs];
            }
            $row=null;
            if($op==='claim'){
                // Bounded lease means a disconnected Gentoo node can reclaim after restart.
                $this->db->prepare("UPDATE work_jobs SET state='failed',
                    error='Node lease expired after three attempts',updated_at=?
                    WHERE node_id=? AND state='running' AND claimed_at<? AND attempts>=3")
                    ->execute([$now,$id,$now-120]);
                $this->db->prepare("UPDATE work_jobs SET state='queued',lease_token='',
                    updated_at=? WHERE node_id=? AND state='running'
                    AND claimed_at<? AND attempts<3")
                    ->execute([$now,$id,$now-120]);
                $st=$this->db->prepare("SELECT * FROM work_jobs WHERE node_id=? AND state='queued'
                    ORDER BY created_at,request_id LIMIT 1");
                $st->execute([$id]);$row=$st->fetch();
                if($row){
                    $lease=bin2hex(random_bytes(16));
                    $this->db->prepare("UPDATE work_jobs SET state='running',
                      attempts=attempts+1,claimed_at=?,updated_at=?,lease_token=?
                      WHERE request_id=? AND node_id=? AND state='queued'")
                      ->execute([$now,$now,$lease,$row['request_id'],$id]);
                    $row['state']='running';
                    $row['lease_token']=$lease;
                }
            }else{
                $st=$this->db->prepare('SELECT * FROM work_jobs WHERE node_id=? AND request_id=?');
                $st->execute([$id,$requestId]);$row=$st->fetch();
                if(!$row)throw new InvalidArgumentException('Assigned job not found.');
                if($op==='complete'){
                    $resultJson=json_encode($result,JSON_THROW_ON_ERROR);
                    if(!hash_equals((string)$row['lease_token'],$lease))
                        throw new InvalidArgumentException('Incorrect job lease.');
                    if(in_array($row['state'],['done','failed'],true)){
                        // Completion may have succeeded while the HTTPS reply was lost.
                        // The identical lease and immutable result may be acknowledged again.
                        if($row['state']!==$state ||
                           $row['result_json']!==$resultJson || $row['error']!==$error)
                            throw new InvalidArgumentException('Conflicting job completion.');
                    }else{
                        if((int)$row['claimed_at']+120 < $now || $row['state']!=='running')
                            throw new InvalidArgumentException('Stale or expired job lease.');
                        $this->db->prepare("UPDATE work_jobs
                          SET state=?,result_json=?,error=?,updated_at=?
                          WHERE request_id=? AND node_id=? AND state='running'")
                          ->execute([$state,$resultJson,$error,$now,$requestId,$id]);
                        $row['state']=$state;
                        $row['result_json']=$resultJson;
                        $row['error']=$error;
                    }
                }
            }
            $this->db->commit();
            if(!$row){
                return ['ok'=>true,'state'=>'idle','job'=>null,'poll_after'=>4];
            }
            $response=['ok'=>true,'request_id'=>$row['request_id'],
                'kind'=>$row['kind'],'state'=>$row['state']];
            if($op==='claim')$response['lease_token']=$row['lease_token'];
            if($op!=='claim'){
                $decoded=json_decode((string)$row['result_json'],true);
                $response['result']=is_array($decoded)?$decoded:[];
                $response['error']=$row['error'];
            }
            return $response;
        }catch(Throwable $e){
            if($this->db->inTransaction())$this->db->rollBack();
            throw $e;
        }
    }

    /** Administrator-only view: IDs, status and timestamps, no secrets. */
    public function recent(int $limit=30): array {
        $limit=max(1,min(100,$limit));
        return $this->db->query("SELECT request_id,node_id,kind,state,created_at,updated_at,
                                 attempts,result_json,error FROM work_jobs ORDER BY created_at DESC LIMIT ".$limit)
                        ->fetchAll();
    }
}
