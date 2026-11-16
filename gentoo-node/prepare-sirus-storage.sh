#!/usr/bin/env bash
# Gentoo: migrate existing SirusSofts documents to an independent SSD safely.
# --plan changes nothing. --apply DESTROYS ALL DATA on the exact identified /dev/sdc.
set -Eeuo pipefail
fail(){ printf 'FAIL: %s\n' "$*" >&2; exit 1; }
mode=--plan
if [[ $# -gt 0 ]]; then mode="$1"; shift; fi
[[ $# == 0 && ( "$mode" == --plan || "$mode" == --apply || "$mode" == --check ) ]] ||
  fail 'Usage: gentoo-node/prepare-sirus-storage.sh --check|--plan|--apply'
if [[ "$mode" == --check ]]; then
  bash -n "$0" || fail 'Invalid script syntax'
  echo 'PASS: Gentoo storage migration script syntax; no disks accessed.'
  exit 0
fi
[[ "$(id -u)" == 0 ]] || fail 'Run with sudo or as Gentoo root.'
[[ -f /etc/gentoo-release ]] || fail 'This script is exclusively for Gentoo.'
disk=/dev/sdc
partition=/dev/sdc1
storage=/srv/sirussofts/storage
expected_model='SPCC Solid State'
expected_bytes=512110190592
for cmd in lsblk sfdisk wipefs blkid findmnt mountpoint rsync mount umount mkfs.ext4 df du swapon; do
  command -v "$cmd" >/dev/null || fail "Required tool missing: $cmd"
done
[[ -b "$disk" && -b "$partition" ]] || fail 'Expected /dev/sdc and /dev/sdc1 are missing.'
model="$(lsblk -dn -o MODEL "$disk" | xargs)"
size="$(lsblk -bdn -o SIZE "$disk" | tr -d '[:space:]')"
[[ "$model" == "$expected_model" && "$size" == "$expected_bytes" ]] ||
  fail "SSD identity mismatch. Found model='$model', bytes='$size'. Stop."
[[ "$(lsblk -rn -o NAME "$disk" | wc -l)" -eq 2 ]] ||
  fail 'Expected exactly one partition on /dev/sdc.'
[[ "$(lsblk -rn -o FSTYPE "$partition" | tr -d '[:space:]')" == '' ]] ||
  fail 'Partition has a recognized filesystem. Do NOT erase it.'
[[ -z "$(blkid -p -s TYPE -o value "$partition" 2>/dev/null || true)" ]] ||
  fail 'Partition contains a filesystem signature. Do NOT erase it.'
disk_sigs="$(wipefs -n --noheadings --output TYPE "$disk" | xargs)"
partition_sigs="$(wipefs -n --noheadings --output TYPE "$partition" | xargs)"
[[ "$disk_sigs" == 'dos' && -z "$partition_sigs" ]] ||
  fail "Unexpected filesystem or disk signature (disk='$disk_sigs', partition='$partition_sigs'); stop."
if command -v mdadm >/dev/null; then
  if mdadm --examine "$disk" >/dev/null 2>&1 ||
     mdadm --examine "$partition" >/dev/null 2>&1; then
    fail 'Existing MD RAID superblock detected on SSD; stop.'
  fi
fi
[[ -z "$(lsblk -rn -o MOUNTPOINTS "$disk" | tr -d '[:space:]')" ]] ||
  fail '/dev/sdc or a child is already mounted.'
if swapon --show=NAME --noheadings | grep -Eq '^/dev/sdc([0-9]+)?$'; then
  fail '/dev/sdc is in use for swap.'
fi
[[ -z "$(ls -A /sys/class/block/sdc/holders)" ]] ||
  fail 'SSD is held by another block device.'
[[ -z "$(ls -A /sys/class/block/sdc1/holders)" ]] ||
  fail 'SSD partition is held by another block device.'
if grep -Eq '(^|[[:space:]])sdc[0-9]*\[' /proc/mdstat; then
  fail 'SSD appears to be part of a software RAID array.'
fi
table="$(sfdisk -d "$disk" 2>/dev/null)" ||
  fail 'Could not inspect the existing partition table.'
grep -qx 'label: dos' <<< "$table" || fail 'Unexpected partition table; manual inspection needed.'
[[ "$(grep -Ec '^/dev/sdc[0-9]+[[:space:]]*:' <<< "$table")" -eq 1 ]] ||
  fail 'Expected one historical swap-type partition.'
grep -Eq '^/dev/sdc1[[:space:]]*:.*type=82([[:space:],]|$)' <<< "$table" ||
  fail 'Existing partition is not the recorded old Linux swap type.'
[[ -d "$storage" && ! -L "$storage" ]] || fail 'Existing SirusSofts storage folder missing/linked.'
if mountpoint -q "$storage"; then fail 'Storage path is already mounted. Never reformat.'; fi
[[ "$(findmnt -rn -T "$storage" -o TARGET)" == '/' ]] ||
  fail 'The existing storage path is no longer on Gentoo root; stop.'
[[ "$(findmnt -rn -T "$storage" -o FSTYPE)" == 'ext4' ]] ||
  fail 'Expected existing Gentoo ext4 root filesystem; stop.'

[[ -d /srv/sirussofts && ! -L /srv/sirussofts ]] ||
  fail 'SirusSofts parent directory missing or linked.'
if grep -Eq '^[[:space:]]*[^#][^[:space:]]+[[:space:]]+/srv/sirussofts/storage[[:space:]]' /etc/fstab; then
  fail 'Existing fstab mount for SirusSofts storage; manual inspection required.'
fi
[[ -z "$(findmnt -rn -o TARGET -R "$storage" 2>/dev/null | tail -n +2)" ]] ||
  fail 'Nested mounts detected inside existing storage.'
bytes="$(du -sB1 "$storage" | awk '{print $1}')"
free="$(df -PB1 "$storage" | awk 'NR==2{print $4}')"
echo '=== REVIEWED DRIVE IDENTITY ==='
echo "$disk: $model | $size bytes | historical DOS swap-type partition"
echo "Existing source: $storage ($bytes bytes, root free space $free bytes)"
echo 'No filesystem, active swap, RAID membership, mounts, or block holders detected.'
echo 'Plan: ERASE /dev/sdc entirely, create GPT + ext4, temporarily mount and'
echo '      CHECKSUM-COPY all existing SirusSofts data, verify, preserve RAID0'
echo '      source directory as a rollback copy, and mount SSD at the original path.'
echo 'No formatting, copying or mount changes occur during --plan.'
if [[ "$mode" == --plan ]]; then exit 0; fi
[[ -t 0 && -t 1 ]] || fail 'Interactive local Gentoo terminal required.'
echo 'WARNING: THIS DESTROYS ANY RECOVERABLE DATA ON THE ENTIRE /dev/sdc DISK.'
echo 'Stop all downloads and writes under /srv/sirussofts/storage before proceeding.'
read -r -p 'Type ERASE /dev/sdc SPCC 512GB to continue: ' confirm
[[ "$confirm" == 'ERASE /dev/sdc SPCC 512GB' ]] || fail 'Erase not authorized.'
# Repeat identity checks immediately before writing to avoid accidental device changes.
[[ "$(lsblk -bdn -o SIZE "$disk" | tr -d '[:space:]')" == "$expected_bytes" ]] ||
  fail 'SSD changed since preflight.'
[[ "$(lsblk -dn -o MODEL "$disk" | xargs)" == "$expected_model" ]] ||
  fail 'SSD model changed since preflight.'
# No writes to the old directory until verified copy and mount cutover.
printf 'label: gpt\nunit: sectors\n\nstart=2048, type=linux\n' |
  sfdisk --wipe always --wipe-partitions always "$disk"
if command -v udevadm >/dev/null; then udevadm settle; fi
[[ -b "$partition" ]] || fail 'Expected newly partitioned SSD partition missing.'
mkfs.ext4 -F -m 1 -L SIRUS_DATA "$partition"
uuid="$(blkid -s UUID -o value "$partition")"
[[ "$uuid" =~ ^[a-fA-F0-9-]{36}$ ]] || fail 'Could not read new ext4 UUID.'
stage="$(mktemp -d /mnt/sirus-ssd-new.XXXXXXXX)"
backup=''
cutover=0
fstab_backup="$(mktemp /root/sirus-fstab-before-ssd.XXXXXXXX)"
cp -p /etc/fstab "$fstab_backup"
cleanup(){
  rc=$?
  trap - EXIT
  if mountpoint -q "$stage" 2>/dev/null; then umount "$stage" || true; fi
  rmdir "$stage" 2>/dev/null || true
  if [[ "$rc" -ne 0 ]]; then
    echo "Migration interrupted (exit $rc)." >&2
    if [[ "$cutover" -eq 1 ]]; then
      echo "The RAID0 originals were NOT deleted; saved at $backup" >&2
      echo "The SSD mount may need manual rollback; stop before deleting anything." >&2
    else
      echo "Original SirusSofts storage remains at $storage" >&2
    fi
    echo "Original fstab backup is $fstab_backup" >&2
  fi
  exit "$rc"
}
trap cleanup EXIT
mount -t ext4 -o rw,noatime "UUID=$uuid" "$stage"
mountpoint -q "$stage" || fail 'Temporary SSD mount verification failed.'
echo 'Copying 7+ GiB to SSD with ownership, ACLs, hardlinks, and xattrs preserved...'
rsync -aHAX --numeric-ids --one-file-system "$storage/" "$stage/"
echo 'Performing checksum comparison against the original RAID0 folder...'
diffs="$(rsync -aHAXnci --numeric-ids --one-file-system --out-format='%n' "$storage/" "$stage/")"
if [[ -n "$diffs" ]]; then
  echo "$diffs" | head -n 25 >&2
  fail 'Source files changed or checksum verification failed. No cutover attempted.'
fi
echo 'PASS: Checksum verification completed.'
umount "$stage"
backup="/srv/sirussofts/storage.raid0-preserved-$(date -u +%Y%m%dT%H%M%SZ)"
[[ ! -e "$backup" ]] || fail "Existing backup path: $backup"
mv -- "$storage" "$backup"
cutover=1
install -d -m 000 -o root -g root "$storage"
if ! mount -t ext4 -o rw,noatime "UUID=$uuid" "$storage"; then
  rmdir "$storage" 2>/dev/null || true
  mv -- "$backup" "$storage" || true
  cutover=0
  fail 'Final SSD mount failed; attempted to restore RAID0 originals.'
fi
findmnt -M "$storage" -n -o SOURCE,FSTYPE ||
  fail 'Failed to verify SSD is mounted on final path.'
# Recheck after cutover. The preserved source remains intact for rollback.
diffs="$(rsync -aHAXnci --numeric-ids --one-file-system --out-format='%n' "$backup/" "$storage/")"
[[ -z "$diffs" ]] || fail 'Post-mount file verification failed; originals preserved.'
printf '\n# SirusSofts dedicated SSD; fail-closed worker checks require this mount\nUUID=%s /srv/sirussofts/storage ext4 noatime,nofail 0 2\n' "$uuid" >> /etc/fstab
echo 'PASS: Dedicated SSD mounted at /srv/sirussofts/storage and registered by UUID.'
echo "RAID0 original data preserved at $backup — DO NOT DELETE until verified."
echo "fstab backup: $fstab_backup"
echo 'Workers must verify mountpoint /srv/sirussofts/storage before reading or writing.'
