# Gentoo SirusSofts dedicated SSD: in-place migration

This migration is **NOT** a CMS/database migration and **NOT** the new
watched-folder/PDF-worker installer. It safely relocates the existing
`/srv/sirussofts/storage` tree, including the BRP PDFs already copied there.

## Exact previously inspected hardware

- Gentoo `/dev/md127` RAID 0 contains the existing folder.
- Separate disk: `/dev/sdc`, model **SPCC Solid State**, exactly
  **512110190592 bytes**, historical DOS partition table with one
  obsolete type-82 swap partition. The partition table overstates the disk
  by 63 sectors and must be replaced rather than reused.
- Active swap: Gentoo root `/swapfile`, **not** this SSD.
- Existing SirusSofts folder: approximately 7.3 GB of BRP PDF manuals.

**The script will irreversibly erase the entire matching SSD only in
`--apply` mode after an interactive, exact-match confirmation.**
If device model, exact size, old partition layout, signatures, mount state,
RAID status, or expected source filesystem differ, it refuses to proceed.
The `--plan` and `--check` modes change nothing.

## Usage on Gentoo (not Ubuntu)

Pause all PDF downloads/copies into this directory before migrating. Use the
reviewed pinned repository checkout, then:

```bash
sudo bash gentoo-node/prepare-sirus-storage.sh --check
sudo bash gentoo-node/prepare-sirus-storage.sh --plan
```

Review the plan, disk identity and existing source data. The script requires
Gentoo `sfdisk`, `mkfs.ext4`, `rsync`, `blkid`, `wipefs`, and standard
util-linux/coreutils commands. Install missing prerequisites using the
Gentoo system package manager rather than rerunning app installers.

If the plan passes and the operator intends to erase **this exact SSD**, run:

```bash
sudo bash gentoo-node/prepare-sirus-storage.sh --apply
```

The interactive confirmation is **`ERASE /dev/sdc SPCC 512GB`**.

The script then:
1. Replaces only this SSD's obsolete DOS partition table with GPT.
2. Creates one ext4 filesystem with label `SIRUS_DATA` and a unique UUID.
3. Mounts temporarily at a newly created `/mnt/sirus-ssd-new.*` directory.
4. Copies **the entire existing SirusSofts storage tree** using
   `rsync -aHAX --numeric-ids`; compares every file with `rsync --checksum
   --dry-run`. If the source changes during copy, no cutover occurs.
5. Renames the intact original directory on RAID 0 to
   `/srv/sirussofts/storage.raid0-preserved-<timestamp>`. It is **not
   deleted**, even on success.
6. Mounts the new SSD at `/srv/sirussofts/storage`, verifies source files
   again, and adds a filesystem-UUID entry to `/etc/fstab`.

The empty underlying mount-point directory has mode `000` so unprivileged
workers cannot silently fall back to writing to RAID 0 if the SSD does not
mount. All future services MUST independently verify `mountpoint -q
/srv/sirussofts/storage` **before** writing documents or databases.
`nofail` prevents an unavailable data SSD from preventing OS boot, but it
does not mean workloads may use a fallback storage path.

## Verify after migration

```bash
findmnt -M /srv/sirussofts/storage -o TARGET,SOURCE,FSTYPE,UUID
df -hT /srv/sirussofts/storage
sudo du -sh /srv/sirussofts/storage/manuals/inbox
sudo ls -lah /srv/sirussofts/storage/manuals/inbox | head -40
sudo grep -F /srv/sirussofts/storage /etc/fstab
```

Retain the RAID 0 rollback copy until after testing reads, writing a sample
file, checking mount persistence after a reboot, and validating an independent
backup. Removing the old RAID 0 copy is a **separate explicit decision**; it
is never performed by the preparation script.

The dedicated SSD is independent of RAID 0, but it has no inherent redundancy:
one drive failure still loses its data. A directory named `backups` on the
same SSD is **not** a failure-independent backup. Make verified external
backups, including database-native consistent snapshots, before any CMS
database cutover.

## Next phase (not activated by this script)

Build a Gentoo-local spool and deduplicated object library, automatic stable
file detection, three concurrent isolated PDF ingestion workers with
admin-adjustable concurrency, and secure document-status/review synchronization
to the CMS. The live Ubuntu SQLite database stays authoritative until a
separate validated Gentoo CMS/runtime migration and rollback procedure.
