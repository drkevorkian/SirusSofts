<?php
declare(strict_types=1);
if(!defined('SIRUS_CMS_BOOTSTRAPPED')){http_response_code(404);exit;}
require_once __DIR__.'/ai_api.php';

/**
 * CMS administrator facade for the loopback-only Gentoo continuous scheduler.
 * Never accept a path, command, arbitrary URL, source body or model endpoint.
 */
function cms_ai_continuous_payload(array $post):array {
    $action=$post['operation']??null;
    if(!is_string($action)||!in_array($action,[
        'enable','pause','resume','stop','disable','scan',
        'configure','include','exclude','priority'],true))
        throw new InvalidArgumentException('Unknown continuous research operation.');
    $data=['local_only'=>true,'action'=>$action];
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
        $target=$post['target']??null;
        $model=$post['model_id']??null;
        if($passes===false||$passes<1||$passes>100||
           $interval===false||$interval<60||$interval>604800||
           $chat===false||$chat<0||$chat>4||
           !is_string($load)||!is_numeric($load)||
           (float)$load<0||(float)$load>1024||
           !in_array($target,['auto','cpu','nvidia','amd'],true)||
           !is_string($model)||!preg_match(
               '#^[a-zA-Z0-9][a-zA-Z0-9_.:+/-]{0,127}$#D',$model))
            throw new InvalidArgumentException('Invalid continuous research settings.');
        $data['value']=['passes'=>$passes,'revisit_seconds'=>$interval,
            'max_chat_jobs'=>$chat,'max_cpu_load'=>(float)$load,
            'target'=>$target,'model_id'=>$model];
    }
    return $data;
}

function cms_ai_continuous_post(PDO $db,array $post):never {
    cms_need_admin(); // CMS router has already enforced POST CSRF.
    cms_ai_api_rate($db,'continuous:'.hash('sha256',session_id()),'admin');
    $payload=cms_ai_continuous_payload($post);
    $response=cms_ai_api_upstream('input',
        '/api/in/research/continuous/control',$payload);
    if(!isset($response['config'])||!is_array($response['config']))
        throw new RuntimeException('Research controller confirmation unavailable.');
    cms_audit($db,'ai-continuous-'.$payload['action'],
        substr((string)($payload['source_id']??'research'),0,80));
    cms_flash('Continuous research control saved. No deployment or model training performed.');
    cms_go('/admin/ai');
}

function cms_ai_continuous_panel(PDO $db):void {
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
    $active=is_array($info['active']??null)?$info['active']:[];
    $sourceRows=is_array($info['sources']??null)?$info['sources']:[];
    $mode=(string)($cfg['mode']??'unknown');
    ?>
    <section class="admin-panel">
      <h2>Continuous Research — private Gentoo</h2>
      <p>Research is <strong><?=cms_esc($mode)?></strong>. Drafts are unreviewed; none are exported as verified knowledge or used to update model weights. An inventory scan reads existing private extractions without fetching websites.</p>
      <p>Coverage cycle: <?=cms_esc((string)($cycle['id']??'not started'))?>
        (<?=cms_esc((string)($cycle['state']??'idle'))?>).
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
        CPU load (1m): <?=cms_esc((string)($info['cpu_load_1m']??'?'))?>.</p>
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
          <small><?=cms_esc($sid)?></small></td>
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
    <?php
}
