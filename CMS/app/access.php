<?php
declare(strict_types=1);
if(!defined('SIRUS_CMS_BOOTSTRAPPED')){http_response_code(404);exit;}

/** Separate viewer identities from the existing CMS administrator accounts. */
function cms_current_viewer(PDO $db): ?array {
    if(!isset($_SESSION['cms_member_id'],$_SESSION['cms_member_version']))return null;
    $st=$db->prepare('SELECT id,username,password_hash,enabled,auth_version FROM cms_viewers WHERE id=?');
    $st->execute([(int)$_SESSION['cms_member_id']]);
    $member=$st->fetch(PDO::FETCH_ASSOC);
    if(!$member || (int)$member['enabled']!==1 ||
       (int)$member['auth_version']!==(int)$_SESSION['cms_member_version']){
        unset($_SESSION['cms_member_id'],$_SESSION['cms_member_version']);
        return null;
    }
    return $member;
}
function cms_authenticated(PDO $db): bool {
    return cms_is_admin() || cms_current_viewer($db)!==null;
}
function cms_can_view_source(PDO $db,int $githubId): bool {
    if($githubId<1)return false;
    $st=$db->prepare('SELECT 1 FROM cms_github_repositories WHERE github_id=?');
    $st->execute([$githubId]);if(!$st->fetchColumn())return false;
    if(cms_is_admin())return true;
    $viewer=cms_current_viewer($db);
    if(!$viewer)return false;
    $st=$db->prepare('SELECT 1 FROM cms_source_permissions
                      WHERE viewer_id=? AND github_id=?');
    $st->execute([(int)$viewer['id'],$githubId]);
    return (bool)$st->fetchColumn();
}
function cms_granted_repositories(PDO $db): array {
    if(cms_is_admin()){
        return $db->query('SELECT github_id,full_name,github_private FROM cms_github_repositories
                           ORDER BY full_name COLLATE NOCASE')->fetchAll();
    }
    $viewer=cms_current_viewer($db);if(!$viewer)return [];
    $st=$db->prepare('SELECT r.github_id,r.full_name,r.github_private
        FROM cms_github_repositories r
        JOIN cms_source_permissions a ON a.github_id=r.github_id
        WHERE a.viewer_id=? ORDER BY r.full_name COLLATE NOCASE');
    $st->execute([$viewer['id']]);return $st->fetchAll();
}
function cms_change_viewer_password(PDO $db): never {
    $viewer=cms_current_viewer($db);
    if(!$viewer)throw new InvalidArgumentException('Viewer session expired. Sign in again.');
    $current=(string)($_POST['old_password']??'');
    $password=(string)($_POST['new_password']??'');
    if(strlen($password)<14 || strlen($password)>512 ||
       !hash_equals($password,(string)($_POST['confirm_password']??'')))
        throw new InvalidArgumentException('Password confirmation must match (14 characters minimum).');
    if(!password_verify($current,(string)$viewer['password_hash']))
        throw new InvalidArgumentException('Current password is incorrect.');
    $db->prepare('UPDATE cms_viewers SET password_hash=?,auth_version=auth_version+1 WHERE id=?')
       ->execute([password_hash($password,PASSWORD_DEFAULT),$viewer['id']]);
    $_SESSION['cms_member_version']=(int)$viewer['auth_version']+1;
    session_regenerate_id(true);
    $_SESSION['cms_csrf']=bin2hex(random_bytes(32));
    cms_audit($db,'viewer-change-password',(string)$viewer['username']);
    cms_flash('Your password was changed.');
    cms_go('/admin/account');
}
function cms_users_post(PDO $db,string $action): never {
    cms_need_admin();
    if($action==='create_viewer'){
        $username=cms_text($_POST['username']??'',40,'Username',true);
        $password=(string)($_POST['password']??'');
        if(!preg_match('/^[A-Za-z0-9_.-]{3,40}$/D',$username) ||
           strlen($password)<14 || strlen($password)>512)
            throw new InvalidArgumentException('Use a 3–40 character username and password of at least 14 characters.');
        $st=$db->prepare('SELECT 1 FROM cms_admins WHERE username=? COLLATE NOCASE');
        $st->execute([$username]);
        if($st->fetchColumn())throw new InvalidArgumentException('Username already belongs to an administrator.');
        $db->prepare('INSERT INTO cms_viewers(username,password_hash,created_at) VALUES(?,?,?)')
           ->execute([$username,password_hash($password,PASSWORD_DEFAULT),cms_now()]);
        cms_audit($db,'create-viewer',$username);
        cms_flash('Viewer created. Access defaults to NONE until repositories are granted.');
        cms_go('/admin/users');
    }
    $id=cms_get_int($_POST['viewer_id']??'');
    $st=$db->prepare('SELECT id,username FROM cms_viewers WHERE id=?');
    $st->execute([$id]);$viewer=$st->fetch();
    if(!$viewer)throw new InvalidArgumentException('Viewer not found.');
    if($action==='set_viewer_status'){
        $enabled=(string)($_POST['enabled']??'');
        if(!in_array($enabled,['0','1'],true))throw new InvalidArgumentException('Invalid status.');
        $db->prepare('UPDATE cms_viewers SET enabled=?,auth_version=auth_version+1 WHERE id=?')
           ->execute([(int)$enabled,$id]);
        cms_audit($db,'viewer-status',$viewer['username'].':'.$enabled);
        cms_flash('Viewer status changed; existing viewer sessions are invalidated.');
        cms_go('/admin/users');
    }
    if($action==='reset_viewer_password'){
        $password=(string)($_POST['password']??'');
        if(strlen($password)<14 || strlen($password)>512)
            throw new InvalidArgumentException('New password must contain at least 14 characters.');
        $db->prepare('UPDATE cms_viewers SET password_hash=?,auth_version=auth_version+1 WHERE id=?')
           ->execute([password_hash($password,PASSWORD_DEFAULT),$id]);
        cms_audit($db,'reset-viewer-password',$viewer['username']);
        cms_flash('Password reset. Existing sessions are invalidated.');
        cms_go('/admin/users');
    }
    if($action==='save_viewer_grants'){
        $selected=$_POST['github_ids']??[];
        if(!is_array($selected) || count($selected)>10000)
            throw new InvalidArgumentException('Invalid repository grant list.');
        $ids=[];
        foreach($selected as $item){
            if(!is_string($item) || !preg_match('/^[1-9][0-9]{0,15}$/D',$item))
                throw new InvalidArgumentException('Invalid repository identifier.');
            $ids[]=(int)$item;
        }
        if(count($ids)!==count(array_unique($ids)))throw new InvalidArgumentException('Duplicate repository grant.');
        if($ids){
            $placeholders=implode(',',array_fill(0,count($ids),'?'));
            $check=$db->prepare("SELECT COUNT(*) FROM cms_github_repositories WHERE github_id IN ($placeholders)");
            $check->execute($ids);
            if((int)$check->fetchColumn()!==count($ids))
                throw new InvalidArgumentException('An unknown repository was selected.');
        }
        $db->beginTransaction();
        try{
            $db->prepare('DELETE FROM cms_source_permissions WHERE viewer_id=?')->execute([$id]);
            $insert=$db->prepare('INSERT INTO cms_source_permissions(viewer_id,github_id) VALUES(?,?)');
            foreach($ids as $repoId)$insert->execute([$id,$repoId]);
            cms_audit($db,'save-viewer-grants',$viewer['username'].':'.count($ids));
            $db->commit();
        }catch(Throwable $e){if($db->inTransaction())$db->rollBack();throw $e;}
        cms_flash('Source viewing permissions saved. Changes take effect immediately.');
        cms_go('/admin/users');
    }
    throw new InvalidArgumentException('Unrecognized user-management action.');
}
function cms_users_page(PDO $db,?string $error): void {
    cms_need_admin();
    $people=$db->query('SELECT id,username,enabled,last_login FROM cms_viewers
                        ORDER BY username COLLATE NOCASE')->fetchAll();
    $repos=$db->query('SELECT github_id,full_name,github_private FROM cms_github_repositories
                       ORDER BY full_name COLLATE NOCASE')->fetchAll();
    $grants=$db->query('SELECT viewer_id,github_id FROM cms_source_permissions')->fetchAll();
    $map=[];foreach($grants as $entry)$map[(int)$entry['viewer_id']][(int)$entry['github_id']]=true;
    cms_admin_shell_start($db,'Users & source permissions');
    $flash=cms_take_flash();
    if($flash):?><div class="flash-message"><?=cms_esc($flash['message'])?></div><?php endif;?>
    <?php if($error):?><div class="form-error"><?=cms_esc($error)?></div><?php endif;?>
    <div class="admin-panel">
      <h2>Create a source viewer</h2>
      <p>New users can sign in but cannot view any repository source until explicitly authorized here.
      These accounts cannot edit CMS settings, users or published pages.</p>
      <form method="post" action="/admin/users">
        <input type="hidden" name="csrf" value="<?=cms_esc(cms_csrf())?>">
        <input type="hidden" name="action" value="create_viewer">
        <label>Username<input name="username" maxlength="40" autocomplete="off" required></label>
        <label>Initial password<input type="password" name="password" minlength="14" maxlength="512" autocomplete="new-password" required></label>
        <button class="btn primary" type="submit">Create user</button>
      </form>
    </div>
    <?php foreach($people as $person):
      $id=(int)$person['id'];?>
    <div class="admin-panel">
      <h2><?=cms_esc($person['username'])?> <small><?=((int)$person['enabled']===1?'Enabled':'Disabled')?></small></h2>
      <p>Last login: <?=cms_esc($person['last_login']??'Never')?></p>
      <form method="post" action="/admin/users">
        <input type="hidden" name="csrf" value="<?=cms_esc(cms_csrf())?>">
        <input type="hidden" name="action" value="save_viewer_grants">
        <input type="hidden" name="viewer_id" value="<?=$id?>">
        <fieldset class="grant-grid"><legend>Source repositories this user may view</legend>
        <?php foreach($repos as $repo):?>
          <label class="grant-option"><input type="checkbox" name="github_ids[]" value="<?=(int)$repo['github_id']?>"
            <?=isset($map[$id][(int)$repo['github_id']])?'checked':''?>>
            <?=cms_esc($repo['full_name'])?> <?=((int)$repo['github_private']===1?'(private)':'(public)')?>
          </label>
        <?php endforeach;?>
        </fieldset>
        <button class="btn primary" type="submit">Save source permissions</button>
      </form>
      <div class="form-actions">
        <form method="post" action="/admin/users">
          <input type="hidden" name="csrf" value="<?=cms_esc(cms_csrf())?>">
          <input type="hidden" name="action" value="set_viewer_status">
          <input type="hidden" name="viewer_id" value="<?=$id?>">
          <input type="hidden" name="enabled" value="<?=((int)$person['enabled']===1?'0':'1')?>">
          <button class="btn small" type="submit"><?=((int)$person['enabled']===1?'Disable':'Enable')?> account</button>
        </form>
        <form method="post" action="/admin/users">
          <input type="hidden" name="csrf" value="<?=cms_esc(cms_csrf())?>">
          <input type="hidden" name="action" value="reset_viewer_password">
          <input type="hidden" name="viewer_id" value="<?=$id?>">
          <label>New password <input type="password" name="password" minlength="14" maxlength="512" autocomplete="new-password" required></label>
          <button class="btn small" type="submit">Reset password</button>
        </form>
      </div>
    </div>
    <?php endforeach;?>
    <?php if(!$people):?><div class="admin-panel"><p>No viewer accounts yet.</p></div><?php endif;?>
    <?php cms_admin_shell_end($db);
}
function cms_viewer_account_page(PDO $db,?string $error): void {
    $viewer=cms_current_viewer($db);
    if(!$viewer)throw new RuntimeException('Not signed in as a viewer.');
    cms_layout_start($db,'My account');
    ?><section class="page-intro"><div class="eyebrow">PRIVATE WORKSPACE</div>
      <h1>My account</h1><p><?=cms_esc($viewer['username'])?></p>
      <p><a class="inline-link" href="/admin/source">My source repositories</a></p></section>
      <?php if($error):?><div class="form-error"><?=cms_esc($error)?></div><?php endif;?>
      <?php $flash=cms_take_flash();if($flash):?><div class="flash-message"><?=cms_esc($flash['message'])?></div><?php endif;?>
      <div class="admin-panel"><h2>Change password</h2>
        <form method="post" action="/admin/account">
          <input type="hidden" name="csrf" value="<?=cms_esc(cms_csrf())?>">
          <input type="hidden" name="action" value="viewer_password">
          <label>Current password<input type="password" name="old_password" required></label>
          <label>New password<input type="password" name="new_password" minlength="14" required></label>
          <label>Confirm password<input type="password" name="confirm_password" minlength="14" required></label>
          <button class="btn primary" type="submit">Change password</button>
        </form>
        <form method="post" action="/admin/logout">
          <input type="hidden" name="csrf" value="<?=cms_esc(cms_csrf())?>">
          <input type="hidden" name="action" value="logout">
          <button class="btn subtle" type="submit">Sign out</button>
        </form>
      </div>
    <?php cms_layout_end($db);
}
