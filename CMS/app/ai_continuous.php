<?php
declare(strict_types=1);
if(!defined('SIRUS_CMS_BOOTSTRAPPED')){http_response_code(404);exit;}

/**
 * CMS administrator facade for the loopback-only Gentoo continuous scheduler.
 * Never accept a path, command, arbitrary URL, source body or model endpoint.
 */
function cms_ai_continuous_payload(array $post,string $actor=''):array {
    $action=$post['operation']??null;
    if(!is_string($action)||!in_array($action,[
        'enable','pause','resume','stop','disable','scan',
        'configure','include','exclude','priority','cancel'],true))
        throw new InvalidArgumentException('Unknown continuous research operation.');
    $data=['local_only'=>true,'action'=>$action];
    if($action==='cancel'){
        $job=$post['job_id']??null;
        if(!is_string($job)||!preg_match('/^[a-f0-9]{32}$/D',$job)||
           !preg_match('/^cms-admin:[1-9][0-9]{0,12}$/D',$actor)||
           ($post['confirmation']??null)!=='CANCEL JOB')
            throw new InvalidArgumentException(
                'Select one valid job and explicitly confirm cancellation.');
        $data['job_id']=$job;
        $data['actor']=$actor; // Not trusted from form data.
    }
    if(in_array($action,['include','exclude','priority'],true)){
        $source=$post['source_id']??null;
        if(!is_string($source)||!preg_match(
            '/^(?:pdf:[a-f0-9]{64}|research:[a-f0-9]{32})$/D',$source))
            throw new InvalidArgumentException('Invalid source identifier.');
        $data['source_id']=$source;
    }
    if($action==='priority'){
        $raw=$post['priority']??null;
        if(!is_string($raw)||!preg_match('/^(?:-10|-[1-9]|0|[1-9]|10)$/D',$raw))
            throw new InvalidArgumentException('Source priority must be -10 through 10.');
        $data['value']=(int)$raw;
    }
    if($action==='configure'){
        $passes=filter_var($post['passes']??null,FILTER_VALIDATE_INT);
        $interval=filter_var($post['revisit_seconds']??null,FILTER_VALIDATE_INT);
        $chat=filter_var($post['max_chat_jobs']??null,FILTER_VALIDATE_INT);
        $load=$post['max_cpu_load']??null;
        $ram=filter_var($post['min_available_ram_mib']??'0',FILTER_VALIDATE_INT);
        $vram=filter_var($post['min_gpu_free_mib']??'0',FILTER_VALIDATE_INT);
        $dbCeiling=filter_var($post['max_database_mib']??'0',FILTER_VALIDATE_INT);
        $quietStart=filter_var($post['quiet_start_utc']??'-1',FILTER_VALIDATE_INT);
        $quietEnd=filter_var($post['quiet_end_utc']??'-1',FILTER_VALIDATE_INT);
        $target=$post['target']??null;
        $model=$post['model_id']??null;
        if($passes===false||$passes<1||$passes>100||
           $interval===false||$interval<60||$interval>604800||
           $chat===false||$chat<0||$chat>4||
           $ram===false||$ram<0||$ram>1048576||
           $vram===false||$vram<0||$vram>1048576||
           $dbCeiling===false||$dbCeiling<0||$dbCeiling>1048576||
           $quietStart===false||$quietEnd===false||
           !(($quietStart===-1&&$quietEnd===-1)||
              ($quietStart>=0&&$quietStart<=23&&
               $quietEnd>=0&&$quietEnd<=23&&$quietStart!==$quietEnd))||
           !is_string($load)||!is_numeric($load)||
           (float)$load<0||(float)$load>1024||
           !in_array($target,['auto','cpu','nvidia','amd'],true)||
           !is_string($model)||str_contains($model,'://')||
           str_starts_with($model,'//')||!preg_match(
               '#^[a-zA-Z0-9][a-zA-Z0-9_.:+/-]{0,127}$#D',$model))
            throw new InvalidArgumentException('Invalid continuous research settings.');
        $data['value']=['passes'=>$passes,'revisit_seconds'=>$interval,
            'max_chat_jobs'=>$chat,'max_cpu_load'=>(float)$load,
            'min_available_ram_mib'=>$ram,'min_gpu_free_mib'=>$vram,
            'max_database_mib'=>$dbCeiling,
            'quiet_start_utc'=>$quietStart,
            'quiet_end_utc'=>$quietEnd,
            'target'=>$target,'model_id'=>$model];
        // An older form omits these fields and preserves the current estimates.
        if(array_key_exists('research_watts',$post)){
            $watts=filter_var($post['research_watts'],FILTER_VALIDATE_INT);
            if($watts===false||$watts<0||$watts>100000)
                throw new InvalidArgumentException('Enter an estimated draw of 0–100000 watts.');
            $data['value']['research_watts']=$watts;
        }
        if(array_key_exists('usd_kwh',$post)){
            $price=$post['usd_kwh'];
            if(!is_string($price)||($price!==''&&
               !preg_match('/^(?:[0-9]|10)(?:\.[0-9]{1,6})?$/D',$price)))
                throw new InvalidArgumentException('Enter USD per kWh to six decimal places.');
            $micro=-1;
            if($price!==''){
                [$whole,$fraction]=array_pad(explode('.',$price,2),2,'');
                $micro=(int)$whole*1000000+(int)str_pad($fraction,6,'0');
            }
            if($micro>10000000)throw new InvalidArgumentException('Maximum rate is 10 USD/kWh.');
            $data['value']['rate_microusd_kwh']=$micro;
        }
    }
    return $data;
}

function cms_ai_continuous_post(PDO $db,array $post):never {
    require_once __DIR__.'/ai_api.php';
    cms_need_admin(); // CMS router has already enforced POST CSRF.
    cms_ai_api_rate($db,'continuous:'.hash('sha256',session_id()),'admin');
    $actor='cms-admin:'.(string)($_SESSION['cms_admin_id']??'');
    $payload=cms_ai_continuous_payload($post,$actor);
    $response=cms_ai_api_upstream('input',
        '/api/in/research/continuous/control',$payload);
    if(!isset($response['config'])||!is_array($response['config']))
        throw new RuntimeException('Research controller confirmation unavailable.');
    cms_audit($db,'ai-continuous-'.$payload['action'],
        substr((string)($payload['job_id']??$payload['source_id']??'research'),0,80));
    cms_flash('Continuous research control saved. No deployment or model training performed.');
    cms_go('/admin/ai');
}

/** Strictly bounded, side-effect-free input for an HMAC-authenticated history read. */
function cms_ai_continuous_history_payload(mixed $source,mixed $offset,
                                           mixed $auditOffset='0'):array {
    if(!is_string($source) || !preg_match(
        '/^(?:pdf:[a-f0-9]{64}|research:[a-f0-9]{32})$/D',$source))
        throw new InvalidArgumentException('Select a valid inventoried source ID.');
    if(!is_string($offset)||!preg_match('/^(?:0|[1-9][0-9]{0,6})$/D',$offset)
        || (int)$offset>1000000)
        throw new InvalidArgumentException('Invalid source history page.');
    if(!is_string($auditOffset)||!preg_match(
        '/^(?:0|[1-9][0-9]{0,6})$/D',$auditOffset)||
       (int)$auditOffset>1000000)
        throw new InvalidArgumentException('Invalid private research audit page.');
    return ['source_id'=>$source,'offset'=>(int)$offset,
            'audit_offset'=>(int)$auditOffset,'local_only'=>true];
}

/** Build a bounded private report, never a knowledge/training export. */
function cms_ai_continuous_report_data(array $request,array $reply):array {
    if(!is_array($reply['source']??null)||
       ($reply['source']['id']??null)!==$request['source_id']||
       !is_array($reply['passes']??null)||count($reply['passes'])>8||
       !isset($reply['pass_results_total'],$reply['offset'])||
       $reply['offset']!==$request['offset'])
        throw new RuntimeException('Private source report response mismatch.');
    $records=[];
    if(!is_array($reply['audit_events']??null)||
       count($reply['audit_events'])>20||
       ($reply['audit_offset']??null)!==$request['audit_offset'])
        throw new RuntimeException('Private research audit response mismatch.');
    foreach($reply['passes'] as $pass){
        if(!is_array($pass)||!is_string($pass['notes']??null)||
           !is_string($pass['sha256']??null)||
           !preg_match('/^[a-f0-9]{64}$/D',$pass['sha256'])||
           ($pass['review_state']??null)!=='unreviewed')
            throw new RuntimeException('Invalid unreviewed research pass.');
        $records[]=[
            'cycle'=>(int)($pass['cycle']??0),
            'job_id'=>(string)($pass['job_id']??''),
            'page'=>(int)($pass['page']??0),
            'part'=>(int)($pass['part']??0),
            'pass_number'=>(int)($pass['pass_number']??0),
            'source_revision'=>(string)($pass['source_revision']??''),
            'section_revision'=>(string)($pass['revision']??''),
            'result_sha256'=>$pass['sha256'],
            'draft_notes'=>$pass['notes'],
            'inference_duration_ms'=>(int)($pass['duration_ms']??0),
             'prompt_tokens'=>isset($pass['prompt_tokens'])?(int)$pass['prompt_tokens']:null,
             'completion_tokens'=>isset($pass['completion_tokens'])?(int)$pass['completion_tokens']:null,
             'model_generation_tps'=>isset($pass['generation_tps'])?(float)$pass['generation_tps']:null,
            'unreviewed'=>true,
        ];
    }
    return [
        'kind'=>'sirus-continuous-research-report-v1',
        'source_id'=>$request['source_id'],
        'source_title'=>(string)($reply['source']['title']??''),
        'current_source_revision'=>(string)($reply['source']['revision']??''),
        'source_extraction_status'=>(string)($reply['source']['extraction_status']??''),
        'page_offset'=>$request['offset'],
        'page_size'=>8,
        'total_pass_results'=>(int)$reply['pass_results_total'],
        'contains_private_unreviewed_drafts'=>true,
        'verified_knowledge'=>false,
        'training_export'=>false,
        'no_public_distribution'=>true,
        'audit_offset'=>$request['audit_offset'],
        'audit_total'=>(int)($reply['audit_total']??0),
        'audit_events'=>$reply['audit_events'],
        'passes'=>$records
    ];
}

/** Explicit admin-only report download; no disk writes or public routes. */
function cms_ai_continuous_export(PDO $db,array $post):never {
    require_once __DIR__.'/ai_api.php';
    cms_need_admin(); // The existing admin router verifies POST CSRF.
    $request=cms_ai_continuous_history_payload(
        $post['source_id']??null,$post['offset']??null,
        $post['audit_offset']??'0');
    cms_ai_api_rate($db,'continuous-report:'.hash('sha256',session_id()),'admin');
    $reply=cms_ai_api_upstream('input',
        '/api/in/research/continuous/history',$request);
    $report=cms_ai_continuous_report_data($request,$reply);
    cms_audit($db,'ai-continuous-report',
        substr($request['source_id'],0,80).':'.$request['offset']);
    // The filename contains only a validated source hash and a decimal offset.
    $name='sirus-research-'.substr($request['source_id'],4,12).
        '-page-'.$request['offset'].'.json';
    header('Content-Type: application/json; charset=utf-8');
    header('Content-Disposition: attachment; filename="'.$name.'"');
    header('Cache-Control: private, no-store');
    header('X-Content-Type-Options: nosniff');
    header("Content-Security-Policy: default-src 'none'");
    echo json_encode($report,JSON_UNESCAPED_UNICODE|
        JSON_INVALID_UTF8_SUBSTITUTE|JSON_THROW_ON_ERROR);
    exit;
}

function cms_ai_continuous_panel(PDO $db):void {
    require_once __DIR__.'/ai_api.php';
    // Called only from cms_ai_page(), whose entry guard requires an admin.
    try {
        $info=cms_ai_api_upstream('input','/api/in/research/continuous');
    }catch(Throwable $e) {
        echo '<div class="admin-panel"><h2>Continuous Research</h2>'.
             '<p>Private Gentoo research controller is not reachable. No changes made.</p></div>';
        return;
    }
    $cfg=is_array($info['config']??null)?$info['config']:[];
    $inv=is_array($info['inventory']??null)?$info['inventory']:[];
    $jobs=is_array($info['jobs']??null)?$info['jobs']:[];
    $sections=is_array($info['sections']??null)?$info['sections']:[];
    $cycle=is_array($info['cycle']??null)?$info['cycle']:[];
    $progress=is_array($info['cycle_progress']??null)?$info['cycle_progress']:[];
    $throughput=is_array($info['throughput']??null)?$info['throughput']:[];
    $resourceHistory=is_array($info['resource_history']??null)?
        array_slice($info['resource_history'],-60):[];
    $active=is_array($info['active']??null)?$info['active']:[];
    $sourceRows=is_array($info['sources']??null)?$info['sources']:[];
    $recentJobs=is_array($info['recent_jobs']??null)?
        array_slice($info['recent_jobs'],0,24):[];
    $mode=(string)($cfg['mode']??'unknown');
    ?>
    <section class="admin-panel">
      <h2>Continuous Research — private Gentoo</h2>
      <p>Research is <strong><?=cms_esc($mode)?></strong>. Drafts are unreviewed; none are exported as verified knowledge or used to update model weights. An inventory scan reads existing private extractions without fetching websites.</p>
      <p>Coverage cycle: <?=cms_esc((string)($cycle['id']??'not started'))?>
        (<?=cms_esc((string)($cycle['state']??'idle'))?>).
        Pass completion: <?=cms_esc((string)($progress['finished_passes']??0))?> /
        <?=cms_esc((string)($progress['planned_passes']??0))?>
        (<?=cms_esc((string)($progress['pass_percent']??0))?>%).
        Failed jobs this cycle: <?=cms_esc((string)($progress['failed_jobs']??0))?>.
        Sources: <?=cms_esc((string)($inv['sources']??0))?> total,
        <?=cms_esc((string)($inv['eligible_sources']??0))?> eligible,
        <?=cms_esc((string)($inv['excluded_sources']??0))?> excluded,
        <?=cms_esc((string)($inv['incomplete_sources']??0))?> pending/failed.
        Sections: <?=cms_esc((string)($sections['current']??0))?> current,
        <?=cms_esc((string)($sections['total']??0))?> historical.
        Pages without extracted text: <?=cms_esc((string)($inv['missing_pages']??0))?>.</p>
      <p>Queue: <?=cms_esc((string)($jobs['queued']??0))?> queued,
        <?=cms_esc((string)($jobs['leased']??0))?> leased,
        <?=cms_esc((string)($jobs['completed']??0))?> completed,
        <?=cms_esc((string)($jobs['failed']??0))?> failed,
        <?=cms_esc((string)($jobs['superseded']??0))?> superseded.
        Chat queue: <?=cms_esc((string)($info['interactive_jobs']??0))?>.
        CPU load (1m): <?=cms_esc((string)($info['cpu_load_1m']??'?'))?>.
        Available logical CPU threads: <?=cms_esc((string)($info['cpu_logical']??'?'))?>.
        Free host RAM (MiB): <?=cms_esc((string)($info['free_ram_mib']??'?'))?>.
        Input service resident RAM (MiB):
        <?=cms_esc((string)($info['process_rss_mib']??'unavailable'))?>.</p>
      <p>Private Gentoo input SQLite footprint (main database + WAL + SHM):
        <?=cms_esc(isset($info['database_bytes'])?
          (string)round(((int)$info['database_bytes'])/1048576,2).' MiB':'unavailable')?>.
        Configured background database ceiling:
        <?=cms_esc((string)($cfg['max_database_mib']??0))?> MiB
        (0 disables ceiling). When reached, new autonomous research
        admission stops. It does not delete history or interrupt chat,
        and is not a guarantee of available physical disk space.</p>
      <h3>Local GPU / VRAM telemetry</h3>
      <p>This is a read-only point-in-time snapshot from Gentoo. Unavailable
      GPU telemetry is <strong>not</strong> interpreted as free VRAM.
      Selecting a minimum VRAM limit requires an explicit NVIDIA or AMD worker;
      the automatic and CPU targets remain ineligible while that limit is on.</p>
      <?php $gpus=is_array($info['gpu_devices']??null)?array_slice($info['gpu_devices'],0,32):[]; ?>
      <?php if(!$gpus): ?>
        <p>No supported GPU memory telemetry returned; no GPU free-memory assumption.</p>
      <?php else: ?>
        <div class="table-scroll"><table><thead><tr><th>Backend</th><th>Device</th>
          <th>Total VRAM (MiB)</th><th>Free VRAM (MiB)</th><th>Telemetry</th>
        </tr></thead><tbody>
        <?php foreach($gpus as $gpu):if(!is_array($gpu))continue;?>
          <tr><td><?=cms_esc((string)($gpu['backend']??'?'))?></td>
          <td><?=cms_esc((string)($gpu['device']??'?'))?></td>
          <td><?=cms_esc(isset($gpu['total_mib'])?(string)$gpu['total_mib']:'unknown')?></td>
          <td><?=cms_esc(isset($gpu['free_mib'])?(string)$gpu['free_mib']:'unknown')?></td>
          <td><?=cms_esc((string)($gpu['telemetry']??'unavailable'))?></td></tr>
        <?php endforeach;?></tbody></table></div>
      <?php endif; ?>
      <?php $energy=$info['energy_7d']??[]; ?>
      <h3>Background inference energy · last seven days</h3>
      <p>Estimated <?=isset($energy['kwh'])?number_format((float)$energy['kwh'],6).' kWh':'energy unavailable'?>
         across <?=(int)($energy['energy_samples']??0)?> attempts.
         Estimated cost <?=isset($energy['usd'])?'$'.number_format((float)$energy['usd'],6):'unavailable'?>
         across <?=(int)($energy['priced_samples']??0)?> priced attempts.</p>
      <p><?=(int)($energy['timed']??0)?> timed of <?=(int)($energy['attempts']??0)?> started;
         <?=(int)($energy['errors']??0)?> timed calls raised an error. Estimates use elapsed
         model-call time and the power and rate saved when each attempt started.
         Idle time, chat and attempts without an end time are excluded.</p>
      <p>Throughput (measured): <?=cms_esc((string)($throughput['passes_last_60_minutes']??0))?>
        completed passes in the last 60 minutes.
        Average inference latency:
        <?=cms_esc(isset($throughput['average_inference_ms'])?
            (string)$throughput['average_inference_ms'].' ms':'not yet measured')?>.
        Estimated uninterrupted throughput:
        <?=cms_esc(isset($throughput['estimated_passes_per_hour'])?
            (string)$throughput['estimated_passes_per_hour'].' passes/hour':'not yet measured')?>.
        This estimate excludes queue delays, source reads, retries and GPU contention.</p>
      <p>Research-model tokens reported by the local model server:
        prompt <?=cms_esc(isset($throughput['prompt_tokens'])?
          (string)$throughput['prompt_tokens']:'unknown')?> tokens
        (<?=cms_esc((string)($throughput['prompt_token_samples']??0))?> measured passes);
        generated <?=cms_esc(isset($throughput['completion_tokens'])?
          (string)$throughput['completion_tokens']:'unknown')?> tokens
        (<?=cms_esc((string)($throughput['completion_token_samples']??0))?> measured passes).
        Measured output throughput:
        <?=cms_esc(isset($throughput['observed_output_tps_inference_wall'])?
          (string)$throughput['observed_output_tps_inference_wall']:'unknown')?> tokens/s
        (inference-wall time, includes prefill; not isolated decoding speed).
        Missing token counters are never derived from character counts.</p>
      <p>Queued research growth across available recent samples:
        <?=cms_esc((string)($info['queued_growth_last_samples']??0))?> jobs
        (positive means the queue grew). The latest 60 one-minute operational
        samples retain process RSS, host RAM, private DB size and queued,
        leased and failed counts; data is retained for seven days.
        This records the input service's memory, not the separate llama.cpp
        processes or their GPU allocations.</p>
      <details><summary>Recent resource history (up to 60 samples)</summary>
        <div class="table-scroll"><table><thead><tr>
          <th>Unix minute (UTC)</th><th>Queued</th><th>Leased</th><th>Failed</th>
          <th>Input RSS (MiB)</th><th>Host free RAM (MiB)</th>
          <th>Database (MiB)</th></tr></thead><tbody>
          <?php foreach($resourceHistory as $sample):if(!is_array($sample))continue;?>
          <tr><td><?=cms_esc((string)($sample['minute']??''))?></td>
            <td><?=cms_esc((string)($sample['queued']??0))?></td>
            <td><?=cms_esc((string)($sample['leased']??0))?></td>
            <td><?=cms_esc((string)($sample['failed']??0))?></td>
            <td><?=cms_esc((string)($sample['process_rss_mib']??-1))?></td>
            <td><?=cms_esc((string)($sample['host_free_ram_mib']??-1))?></td>
            <td><?=cms_esc(isset($sample['database_bytes']) &&
                (int)$sample['database_bytes']>=0?
                (string)round((int)$sample['database_bytes']/1048576,2):'unknown')?></td>
          </tr>
          <?php endforeach;?></tbody></table></div>
          <p>-1 means a memory reading was unavailable, never zero consumption.</p>
      </details>
      <p>Admission status: <?=cms_esc(implode(', ',is_array($info['admission_blocks']??null)?
           $info['admission_blocks']:[])?:'Eligible for new background work')?></p>
      <?php if($active): ?>
      <p>Active: <?=cms_esc((string)($active['source_id']??''))?>,
        page <?=cms_esc((string)($active['page']??0))?>,
        part <?=cms_esc((string)($active['part']??0))?>,
        pass <?=cms_esc((string)($active['pass_completed']??0))?> /
        <?=cms_esc((string)($active['passes']??1))?>,
        attempts <?=cms_esc((string)($active['attempts']??0))?>.</p>
      <?php endif; ?>
      <?php if(!empty($cfg['scan_error'])): ?>
      <p>Last inventory scan blocked: <?=cms_esc((string)$cfg['scan_error'])?>.</p>
      <?php endif; ?>
      <div class="form-actions">
      <?php foreach(['enable'=>'Enable / Start','pause'=>'Pause','resume'=>'Resume',
                    'stop'=>'Stop queued jobs','disable'=>'Disable','scan'=>'Scan inventory'] as $op=>$label):?>
        <form method="post" action="/admin/ai" class="inline-action">
          <input type="hidden" name="csrf" value="<?=cms_esc(cms_csrf())?>">
          <input type="hidden" name="action" value="ai_continuous">
          <input type="hidden" name="operation" value="<?=cms_esc($op)?>">
          <button class="btn small" type="submit"><?=cms_esc($label)?></button>
        </form>
      <?php endforeach; ?>
      </div>
      <h3>Individual job cancellation (latest 24 pending / failed)</h3>
      <p>Cancel only the selected background job, without stopping the rest
      of the queue. An active model request may finish computing, but its
      stale lease is fenced and its result is not saved. Earlier checkpointed
      passes remain in the private history. Cancellation is persistent and
      cannot be undone by a global Resume; a future full coverage cycle may
      revisit that section normally.</p>
      <div class="table-scroll"><table><thead><tr>
        <th>Job</th><th>Source</th><th>Cycle / section</th>
        <th>State / pass</th><th>Action</th></tr></thead><tbody>
      <?php foreach($recentJobs as $job):if(!is_array($job))continue;
          $jid=(string)($job['id']??'');
          if(!preg_match('/^[a-f0-9]{32}$/D',$jid))continue;
          $state=(string)($job['state']??'');
      ?>
      <tr><td><code><?=cms_esc($jid)?></code></td>
        <td><?=cms_esc((string)($job['title']??''))?>
          <small><?=cms_esc((string)($job['source_id']??''))?></small></td>
        <td>Cycle <?=cms_esc((string)($job['cycle']??0))?>,
          page <?=cms_esc((string)($job['page']??0))?>,
          part <?=cms_esc((string)($job['part']??0))?></td>
        <td><?=cms_esc($state)?>,
          pass <?=cms_esc((string)($job['pass_completed']??0))?> /
          <?=cms_esc((string)($job['passes']??0))?>
          <?php if(!empty($job['cancelled_by'])):?>
            <small>Cancelled by <?=cms_esc((string)$job['cancelled_by'])?></small>
          <?php endif;?></td>
        <td><?php if(in_array($state,['queued','leased'],true)):?>
          <form method="post" action="/admin/ai">
            <input type="hidden" name="csrf" value="<?=cms_esc(cms_csrf())?>">
            <input type="hidden" name="action" value="ai_continuous">
            <input type="hidden" name="operation" value="cancel">
            <input type="hidden" name="job_id" value="<?=cms_esc($jid)?>">
            <label>Confirm <input type="checkbox" name="confirmation"
              value="CANCEL JOB" required> cancel this job</label>
            <button class="btn small" type="submit">Cancel job</button>
          </form>
          <?php else:?>
            <?=cms_esc((string)($job['error']??'No action available'))?>
          <?php endif;?></td></tr>
      <?php endforeach;?></tbody></table></div>
      <h3>Background scheduling and admission limits</h3>
      <form method="post" action="/admin/ai">
        <input type="hidden" name="csrf" value="<?=cms_esc(cms_csrf())?>">
        <input type="hidden" name="action" value="ai_continuous">
        <input type="hidden" name="operation" value="configure">
        <label>Passes per section (1–100)
          <input type="number" name="passes" min="1" max="100" value="<?=cms_esc((string)($cfg['passes']??1))?>">
        </label>
        <label>Revisit interval (seconds; 60–604800)
          <input type="number" name="revisit_seconds" min="60" max="604800"
            value="<?=cms_esc((string)($cfg['revisit_seconds']??86400))?>">
        </label>
        <label>Maximum active/queued interactive jobs before deferring research
          <input type="number" name="max_chat_jobs" min="0" max="4"
            value="<?=cms_esc((string)($cfg['max_chat_jobs']??0))?>">
        </label>
        <label>Max 1-minute CPU load (0 disables load check)
          <input type="number" name="max_cpu_load" min="0" max="1024" step="0.1"
            value="<?=cms_esc((string)($cfg['max_cpu_load']??0))?>">
        </label>
        <label>Estimated background inference draw in watts (0 means unknown)
          <input type="number" name="research_watts" min="0" max="100000" required
            value="<?=(int)($cfg['research_watts']??0)?>">
        </label>
        <label>Electricity rate in USD/kWh (blank means unknown)
          <input type="number" name="usd_kwh" min="0" max="10" step="0.000001"
            value="<?=($cfg['rate_microusd_kwh']??-1)>=0?
                number_format($cfg['rate_microusd_kwh']/1000000,6,'.',''):''?>">
        </label>
        <label>Minimum available host RAM (MiB; 0 disables limit)
          <input type="number" name="min_available_ram_mib" min="0" max="1048576"
            value="<?=cms_esc((string)($cfg['min_available_ram_mib']??0))?>">
        </label>
        <label>Minimum free GPU VRAM (MiB; 0 disables GPU admission gate)
          <input type="number" name="min_gpu_free_mib" min="0" max="1048576"
            value="<?=cms_esc((string)($cfg['min_gpu_free_mib']??0))?>">
        </label>
        <label>Maximum private input database footprint (MiB; 0 disables limit)
          <input type="number" name="max_database_mib" min="0" max="1048576"
            value="<?=cms_esc((string)($cfg['max_database_mib']??0))?>">
        </label>
        <p>Once the private input SQLite database, write-ahead log and metadata
        exceed this limit, the system refuses new background research jobs.
        Existing history is retained, and a verified private archive and
        operator-approved retention plan are still required.</p>
        <label>Quiet hours start UTC (-1 disables)
          <input type="number" name="quiet_start_utc" min="-1" max="23"
            value="<?=cms_esc((string)($cfg['quiet_start_utc']??-1))?>">
        </label>
        <label>Quiet hours end UTC (-1 disables)
          <input type="number" name="quiet_end_utc" min="-1" max="23"
            value="<?=cms_esc((string)($cfg['quiet_end_utc']??-1))?>">
        </label>
        <label>Local model identifier
          <input type="text" name="model_id" maxlength="128"
            value="<?=cms_esc((string)($cfg['model_id']??'sirussofts-coder'))?>">
        </label>
        <label>Worker target
          <select name="target">
          <?php foreach(['auto','cpu','nvidia','amd'] as $worker):?>
            <option value="<?=cms_esc($worker)?>" <?=($cfg['target']??'auto')===$worker?'selected':''?>><?=cms_esc($worker)?></option>
          <?php endforeach; ?>
          </select>
        </label>
        <button class="btn" type="submit">Save scheduling limits</button>
        <p>Enabling a VRAM gate on an explicit GPU target blocks new research
        when that backend's memory counters cannot be read or are below the
        configured threshold. It is not a reservation and does not preempt
        already-running model calls.</p>
      </form>
    </section>
    <section class="admin-panel table-scroll">
      <h2>Inventory and source priorities (first 60)</h2>
      <p>Read-only source metadata. Content stays in private Gentoo storage. An incomplete source is counted even if it cannot currently be analyzed.</p>
      <table><thead><tr><th>Source</th><th>Status / coverage</th><th>Priority</th><th>Exclusion</th></tr></thead><tbody>
      <?php foreach(array_slice($sourceRows,0,60) as $source):
        if(!is_array($source))continue;
        $sid=(string)($source['id']??'');
        if(!preg_match('/^(?:pdf:[a-f0-9]{64}|research:[a-f0-9]{32})$/D',$sid))continue;
      ?>
      <tr>
        <td><?=cms_esc((string)($source['title']??''))?>
          <small><?=cms_esc($sid)?></small>
          <p><a href="/admin/ai?cr_source=<?=rawurlencode($sid)?>">View pass history</a></p></td>
        <td><?=cms_esc((string)($source['extraction_status']??''))?>,
          parts <?=cms_esc((string)($source['extracted_parts']??0))?>,
          empty pages <?=cms_esc((string)($source['missing_pages']??0))?></td>
        <td>
          <form method="post" action="/admin/ai">
            <input type="hidden" name="csrf" value="<?=cms_esc(cms_csrf())?>">
            <input type="hidden" name="action" value="ai_continuous">
            <input type="hidden" name="operation" value="priority">
            <input type="hidden" name="source_id" value="<?=cms_esc($sid)?>">
            <input type="number" name="priority" min="-10" max="10"
              value="<?=cms_esc((string)($source['priority']??0))?>">
            <button class="btn small" type="submit">Set</button>
          </form>
        </td><td>
          <form method="post" action="/admin/ai">
            <input type="hidden" name="csrf" value="<?=cms_esc(cms_csrf())?>">
            <input type="hidden" name="action" value="ai_continuous">
            <input type="hidden" name="operation"
              value="<?=!empty($source['excluded'])?'include':'exclude'?>">
            <input type="hidden" name="source_id" value="<?=cms_esc($sid)?>">
            <button class="btn small" type="submit">
              <?=!empty($source['excluded'])?'Include':'Exclude'?>
            </button>
          </form>
        </td>
      </tr>
      <?php endforeach; ?>
      </tbody></table>
    </section>
    <section class="admin-panel">
      <h2>Research history for one source</h2>
      <p>Browse checkpointed draft results across all cycles, including superseded revisions.
      Results are unreviewed model outputs, not independently verified facts.
      The history is accessible only through the signed private administrator bridge.</p>
      <form method="get" action="/admin/ai">
        <label>Original source ID
          <input type="text" name="cr_source" maxlength="75"
            value="<?=cms_esc((string)($_GET['cr_source']??''))?>"
            placeholder="pdf:… or research:…">
        </label>
        <button type="submit" class="btn small">View source history</button>
      </form>
      <?php if(isset($_GET['cr_source'])):
          try{
              $request=cms_ai_continuous_history_payload(
                  $_GET['cr_source'],$_GET['cr_offset']??'0',
                  $_GET['cr_audit_offset']??'0');
              $history=cms_ai_api_upstream('input',
                  '/api/in/research/continuous/history',$request);
              if(!isset($history['source'])||!is_array($history['source'])||
                 ($history['source']['id']??null)!==$request['source_id']||
                 !is_array($history['passes']??null)||count($history['passes'])>8||
                 !is_array($history['audit_events']??null)||
                 count($history['audit_events'])>20||
                 ($history['audit_offset']??null)!==$request['audit_offset'])
                  throw new RuntimeException('Invalid private history response.');
              $histSource=$history['source'];
              $histPasses=$history['passes'];
              $histJobs=is_array($history['jobs']??null)?$history['jobs']:[];
              $histOffset=$request['offset'];
              $histTotal=(int)($history['pass_results_total']??0);
              $histAudits=$history['audit_events'];
              $histAuditOffset=$request['audit_offset'];
              $histAuditTotal=(int)($history['audit_total']??0);
          }catch(Throwable $error){
              $history=null;
              echo '<p>Source history unavailable or invalid. No changes were made.</p>';
          }
          if(is_array($history??null)):
      ?>
      <h3><?=cms_esc((string)($histSource['title']??'Untitled source'))?></h3>
      <p><?=cms_esc((string)($histSource['id']??''))?>.
        Saved jobs: <?=cms_esc((string)($history['jobs_total']??0))?>.
        Saved pass results: <?=cms_esc((string)$histTotal)?>.
        Current extraction: <?=cms_esc((string)($histSource['extraction_status']??'?'))?>.</p>
      <h4>Recent jobs (latest 12)</h4>
      <div class="table-scroll"><table><thead><tr>
        <th>Cycle</th><th>Source page / part</th><th>State</th><th>Passes</th><th>Retries / error</th><th>Measured prompt / output tokens</th><th>Output tokens/s (inference-wall)</th>
      </tr></thead><tbody>
      <?php foreach(array_slice($histJobs,0,12) as $job):if(!is_array($job))continue;?>
      <tr><td><?=cms_esc((string)($job['cycle']??''))?></td>
        <td><?=cms_esc((string)($job['page']??''))?> /
            <?=cms_esc((string)($job['part']??''))?></td>
        <td><?=cms_esc((string)($job['state']??''))?></td>
        <td><?=cms_esc((string)($job['pass_completed']??0))?> /
            <?=cms_esc((string)($job['passes']??0))?></td>
        <td><?=cms_esc((string)($job['attempts']??0))?>
            <?=cms_esc((string)($job['error']??''))?></td>
        <td><?=cms_esc(isset($job['prompt_tokens'])?(string)$job['prompt_tokens']:'unknown')?> /
            <?=cms_esc(isset($job['completion_tokens'])?(string)$job['completion_tokens']:'unknown')?></td>
        <td><?=cms_esc(isset($job['observed_output_tps_inference_wall'])?
            (string)$job['observed_output_tps_inference_wall']:'unknown')?></td></tr>
      <?php endforeach; ?></tbody></table></div>
      <h4>Recorded passes (8 per page)</h4>
      <p>This optional download includes **private, unreviewed model notes**.
      Save it only to a trusted private location; it is neither validated repair
      guidance nor a model training dataset.</p>
      <form method="post" action="/admin/ai">
        <input type="hidden" name="csrf" value="<?=cms_esc(cms_csrf())?>">
        <input type="hidden" name="action" value="ai_continuous_export">
        <input type="hidden" name="source_id"
          value="<?=cms_esc($request['source_id'])?>">
        <input type="hidden" name="offset" value="<?=cms_esc((string)$histOffset)?>">
        <input type="hidden" name="audit_offset"
          value="<?=cms_esc((string)$histAuditOffset)?>">
        <button type="submit" class="btn small">Download this page's private research report (JSON)</button>
      </form>
      <?php foreach($histPasses as $result):if(!is_array($result))continue;?>
      <details>
        <summary>Cycle <?=cms_esc((string)($result['cycle']??''))?>,
          page <?=cms_esc((string)($result['page']??0))?>,
          part <?=cms_esc((string)($result['part']??0))?>,
          pass <?=cms_esc((string)($result['pass_number']??0))?> —
          <?=cms_esc((string)($result['review_state']??'unreviewed'))?>
          (<?=cms_esc((string)($result['state']??''))?>)</summary>
        <p>Source section revision: <code><?=cms_esc((string)($result['revision']??''))?></code>.
           Draft hash: <code><?=cms_esc((string)($result['sha256']??''))?></code>.
           Inference duration: <?=cms_esc((string)($result['duration_ms']??0))?> ms
           (0 means not measured). Prompt tokens:
           <?=cms_esc(isset($result['prompt_tokens'])?(string)$result['prompt_tokens']:'unknown')?>.
           Output tokens:
           <?=cms_esc(isset($result['completion_tokens'])?(string)$result['completion_tokens']:'unknown')?>.
           Model-reported generation rate:
           <?=cms_esc(isset($result['generation_tps'])?(string)$result['generation_tps']:'unknown')?> tokens/s.</p>
        <pre style="white-space:pre-wrap;overflow-wrap:anywhere"><?=cms_esc((string)($result['notes']??''))?></pre>
        <p><strong>Unreviewed AI-generated draft; verify against the original PDF.</strong></p>
      </details>
      <?php endforeach; ?>
      <p>
      <?php if($histOffset>0): ?>
        <a href="/admin/ai?cr_source=<?=rawurlencode($request['source_id'])?>&amp;cr_offset=<?=max(0,$histOffset-8)?>&amp;cr_audit_offset=<?=$histAuditOffset?>">Previous 8 passes</a>
      <?php endif; ?>
      <?php if(!empty($history['has_more'])): ?>
        <a href="/admin/ai?cr_source=<?=rawurlencode($request['source_id'])?>&amp;cr_offset=<?=($histOffset+8)?>&amp;cr_audit_offset=<?=$histAuditOffset?>">Next 8 passes</a>
      <?php endif; ?>
      </p>
      <h4>Append-only lifecycle events</h4>
      <p><?=cms_esc((string)$histAuditTotal)?> events associated with this source,
      including original-source changes, leases, passes, errors, retries,
      cancellations and cycle outcomes. Events recorded before this
      implementation may not contain every lifecycle transition.</p>
      <div class="table-scroll"><table><thead><tr>
        <th>Event ID</th><th>When (UTC epoch)</th><th>Kind</th>
        <th>Subject</th><th>Metadata</th></tr></thead><tbody>
      <?php foreach($histAudits as $audit):if(!is_array($audit))continue;?>
      <tr><td><?=cms_esc((string)($audit['id']??''))?></td>
        <td><?=cms_esc((string)($audit['happened']??''))?></td>
        <td><?=cms_esc((string)($audit['kind']??''))?></td>
        <td><?=cms_esc((string)($audit['subject']??''))?></td>
        <td><?=cms_esc((string)($audit['detail']??''))?></td></tr>
      <?php endforeach;?></tbody></table></div>
      <p>
      <?php if($histAuditOffset>0):?>
        <a href="/admin/ai?cr_source=<?=rawurlencode($request['source_id'])?>&amp;cr_offset=<?=$histOffset?>&amp;cr_audit_offset=<?=max(0,$histAuditOffset-20)?>">Previous 20 events</a>
      <?php endif;?>
      <?php if(!empty($history['audit_has_more'])):?>
        <a href="/admin/ai?cr_source=<?=rawurlencode($request['source_id'])?>&amp;cr_offset=<?=$histOffset?>&amp;cr_audit_offset=<?=($histAuditOffset+20)?>">Next 20 events</a>
      <?php endif;?>
      </p>
      <?php endif; endif; ?>
    </section>
    <?php
}


/**
 * A fixed-schema, administrator-only facade for original-source evidence.
 * No free-form SQL, raw source upload, arbitrary endpoints, file paths or
 * generated knowledge exports are accepted.
 */
function cms_ai_evidence_payload(array $post,string $reviewer):array {
    if(!preg_match('/^cms-admin:[1-9][0-9]{0,12}$/D',$reviewer))
        throw new InvalidArgumentException('Missing administrator reviewer identity.');
    $action=$post['operation']??null;
    if(!is_string($action)||!in_array($action,
        ['index','scope','review','dismiss'],true))
        throw new InvalidArgumentException('Invalid evidence action.');
    $data=['local_only'=>true,'action'=>$action];
    if($action==='scope'){
        $source=$post['source_id']??null;
        $scope=$post['applicability']??null;
        if(!is_string($source)||!preg_match('/^pdf:[a-f0-9]{64}$/D',$source)||
           !is_string($scope)||($scope!==''&&!preg_match(
              '#^[a-z0-9][a-z0-9._-]{0,23}(?:/[a-z0-9][a-z0-9._-]{0,23}){1,5}$#D',
              $scope)))
            throw new InvalidArgumentException('Invalid original document or applicability.');
        $data['source_id']=$source;
        $data['applicability']=$scope;
        $data['reviewer']=$reviewer; // Audited source scope changes
    }
    if($action==='review'||$action==='dismiss'){
        $id=$post['evidence_id']??null;
        if(!is_string($id)||!preg_match('/^[0-9a-f]{64}$/D',$id))
            throw new InvalidArgumentException('Invalid evidence identifier.');
        $data['id']=$id;
        $data['reviewer']=$reviewer;
    }
    if($action==='review'){
        $quoteSha=$post['quote_sha']??null;
        $revision=$post['revision']??null;
        $reviewAction=$post['review_action']??null;
        $reason=$post['reason']??'';
        $scope=$post['expected_scope']??null;
        if(!is_string($scope) || ($scope!==''&&!preg_match(
            '#^[a-z0-9][a-z0-9._-]{0,23}(?:/[a-z0-9][a-z0-9._-]{0,23}){1,5}$#D',
            $scope)))
            throw new InvalidArgumentException('Scope changed; reopen the evidence review.');
        if(!is_string($quoteSha)||!preg_match('/^[0-9a-f]{64}$/D',$quoteSha)||
           !is_string($revision)||!preg_match('/^[0-9a-f]{64}$/D',$revision)||
           !in_array($reviewAction,['support','dispute','reject','supersede'],true)||
           !is_string($reason)||strlen($reason)>200)
            throw new InvalidArgumentException('Invalid claim review fields.');
        // The operator must explicitly confirm examining the original PDF.
        if($reviewAction==='support'&&
           ($post['confirmation']??null)!=='I VERIFIED ORIGINAL')
            throw new InvalidArgumentException(
                'Supporting a claim requires typing I VERIFIED ORIGINAL.');
        $data['quote_sha']=$quoteSha;
        $data['revision']=$revision;
        $data['expected_scope']=$scope;
        $data['review_action']=$reviewAction;
        $data['reason']=$reason;
    }
    return $data;
}

function cms_ai_evidence_post(PDO $db,array $post):never {
    require_once __DIR__.'/ai_api.php';
    cms_need_admin();
    // CMS admin router already verifies the form CSRF before reaching this.
    cms_ai_api_rate($db,'research-evidence:'.hash('sha256',session_id()),'admin');
    $reviewer='cms-admin:'.(string)($_SESSION['cms_admin_id']??'');
    $payload=cms_ai_evidence_payload($post,$reviewer);
    $result=cms_ai_api_upstream('input','/api/in/research/evidence/control',$payload);
    if(!is_array($result)||$result===[])
        throw new RuntimeException('Private evidence controller did not confirm operation.');
    cms_audit($db,'ai-evidence-'.$payload['action'],
        substr((string)($payload['id']??$payload['source_id']??'index'),0,70));
    cms_flash('Private evidence review updated. Training and knowledge exports are unchanged.');
    cms_go('/admin/ai');
}

function cms_ai_evidence_panel(PDO $db):void {
    require_once __DIR__.'/ai_api.php';
    // This panel is called exclusively by the guarded cms_ai_page() route.
    try {
        $data=cms_ai_api_upstream('input','/api/in/research/evidence');
    }catch(Throwable $error){
        echo '<section class="admin-panel"><h2>Source Evidence &amp; Review</h2>'.
             '<p>Private original-source evidence service is unavailable.</p></section>';
        return;
    }
    $counts=is_array($data['counts']??null)?$data['counts']:[];
    $evidence=is_array($data['evidence']??null)?$data['evidence']:[];
    $conflicts=is_array($data['conflicts']??null)?$data['conflicts']:[];
    $claims=is_array($data['claims']??null)?$data['claims']:[];
    $supportedFindings=is_array($data['supported_findings']??null)?
        array_slice($data['supported_findings'],0,20):[];
    $scopes=is_array($data['scopes']??null)?$data['scopes']:[];
    $scopeHistory=is_array($data['scope_history']??null)?
        array_slice($data['scope_history'],0,20):[];
    ?>
    <section class="admin-panel">
      <h2>Original-source evidence and claim review</h2>
      <p>Comparison candidates are deterministic extracts from existing PDF page text, not LLM answers. Matching application scopes must be configured manually. A contradiction is a review request, never automatic proof of an error. Supported means a human has checked the exact excerpt against an approved original PDF; it does not establish that every real-world application is correct. No claims enter model training or the approved RAG export automatically.</p>
      <p>Candidate excerpts: <?=cms_esc((string)($counts['evidence_total']??0))?>.
        Not yet indexed: <?=cms_esc((string)($counts['pending_sections']??0))?> sections.
        Open comparison candidates: <?=cms_esc((string)($counts['open_conflicts']??0))?>.
        Human-supported: <?=cms_esc((string)($counts['supported_claims']??0))?>.
        Superseded claims: <?=cms_esc((string)($counts['superseded_claims']??0))?>.</p>
      <form method="post" action="/admin/ai">
        <input type="hidden" name="csrf" value="<?=cms_esc(cms_csrf())?>">
        <input type="hidden" name="action" value="ai_evidence">
        <input type="hidden" name="operation" value="index">
        <button class="btn small" type="submit">Index next 32 eligible PDF sections</button>
      </form>
      <h3>Set exact comparison applicability</h3>
      <p><strong>Changing a scope immediately supersedes previously reviewed
      statements for that source.</strong> The change is appended to a private
      scope history with the administrator identity. An administrator must
      recheck each original citation to support claims in the new scope.</p>
      <p>Format: <code>make/model/year/engine/assembly</code> (lowercase; omit optional trailing segments). Leave blank to disable comparisons for a document. Never equate different years or engines without confirming applicability.</p>
      <div class="table-scroll"><table><thead><tr>
        <th>Original source</th><th>Application scope</th>
      </tr></thead><tbody>
      <?php foreach(array_slice($scopes,0,40) as $scope):
        if(!is_array($scope))continue;
        $sourceId=(string)($scope['id']??'');
        if(!preg_match('/^pdf:[a-f0-9]{64}$/D',$sourceId))continue;?>
      <tr><td><?=cms_esc((string)($scope['title']??''))?>
        <small><?=cms_esc(substr($sourceId,0,24))?>...</small></td>
      <td><form method="post" action="/admin/ai">
        <input type="hidden" name="csrf" value="<?=cms_esc(cms_csrf())?>">
        <input type="hidden" name="action" value="ai_evidence">
        <input type="hidden" name="operation" value="scope">
        <input type="hidden" name="source_id" value="<?=cms_esc($sourceId)?>">
        <input name="applicability" maxlength="149" pattern="[a-z0-9._/-]*"
          value="<?=cms_esc((string)($scope['applicability']??''))?>">
        <button class="btn small" type="submit">Save scope</button>
      </form></td></tr>
      <?php endforeach;?></tbody></table></div>
      <h3>Application scope audit (latest 20)</h3>
      <p>Changing an original document's scope records the administrator,
      previous scope and new scope. Earlier human-supported claims are
      superseded immediately and require an explicit new review.</p>
      <div class="table-scroll"><table><thead><tr>
        <th>Source</th><th>Previous scope</th><th>New scope</th>
        <th>Administrator</th><th>Version</th>
      </tr></thead><tbody>
      <?php foreach($scopeHistory as $change):if(!is_array($change))continue;?>
      <tr><td><?=cms_esc((string)($change['source_id']??''))?></td>
        <td><?=cms_esc((string)($change['previous_scope']??''))?></td>
        <td><?=cms_esc((string)($change['new_scope']??''))?></td>
        <td><?=cms_esc((string)($change['reviewed_by']??''))?></td>
        <td><?=cms_esc((string)($change['version']??0))?></td></tr>
      <?php endforeach; ?></tbody></table></div>
    </section>
    <section class="admin-panel table-scroll">
      <h2>Recently human-supported source quotations</h2>
      <p>These excerpts have been explicitly checked by a CMS administrator
      against an already approved PDF in the private inventory. They are
      <strong>not</strong> independent proof of a safe/correct service
      procedure or the correct application for another model/year.
      Entries with superseded, disputed, rejected or stale source revisions
      are deliberately excluded. The list updates only after a fresh
      inventory reconciliation.</p>
      <table><thead><tr><th>Original document</th><th>Exact quotation</th>
        <th>Application / reviewed by</th></tr></thead><tbody>
      <?php foreach($supportedFindings as $finding):if(!is_array($finding))continue;?>
      <tr><td><?=cms_esc((string)($finding['title']??''))?>
        <small><?=cms_esc((string)($finding['source_id']??''))?></small>
        <p>Page <?=cms_esc((string)($finding['page']??0))?>,
        part <?=cms_esc((string)($finding['part']??0))?></p></td>
        <td><code><?=cms_esc((string)($finding['quote']??''))?></code>
          <small><?=cms_esc((string)($finding['quote_sha']??''))?></small></td>
        <td><?=cms_esc((string)($finding['applicability']??''))?>
          <p><?=cms_esc((string)($finding['reviewed_by']??''))?></p></td></tr>
      <?php endforeach;?></tbody></table>
      <?php if(!$supportedFindings):?>
        <p>No current original-source quotations have completed manual support review.</p>
      <?php endif;?>
    </section>
    <section class="admin-panel table-scroll">
      <h2>Unreviewed original-source measurements (latest 20)</h2>
      <p>Review the **original** manual page, model applicability and extraction accuracy before approving. The quote and hash are linked to one specific source revision. An update invalidates old evidence instead of silently carrying forward approvals.</p>
      <table><thead><tr><th>Source citation</th><th>Exact extracted quote</th><th>Review</th></tr></thead><tbody>
      <?php foreach(array_slice($evidence,0,20) as $item):
        if(!is_array($item))continue;
        $id=(string)($item['id']??'');
        $quoteSha=(string)($item['quote_sha']??'');
        $revision=(string)($item['revision']??'');
        if(!preg_match('/^[a-f0-9]{64}$/D',$id)||
           !preg_match('/^[a-f0-9]{64}$/D',$quoteSha)||
           !preg_match('/^[a-f0-9]{64}$/D',$revision))continue;
        ?>
      <tr><td><small><?=cms_esc((string)($item['source_id']??''))?></small>
        <p>Page <?=cms_esc((string)($item['page']??0))?>,
          part <?=cms_esc((string)($item['part']??0))?></p>
        <small><?=cms_esc((string)($item['applicability']??'Scope unassigned'))?></small></td>
      <td><code><?=cms_esc((string)($item['quote']??''))?></code>
        <p>State: <?=cms_esc((string)($item['review_state']??'unreviewed'))?></p></td>
      <td><form method="post" action="/admin/ai">
        <input type="hidden" name="csrf" value="<?=cms_esc(cms_csrf())?>">
        <input type="hidden" name="action" value="ai_evidence">
        <input type="hidden" name="operation" value="review">
        <input type="hidden" name="evidence_id" value="<?=cms_esc($id)?>">
        <input type="hidden" name="quote_sha" value="<?=cms_esc($quoteSha)?>">
        <input type="hidden" name="revision" value="<?=cms_esc($revision)?>">
        <input type="hidden" name="expected_scope"
          value="<?=cms_esc((string)($item['applicability']??''))?>">
        <label>Action <select name="review_action">
          <option value="dispute">Dispute</option>
          <option value="reject">Reject</option>
          <option value="support">Support after independent source check</option>
          <option value="supersede">Supersede</option>
        </select></label>
        <label>Reason <input name="reason" maxlength="200"></label>
        <label>To support, type I VERIFIED ORIGINAL
          <input name="confirmation" maxlength="20"></label>
        <button class="btn small" type="submit">Record human review</button>
      </form></td></tr>
      <?php endforeach;?></tbody></table>
    </section>
    <section class="admin-panel table-scroll">
      <h2>Cross-document differences (latest 20)</h2>
      <p>These values are not unit-converted and equal scope does not prove independent authorship. Confirm each original manual before accepting a conflict. Dismissals do not delete history.</p>
      <table><thead><tr><th>Source A</th><th>Source B</th><th>Action</th></tr></thead><tbody>
      <?php foreach(array_slice($conflicts,0,20) as $item):
        if(!is_array($item))continue;
        $id=(string)($item['id']??'');if(!preg_match('/^[a-f0-9]{64}$/D',$id))continue;?>
      <tr>
        <td><?=cms_esc((string)($item['source_a']??''))?>,
          page <?=cms_esc((string)($item['page_a']??0))?>:
          <code><?=cms_esc((string)($item['quote_a']??''))?></code></td>
        <td><?=cms_esc((string)($item['source_b']??''))?>,
          page <?=cms_esc((string)($item['page_b']??0))?>:
          <code><?=cms_esc((string)($item['quote_b']??''))?></code></td>
        <td><form method="post" action="/admin/ai">
          <input type="hidden" name="csrf" value="<?=cms_esc(cms_csrf())?>">
          <input type="hidden" name="action" value="ai_evidence">
          <input type="hidden" name="operation" value="dismiss">
          <input type="hidden" name="evidence_id" value="<?=cms_esc($id)?>">
          <button class="btn small" type="submit">Dismiss candidate</button>
        </form></td></tr>
      <?php endforeach;?></tbody></table>
      <h3>Reviewed claim history (latest 20)</h3>
      <table><thead><tr><th>Source</th><th>Latest state</th><th>Revision</th></tr></thead><tbody>
      <?php foreach(array_slice($claims,0,20) as $item):
        if(!is_array($item))continue;?>
      <tr><td><?=cms_esc((string)($item['source_id']??''))?>,
        page <?=cms_esc((string)($item['page']??0))?></td>
        <td><?=cms_esc((string)($item['state']??''))?></td>
        <td><?=cms_esc((string)($item['version']??0))?></td></tr>
      <?php endforeach;?></tbody></table>
    </section>
    <?php
}
