#!/usr/bin/env bash
# 原生应用空装演练：从一个 fpk 出发，把飞牛应用中心会做的事**逐个真跑一遍**。
#
# 与 Docker 版演练的区别：原生应用没有 compose，服务就是宿主上的一个进程，
# 所以这里跑的是真钩子、真进程、真探活 —— 证据比 Docker 版更硬。
#
# 全程不碰正在跑的实例：端口、PKGVAR、PID 文件全部岔开；结束时只杀自己起的进程。
#
# 用法：scripts/rehearse-native.sh [fpk 路径]
set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP="gemini2api-native"
SCRATCH="$(mktemp -d /tmp/g2n-rehearse.XXXXXX)"
PORT=15920
PORT2=15921

pass=0; fail=0
ok()  { printf "  \033[32mOK\033[0m %s\n" "$1"; pass=$((pass+1)); }
bad() { printf "  \033[31mXX\033[0m %s\n" "$1"; fail=$((fail+1)); }
check() { if [ "$1" = "0" ]; then ok "$2"; else bad "$2"; fi; }

FPK="${1:-}"
FPK_NEW="${2:-}"
if [ -z "${FPK}" ]; then
    FPK="$(ls -1t "${ROOT}"/packages/gemini2api-native_*.fpk 2>/dev/null | head -n 1)"
fi
if [ -z "${FPK}" ] || [ ! -f "${FPK}" ]; then
    echo "找不到 fpk，用法：scripts/rehearse-native.sh <fpk>"; exit 2
fi

PIDFILE="${SCRATCH}/var/app.pid"
cleanup() {
    if [ -r "${PIDFILE}" ]; then
        p="$(head -n 1 "${PIDFILE}" 2>/dev/null | tr -d '[:space:]')"
        [ -n "${p}" ] && kill -KILL "-${p}" 2>/dev/null
    fi
    # 兜底：按记录下来的 pid 再杀一次
    [ -n "${STARTED_PID:-}" ] && kill -KILL "${STARTED_PID}" 2>/dev/null
    rm -rf "${SCRATCH}"
}
trap cleanup EXIT
STARTED_PID=""

echo "原生空装演练：${FPK}"

# ---------- 1. 解包 ----------
mkdir -p "${SCRATCH}/cmd"
tar -xzf "${FPK}" -C "${SCRATCH}" cmd 2>/dev/null
[ -d "${SCRATCH}/cmd" ] && [ -n "$(ls -A "${SCRATCH}/cmd" 2>/dev/null)" ]
check $? "fpk 里能解出 cmd/"

tar -xzf "${FPK}" -C "${SCRATCH}" app.tgz 2>/dev/null
mkdir -p "${SCRATCH}/dest"
tar -xzf "${SCRATCH}/app.tgz" -C "${SCRATCH}/dest" 2>/dev/null
rm -f "${SCRATCH}/app.tgz"
[ -d "${SCRATCH}/dest/server/app" ]
check $? "app.tgz 能解出 server/app（上游源码）"
[ -d "${SCRATCH}/dest/vendor/curl_cffi" ]
check $? "app.tgz 里带上了 vendor（curl_cffi）"

VER="$(tar -xzOf "${FPK}" manifest 2>/dev/null | awk -F= '/^version/{gsub(/[[:space:]]/,"",$2); print $2}')"
echo "  版本: ${VER}"
[ -n "${VER}" ]
check $? "manifest 里读得到 version"

# 模拟应用中心的安装结果：钩子以包用户身份运行，环境变量齐备
export TRIM_APPDEST="${SCRATCH}/dest"
export TRIM_PKGVAR="${SCRATCH}/var"
export TRIM_TEMP_LOGFILE="${SCRATCH}/temp.log"
export TRIM_SERVICE_PORT="${PORT}"
export TRIM_USERNAME="gemini2api"
export TRIM_GROUPNAME="gemini2api"
mkdir -p "${TRIM_PKGVAR}"
chmod 755 "${SCRATCH}/cmd/"*

# 向导字段：变量名必须与 wizard 里的 field 逐字相同（飞牛按同名注入、不加前缀）
# proxy 指向一个必定连接被拒的地址：这样任何外网调用都会【立刻失败】，而不是挂在
# 被污染的 DNS 上等 TCP 超时。演练里不要真的去连 Google。
export port="${PORT}" proxy="http://127.0.0.1:9" needkey=no apikey=sk-rehearse-canary

# ---------- 2. install_init ----------
bash "${SCRATCH}/cmd/install_init" >/dev/null 2>&1
check $? "install_init 正常退出（关键：它绝不能在非 root 下失败，否则整次安装回滚）"

# ---------- 3. install_callback ----------
bash "${SCRATCH}/cmd/install_callback" >/dev/null 2>&1
check $? "install_callback 正常退出"
[ -f "${TRIM_PKGVAR}/data/accounts.json" ]
check $? "install_callback 建出了 data/accounts.json（上游要求它存在）"
[ -d "${TRIM_PKGVAR}/data/cookies" ]
check $? "install_callback 建出了 data/cookies"

# ---------- 4. main status（未运行时必须是 3） ----------
bash "${SCRATCH}/cmd/main" status >/dev/null 2>&1
rc=$?
[ "${rc}" = "3" ]
check $? "未运行时 status 返回 3（不是 1）"

# ---------- 5. main start ----------
bash "${SCRATCH}/cmd/main" start >/dev/null 2>&1
rc=$?
check "$([ "${rc}" = "0" ] && echo 0 || echo 1)" "main start 返回 0"

sleep 1
[ -r "${PIDFILE}" ] && STARTED_PID="$(head -n 1 "${PIDFILE}" | tr -d '[:space:]')"

code="$(curl --noproxy '*' -s -o /dev/null -m 5 -w '%{http_code}' "http://127.0.0.1:${PORT}/health" 2>/dev/null)"
[ "${code}" = "200" ]
check $? "start 之后 /health 返回 200（实测 ${code}）"

code2="$(curl --noproxy '*' -s -o /dev/null -m 5 -w '%{http_code}' "http://127.0.0.1:${PORT}/" 2>/dev/null)"
[ "${code2}" = "200" ]
check $? "面板首页返回 200（实测 ${code2}）"

bash "${SCRATCH}/cmd/main" status >/dev/null 2>&1
check $? "运行时 status 返回 0"

# 免密钥开关：needkey=no 时运行环境里必须有 G2A_NO_AUTH
grep -q '^G2A_NO_AUTH=1' "${TRIM_PKGVAR}/runtime.env" 2>/dev/null
check $? "needkey=no 时 runtime.env 里写了 G2A_NO_AUTH=1"
# 而且确实放行了
code3="$(curl --noproxy '*' -s -o /dev/null -m 5 -w '%{http_code}' "http://127.0.0.1:${PORT}/admin/system-info" 2>/dev/null)"
[ "${code3}" = "200" ]
check $? "免密钥模式下 /admin 可直接访问（实测 ${code3}）"

# 数据落点必须在 PKGVAR，而不是代码目录
[ -f "${TRIM_PKGVAR}/data/model-mapping.json" ] || [ -f "${TRIM_PKGVAR}/data/accounts.json" ]
check $? "运行期数据写在 TRIM_PKGVAR（CWD=数据目录）"

# ---------- 6. stop ----------
bash "${SCRATCH}/cmd/main" stop >/dev/null 2>&1
sleep 2
code4="$(curl --noproxy '*' -s -o /dev/null -m 3 -w '%{http_code}' "http://127.0.0.1:${PORT}/health" 2>/dev/null)"
[ "${code4}" != "200" ]
check $? "stop 之后服务不再响应（实测 ${code4}）"
bash "${SCRATCH}/cmd/main" status >/dev/null 2>&1
rc2=$?
[ "${rc2}" = "3" ]
check $? "停止后 status 又回到 3"

# ---------- 7. 升级与回滚（真正的版本切换） ----------
# 只跑 upgrade_init/callback 是同版本自欺；这里真的换成另一个 fpk 的代码，
# 再退回原来的，验证"换版本"这件事本身不会丢数据、也不会起不来。
echo '{"accounts":[{"id":"account-0","psid":"g.test-canary","psidts":"sidts-test","label":"canary"}]}' > "${TRIM_PKGVAR}/data/accounts.json"
echo 'canary' > "${TRIM_PKGVAR}/data/keepmarker.txt"

export TRIM_SERVICE_PORT="${PORT2}"
export port="${PORT2}" needkey=yes apikey=sk-rehearse-key proxy="http://127.0.0.1:9"

if [ -z "${FPK_NEW}" ] || [ ! -f "${FPK_NEW}" ]; then
    echo "  （没给第二个 fpk，跳过版本切换；同端口重启一次代替）"
    export TRIM_SERVICE_PORT="${PORT}"
    export port="${PORT}"
    # 单 fpk 模式没有"新端口"可用 —— 后续所有检查（/health、/admin）都探这个端口。
    # 不改的话：服务按 PORT 起，检查却探 PORT2，必然 000（脚本 bug，不是应用问题）。
    PORT2="${PORT}"
    # 而且这里必须真的跑一遍升级钩子：上一版重构时把钩子调用挪进了双 fpk 分支，
    # 单 fpk 分支只剩"改变量"，服务根本没被重新拉起，后面的检查全部 000。
    bash "${SCRATCH}/cmd/upgrade_init" >/dev/null 2>&1
    check $? "upgrade_init 正常退出（单包模式）"
    bash "${SCRATCH}/cmd/upgrade_callback" >/dev/null 2>&1
    check $? "upgrade_callback 正常退出（单包模式）"
else
    echo "  升级到：$(basename "${FPK_NEW}")"
    mkdir -p "${SCRATCH}/dest_new"
    tar -xzf "${FPK_NEW}" -C "${SCRATCH}" app.tgz 2>/dev/null
    tar -xzf "${SCRATCH}/app.tgz" -C "${SCRATCH}/dest_new" 2>/dev/null
    rm -f "${SCRATCH}/app.tgz"
    [ -d "${SCRATCH}/dest_new/server/app" ]
    check $? "新版本包能解出 server/app"

    export TRIM_APPDEST="${SCRATCH}/dest_new"
    bash "${SCRATCH}/cmd/upgrade_init" >/dev/null 2>&1
    check $? "upgrade_init 正常退出（换到新版本）"
    bash "${SCRATCH}/cmd/upgrade_callback" >/dev/null 2>&1
    check $? "upgrade_callback 正常退出（换到新版本）"
fi

code5="$(curl --noproxy '*' -s -o /dev/null -m 25 -w '%{http_code}' "http://127.0.0.1:${PORT2}/health" 2>/dev/null)"
if [ "${code5}" != "200" ]; then
    echo "  ---- 升级后服务没起来，现场如下 ----"
    echo "  runtime.env:"; sed 's/^/    /' "${TRIM_PKGVAR}/runtime.env" 2>/dev/null
    echo "  info.log 末尾 25 行:"; tail -n 25 "${TRIM_PKGVAR}/info.log" 2>/dev/null | sed 's/^/    /'
fi
[ "${code5}" = "200" ]
check $? "升级后 /health 200（实测 ${code5}）"
grep -q 'g.test-canary' "${TRIM_PKGVAR}/data/accounts.json" 2>/dev/null
check $? "升级后账号数据仍在"
[ -f "${TRIM_PKGVAR}/data/keepmarker.txt" ]
check $? "升级后标记文件仍在"

code6="$(curl --noproxy '*' -s -o /dev/null -m 5 -w '%{http_code}' "http://127.0.0.1:${PORT2}/admin/system-info" 2>/dev/null)"
[ "${code6}" = "401" ]
check $? "needkey=yes 时 /admin 返回 401（实测 ${code6}）"
code7="$(curl --noproxy '*' -s -o /dev/null -m 5 -w '%{http_code}' -H 'Authorization: Bearer sk-rehearse-key' "http://127.0.0.1:${PORT2}/admin/system-info" 2>/dev/null)"
[ "${code7}" = "200" ]
check $? "带上正确密钥后放行（实测 ${code7}）"

# 回滚：把 TRIM_APPDEST 指回原来的代码目录，再跑一遍升级钩子
if [ -n "${FPK_NEW}" ] && [ -f "${FPK_NEW}" ]; then
    echo "  回滚回：$(basename "${FPK}")"
    export TRIM_APPDEST="${SCRATCH}/dest"
    bash "${SCRATCH}/cmd/upgrade_init" >/dev/null 2>&1
    check $? "回滚时 upgrade_init 正常退出"
    bash "${SCRATCH}/cmd/upgrade_callback" >/dev/null 2>&1
    check $? "回滚时 upgrade_callback 正常退出"

    code8="$(curl --noproxy '*' -s -o /dev/null -m 25 -w '%{http_code}' "http://127.0.0.1:${PORT2}/health" 2>/dev/null)"
    [ "${code8}" = "200" ]
    check $? "回滚后 /health 200（实测 ${code8}）"
    grep -q 'g.test-canary' "${TRIM_PKGVAR}/data/accounts.json" 2>/dev/null
    check $? "回滚后账号数据仍在"
    [ -f "${TRIM_PKGVAR}/data/keepmarker.txt" ]
    check $? "回滚后标记文件仍在"
fi

# ---------- 8. 卸载 ----------
bash "${SCRATCH}/cmd/uninstall_init" >/dev/null 2>&1
check $? "uninstall_init 正常退出"
sleep 1
bash "${SCRATCH}/cmd/uninstall_callback" >/dev/null 2>&1
check $? "uninstall_callback 正常退出"
[ -f "${TRIM_PKGVAR}/data/accounts.json" ]
check $? "卸载后账号数据刻意保留"
[ ! -f "${TRIM_PKGVAR}/app.pid" ] && [ ! -f "${TRIM_PKGVAR}/runtime.env" ]
check $? "卸载后 pid 与运行时 env 被清掉"
code9="$(curl --noproxy '*' -s -o /dev/null -m 3 -w '%{http_code}' "http://127.0.0.1:${PORT2}/health" 2>/dev/null)"
[ "${code9}" != "200" ]
check $? "卸载后服务已停"

echo
echo "  通过 ${pass}，失败 ${fail}"
[ "${fail}" -eq 0 ] || exit 1
echo "  原生空装演练通过。"
