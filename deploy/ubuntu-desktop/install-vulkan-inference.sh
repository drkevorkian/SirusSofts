#!/usr/bin/env bash
set -Eeuo pipefail
. /etc/os-release
[[ ${ID:-} == ubuntu ]] || { echo "Ubuntu required"; exit 1; }
base="$HOME/.local/share/sirus-vulkan"
src="$base/llama.cpp"
build="$base/build"
case "${1:-}" in
--install)
  [[ $EUID != 0 ]] || { echo "Run as desktop user (sudo is invoked for packages only)"; exit 1; }
  sudo apt-get update
  sudo apt-get install -y --no-remove git cmake ninja-build build-essential libvulkan-dev glslang-tools vulkan-tools libcurl4-openssl-dev
  mkdir -p "$base"
  if [[ ! -d "$src/.git" ]]; then
    git clone https://github.com/ggml-org/llama.cpp.git "$src"
  fi
  [[ -z $(git -C "$src" status --porcelain) ]] || { echo "Source has local changes; stopping"; exit 1; }
  git -C "$src" rev-parse HEAD | tee "$base/source-commit.txt"
  cmake -S "$src" -B "$build" -G Ninja -DGGML_VULKAN=ON -DGGML_CUDA=OFF -DGGML_HIP=OFF -DCMAKE_BUILD_TYPE=Release
  cmake --build "$build" --parallel 4 --target llama-cli llama-bench
  "$build/bin/llama-cli" --list-devices
  ;;
--bench)
  [[ $# == 2 && -r "$2" ]] || { echo "Usage: $0 --bench /path/model.gguf"; exit 2; }
  [[ $EUID != 0 ]] || exit 1
  bench="$build/bin/llama-bench"
  [[ -x "$bench" ]] || { echo "Run --install first"; exit 1; }
  "$build/bin/llama-cli" --list-devices
  mkdir -p "$base/results"
  log="$base/results/$(date +%Y%m%d-%H%M%S).log"
  for devices in 0 1 0,1; do
    echo "=== GGML_VK_VISIBLE_DEVICES=$devices ===" | tee -a "$log"
    GGML_VK_VISIBLE_DEVICES="$devices" "$build/bin/llama-cli" --list-devices 2>&1 | tee -a "$log"
    # A short test, not a thermal stress run. Stop on any failure.
    GGML_VK_VISIBLE_DEVICES="$devices" timeout 180 "$bench" -m "$2" -ngl 99 -p 128 -n 32 -r 1 -sm layer 2>&1 | tee -a "$log"
    sleep 20
  done
  echo "Results: $log"
  ;;
*) echo "Usage: bash $0 --install | --bench /path/model.gguf"; exit 2;;
esac
