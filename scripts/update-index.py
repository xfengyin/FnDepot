#!/usr/bin/env python3
"""把刚打好的 fpk 写回 fnpack.json 的 FnDepot 索引。

用法：update-index.py <repo_root> <app> <version> <platform> <fpk_filename>
"""
import datetime
import hashlib
import json
import os
import sys


def main():
    root, app, ver, platform, fpk = sys.argv[1:6]
    path = os.path.join(root, "packages", fpk)
    blob = open(path, "rb").read()

    index_path = os.path.join(root, "fnpack.json")
    with open(index_path, encoding="utf-8") as fh:
        index = json.load(fh)

    if app not in index["apps"]:
        index["apps"][app] = {
            "display_name": app,
            "desc": "",
            "platform": platform,
            "source": "thirdparty",
            "releases": {},
        }
        print("  已新建 apps[%s]" % app)

    releases = index["apps"][app].setdefault("releases", {})
    if ver not in releases:
        releases[ver] = {
            "changelog": "更新到 %s %s。" % (app, ver),
            "updated_at": datetime.datetime.now().astimezone().replace(microsecond=0).isoformat(),
            "os_min_version": "1.2.0",
            "packages": {},
        }
        print("  已新建 releases[%s]，记得补写 changelog" % ver)

    pkg = releases[ver].setdefault("packages", {}).setdefault(platform, {})
    pkg.update({
        "download_url": "./packages/" + fpk,
        "sha256": hashlib.sha256(blob).hexdigest(),
        "size": len(blob),
    })

    with open(index_path, "w", encoding="utf-8") as fh:
        json.dump(index, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    print("  %s: %d bytes  sha256=%s" % (fpk, len(blob), pkg["sha256"]))


if __name__ == "__main__":
    main()
