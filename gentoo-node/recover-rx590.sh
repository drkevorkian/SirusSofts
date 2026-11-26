#!/usr/bin/env bash
# Run on Gentoo from a text console. Default operation is read-only.
set -euo pipefail
shopt -s nullglob

die() { echo "ERROR: $*" >&2; exit 1; }
mode=${1:---diagnose}
case "$mode" in
    --diagnose|--plan|--apply) ;;
    --help|-h) echo "Usage: bash $0 [--diagnose | --plan | --apply]"; exit 0 ;;
    *) die "Unknown option: $mode" ;;
esac
(( $# <= 1 )) || die "Expected one option"
[[ -f /etc/gentoo-release ]] || die "Run this on the Gentoo host"

amd=0
nvidia=0
echo "Boot ID: $(cat /proc/sys/kernel/random/boot_id)"
uptime
for device in /sys/bus/pci/devices/*; do
    [[ $(<"$device/class") == 0x03* ]] || continue
    vendor=$(<"$device/vendor")
    driver=unbound
    [[ ! -L $device/driver ]] || driver=$(basename "$(readlink "$device/driver")")
    printf 'PCI %s vendor=%s device=%s driver=%s\n' "${device##*/}" "$vendor" "$(<"$device/device")" "$driver"
    case "$vendor" in 0x1002) amd=$((amd+1)) ;; 0x10de) nvidia=$((nvidia+1)) ;; esac
done

echo "Local Xorg device selections (BusID values are usually decimal):"
for f in /etc/X11/xorg.conf /etc/X11/xorg.conf.d/*.conf; do
    [[ -f $f ]] || continue
    grep -inHE '^[[:space:]]*(BusID|Driver|PrimaryGPU|Option[[:space:]]+"PrimaryGPU")' "$f" || true
done

if [[ $mode == --diagnose ]]; then
    echo "Recent GPU, memory and session failures in this boot:"
    if kernel_log=$(dmesg -T); then
        printf '%s\n' "$kernel_log" | grep -Ei 'amdgpu|GPU reset|ring .*timeout|segfault|out of memory|killed process|PCIe Bus Error' | tail -60 || true
    else
        echo "Run with sudo for kernel messages."
    fi
    user_home=$(getent passwd "${SUDO_USER:-$(id -un)}" | cut -d: -f6)
    for f in /var/log/Xorg.*.log{,.old} "$user_home"/.local/share/xorg/Xorg.*.log{,.old} /var/log/lightdm/lightdm.log "$user_home"/.local/share/sddm/{xorg,wayland}-session.log; do
        [[ -r $f ]] || continue
        echo "$f"
        tail -250 "$f" | grep -Ei 'error|failed|fatal|segfault|crash|terminated|session stopped|\(EE\)' | tail -15 || true
    done
    exit 0
fi

(( amd >= 1 && nvidia == 0 )) || die "Expected AMD display hardware with the NVIDIA card removed"
# Re-merge the installed versions. Mesa stays usable if building its replacement fails.
atoms=()
pin_installed() {
    local installed
    installed=$(portageq best_version / "$1")
    [[ -n $installed ]] || die "Expected installed package $1"
    atoms+=("=$installed${2:-}")
}
pin_installed media-libs/mesa '[video_cards_radeonsi]'
pin_installed sys-kernel/linux-firmware
if portageq has_version / x11-drivers/xf86-video-amdgpu; then
    pin_installed x11-drivers/xf86-video-amdgpu
fi
echo "AMD replacement plan (same versions, build from source):"
opts=(--oneshot --usepkg=n --autounmask=n)
emerge --pretend --verbose "${opts[@]}" "${atoms[@]}"
have_nvidia=0
if portageq has_version / x11-drivers/nvidia-drivers; then
    have_nvidia=1
    echo "Dependency-checked NVIDIA removal plan:"
    emerge --pretend --verbose --depclean x11-drivers/nvidia-drivers
fi
[[ $mode == --apply ]] || exit 0
(( EUID == 0 )) || die "Apply requires root"
console=$(tty || true)
[[ $console =~ ^/dev/tty[0-9]+$ ]] || die "Run apply from a local text console (Ctrl+Alt+F3)"
echo "Portage will ask before each change. Keep PDF and inference workers idle."
emerge --ask --verbose "${opts[@]}" "${atoms[@]}"
if (( have_nvidia )); then
    emerge --ask --verbose --depclean x11-drivers/nvidia-drivers
    if portageq has_version / x11-drivers/nvidia-drivers; then
        die "NVIDIA is still installed. Review Portage's dependency report before proceeding."
    fi
fi
echo "AMD packages replaced. Reboot manually when ready and run --diagnose after login."
