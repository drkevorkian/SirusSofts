#!/usr/bin/env bash
# Install the full Kubuntu desktop on an existing Ubuntu workstation.
set -Eeuo pipefail

usage() {
    cat <<'EOF'
Usage: sudo bash install-kde.sh [--dry-run]

Installs KDE Plasma and the standard Kubuntu desktop apps from Ubuntu's
repositories. A fresh Ubuntu Server install uses SDDM. If a login manager
is already configured, keep it and choose Plasma at the login screen.

--dry-run  Preview package changes using the current APT cache.
After a successful install, reboot when ready. Run on your workstation.
EOF
}

case "${1:-}" in
    -h|--help) usage; exit 0 ;;
    --dry-run|'') ;;
    *) usage >&2; exit 2 ;;
esac
if (( $# > 1 )); then
    usage >&2
    exit 2
fi

if [[ -r /etc/os-release ]]; then
    . /etc/os-release
fi
if [[ ${ID:-} != ubuntu ]]; then
    echo 'This installer requires Ubuntu.' >&2
    exit 1
fi

if [[ ${1:-} == --dry-run ]]; then
    apt-get --simulate --no-remove install kubuntu-desktop
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
candidate=$(LC_ALL=C apt-cache policy kubuntu-desktop | awk '/Candidate:/ {print $2}')
if [[ -z $candidate || $candidate == '(none)' ]]; then
    apt-get --yes --no-remove install software-properties-common
    add-apt-repository --yes --no-update universe
    apt-get -o APT::Update::Error-Mode=any update
fi

apt-get --simulate --no-remove install kubuntu-desktop >/dev/null

display_manager_file=/etc/X11/default-display-manager
display_manager=sddm
if [[ -s $display_manager_file ]]; then
    display_manager=$(basename "$(cat "$display_manager_file")")
fi
printf 'sddm shared/default-x-display-manager select %s\n' "$display_manager" |
    debconf-set-selections

apt-get --yes --no-remove install kubuntu-desktop
systemctl set-default graphical.target

printf '\nKDE Plasma is installed. Reboot when ready: sudo reboot\n'
printf 'Select Plasma at the login screen if a session chooser is shown.\n'
