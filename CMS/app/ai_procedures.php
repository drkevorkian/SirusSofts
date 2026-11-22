<?php
declare(strict_types=1);
if(!defined('SIRUS_CMS_BOOTSTRAPPED')){http_response_code(404);exit;}

/**
 * Admin-only procedure-suggestion review. The CMS CSRF/session check is owned
 * by the existing /admin/ai router, followed by the signed Gentoo bridge.
 */
function cms_ai_procedures_payload(array $post,string $actor):array {
    if(!preg_match('/^cms-admin:[1-9][0-9]{0,12}$/D',$actor))
        throw new InvalidArgumentException('An authenticated administrator is required.');
    $operation=$post['operation']??null;
    if(!is_string($operation) ||
       !in_array($operation,['seed','accept','reject','pause','resume','propose_scope','review_scope','vote_procedure'],true))
        throw new InvalidArgumentException('Unknown procedure-review operation.');
    $out=['local_only'=>true,'action'=>$operation,'actor'=>$actor];
    if($operation==='propose_scope'){
        foreach(['page','part'] as $field){
            $value=filter_var($post[$field]??null,FILTER_VALIDATE_INT,
                ['options'=>['min_range'=>1,'max_range'=>100000]]);
            if($value===false)throw new InvalidArgumentException('Invalid PDF page or part.');
            $out[$field]=$value;
        }
        foreach(['source_id'=>68,'quote'=>6000,'expected_scope'=>149] as $field=>$max){
            $value=$post[$field]??null;
            if(!is_string($value)||trim($value)===''||strlen($value)>$max)
                throw new InvalidArgumentException('Incomplete PDF citation or scope.');
            $out[$field]=$value;
        }
        if(!preg_match('/^pdf:[0-9a-f]{64}$/D',$out['source_id']))
            throw new InvalidArgumentException('Invalid original PDF identifier.');
        return $out;
    }
    if($operation==='review_scope'||$operation==='vote_procedure'){
        $suffix=$operation==='vote_procedure'?' PRINTED EVIDENCE':' SCOPE';
        if($operation==='vote_procedure'){
            foreach(['left_scope','right_scope','expected_hash'] as $field){
                if(!is_string($post[$field]??null)||!preg_match('/^[a-f0-9]{64}$/D',$post[$field]))
                    throw new InvalidArgumentException('Invalid procedure evidence reference.');
                $out[$field]=$post[$field];
            }
        }
        $decision=$post['decision']??null;
        $id=$post['id']??null;
        $reason=$post['reason']??null;
        if(!in_array($decision,['support','reject'],true)||!is_string($id)||
           !preg_match('/^[0-9a-f]{64}$/D',$id)||!is_string($reason)||
           strlen(trim($reason))<3||strlen($reason)>200||
           ($post['confirmation']??null)!==strtoupper($decision).$suffix)
            throw new InvalidArgumentException('Invalid scope decision, confirmation or reason.');
        return $out+['decision'=>$decision,'id'=>$id,'reason'=>trim($reason),
            'confirmation'=>$post['confirmation']];
    }
    if($operation==='seed'){
        $sid=$post['source_id']??null;
        $heading=$post['heading']??null;
        if(!is_string($sid)||!preg_match('/^pdf:[a-f0-9]{64}$/D',$sid) ||
           !is_string($heading)||!preg_match(
             '#^[A-Za-z0-9][A-Za-z0-9 ._/()-]{0,95}$#D',$heading))
            throw new InvalidArgumentException('Select an exact original PDF and procedure heading.');
        $out['source_id']=$sid;
        $out['heading']=$heading;
    } elseif($operation==='pause'||$operation==='resume') {
        $seed=$post['seed_id']??null;
        if(!is_string($seed)||!preg_match('/^[a-f0-9]{64}$/D',$seed))
            throw new InvalidArgumentException('Invalid procedure discovery seed.');
        $out['seed_id']=$seed;
    } else {
        $id=$post['id']??null;
        $hash=$post['expected_hash']??null;
        $reason=$post['reason']??null;
        $confirmation=$post['confirmation']??null;
        $required=$operation==='accept'?'ACCEPT FOR REVIEW':'REJECT SUGGESTION';
        if(!is_string($id)||!preg_match('/^[a-f0-9]{64}$/D',$id) ||
           !is_string($hash)||!preg_match('/^[a-f0-9]{64}$/D',$hash) ||
           !is_string($reason)||strlen($reason)>200||
           strlen(trim($reason))<3||
           $confirmation!==$required)
            throw new InvalidArgumentException(
                'Refresh the source evidence, give a review reason and type the exact confirmation.');
        $out['id']=$id;
        $out['expected_hash']=$hash;
        $out['reason']=trim($reason);
        $out['confirmation']=$confirmation;
    }
    return $out;
}

function cms_ai_procedures_post(PDO $db,array $post):never {
    require_once __DIR__.'/ai_api.php';
    cms_need_admin(); // Central /admin POST router already checks CSRF.
    cms_ai_api_rate($db,'procedure-review:'.hash('sha256',session_id()),'admin');
    $actor='cms-admin:'.(string)($_SESSION['cms_admin_id']??'');
    $payload=cms_ai_procedures_payload($post,$actor);
    $result=cms_ai_api_upstream('input','/api/in/research/procedures/control',$payload);
    $action=$payload['action'];
    if(in_array($action,['accept','reject','propose_scope','review_scope','vote_procedure'],true) &&
       (($result['recorded']??null)!==true||($result['action']??null)!==$action))
        throw new RuntimeException('Private procedure review was not confirmed.');
    if($action==='seed' && ($result['seed_id']??null)===null)
        throw new RuntimeException('Private procedure discovery seed not confirmed.');
    if(in_array($action,['pause','resume'],true)&&($result['found']??null)!==true)
        throw new RuntimeException('Private procedure discovery seed not found.');
    cms_audit($db,'ai-procedure-'.$action,
        substr((string)($payload['id']??$payload['seed_id']??$payload['source_id']??'unknown'),0,80));
    cms_flash('Private procedure review recorded. No repair or source claim approved.');
    if(in_array($action,['propose_scope','review_scope'],true))
        cms_go('/admin/ai?scope_proposal='.rawurlencode((string)$result['id']).'#procedure-reviews');
    cms_go('/admin/ai#procedure-reviews');
}

function cms_ai_procedures_panel(PDO $db):void {
    require_once __DIR__.'/ai_api.php';
    // Called only from cms_ai_page() following cms_need_admin().
    try {
        cms_ai_api_rate($db,'procedure-overview:'.hash('sha256',session_id()),'admin');
        $overview=cms_ai_api_upstream('input','/api/in/research/procedures');
        $list=$overview['suggestions']??[];
        $seeds=$overview['seeds']??[];
        $decisions=$overview['decisions']??[];
        if(!is_array($list)||count($list)>10||
           !is_array($seeds)||count($seeds)>10||
           !is_array($decisions)||count($decisions)>10)
            throw new RuntimeException('Invalid bounded procedure response.');
    } catch(Throwable $e) {
        echo '<section id="procedure-reviews" class="admin-panel">'.
             '<h2>Procedure suggestion review</h2>'.
             '<p>The private signed Gentoo review service is unavailable; no operation performed.</p></section>';
        return;
    }
    $detail=null;
    $selected=$_GET['procedure_candidate']??null;
    if($selected!==null){
        try {
            if(!is_string($selected)||!preg_match('/^[a-f0-9]{64}$/D',$selected))
                throw new InvalidArgumentException('Invalid suggestion ID.');
            cms_ai_api_rate($db,'procedure-detail:'.hash('sha256',session_id()),'admin');
            $detail=cms_ai_api_upstream('input',
                '/api/in/research/procedures/control',
                ['local_only'=>true,'action'=>'detail','id'=>$selected]);
            if(!is_array($detail)||($detail['id']??null)!==$selected)
                throw new RuntimeException('Private detail mismatch.');
        } catch(Throwable $e){
            $detail=['status'=>'stale_or_unavailable','reviewable'=>false];
        }
    }
    ?>
    <section id="procedure-reviews" class="admin-panel">
      <h2>Equipment applicability evidence</h2>
      <p>Submit a passage that explicitly identifies the full declared equipment scope.
      A separate administrator reviews the original passage. Support applies only to
      the cited scope. Service use requires a separate determination.</p>
      <form method="post" action="/admin/ai" class="form-grid">
        <input type="hidden" name="csrf" value="<?=cms_esc(cms_csrf())?>">
        <input type="hidden" name="action" value="ai_procedure_review">
        <input type="hidden" name="operation" value="propose_scope">
        <label>Original PDF identifier <input name="source_id" required maxlength="68"></label>
        <label>Manufacturer/model/year/engine/assembly/revision
          <input name="expected_scope" required maxlength="149"></label>
        <label>Page <input type="number" name="page" min="1" max="100000" required></label>
        <label>Part <input type="number" name="part" min="1" max="100000" required></label>
        <label>Exact original quotation <textarea name="quote" maxlength="1500" required></textarea></label>
        <button type="submit">Submit for independent evidence review</button>
      </form>
      <?php foreach(array_slice($overview['attestations']??[],0,10) as $entry): ?>
        <p><a href="?scope_proposal=<?=rawurlencode((string)$entry['id'])?>#procedure-reviews">
          <?=cms_esc((string)$entry['id'])?></a>
          Submitted by <?=cms_esc((string)$entry['proposer'])?> ·
          Recorded decision <?=cms_esc((string)($entry['decision']??'pending'))?></p>
      <?php endforeach;
      $proposal=$_GET['scope_proposal']??null;
      if(is_string($proposal)&&preg_match('/^[a-f0-9]{64}$/D',$proposal)):
        try {
            cms_ai_api_rate($db,'scope-detail:'.hash('sha256',session_id()),'admin');
            $item=cms_ai_api_upstream('input','/api/in/research/procedures/control',
                ['action'=>'scope_detail','id'=>$proposal,'local_only'=>true]);
            if(($item['id']??null)!==$proposal)throw new RuntimeException();
        } catch(Throwable $e){$item=['state'=>'unavailable'];}
        ?>
        <h3>Selected scope evidence</h3>
        <p>Current state <?=cms_esc((string)$item['state'])?> · Scope version <?=(int)($item['scope_version']??0)?></p>
        <?php if(is_array($item['evidence']??null)): $ev=$item['evidence']; ?>
        <p><?=cms_esc((string)$ev['expected_scope'])?> · <?=cms_esc((string)$ev['source_id'])?>
        · page <?=(int)$ev['page']?>, part <?=(int)$ev['part']?></p>
        <blockquote><?=cms_esc((string)$ev['quote'])?></blockquote>
        <?php endif; if(!empty($item['reviewer'])): ?>
        <p><?=cms_esc((string)$item['reviewer'])?> · <?=cms_esc((string)$item['historical_decision'])?>
        · <?=cms_esc((string)$item['reason'])?></p>
        <?php endif; if($item['state']==='pending'): ?>
        <form method="post" action="/admin/ai">
          <input type="hidden" name="csrf" value="<?=cms_esc(cms_csrf())?>">
          <input type="hidden" name="action" value="ai_procedure_review">
          <input type="hidden" name="operation" value="review_scope">
          <input type="hidden" name="id" value="<?=cms_esc($proposal)?>">
          <label>Decision <select name="decision"><option>support</option><option>reject</option></select></label>
          <label>Review reason <input name="reason" maxlength="200" required></label>
          <label>Type SUPPORT SCOPE or REJECT SCOPE <input name="confirmation" required></label>
          <button type="submit">Record scope evidence decision</button>
        </form>
        <?php endif; endif; ?>
      <h3>Independent review of printed procedure evidence</h3>
      <p>Select the candidate and the supported scope proposal for each PDF in the
      displayed source order. Two separate administrators must support the same
      original evidence. A rejection closes that evidence packet.</p>
      <form method="get" action="/admin/ai#procedure-reviews">
        <?php foreach(['packet'=>'Candidate ID','left_scope'=>'First PDF scope proposal ID',
                       'right_scope'=>'Second PDF scope proposal ID'] as $field=>$label): ?>
          <label><?=cms_esc($label)?> <input name="<?=cms_esc($field)?>" maxlength="64" required></label>
        <?php endforeach; ?>
        <button>Inspect procedure evidence</button>
      </form>
      <?php if(isset($_GET['packet'])):
        try{
            $params=['id'=>$_GET['packet'],'left_scope'=>$_GET['left_scope']??null,
                     'right_scope'=>$_GET['right_scope']??null];
            foreach($params as $v)if(!is_string($v)||!preg_match('/^[a-f0-9]{64}$/D',$v))
                throw new InvalidArgumentException();
            cms_ai_api_rate($db,'procedure-packet:'.hash('sha256',session_id()),'admin');
            $packet=cms_ai_api_upstream('input','/api/in/research/procedures/control',
                $params+['local_only'=>true,'action'=>'inspect_procedure']);
            if(($packet['id']??null)!==$params['id'])throw new RuntimeException();
        }catch(Throwable $e){$packet=['state'=>'blocked'];}
        ?>
        <p>Printed evidence review <strong><?=cms_esc((string)$packet['state'])?></strong>.
        Service use requires separate authorization.</p>
        <?php foreach($packet['scopes']??[] as $scope): $ev=$scope['evidence']; ?>
          <p><?=cms_esc((string)$ev['source_id'])?> · <?=cms_esc((string)$ev['expected_scope'])?>
          · scope version <?=(int)$ev['scope_version']?> · page <?=(int)$ev['page']?>, part <?=(int)$ev['part']?></p>
          <blockquote><?=cms_esc((string)$ev['quote'])?></blockquote>
        <?php endforeach;
        foreach($packet['windows']??[] as $w): ?>
          <h4><?=cms_esc((string)$w['source_id'])?></h4>
          <?php foreach(array_merge([$w['heading_citation']],array_column($w['steps'],'citation'),
                array_column($w['explicit_relations'],'citation')) as $cite): ?>
              <p>Page <?=(int)$cite['page']?>, part <?=(int)$cite['part']?> ·
              <?=cms_esc((string)$cite['quote'])?></p>
          <?php endforeach; endforeach;
        if($packet['state']==='pending'): ?>
          <form method="post" action="/admin/ai">
            <input type="hidden" name="csrf" value="<?=cms_esc(cms_csrf())?>">
            <input type="hidden" name="action" value="ai_procedure_review">
            <input type="hidden" name="operation" value="vote_procedure">
            <?php foreach($params+['expected_hash'=>$packet['digest']] as $field=>$v): ?>
              <input type="hidden" name="<?=cms_esc($field)?>" value="<?=cms_esc($v)?>">
            <?php endforeach; ?>
            <label>Printed evidence <select name="decision"><option>support</option><option>reject</option></select></label>
            <label>Reason <input name="reason" maxlength="200" required></label>
            <label>Type SUPPORT PRINTED EVIDENCE or REJECT PRINTED EVIDENCE
              <input name="confirmation" required></label>
            <button>Record evidence review</button>
          </form>
        <?php endif; endif; ?>
      <?php foreach($overview['votes']??[] as $vote):
        $query=http_build_query(['packet'=>$vote['suggestion'],'left_scope'=>$vote['left_scope'],
                                 'right_scope'=>$vote['right_scope']]); ?>
        <p>Recorded <?=cms_esc((string)$vote['decision'])?> · <?=cms_esc((string)$vote['actor'])?>
          · <?=cms_esc((string)$vote['reason'])?> ·
          <a href="?<?=cms_esc($query)?>#procedure-reviews">Recheck current evidence</a></p>
      <?php endforeach;
      foreach($overview['blocked']??[] as $target): ?>
        <p>Blocked recheck <?=cms_esc((string)$target['id'])?> · observation
          <?=(int)$target['last_version']?> · <?=cms_esc((string)$target['last_reason'])?></p>
      <?php endforeach; ?>
      <h2>Cross-manual procedure suggestions — administrator review</h2>
      <p>Only exact approved original-PDF citations appear here. Scope entries are
      <strong>administrator declarations, not verified fitment</strong>. A reversed
      procedure order is an unreviewed candidate, not a verified contradiction
      or service instruction. Accepting adds the selected pair to the separate
      private recheck queue, never to model training or approved knowledge.</p>
      <p>Opt-in discovery: <?=!empty($overview['configured'])?'Configured':'Not configured'?>.
      Suggestions shown: <?=count($list)?> (bounded page of ten).</p>
      <form method="post" action="/admin/ai" class="form-grid">
        <input type="hidden" name="csrf" value="<?=cms_esc(cms_csrf())?>">
        <input type="hidden" name="action" value="ai_procedure_review">
        <input type="hidden" name="operation" value="seed">
        <label>Exact approved PDF identifier
          <input name="source_id" type="text" maxlength="68"
            placeholder="pdf:64-character-sha256" required></label>
        <label>Exact indexed procedure heading
          <input name="heading" type="text" maxlength="96"
            placeholder="Removal of primary clutch" required></label>
        <button class="btn small" type="submit">Opt in to bounded discovery</button>
      </form>
      <p class="muted small">The selected PDF must have a current complete
      CMS-admin-declared manufacturer/model/year/engine/assembly/revision scope.
      Without operator opt-in, no automatic cross-manual discovery starts.</p>
      <?php if($seeds):?>
      <h3>Opted-in discovery seeds</h3>
      <div class="table-scroll"><table><thead><tr><th>Original PDF / heading</th>
      <th>State</th><th>Control</th></tr></thead><tbody>
      <?php foreach($seeds as $seed):
        if(!is_array($seed))continue;
        $id=(string)($seed['id']??'');
        if(!preg_match('/^[a-f0-9]{64}$/D',$id))continue;
        $disabled=($seed['state']??'')==='disabled';?>
        <tr><td><code><?=cms_esc((string)($seed['source_id']??''))?></code>
          <p><?=cms_esc((string)($seed['heading']??''))?></p></td>
          <td><?=cms_esc((string)($seed['state']??''))?>
              <?=cms_esc((string)($seed['last_reason']??''))?></td>
          <td><form method="post" action="/admin/ai">
            <input type="hidden" name="csrf" value="<?=cms_esc(cms_csrf())?>">
            <input type="hidden" name="action" value="ai_procedure_review">
            <input type="hidden" name="seed_id" value="<?=cms_esc($id)?>">
            <input type="hidden" name="operation" value="<?=$disabled?'resume':'pause'?>">
            <button type="submit" class="btn small"><?=$disabled?'Resume':'Pause'?></button>
          </form></td></tr>
      <?php endforeach;?></tbody></table></div>
      <?php endif;?>
      <h3>Unreviewed source-cited suggestions</h3>
      <?php if(!$list):?><p>No pending candidate suggestions in this bounded view.</p>
      <?php else:?><div class="table-scroll"><table><thead>
      <tr><th>Procedure</th><th>Source pair</th><th>Evidence</th></tr>
      </thead><tbody>
      <?php foreach($list as $item):
        if(!is_array($item))continue;
        $id=(string)($item['id']??'');
        if(!preg_match('/^[a-f0-9]{64}$/D',$id))continue;
      ?><tr><td><?=cms_esc((string)($item['heading']??''))?></td>
      <td><small><?=cms_esc(implode(' / ',array_map('strval',
          is_array($item['source_ids']??null)?$item['source_ids']:[])))?></small></td>
      <td><code><?=cms_esc($id)?></code> <?=cms_esc((string)($item['status']??'unknown'))?>
        <a href="/admin/ai?procedure_candidate=<?=rawurlencode($id)?>#procedure-reviews">
        View original evidence</a></td></tr><?php endforeach;?>
      </tbody></table></div><?php endif;?>
      <?php if($selected!==null):?>
        <details open class="admin-panel"><summary>Selected candidate evidence</summary>
        <?php if(!is_array($detail)||empty($detail['reviewable'])):?>
        <p>The candidate is stale, already decided, or unavailable. No decision is possible.</p>
        <?php else:
          $hash=(string)($detail['evidence_hash']??'');
          $cid=(string)($detail['id']??'');
          $safe= preg_match('/^[a-f0-9]{64}$/D',$cid) &&
                 preg_match('/^[a-f0-9]{64}$/D',$hash);
        ?>
        <p>Review original PDF pages and ensure the declared equipment
        applicability is correct. Revalidation is repeated when submitting.</p>
        <?php foreach(array_slice(is_array($detail['selections']??null)?
              $detail['selections']:[],0,2) as $selection):
          if(!is_array($selection))continue;?>
          <h4><?=cms_esc((string)($selection['source_id']??''))?></h4>
          <p>Declared scope:
          <?=cms_esc((string)json_encode($selection['scope']??[],
              JSON_UNESCAPED_UNICODE|JSON_INVALID_UTF8_SUBSTITUTE))?></p>
        <?php endforeach;?>
        <?php foreach(array_slice(is_array($detail['windows']??null)?
              $detail['windows']:[],0,2) as $window):
          if(!is_array($window))continue;
          $heading=is_array($window['heading_citation']??null)?
              $window['heading_citation']:[];?>
          <h4>Original <?=cms_esc((string)($window['source_id']??''))?> —
              page <?=(int)($heading['page']??0)?>, part <?=(int)($heading['part']??0)?></h4>
          <p>Heading: <?=cms_esc((string)($heading['quote']??''))?></p>
          <?php foreach(array_slice(is_array($window['steps']??null)?
              $window['steps']:[],0,32) as $step):
              if(!is_array($step))continue;
              $citation=is_array($step['citation']??null)?
                  $step['citation']:[];?>
             <p><small>Page <?=(int)($citation['page']??0)?>,
               part <?=(int)($citation['part']??0)?> —
               SHA <?=cms_esc((string)($citation['quote_sha256']??''))?></small>
               <code><?=cms_esc((string)($citation['quote']??''))?></code></p>
          <?php endforeach;?>
          <?php foreach(array_slice(is_array($window['explicit_relations']??null)?
              $window['explicit_relations']:[],0,16) as $relation):
              if(!is_array($relation))continue;
              $citation=is_array($relation['citation']??null)?
                  $relation['citation']:[];?>
              <p>Explicit ordering clause (page <?=(int)($citation['page']??0)?>,
               part <?=(int)($citation['part']??0)?>):
               <code><?=cms_esc((string)($citation['quote']??''))?></code></p>
          <?php endforeach;?>
        <?php endforeach;?>
        <p>Order-disagreement candidates:
          <?=count(is_array($detail['candidates']??null)?$detail['candidates']:[])?>.
          These are printed-text comparisons only, not service approvals.</p>
        <?php foreach(array_slice(is_array($detail['candidates']??null)?
            $detail['candidates']:[],0,10) as $candidate):
            if(!is_array($candidate))continue;?>
            <p><strong><?=cms_esc((string)($candidate['status']??''))?></strong> —
            <?=cms_esc((string)json_encode($candidate,
                  JSON_UNESCAPED_UNICODE|JSON_INVALID_UTF8_SUBSTITUTE))?></p>
        <?php endforeach;?>
        <?php if($safe):?>
        <?php foreach(['accept'=>'ACCEPT FOR REVIEW',
                       'reject'=>'REJECT SUGGESTION'] as $action=>$confirm):?>
        <form method="post" action="/admin/ai" class="admin-panel">
          <input type="hidden" name="csrf" value="<?=cms_esc(cms_csrf())?>">
          <input type="hidden" name="action" value="ai_procedure_review">
          <input type="hidden" name="operation" value="<?=$action?>">
          <input type="hidden" name="id" value="<?=cms_esc($cid)?>">
          <input type="hidden" name="expected_hash" value="<?=cms_esc($hash)?>">
          <label>Administrator reason (recorded, 3–200 characters)
            <textarea name="reason" maxlength="200" required></textarea></label>
          <label>Type <?=$confirm?> to <?=$action?> this unreviewed suggestion
            <input type="text" name="confirmation" required></label>
          <button type="submit" class="btn small"><?=$action==='accept'?
              'Accept into private review queue':'Reject suggestion'?></button>
        </form>
        <?php endforeach;endif;?>
        <?php endif;?>
        <a href="/admin/ai#procedure-reviews">Close evidence</a>
        </details>
      <?php endif;?>
      <?php if($decisions):?>
      <h3>Immutable administrator decision history (latest ten)</h3>
      <div class="table-scroll"><table><thead>
      <tr><th>Suggestion</th><th>Decision</th><th>Actor / reason</th><th>Review target</th></tr>
      </thead><tbody><?php foreach($decisions as $decision):
        if(!is_array($decision))continue;?>
        <tr><td><code><?=cms_esc((string)($decision['suggestion_id']??''))?></code></td>
          <td><?=cms_esc((string)($decision['action']??''))?></td>
          <td><?=cms_esc((string)($decision['actor']??''))?>:
              <?=cms_esc((string)($decision['reason']??''))?></td>
          <td><?=cms_esc((string)($decision['review_id']??''))?></td></tr>
      <?php endforeach;?></tbody></table></div><?php endif;?>
    </section>
    <?php
}
