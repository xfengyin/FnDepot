#!/bin/bash
# 把 vendor 依赖准备好（约 66M / 35 个包，不进 git，构建时生成 —— build.sh 发现缺它就调这里）
set -euo pipefail

die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

# 本脚本住在 scripts/ 下，仓根是它的上一级。
# 2026-10-26 修：原来写 ROOT="$(cd "$(dirname "$0")" && pwd)"，从 build.sh 调进来时等于把
# scripts/ 当成仓根 —— requirements 路径变成 scripts/src/gemini2api-native/…（不存在），
# 于是 ./build.sh gemini2api-native 在 main 上必挂：
#   ERROR: Could not open requirements file: …/scripts/src/gemini2api-native/requirements-frozen.txt
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_DIR="$ROOT/src/gemini2api-native"
VENDOR="$APP_DIR/app/vendor"
REQ="$APP_DIR/requirements-frozen.txt"

[ -f "$REQ" ] || die "找不到依赖清单 $REQ（仓根解析错了？本脚本应位于 <repo>/scripts/ 下）"

if [ -d "$VENDOR" ] && [ -n "$(ls -A "$VENDOR" 2>/dev/null)" ]; then
    echo "  vendor 已存在（$(du -sh "$VENDOR" | cut -f1)），跳过"
    exit 0
fi

echo "  准备 vendor 依赖（按上游镜像的精确版本）…"
mkdir -p "$VENDOR"
# 用 python3.12 装，与上游运行时一致；--only-binary 保证不去编译源码。
# 这里不再有「pip 失败就改用容器内 pip」的回退：它的前提是有一个名为 gemini2api 的**运行中容器**，
# 那是 Docker 一代的形态（2026-10-26 已下线，本机 docker ps -a 里也没有这个容器）。回退留着只剩
# 副作用 —— 它会把 pip 的真实错误盖成 `lstat …: no such file or directory`：
#   宿主 pip 失败，改用容器内的 pip…
#   lstat …/requirements-frozen.txt: no such file or directory
# 现在让 pip 自己把话说完（--quiet 成功时仍然安静）。
/usr/local/bin/python3.12 -m pip install --quiet --no-cache-dir --only-binary=:all: \
    --target "$VENDOR" -r "$REQ"

# pip 成功但一个包都没装（清单被清空/被改成空文件）时，绝不能当成功往下走：
# build.sh 会拿空 vendor 打出一个缺全部依赖的包（实测 630 KB vs 正常 21 MB），而且退出码是 0。
[ -n "$(ls -A "$VENDOR" 2>/dev/null)" ] || die "vendor 装出来是空的（$VENDOR）—— 继续打包会产出缺依赖的包"
echo "  vendor 就绪：$(du -sh "$VENDOR" | cut -f1)"
