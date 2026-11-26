#!/usr/bin/env bash
# Install KDE Plasma on an existing Ubuntu workstation.
set -Eeuo pipefail

usage() {
    cat <<'EOF'
Usage: sudo bash install-kde.sh [--minimal|--full] [--dry-run]

Installs a lean KDE workstation by default, with development and diagnostic
utilities from Ubuntu's repositories. --full installs the Kubuntu desktop. A fresh Ubuntu Server install uses SDDM. If a login manager
is already configured, keep it and choose Plasma at the login screen.

--dry-run  Preview package changes using the current APT cache.
After a successful install, reboot when ready. Run on your workstation.
EOF
}

profile=minimal
dry_run=0
while (( $# )); do
    case "$1" in
        -h|--help) usage; exit 0 ;;
        --minimal) profile=minimal ;;
        --full) profile=full ;;
        --dry-run) dry_run=1 ;;
        *) usage >&2; exit 2 ;;
    esac
    shift
done
packages=(git curl wget ca-certificates build-essential cmake ninja-build
          pkg-config python3 python3-venv pciutils usbutils smartmontools
          nvme-cli mdadm htop lm-sensors)
desktop=kde-plasma-desktop
if [[ $profile == full ]]; then desktop=kubuntu-desktop; fi
packages+=("$desktop" sddm konsole dolphin kate ark)
if [[ -r /etc/os-release ]]; then
    . /etc/os-release
fi
if [[ ${ID:-} != ubuntu ]]; then
    echo 'This installer requires Ubuntu.' >&2
    exit 1
fi

if (( dry_run )); then
    apt-get --simulate --no-remove install "${packages[@]}"
    exit
fi
if (( EUID != 0 )); then
    echo 'Run with sudo: sudo bash install-kde.sh' >&2
    exit 1
fi
if [[ ! -d /run/systemd/system ]]; then
    echo 'Run this on a booted Ubuntu system with systemd.' >&2
    exit 1
fi

trap 'printf "Setup stopped on line %s. See the error above.\n" "$LINENO" >&2' ERR
export DEBIAN_FRONTEND=noninteractive

apt-get -o APT::Update::Error-Mode=any update
candidate=$(LC_ALL=C apt-cache policy "$desktop" | awk '/Candidate:/ {print $2}')
if [[ -z $candidate || $candidate == '(none)' ]]; then
    apt-get --yes --no-remove install software-properties-common
    add-apt-repository --yes --no-update universe
    apt-get -o APT::Update::Error-Mode=any update
fi

apt-get --simulate --no-remove install "${packages[@]}" >/dev/null

display_manager_file=/etc/X11/default-display-manager
display_manager=sddm
if [[ -s $display_manager_file ]]; then
    display_manager=$(basename "$(cat "$display_manager_file")")
fi
printf 'sddm shared/default-x-display-manager select %s\n' "$display_manager" |
    debconf-set-selections

apt-get --yes --no-remove install "${packages[@]}"
systemctl set-default graphical.target

printf '\nKDE Plasma is installed. Reboot when ready: sudo reboot\n'
printf 'Select Plasma at the login screen if a session chooser is shown.\n'
