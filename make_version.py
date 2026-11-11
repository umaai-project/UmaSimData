#encoding: utf-8
"""刷新 version.toml，并为每个频道生成 update.json（umaai-update 协议）。

文件清单两种给法：显式 `filelist`，或用 `glob` 从频道目录展开（展开结果写回 version.toml，
旧客户端照样能读到具体文件）。频道声明了 `version_file` 时，脚本先生成该版本标记再并入清单；
标记内容只由「频道数据日期」决定，重跑不会制造假更新。
"""
import tomlkit
from tomlkit import parse
import datetime, glob, os
import hashlib
import json
import sys

# 新客户端读取的频道清单 schema 版本；未列出的频道不写 schema_version
SCHEMA_VERSIONS = {"ai_data": 1}

# 频道清单文件名（本脚本自己生成，不参与数据日期与文件清单）
MANIFEST_NAME = "update.json"


def get_hash(filename, algorithm="sha1"):
    digest = hashlib.new(algorithm)
    with open(filename, "rb") as f:
        digest.update(f.read())
    ret = digest.hexdigest()
    print(filename, ret)
    return ret


def channel_files(key, info):
    """频道内文件相对路径：显式 filelist，或按 glob 从目录展开（`/` 分隔、去重排序）。"""
    if "glob" not in info:
        return list(info["filelist"])
    found = set()
    for pattern in info["glob"]:
        for path in glob.glob(os.path.join(key, pattern)):
            if os.path.isfile(path):
                found.add(os.path.relpath(path, key).replace(os.sep, "/"))
    return sorted(found)


def get_last_modified(key, info):
    """频道数据日期 = 目录内（递归）最新 mtime；清单与版本标记不参与。"""
    skip = {MANIFEST_NAME, info.get("version_file")}
    latest = None
    latest_path = None
    for root, _dirs, names in os.walk(key):
        for name in names:
            if root == key and name in skip:
                continue
            path = os.path.join(root, name)
            mtime = os.path.getmtime(path)
            if latest is None or mtime > latest:
                latest = mtime
                latest_path = path
    if latest is None:
        return None
    latest_time = datetime.datetime.fromtimestamp(latest)
    print(f"{latest_path}: {latest_time}")
    return latest_time


def write_version_file(key, info, version):
    """写频道版本标记（如 `.portable/version.json`）；内容确定，重跑不会变。"""
    name = info.get("version_file")
    if not name:
        return
    path = os.path.join(key, name)
    payload = {"channel": key, "version": version}
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print(f"{path}: {version}")


def get_cos_config(filename):
    return parse(open(filename, encoding="utf-8").read())


def build_update_manifest(key, info):
    """按 umaai-update 协议生成 <频道>/update.json（逐文件 sha256）。"""
    files = []
    for name in info["filelist"]:
        path = f"{key}/{name}"
        files.append({
            "path": name,
            "size": os.path.getsize(path),
            "sha256": get_hash(path, "sha256"),
        })
    manifest = {
        "protocol": 1,
        "version": info["date"],
        "date": info["date"],
        "files": files,
    }
    if key in SCHEMA_VERSIONS:
        manifest["schema_version"] = SCHEMA_VERSIONS[key]
    with open(f"{key}/update.json", "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print(f"{key}/update.json: {len(files)} 个文件")


current_date = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
print("当前时间:", current_date)

version = {}
with open("version.toml", encoding="utf-8") as f:
    version = parse(f.read())

for key in version:
    print(f"[{key}]")
    info = version[key]
    if not channel_files(key, info):
        print(f"{key}: 频道目录没有可发布的文件，跳过（不生成清单）")
        continue
    latest_time = get_last_modified(key, info)
    if latest_time is not None:
        info["date"] = str(latest_time)
    write_version_file(key, info, str(info["date"]))
    listed = channel_files(key, info)
    version_file = info.get("version_file")
    if version_file:
        listed = sorted(set(listed) | {version_file})
    info["filelist"] = listed
    sha1 = info.get("sha1")
    if sha1:
        info["sha1"] = get_hash(f"{key}/{info['filelist'][0]}")
    package_sha1 = info.get("package_sha1")
    if package_sha1:
        info["package_sha1"] = get_hash(f"{key}/{info['package']}")

with open("version.toml", "w", encoding="utf-8") as f:
    tomlkit.dump(version, f)

# 新协议清单随频道目录上传；旧客户端继续读根目录 version.toml
for key in version:
    if version[key]["filelist"]:
        build_update_manifest(key, version[key])

# coscmd config -a ${COS_SECRET_ID} -s ${COS_SECRET_KEY} -b ${COS_BUCKET_NAME} -r ${COS_BUCKET_REGION}
if "--no-upload" in sys.argv:
    print("跳过上传（--no-upload）")
else:
    print("上传到 cos")
    os.system(f"coscmd upload -rs . /uma/ --ignore ./.git/* ")
