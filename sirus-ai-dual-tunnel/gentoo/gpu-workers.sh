#!/usr/bin/env bash
# Optional per-GPU build. Keeps the working CPU build unchanged.
set -Eeuo pipefail
fail(){ echo "FAIL: $*" >&2; exit 1; }
mode="${1:---check}"
case "$mode" in --check|--plan|--build-nvidia|--build-amd|--enable-nvidia|--enable-amd|--repair-amd|--status) ;; *) fail 'Usage: gpu-workers.sh --check|--plan|--build-nvidia|--build-amd|--enable-nvidia|--enable-amd|--repair-amd|--status';; esac
[[ $# -eq 1 ]] || fail 'Accepts exactly one operation'
source_dir=/opt/sirus-ai/llama.cpp
model=/var/lib/sirus-ai/models/qwen2.5-coder-7b-instruct-q4_k_m.gguf
nvidia_dir="$source_dir/build-sirus-nvidia"
amd_dir="$source_dir/build-sirus-amd"
[[ "$mode" == --check || "$mode" == --plan ]] || [[ "$(id -u)" == 0 ]] || fail 'Root required to operate GPU workers'
if [[ "$mode" == --plan ]]; then
  echo 'NVIDIA: separate CUDA llama-server 127.0.0.1:18486, starting at 16 GPU layers for GTX 1660 6GB.'
  echo 'AMD: separate Vulkan llama-server 127.0.0.1:18487, AMD Radeon device only.'
  echo 'CPU: existing 127.0.0.1:18482 build and service unchanged.'
  echo 'GPU drivers and system packages are not changed automatically.'
  exit 0
fi
if [[ "$mode" == --check ]]; then
  [[ -f /etc/gentoo-release && -d "$source_dir/.git" && -s "$model" ]] || fail 'Gentoo source/model preflight failed'
  echo 'PASS source/model found; no changes'
  exit 0
fi
[[ -f /etc/gentoo-release && -d "$source_dir/.git" && -s "$model" ]] || fail 'Gentoo CPU source/model missing'
if [[ "$mode" == --status ]]; then
  for entry in 'cpu:18482' 'nvidia:18486' 'amd:18487'; do
    label="${entry%%:*}"; port="${entry##*:}"
    if curl -fsS --max-time 2 "http://127.0.0.1:$port/health" >/dev/null; then echo "$label ONLINE"; else echo "$label OFFLINE"; fi
  done
  exit 0
fi
command -v cmake >/dev/null || fail 'cmake required'
if [[ "$mode" == --build-nvidia ]]; then
  command -v nvcc >/dev/null || fail 'NVIDIA nvcc missing'
  command -v nvidia-smi >/dev/null || fail 'NVIDIA driver utilities missing'
  nvidia-smi >/dev/null || fail 'NVIDIA driver unavailable'
  cmake -S "$source_dir" -B "$nvidia_dir" -DCMAKE_BUILD_TYPE=Release -DGGML_CUDA=ON -DGGML_VULKAN=OFF
  cmake --build "$nvidia_dir" --target llama-server -j 2
  "$nvidia_dir/bin/llama-server" --list-devices
  echo 'PASS CUDA binary built; verify CUDA0'
  exit 0
fi
if [[ "$mode" == --build-amd ]]; then
  command -v vulkaninfo >/dev/null || fail 'vulkaninfo missing'
  vulkaninfo --summary >/dev/null || fail 'Vulkan ICD unavailable'
  cmake -S "$source_dir" -B "$amd_dir" -DCMAKE_BUILD_TYPE=Release -DGGML_VULKAN=ON -DGGML_CUDA=OFF
  cmake --build "$amd_dir" --target llama-server -j 2
  "$amd_dir/bin/llama-server" --list-devices
  echo 'PASS Vulkan binary built; verify AMD device'
  exit 0
fi
if [[ "$mode" == --enable-nvidia ]]; then
  binary="$nvidia_dir/bin/llama-server"
  [[ -x "$binary" ]] || fail 'Run --build-nvidia first'
  devices="$("$binary" --list-devices 2>&1)"
  grep -Eq '^[[:space:]]*CUDA0:' <<< "$devices" || fail 'CUDA0 not found'
  service=/etc/init.d/sirus-ai-nvidia
  [[ ! -e "$service" ]] || fail 'NVIDIA service already exists'
  cat > "$service" <<'RC'
#!/sbin/openrc-run
name="SirusSofts AI NVIDIA CUDA worker"
supervisor=supervise-daemon
command="/opt/sirus-ai/llama.cpp/build-sirus-nvidia/bin/llama-server"
command_user="sirusai:sirusai"
command_args="--model /var/lib/sirus-ai/models/qwen2.5-coder-7b-instruct-q4_k_m.gguf --alias sirussofts-coder --host 127.0.0.1 --port 18486 --device CUDA0 --n-gpu-layers 16 --ctx-size 2048 --threads 4 --parallel 1"
respawn_delay=5
respawn_period=30
respawn_max=3
depend() { need net; }
RC
  chmod 0755 "$service"
  rc-service sirus-ai-nvidia start
  healthy=0
  for i in $(seq 1 30); do
    if curl -fsS --max-time 2 http://127.0.0.1:18486/health >/dev/null 2>&1; then healthy=1; break; fi
    sleep 2
  done
  [[ "$healthy" == 1 ]] || fail 'NVIDIA worker health check failed'
  rc-update add sirus-ai-nvidia default
  echo 'PASS NVIDIA worker online'
  exit 0
fi
if [[ "$mode" == --enable-amd || "$mode" == --repair-amd ]]; then
  binary="$amd_dir/bin/llama-server"
  [[ -x "$binary" ]] || fail 'Run --build-amd first'
  command -v runuser >/dev/null || fail 'runuser is required for service-user GPU preflight'
  command -v ss >/dev/null || fail 'ss is required to detect port conflicts'
  [[ -f /usr/share/vulkan/icd.d/radeon_icd.x86_64.json ]] || fail 'AMD RADV Vulkan ICD missing'
  # Probe as the actual daemon account with exactly the same headless, AMD-only environment.
  devices="$(runuser -u sirusai -- env -u DISPLAY -u WAYLAND_DISPLAY -u XAUTHORITY -u XDG_RUNTIME_DIR VK_DRIVER_FILES=/usr/share/vulkan/icd.d/radeon_icd.x86_64.json "$binary" --list-devices 2>&1)" || fail 'Cannot enumerate AMD Vulkan devices as sirusai'
  matches="$(printf '%s\n' "$devices" | grep -Ei '^[[:space:]]*Vulkan[0-9]+:.*(AMD|Radeon)' || true)"
  [[ -n "$matches" ]] || fail 'No AMD Radeon Vulkan device visible to sirusai'
  device="$(printf '%s\n' "$matches" | head -n1 | sed -E 's/^[[:space:]]*(Vulkan[0-9]+):.*/\1/')"
  [[ "$device" =~ ^Vulkan[0-9]+$ ]] || fail 'Invalid Vulkan device name'
  service=/etc/init.d/sirus-ai-amd
  if [[ "$mode" == --repair-amd ]]; then
    [[ -f "$service" && ! -L "$service" ]] || fail 'Existing AMD OpenRC service is missing or is a symlink'
    grep -Fxq 'name="SirusSofts AI AMD Vulkan worker"' "$service" || fail 'Refusing to replace an unrecognized AMD OpenRC service'
    grep -Fq '/opt/sirus-ai/llama.cpp/build-sirus-amd/bin/llama-server' "$service" || fail 'AMD service executable differs from expected'
    grep -Fq -- '--port 18487' "$service" || fail 'AMD service does not use expected port 18487'
    if rc-service sirus-ai-amd status >/dev/null 2>&1; then
      fail 'Stop the existing AMD OpenRC service before repair'
    fi
  else
    [[ ! -e "$service" && ! -L "$service" ]] || fail 'AMD service already exists; use --repair-amd'
  fi
  # Never start a second server while an interactive diagnostic server owns the port.
  [[ -z "$(ss -ltnH '( sport = :18487 )')" ]] || fail 'Port 18487 is occupied. Stop the foreground AMD llama-server with Ctrl+C before continuing.'
  if [[ "$mode" == --repair-amd ]]; then
    backup="$(mktemp /root/sirus-ai-amd.init.XXXXXXXX)"
    cp -p "$service" "$backup"
    echo "Backup of previous AMD service: $backup"
  fi
  tmp="$(mktemp /etc/init.d/.sirus-ai-amd.XXXXXXXX)"
  cat > "$tmp" <<RC
#!/sbin/openrc-run
name="SirusSofts AI AMD Vulkan worker"
supervisor=supervise-daemon
# Isolate RADV, and strip graphical session variables in this headless worker.
command="/usr/bin/env"
command_user="sirusai:sirusai"
command_args="-u DISPLAY -u WAYLAND_DISPLAY -u XAUTHORITY -u XDG_RUNTIME_DIR VK_DRIVER_FILES=/usr/share/vulkan/icd.d/radeon_icd.x86_64.json /opt/sirus-ai/llama.cpp/build-sirus-amd/bin/llama-server --model /var/lib/sirus-ai/models/qwen2.5-coder-7b-instruct-q4_k_m.gguf --alias sirussofts-coder --host 127.0.0.1 --port 18487 --device $device --n-gpu-layers 99 --ctx-size 2048 --threads 4 --parallel 1"
respawn_delay=5
respawn_period=30
respawn_max=3
depend() { need net; }
RC
  chmod 0755 "$tmp"
  mv -f "$tmp" "$service"
  rc-service sirus-ai-amd start || fail 'AMD service did not start'
  healthy=0
  for i in $(seq 1 30); do
    if curl -fsS --max-time 2 http://127.0.0.1:18487/health >/dev/null 2>&1; then healthy=1; break; fi
    sleep 2
  done
  if [[ "$healthy" != 1 ]]; then
    rc-service sirus-ai-amd status || true
    fail 'AMD worker health check failed; service not enabled at boot'
  fi
  rc-update add sirus-ai-amd default
  echo "PASS AMD worker online ($device) with dedicated RADV Vulkan ICD"
  exit 0
fi
