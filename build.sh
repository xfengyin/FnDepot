#!/usr/bin/env bash
# 构建 gemini-web2api 的飞牛 fnOS 安装包（FPK），并刷新 FnDepot 应用源索引 fnpack.json
#
# 依赖：git、python3(含 pip)、ImageMagick(convert)、fnpack
# 用法：./build.sh
#   离线/网络受限时：UPSTREAM_DIR=/path/to/gemini-web2api ./build.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_NAME="gemini-web2api"
UPSTREAM_REPO="https://github.com/Sophomoresty/gemini-web2api.git"
UPSTREAM_REF="${UPSTREAM_REF:-2bb988bfcbb82a7fab5d2c99aa5560ff40d64f7e}"

APP_DIR="${ROOT}/src/${APP_NAME}"
PKG_DIR="${APP_DIR}/app"
WORK="${ROOT}/.build"

APP_VER="$(awk -F= '/^version/{gsub(/[[:space:]]/,"",$2); print $2}' "${APP_DIR}/manifest")"
FPK_NAME="${APP_NAME}_${APP_VER}_all.fpk"

log() { printf '==> %s\n' "$*"; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

for tool in git python3 convert fnpack; do
    command -v "${tool}" > /dev/null 2>&1 || die "缺少命令 ${tool}"
done

[ -n "${APP_VER}" ] || die "无法从 ${APP_DIR}/manifest 读取 version"

mkdir -p "${WORK}" "${ROOT}/packages"

# 上游源码：优先使用 UPSTREAM_DIR，其次复用 .build/upstream 缓存，最后才联网拉取
if [ -n "${UPSTREAM_DIR:-}" ]; then
    SRC_DIR="${UPSTREAM_DIR}"
    log "使用本地上游源码 ${SRC_DIR}"
elif [ -d "${WORK}/upstream/.git" ]; then
    SRC_DIR="${WORK}/upstream"
    log "复用已缓存的上游源码 ${SRC_DIR}"
    git -C "${SRC_DIR}" checkout --quiet "${UPSTREAM_REF}" 2>/dev/null || true
else
    log "拉取上游源码 ${UPSTREAM_REF:0:8} (${UPSTREAM_REPO})"
    for attempt in 1 2 3; do
        rm -rf "${WORK}/upstream"
        if git clone --quiet "${UPSTREAM_REPO}" "${WORK}/upstream" 2>/dev/null; then
            break
        fi
        log "第 ${attempt} 次拉取失败，重试中..."
        sleep 3
    done
    [ -d "${WORK}/upstream/.git" ] || die "无法拉取上游源码；可先 git clone 到本地，再用 UPSTREAM_DIR 指定后重试"
    git -C "${WORK}/upstream" checkout --quiet "${UPSTREAM_REF}"
    SRC_DIR="${WORK}/upstream"
fi
[ -d "${SRC_DIR}/gemini_web2api" ] || die "${SRC_DIR} 中找不到 gemini_web2api 包"

log "同步 Python 源码到 app/server"
rm -rf "${PKG_DIR}/server"
mkdir -p "${PKG_DIR}/server"
cp -R "${SRC_DIR}/gemini_web2api" "${PKG_DIR}/server/"
cp "${SRC_DIR}/LICENSE" "${PKG_DIR}/server/LICENSE"
cp "${SRC_DIR}/config.example.json" "${PKG_DIR}/config.example.json"

log "打补丁：让 BardErrorInfo 被正确识别（否则 Google 返回 1060 时会静默回空内容且不重试）"
python3 - "${PKG_DIR}/server/gemini_web2api/gemini.py" <<'PY'
import pathlib, sys

path = pathlib.Path(sys.argv[1])
src = path.read_text(encoding="utf-8")
old = r"BardErrorInfo\s*\[(\d+)\]"
new = r'BardErrorInfo"?\s*,?\s*\[(\d+)\]'
count = src.count(old)
if count:
    path.write_text(src.replace(old, new), encoding="utf-8")
print(f"  BardErrorInfo 正则已修正 {count} 处")
PY

log "调整默认配置（匿名访问更需要重试）"
python3 - "${PKG_DIR}/config.example.json" <<'PY'
import json, sys

path = sys.argv[1]
cfg = json.load(open(path, encoding="utf-8"))
cfg["retry_attempts"] = 8
cfg["retry_delay_sec"] = 1
with open(path, "w", encoding="utf-8") as fh:
    json.dump(cfg, fh, ensure_ascii=False, indent=2)
    fh.write("\n")
print("  config.example.json: retry_attempts=8, retry_delay_sec=1")
PY

log "安装 httpx 到 app/vendor（开箱即有真正的流式输出）"
rm -rf "${PKG_DIR}/vendor"
python3 -m pip install --quiet --no-cache-dir --no-compile --target "${PKG_DIR}/vendor" "httpx>=0.28,<0.29"

log "生成图标"
mkdir -p "${PKG_DIR}/ui/images" "${ROOT}/assets"
convert "${SRC_DIR}/logo.png" -resize 1024x1024! -crop 880x400+80+220 +repage "${WORK}/mark.png"
convert -size 256x256 xc:none -fill "#060d27" -draw "roundrectangle 0,0,255,255,46,46" "${WORK}/plate.png"
convert "${WORK}/mark.png" -resize 200x "${WORK}/mark_resized.png"
convert "${WORK}/plate.png" "${WORK}/mark_resized.png" -gravity center -composite -strip "PNG32:${APP_DIR}/ICON_256.PNG"
convert "${APP_DIR}/ICON_256.PNG" -resize 64x64 -strip "PNG32:${APP_DIR}/ICON.PNG"
cp "${APP_DIR}/ICON.PNG" "${PKG_DIR}/ui/images/icon_64.png"
cp "${APP_DIR}/ICON_256.PNG" "${PKG_DIR}/ui/images/icon_256.png"
cp "${APP_DIR}/ICON_256.PNG" "${ROOT}/assets/${APP_NAME}-icon.png"

log "构建 fpk"
rm -f "${ROOT}/${APP_NAME}.fpk" "${ROOT}/packages/${FPK_NAME}"
( cd "${ROOT}" && fnpack build -d "src/${APP_NAME}" )
mv "${ROOT}/${APP_NAME}.fpk" "${ROOT}/packages/${FPK_NAME}"

log "刷新 fnpack.json 中的 download_url / size / sha256"
python3 - "${ROOT}" "${APP_NAME}" "${APP_VER}" "${FPK_NAME}" <<'PY'
import datetime, hashlib, json, os, sys

root, app, ver, fpk = sys.argv[1:5]
blob = open(os.path.join(root, "packages", fpk), "rb").read()
index_path = os.path.join(root, "fnpack.json")
with open(index_path, encoding="utf-8") as fh:
    index = json.load(fh)

releases = index["apps"][app].setdefault("releases", {})
if ver not in releases:
    releases[ver] = {
        "changelog": f"更新到 gemini-web2api {ver}。",
        "updated_at": datetime.datetime.now().astimezone().replace(microsecond=0).isoformat(),
        "os_min_version": "1.2.0",
        "packages": {},
    }
    print(f"  已新建 releases[{ver}]，记得补写 changelog")

pkg = releases[ver].setdefault("packages", {}).setdefault("all", {})
pkg.update({
    "download_url": f"./packages/{fpk}",
    "sha256": hashlib.sha256(blob).hexdigest(),
    "size": len(blob),
})
with open(index_path, "w", encoding="utf-8") as fh:
    json.dump(index, fh, ensure_ascii=False, indent=2)
    fh.write("\n")
print(f"  {fpk}: {len(blob)} bytes  sha256={pkg['sha256']}")
PY

log "完成 -> packages/${FPK_NAME}"
