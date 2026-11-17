<?php
declare(strict_types=1);
if(!defined('SIRUS_CMS_BOOTSTRAPPED')){http_response_code(404);exit;}

/**
 * Administrator-only Ubuntu facade for durable Gentoo research jobs.
 * PHP never fetches remote source URLs, launches workers or approves AI drafts.
 * The Gentoo input API is reachable only through the existing loopback tunnel.
 */
function cms_ai_research_validate(array $raw):array {
    $kind=$raw['source_type']??null;
    $title=$raw['title']??null;
    $focus=$raw['focus']??'';
    $url=$raw['url']??'';
    $text=$raw['text']??'';
    $pdf=$raw['pdf_sha256']??'';
    $pageStart=$raw['page_start']??1;
    $pageEnd=$raw['page_end']??0;
    $passes=$raw['passes']??null;
    $target=$raw['target']??'auto';
    $model=$raw['model_id']??null;
    if(!in_array($kind,['url','text','pdf'],true))
        throw new InvalidArgumentException('Choose a website, pasted text, or existing extracted PDF.');
    if(!is_string($title)||trim($title)===''||mb_strlen($title,'UTF-8')>160
       ||strlen($title)>300)
        throw new InvalidArgumentException('Research title must be 1–160 characters.');
    if(!is_string($focus)||strlen($focus)>1200 ||
       !is_string($url)||!is_string($text))
        throw new InvalidArgumentException('Invalid research source or focus.');
    if(!is_int($passes)||$passes<1||$passes>100)
        throw new InvalidArgumentException('Research passes must be 1–100.');
    if(!in_array($target,['auto','cpu','nvidia','amd'],true) ||
       !is_string($model)||
       !preg_match('#^[a-zA-Z0-9][a-zA-Z0-9_.:+/-]{0,127}$#D',$model))
        throw new InvalidArgumentException('Choose an available local model and worker.');
    if($kind==='url'){
        $parts=parse_url($url);
        if(strlen($url)>2048||!is_array($parts)||
           strtolower((string)($parts['scheme']??''))!=='https'||
           !is_string($parts['host']??null)||
           isset($parts['user'])||isset($parts['pass'])||
           (isset($parts['port'])&&(int)$parts['port']!==443)||
           filter_var($url,FILTER_VALIDATE_URL)===false||trim($text)!=='')
            throw new InvalidArgumentException('Supply one public HTTPS URL; no pasted text.');
    }elseif($kind==='text'){
        if(strlen(trim($text))<1||mb_strlen(trim($text),'UTF-8')>9000||trim($url)!=='')
            throw new InvalidArgumentException('Pasted text must contain 1–9000 characters; no URL.');
    }else{
        if(!is_string($pdf)||!preg_match('/^[a-f0-9]{64}$/D',$pdf)||
           trim($url)!==''||trim($text)!==''||
           !is_int($pageStart)||!is_int($pageEnd)||
           $pageStart<1||$pageStart>20000||$pageEnd<0||$pageEnd>20000||
           ($pageEnd!==0&&$pageEnd<$pageStart))
            throw new InvalidArgumentException('Select an extracted Gentoo PDF and a valid page range.');
    }
    if($kind!=='pdf'&&($pdf!==''||$pageStart!==1||$pageEnd!==0))
        throw new InvalidArgumentException('PDF options require a PDF source.');
    return [
       'source'=>['source_type'=>$kind,'title'=>trim($title),'focus'=>trim($focus),
                  'url'=>$kind==='url'?trim($url):'',
                  'text'=>$kind==='text'?trim($text):'','passes'=>$passes,
                  'pdf_sha256'=>$kind==='pdf'?$pdf:'',
                  'page_start'=>$kind==='pdf'?$pageStart:1,
                  'page_end'=>$kind==='pdf'?$pageEnd:0],
       'target'=>$target,'model_id'=>$model,'local_only'=>true
    ];
}
function cms_ai_research_authorized(array $identity):string {
    if(($identity['role']??'')!=='admin'||!is_string($identity['account']??null))
        throw new RuntimeException('Administrator research access required.');
    return $identity['account'];
}
function cms_ai_research_submit(PDO $db,array $identity,array $payload):array {
    $account=cms_ai_research_authorized($identity);
    $data=cms_ai_research_validate($payload);
    // Recheck the selected model against live Gentoo inventory.
    $inventory=cms_ai_api_upstream('input','/api/in/health');
    $available=[];
    foreach(($inventory['models']??[]) as $worker=>$models){
        if($data['target']!=='auto'&&$worker!==$data['target'])continue;
        if(is_array($models))foreach($models as $name)if(is_string($name))$available[$name]=true;
    }
    if(($inventory['local_only']??false)!==true||!isset($available[$data['model_id']]))
        throw new RuntimeException('Selected local model is unavailable.');
    $response=cms_ai_api_upstream('input','/api/in/research',$data);
    $job=$response['job_id']??null;
    if(!is_string($job)||!preg_match('/^[a-f0-9]{32}$/D',$job))
        throw new RuntimeException('Gentoo did not return a valid research job ID.');
    $db->prepare('INSERT INTO cms_ai_research_jobs
       (id,account,title,spec,target,model_id,passes,created_at)
       VALUES(?,?,?,?,?,?,?,?)')->execute([
       $job,$account,$data['source']['title'],
       json_encode($data['source'],JSON_THROW_ON_ERROR),
       $data['target'],$data['model_id'],$data['source']['passes'],cms_now()
    ]);
    return ['id'=>$job,'state'=>'queued','passes'=>$data['source']['passes'],'weight_training'=>false];
}
function cms_ai_research_owned(PDO $db,array $identity,string $id):array {
    $account=cms_ai_research_authorized($identity);
    if(!preg_match('/^[a-f0-9]{32}$/D',$id))
        throw new InvalidArgumentException('Invalid research job ID.');
    $st=$db->prepare('SELECT * FROM cms_ai_research_jobs WHERE id=? AND account=?');
    $st->execute([$id,$account]);
    $row=$st->fetch(PDO::FETCH_ASSOC);
    if(!$row)throw new RuntimeException('Research job not found.');
    return $row;
}
function cms_ai_research_list(PDO $db,array $identity):array {
    $account=cms_ai_research_authorized($identity);
    $st=$db->prepare('SELECT id,title,passes,target,model_id,created_at
       FROM cms_ai_research_jobs WHERE account=? ORDER BY created_at DESC LIMIT 50');
    $st->execute([$account]);
    return $st->fetchAll(PDO::FETCH_ASSOC);
}
function cms_ai_research_view(PDO $db,array $identity,string $id):array {
    cms_ai_research_owned($db,$identity,$id);
    $response=cms_ai_api_upstream('input','/api/in/research/'.$id);
    if(($response['id']??null)!==$id)
        throw new RuntimeException('Research job status mismatch.');
    return $response;
}
function cms_ai_research_cancel(PDO $db,array $identity,string $id):array {
    cms_ai_research_owned($db,$identity,$id);
    $response=cms_ai_api_upstream('input','/api/in/research/'.$id.'/cancel',[]);
    return ['id'=>$id,'state'=>$response['state']??'running',
            'cancel_requested'=>$response['cancel_requested']??false];
}
function cms_ai_research_rerun(PDO $db,array $identity,string $id):array {
    $row=cms_ai_research_owned($db,$identity,$id);
    $spec=json_decode($row['spec'],true,16,JSON_THROW_ON_ERROR);
    if(!is_array($spec))throw new RuntimeException('Saved research specification unavailable.');
    return cms_ai_research_submit($db,$identity,array_merge(
        $spec,['target'=>$row['target'],'model_id'=>$row['model_id']]));
}

function cms_ai_research_manuals(array $identity,string $query):array {
    cms_ai_research_authorized($identity);
    if(strlen($query)>60||!preg_match('/^[A-Za-z0-9_-]*$/D',$query))
        throw new InvalidArgumentException('Search with one filename keyword (letters, numbers, hyphen or underscore).');
    $reply=cms_ai_api_upstream('input','/api/in/research/manuals'.($query!==''?'?q='.$query:''));
    $docs=$reply['manuals']??null;
    if(!is_array($docs)||count($docs)>120)
        throw new RuntimeException('Gentoo manual catalog response invalid.');
    return $docs;
}

function cms_ai_research_sections(PDO $db,array $identity,string $id,int $offset):array {
    cms_ai_research_owned($db,$identity,$id);
    if($offset<0||$offset>999999)
        throw new InvalidArgumentException('Invalid research section offset.');
    $reply=cms_ai_api_upstream('input','/api/in/research/'.$id.'/sections/'.$offset);
    if(($reply['id']??null)!==$id||!is_array($reply['sections']??null)||
       count($reply['sections'])>12)
        throw new RuntimeException('Invalid research section response.');
    return $reply;
}
