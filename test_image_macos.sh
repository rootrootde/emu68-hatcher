#!/bin/sh
set -eu

usage() {
    echo "usage: $0 [--reuse] [--native] /path/to/emu68hatcher.img"
}

reuse=false
rtg_size=128
while [ "$#" -gt 0 ]; do
    case "$1" in
        -h|--help)
            usage
            exit 0
            ;;
        --reuse)
            reuse=true
            shift
            ;;
        --native)
            rtg_size=0
            shift
            ;;
        *) break ;;
    esac
done

if [ "$#" -ne 1 ]; then
    usage
    exit 2
fi

if [ "$(uname -s)" != "Darwin" ]; then
    echo "this launcher only supports macos" >&2
    exit 1
fi

script_dir=$(CDPATH= cd "$(dirname "$0")" && pwd)
image_arg=$1
if [ ! -f "$image_arg" ]; then
    echo "image not found: $image_arg" >&2
    exit 1
fi
image_dir=$(CDPATH= cd "$(dirname "$image_arg")" && pwd)
image_path="$image_dir/$(basename "$image_arg")"

case "$(uname -m)" in
    arm64) tool_platform=darwin-arm64 ;;
    x86_64) tool_platform=darwin-x64 ;;
    *)
        echo "unsupported mac architecture: $(uname -m)" >&2
        exit 1
        ;;
esac

runtime_home=${HATCHER_HOME:-"$script_dir/hatcher_data"}
cache_dir="$runtime_home/cache/emulator"
dotnet_dir="$runtime_home/cache/dotnet-bundle"
mkdir -p "$cache_dir" "$dotnet_dir"
export DOTNET_BUNDLE_EXTRACT_BASE_DIR="$dotnet_dir"

hst="$runtime_home/tools/$tool_platform/hst-imager"
if [ ! -x "$hst" ]; then
    if command -v hst-imager >/dev/null 2>&1; then
        hst=$(command -v hst-imager)
    else
        echo "hst-imager not found; download the host tools from the Start tab first" >&2
        exit 1
    fi
fi

amiberry=${EMU68HATCHER_AMIBERRY:-}
if [ -z "$amiberry" ]; then
    for candidate in \
        /Applications/Amiberry.app/Contents/MacOS/Amiberry \
        /Applications/AmiKit.app/Contents/MacOS/Amiberry
    do
        if [ -x "$candidate" ]; then
            amiberry=$candidate
            break
        fi
    done
fi
if [ -z "$amiberry" ] && command -v amiberry >/dev/null 2>&1; then
    amiberry=$(command -v amiberry)
fi
if [ -z "$amiberry" ] || [ ! -x "$amiberry" ]; then
    echo "Amiberry not found in /Applications or PATH" >&2
    exit 1
fi

image_name=$(basename "$image_path")
image_stem=${image_name%.*}
image_tag=$(printf '%s' "$image_path" | cksum | awk '{print $1}')
artifact="$image_stem-$image_tag"
hdf_path="$cache_dir/$artifact.hdf"
rom_path="$cache_dir/$artifact.rom"
amiberry_home="$cache_dir/$artifact-amiberry"
amiberry_settings="$amiberry_home/amiberry.conf"
hdf_tmp="$cache_dir/.$artifact.$$.tmp.hdf"
work_dir=$(mktemp -d "$cache_dir/.prepare.XXXXXX")
mkdir -p "$amiberry_home"

cleanup() {
    case "${hdf_tmp:-}" in
        "$cache_dir"/.*.tmp.hdf) rm -f "$hdf_tmp" ;;
    esac
    case "${work_dir:-}" in
        "$cache_dir"/.prepare.*) rm -rf "$work_dir" ;;
    esac
}
trap cleanup EXIT HUP INT TERM

if [ "$reuse" = false ] || [ ! -s "$rom_path" ]; then
    echo "Reading boot configuration from $image_name"
    "$hst" fs copy "$image_path/mbr/1/config.txt" "$work_dir" --force TRUE
    config_path="$work_dir/config.txt"
    if [ ! -f "$config_path" ]; then
        echo "config.txt is missing from the image boot partition" >&2
        exit 1
    fi
    rom_name=$(awk '
        tolower($1) == "initramfs" && tolower($2) ~ /\.rom$/ { print $2; exit }
    ' "$config_path")
    if [ -z "$rom_name" ] || [ "$rom_name" != "$(basename "$rom_name")" ]; then
        echo "could not determine the Kickstart ROM from config.txt" >&2
        exit 1
    fi

    echo "Extracting $rom_name"
    "$hst" fs copy "$image_path/mbr/1/$rom_name" "$work_dir" --force TRUE
    if [ ! -f "$work_dir/$rom_name" ]; then
        echo "Kickstart ROM is missing from the image boot partition: $rom_name" >&2
        exit 1
    fi
    mv -f "$work_dir/$rom_name" "$rom_path"
else
    echo "Reusing cached Kickstart ROM"
fi

if [ "$reuse" = true ] && [ -s "$hdf_path" ]; then
    echo "Reusing converted emulator disk: $hdf_path"
else
    echo "Preparing sparse emulator disk"
    "$hst" transfer "$image_path/mbr/2" "$hdf_tmp"

    echo "Using the image's Startup-Sequence unchanged"
    mv -f "$hdf_tmp" "$hdf_path"
fi

echo "Starting Amiberry with $hdf_path"
echo "The original image is not modified. Emulator writes stay in the cached HDF."
echo "Boot log: $amiberry_home/Amiberry.log"
export AMIBERRY_HOME_DIR="$amiberry_home"
exec "$amiberry" \
    -o "amiberry_config=$amiberry_settings" \
    -o write_logfile=yes \
    --log \
    --model A1200 \
    -r "$rom_path" \
    -W "DH0:$hdf_path" \
    -s cpu_type=68040 \
    -s cpu_model=68040 \
    -s fpu_model=68040 \
    -s cpu_speed=max \
    -s cpu_compatible=false \
    -s cpu_24bit_addressing=false \
    -s bsdsocket_emu=true \
    -s z3mem_size=256 \
    -s gfxcard_type=ZorroIII \
    -s "gfxcard_size=$rtg_size" \
    -G
