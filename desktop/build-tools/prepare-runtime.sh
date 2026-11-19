#!/bin/bash
set -euo pipefail
tools=$(cd -- "$(dirname -- "$0")" && pwd)
repo=$(cd -- "$tools/../.." && pwd)
cache="$repo/desktop/.build"
mkdir -p "$cache"
cd "$repo"

download() {
    local destination=$1 digest=$2 url=$3
    if [[ ! -f "$destination" ]]; then
        curl --fail --location --retry 3 --output "$destination.part" "$url"
        mv "$destination.part" "$destination"
    fi
    echo "$digest  $destination" | shasum -a 256 -c -
}

download "$cache/cpython.tar.gz" 8c5bd56675d883166e200e50af7c894331564dc810dcb3a53ea465c0df11b461 \
    https://github.com/astral-sh/python-build-standalone/releases/download/20251209/cpython-3.13.11%2B20251209-aarch64-apple-darwin-install_only_stripped.tar.gz
[[ -x "$cache/python/bin/python3.13" ]] || tar -xzf "$cache/cpython.tar.gz" -C "$cache"
# A separate copy of every wheel is signed with the app. Never re-sign uv's cache.
uv export --locked --no-emit-project -o "$cache/requirements.txt" > /dev/null
uv pip sync --python "$cache/python/bin/python3.13" --only-binary=:all: --require-hashes --link-mode copy \
    --target "$cache/python/lib/python3.13/site-packages" "$cache/requirements.txt"

llama="$cache/llama-b12521.tar.gz"
if [[ ! -f "$llama" && -f model/qwen/llama-b12521.tar.gz ]]; then
    cp model/qwen/llama-b12521.tar.gz "$llama"
fi
download "$llama" 6ebeeea8575081ff968eb1c26f6fffcafe8e3ff2a0fb5d548c0aea7f5575e038 \
    https://github.com/ggml-org/llama.cpp/releases/download/b12521/llama-b12521-bin-macos-arm64.tar.gz
tar -xzf "$llama" -C "$cache"
if [[ ! -x "$cache/ffmpeg-7.1.1/ffmpeg" || "$tools/build-codecs.sh" -nt "$cache/ffmpeg-7.1.1/ffmpeg" ]]; then
    "$tools/build-codecs.sh" "$cache"
fi
