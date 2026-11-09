#encoding: utf-8
import tomlkit
from tomlkit import parse
import datetime, os
import hashlib
import json
import sys

# 新客户端读取的频道清单 schema 版本；未列出的频道不写 schema_version
SCHEMA_VERSIONS = {"ai_data": 1}

def get_hash(filename, algorithm="sha1"):
    digest = hashlib.new(algorithm)
    with open(filename, "rb") as f:
        digest.update(f.read())
    ret = digest.hexdigest()
    print(filename, ret)
    return ret

def get_last_modified(dir):
    # update.json 是本脚本自己生成的，不参与数据日期
    files = [f for f in os.listdir(dir) if f != "update.json"]
    latest_entry = max(files, key=lambda x: os.path.getmtime(f'{dir}/{x}'))
    latest_time = datetime.datetime.fromtimestamp(os.path.getmtime(f'{dir}/{latest_entry}'))
    print(f"{dir}/{latest_entry}: {latest_time}")
    return latest_time

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
    version[key]["date"] = str(get_last_modified(f"{key}"))
    sha1 = version[key].get("sha1")
    if sha1:
        filename = f"{key}/{version[key]['filelist'][0]}"
        new_sha1 = get_hash(filename)
        version[key]["sha1"] = new_sha1
    package_sha1 = version[key].get("package_sha1")
    if package_sha1:
        filename = f"{key}/{version[key]['package']}"
        new_sha1 = get_hash(filename)
        version[key]["package_sha1"] = new_sha1
        
with open("version.toml", "w", encoding="utf-8") as f:
    tomlkit.dump(version, f)

# 新协议清单随频道目录上传；旧客户端继续读根目录 version.toml
for key in version:
    build_update_manifest(key, version[key])

# coscmd config -a ${COS_SECRET_ID} -s ${COS_SECRET_KEY} -b ${COS_BUCKET_NAME} -r ${COS_BUCKET_REGION}
if "--no-upload" in sys.argv:
    print("跳过上传（--no-upload）")
else:
    print("上传到 cos")
    os.system(f"coscmd upload -rs . /uma/ --ignore ./.git/* ")

