#!/usr/bin/env bash
# 核对"我们依赖上游的哪些东西"。上游一升级先跑这个，别等用户来报障。
#
# 本仓库不改上游代码，而是在它的源码上叠了几层假设：
#   · app/core/auth.py 里追加同名函数来关鉴权（所以上游必须仍有 verify_api_key /
#     verify_admin_key，且导入了 Request）；
#   · app/main.py 从 app.core.auth 导入这两个函数（导入方式变了，追加的就用不上）；
#   · static/app/auth.js 里改 SESSION_TIMEOUT 常量、认 gemini2api_token 这个存储键；
#   · /admin/accounts 的请求体仍接受 psid / psidts（登录助手建号靠它）。
# 每一条都是"它现在成立不等于下个版本还成立"。
#
# 核对对象：**仓库里在树的原生上游源码**（默认 src/gemini2api-native/app/server）。
# 2026-10-26 之前这个脚本核对的是上游 docker 镜像（当时本仓库还有 docker 形态）；那一代
# 下线后，源码是 vendored 在树里的、补丁在打包时直接改进源码，所以核对点改成对源码本身。
#
# 用法：
#   scripts/check-upstream.sh                 # 核对本仓库在树的原生源码
#   scripts/check-upstream.sh <源码根目录>     # 核对别处取来的上游源码（升级前试跑）
#
# 只做静态核对（grep 文件），不需要外网、不需要账号、不需要 docker，因此可以先于任何
# 真实部署跑；升级上游时把它指向新版源码先跑一遍，红了就先补补丁再打包。
set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="${1:-$ROOT/src/gemini2api-native/app/server}"

if [ ! -d "$SRC" ]; then
    echo "找不到源码目录：$SRC"
    echo "用法：scripts/check-upstream.sh [源码根目录]"
    exit 2
fi

pass=0; fail=0
ok()   { printf "  \033[32m✓\033[0m %s\n" "$1"; pass=$((pass+1)); }
bad()  { printf "  \033[31m✗\033[0m %s\n" "$1"; fail=$((fail+1)); }

echo "核对源码：$SRC"
echo ""

have() { [ -f "$1" ]; }
grepf() { grep -q "$1" "$2" 2>/dev/null; }

echo "一、鉴权补丁的前提"
if have "$SRC/app/core/auth.py"; then
    ok "app/core/auth.py 存在"
    grepf "async def verify_api_key" "$SRC/app/core/auth.py" \
        && ok "auth.py 定义 verify_api_key（追加同名函数才会生效）" \
        || bad "auth.py 里找不到 verify_api_key —— 关闭鉴权的补丁会变空操作"
    grepf "async def verify_admin_key" "$SRC/app/core/auth.py" \
        && ok "auth.py 定义 verify_admin_key" \
        || bad "auth.py 里找不到 verify_admin_key"
    grepf "from fastapi import .*Request" "$SRC/app/core/auth.py" \
        && ok "auth.py 导入了 Request（覆盖函数必须带类型标注）" \
        || bad "auth.py 没有导入 Request —— 追加的覆盖函数会让所有请求变 422"
else
    bad "找不到 app/core/auth.py —— 关鉴权的补丁整条失效"
fi
if have "$SRC/app/main.py"; then
    grepf "from app.core.auth import" "$SRC/app/main.py" \
        && ok "main.py 从 app.core.auth 导入校验函数" \
        || bad "main.py 的导入方式变了 —— 追加函数可能不会被用到"
else
    bad "找不到 app/main.py"
fi

echo ""
echo "二、前端补丁的前提"
if have "$SRC/static/app/auth.js"; then
    ok "static/app/auth.js 存在"
    grepf "SESSION_TIMEOUT" "$SRC/static/app/auth.js" \
        && ok "auth.js 里有 SESSION_TIMEOUT（闲置登出的补丁靠它）" \
        || bad "auth.js 里没有 SESSION_TIMEOUT —— 面板会继续闲置踢人"
    grepf "gemini2api_token" "$SRC/static/app/auth.js" \
        && ok "auth.js 用 gemini2api_token 作为存储键" \
        || bad "token 存储键变了 —— 自动登录注入会失效"
else
    bad "找不到 static/app/auth.js（路径或文件名变了？）"
fi

echo ""
echo "三、建账号接口的契约"
if have "$SRC/app/routers/admin.py"; then
    grepf '"/accounts"' "$SRC/app/routers/admin.py" \
        && ok "admin.py 里有 /accounts 路由" \
        || bad "找不到 /accounts 路由 —— 登录助手无法建号"
    grepf "psid" "$SRC/app/routers/admin.py" \
        && ok "请求体仍接受 psid" \
        || bad "请求体字段变了 —— capture 会收到 400"
    grepf "psidts" "$SRC/app/routers/admin.py" \
        && ok "请求体仍接受 psidts" \
        || bad "请求体里没有 psidts"
else
    bad "找不到 app/routers/admin.py"
fi

echo ""
echo "----------------------------------------------"
printf "  通过 %d，失败 %d\n" "$pass" "$fail"
if [ "$fail" -gt 0 ]; then
    echo "  有依赖已失效：升级上游前必须先处理上面标 ✗ 的项。"
    exit 1
fi
echo "  上游结构未变，可以安全升级。两处源码补丁与面板补丁仍应有效。"
exit 0
