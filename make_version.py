#encoding: utf-8
"""刷新 version.toml，并为每个频道生成 update.json（umaai-update 协议）。

文件清单两种给法：显式 `filelist`，或用 `glob` 从频道目录展开（展开结果写回 version.toml，
旧客户端照样能读到具体文件）。频道声明了 `version_file` 时，脚本先生成该版本标记再并入清单；
标记内容只由「频道数据日期」决定，重跑不会制造假更新。

上传后用腾讯云 CDN 的目录刷新把本次发布过的频道踢出边缘缓存——频道里是**同名换内容**的文件，
不刷新的话边缘会继续吐旧包，客户端 sha256 校验不通过（见 readme「缓存刷新」一节）。
刷新优先用官方 SDK（`tencentcloud-sdk-python-cdn`），未安装时退回内置 TC3 签名实现，
两条路径入参出参一致，缺包不影响发布。
"""
import tomlkit
from tomlkit import parse
import datetime, glob, os
import hashlib
import hmac
import json
import sys
import time
import urllib.error
import urllib.request

# 新客户端读取的频道清单 schema 版本；未列出的频道不写 schema_version
SCHEMA_VERSIONS = {"ai_data": 1}

# 频道清单文件名（本脚本自己生成，不参与数据日期与文件清单）
MANIFEST_NAME = "update.json"

# 分发域名根（与客户端 update.rs 的 UPDATE_BASE 一致）；仅用于 CDN 刷新
CDN_BASE = os.environ.get("UMA_CDN_BASE", "https://cdn2.viktorlab.cn/uma").rstrip("/")
# 腾讯云 CDN API 端点与版本
CDN_ENDPOINT = "cdn.tencentcloudapi.com"
CDN_SERVICE = "cdn"
CDN_API_VERSION = "2018-06-06"
# 目录刷新单次上限（腾讯云限制 100 条）
CDN_MAX_PATHS = 100
# 官方 SDK 包名（仅用于提示；未安装时退回内置签名实现）
SDK_PACKAGE = "tencentcloud-sdk-python-cdn"


def get_hash(filename, algorithm="sha1"):
    digest = hashlib.new(algorithm)
    with open(filename, "rb") as f:
        digest.update(f.read())
    ret = digest.hexdigest()
    print(filename, ret)
    return ret


def _canonical_request(host, action, body):
    """规范请求串：方法 / 路径 / 查询串 / 规范头 / 签名头 / 载荷哈希。"""
    content_type = "application/json; charset=utf-8"
    canonical_headers = (
        f"content-type:{content_type}\n"
        f"host:{host}\n"
        f"x-tc-action:{action.lower()}\n"
    )
    signed_headers = "content-type;host;x-tc-action"
    return "\n".join([
        "POST",
        "/",
        "",
        canonical_headers,
        signed_headers,
        hashlib.sha256(body.encode("utf-8")).hexdigest(),
    ])


def _signature(host, service, action, body, timestamp, secret_key):
    """TC3-HMAC-SHA256 签名：`body` 是已序列化的请求体（签的就是这份字节）。

    返回 `(scope, signature, signed_headers)`；签名口径与腾讯云官方样例一致，
    `X-TC-Version` 不参与签名，故不在这里。
    """
    date = time.strftime("%Y-%m-%d", time.gmtime(timestamp))
    canonical_request = _canonical_request(host, action, body)
    signed_headers = "content-type;host;x-tc-action"
    scope = f"{date}/{service}/tc3_request"
    to_sign = "\n".join([
        "TC3-HMAC-SHA256",
        str(timestamp),
        scope,
        hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
    ])

    def _hmac(key, message):
        return hmac.new(key, message.encode("utf-8"), hashlib.sha256).digest()

    secret_date = _hmac(("TC3" + secret_key).encode("utf-8"), date)
    secret_service = _hmac(secret_date, service)
    secret_signing = _hmac(secret_service, "tc3_request")
    signature = hmac.new(
        secret_signing, to_sign.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return scope, signature, signed_headers


def _sign(host, service, action, payload, secret_id, secret_key, timestamp):
    """构造 (URL, body, headers)：序列化请求体后交给 :func:`_signature` 签名。"""
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    scope, signature, signed_headers = _signature(
        host, service, action, body, timestamp, secret_key
    )
    headers = {
        "Authorization": (
            f"TC3-HMAC-SHA256 Credential={secret_id}/{scope}, "
            f"SignedHeaders={signed_headers}, Signature={signature}"
        ),
        "Content-Type": "application/json; charset=utf-8",
        "Host": host,
        "X-TC-Action": action,
        "X-TC-Timestamp": str(timestamp),
        "X-TC-Version": CDN_API_VERSION,
    }
    return f"https://{host}", body, headers


def _api_call(action, params, secret_id, secret_key):
    """内置实现：直接 POST 到 CDN API（TC3 签名，纯标准库），返回 TaskId。"""
    url, body, headers = _sign(
        CDN_ENDPOINT, CDN_SERVICE, action, params, secret_id, secret_key, int(time.time())
    )
    request = urllib.request.Request(
        url, data=body.encode("utf-8"), headers=headers, method="POST"
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        parsed = json.load(response)
    result = parsed.get("Response", {})
    if "Error" in result:
        raise RuntimeError(
            f"{result['Error'].get('Code')}: {result['Error'].get('Message')}"
        )
    return result.get("TaskId")


def _sdk_call(action, params, secret_id, secret_key):
    """官方 SDK 实现（`tencentcloud-sdk-python-cdn`）；未安装时抛 `ImportError`。"""
    from tencentcloud.cdn.v20180606 import cdn_client, models
    from tencentcloud.common import credential
    from tencentcloud.common.profile.client_profile import ClientProfile
    from tencentcloud.common.profile.http_profile import HttpProfile

    http_profile = HttpProfile()
    http_profile.endpoint = CDN_ENDPOINT
    client = cdn_client.CdnClient(
        credential.Credential(secret_id, secret_key),
        "",
        ClientProfile(httpProfile=http_profile),
    )
    request = getattr(models, f"{action}Request")()
    for key, value in params.items():
        setattr(request, key, value)
    return getattr(client, action)(request).TaskId


def has_sdk():
    """是否装了官方 SDK（只用于打印后端名）。"""
    try:
        import tencentcloud.cdn.v20180606  # noqa: F401
    except ImportError:
        return False
    return True


def cdn_call(action, params, secret_id, secret_key):
    """提交一次 CDN 调用并返回 TaskId：优先官方 SDK，缺包时退回内置签名。"""
    try:
        return _sdk_call(action, params, secret_id, secret_key)
    except ImportError:
        return _api_call(action, params, secret_id, secret_key)


def coscmd_credentials():
    """从 coscmd 的配置里取密钥（默认 `~/.cos.conf`，可用 `COS_CONFIG_FILE` 指定）。

    上传本来就走 `coscmd`，那套密钥就在这个文件里，刷新直接复用，不必再配环境变量。
    返回 `(secret_id, secret_key, 配置路径)`；文件缺失或字段不全返回 `None`。
    """
    path = os.path.normpath(
        os.path.expanduser(os.environ.get("COS_CONFIG_FILE", "~/.cos.conf"))
    )
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return None
    values = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line[0] in "#;[":
            continue
        key, sep, value = line.partition("=")
        if sep:
            values[key.strip().lower()] = value.strip()
    secret_id = values.get("secret_id")
    secret_key = values.get("secret_key")
    if secret_id and secret_key:
        return secret_id, secret_key, path
    return None


def cdn_credentials():
    """刷新用的密钥：环境变量优先，最后回落到 coscmd 配置（上传本来就用它）。"""
    for id_name, key_name in (
        ("CDN_SECRET_ID", "CDN_SECRET_KEY"),
        ("TENCENTCLOUD_SECRET_ID", "TENCENTCLOUD_SECRET_KEY"),
        ("COS_SECRET_ID", "COS_SECRET_KEY"),
    ):
        secret_id = os.environ.get(id_name)
        secret_key = os.environ.get(key_name)
        if secret_id and secret_key:
            return secret_id, secret_key, f"环境变量 {id_name}"
    from_cos = coscmd_credentials()
    if from_cos is not None:
        secret_id, secret_key, path = from_cos
        return secret_id, secret_key, f"coscmd 配置 {path}"
    return None


def purge_cache(channels):
    """上传后刷新：各频道目录做目录刷新，根 version.toml 做 URL 刷新。

    优先用官方 SDK（`tencentcloud-sdk-python-cdn`），未安装时退回内置签名实现。
    失败只警告不中断（上传已完成），并打印可直接粘到控制台的手工刷新清单。
    """
    paths = [f"{CDN_BASE}/{key}/" for key in channels]
    urls = [f"{CDN_BASE}/version.toml"]
    if not paths:
        print("没有需要刷新的频道")
        return
    credentials = cdn_credentials()
    if credentials is None:
        print("警告：未找到 CDN 密钥（环境变量 CDN_SECRET_ID，或 coscmd 的 ~/.cos.conf），跳过缓存刷新")
        show_manual_purge(paths, urls)
        return
    secret_id, secret_key, source = credentials
    backend = "官方 SDK" if has_sdk() else f"内置签名（未装 {SDK_PACKAGE}）"
    print(f"刷新 CDN 缓存（{backend}，密钥来自 {source}）：{len(paths)} 个目录")
    failed = False
    # SDK 的异常类型要装包才能 import，这里统一按普通异常兜住，失败降级到手工清单
    for label, action, params in (
        ("目录刷新", "PurgePathCache", {"Paths": paths[:CDN_MAX_PATHS], "FlushType": "flush"}),
        ("清单刷新", "PurgeUrlsCache", {"Urls": urls}),
    ):
        try:
            print(f"  {label}已提交：TaskId {cdn_call(action, params, secret_id, secret_key)}")
        except Exception as err:  # noqa: BLE001
            failed = True
            print(f"  {label}失败：{err}")
    if failed:
        print("警告：缓存刷新未全部成功，边缘可能仍返回旧文件")
        show_manual_purge(paths, urls)
    else:
        print("缓存刷新通常 1-5 分钟生效；未生效前客户端会报「文件校验不一致」")


def show_manual_purge(paths, urls):
    """打印手工刷新清单（CDN 控制台 → 缓存刷新）。"""
    print("  手工刷新（CDN 控制台 → 缓存刷新）：")
    for path in paths:
        print(f"    目录 {path}")
    for url in urls:
        print(f"    URL  {url}")


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
published = []
for key in version:
    if version[key]["filelist"]:
        build_update_manifest(key, version[key])
        published.append(key)

# coscmd config -a ${COS_SECRET_ID} -s ${COS_SECRET_KEY} -b ${COS_BUCKET_NAME} -r ${COS_BUCKET_REGION}
if "--no-upload" in sys.argv:
    print("跳过上传（--no-upload）")
else:
    print("上传到 cos")
    code = os.system(f"coscmd upload -rs . /uma/ --ignore ./.git/* ")
    if code != 0:
        print(f"警告：上传命令返回 {code}，跳过缓存刷新（源站可能没更新，先排查上传）")
    elif "--no-purge" in sys.argv:
        print("跳过缓存刷新（--no-purge）")
    else:
        purge_cache(published)
