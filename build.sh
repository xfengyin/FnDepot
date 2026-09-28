#!/usr/bin/env bash
# 构建 gemini-web2api 的飞牛 fnOS 安装包（FPK），并刷新 FnDepot 应用源索引 fnpack.json
#
# 依赖：python3（含 pip）、fnpack（飞牛官方打包工具）
#   - 源码：直接使用本仓库 src/gemini-web2api/app/server（1.2.1 起仓库内置完整源码，不再拉取上游）
#   - vendor：默认 pip 安装 httpx 到 app/vendor；离线或网络受限时用 VENDOR_DIR=/path/to/vendor
# 用法：./build.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_NAME="gemini-web2api"
APP_DIR="${ROOT}/src/${APP_NAME}"
PKG_DIR="${APP_DIR}/app"

APP_VER="$(awk -F= '/^version/{gsub(/[[:space:]]/,"",$2); print $2}' "${APP_DIR}/manifest")"
FPK_NAME="${APP_NAME}_${APP_VER}_all.fpk"

log() { printf '==> %s\n' "$*"; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

command -v python3 > /dev/null 2>&1 || die "缺少命令 python3"
command -v fnpack  > /dev/null 2>&1 || die "缺少命令 fnpack（飞牛官方打包工具）"
[ -n "${APP_VER}" ] || die "无法从 ${APP_DIR}/manifest 读取 version"
[ -d "${PKG_DIR}/server/gemini_web2api" ] || die "缺少源码 ${PKG_DIR}/server，请检查仓库完整性"
[ -f "${APP_DIR}/ICON.PNG" ] && [ -f "${APP_DIR}/ICON_256.PNG" ] || die "缺少 ICON.PNG / ICON_256.PNG"

mkdir -p "${ROOT}/packages"

log "同步 app/vendor（httpx）"
rm -rf "${PKG_DIR}/vendor"
if [ -n "${VENDOR_DIR:-}" ]; then
    [ -d "${VENDOR_DIR}" ] || die "VENDOR_DIR 不存在: ${VENDOR_DIR}"
    cp -R "${VENDOR_DIR}" "${PKG_DIR}/vendor"
else
    python3 -m pip install --quiet --no-cache-dir --no-compile --target "${PKG_DIR}/vendor" "httpx>=0.28,<0.29"
fi
find "${PKG_DIR}/vendor" \( -name "__pycache__" -o -name "*.pyc" \) -exec rm -rf {} + 2>/dev/null || true

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
