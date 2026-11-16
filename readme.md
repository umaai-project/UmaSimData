UmaSimData 
----
马娘育成计算器和小黑板数据存档

## 频道

| 目录 | 频道 | 内容 | 落点（客户端） |
|---|---|---|---|
| `ai_data/` | `ai_data` | `umaDB.json` / `cardDB.json` / `text_data_dict.json` | `<安装根>/data/` |
| `ura_data/` | `ura_data` | 12 个 `.br`（小黑板数据） | `<安装根>/.portable/` |
| `ura_plugins/` | `ura_plugins` | `Plugins/*.zip`（小黑板插件包）+ `version.json` | `<安装根>/.portable/` |

`ura_plugins` 与 `ura_data` 装到同一个 `.portable/`，但文件集合不重叠：`.br` 归数据频道，
`Plugins/*.zip` 归插件频道。`.portable/PluginData/`（运行时帧与 native 缓存）不属于任何频道。

## 发布

`make_version.py` 刷新根目录 `version.toml`，并为每个频道生成 `update.json`
（逐文件 sha256、文件大小与协议版本），随后随 `coscmd upload -rs . /uma/` 上传到 COS。
旧客户端继续读 `version.toml`；新客户端读 `<频道>/update.json`，两者可并存。

频道的文件清单有两种给法（`version.toml` 里二选一）：

- `filelist = [...]`：显式枚举（`ai_data` / `ura_data` 的现状）；
- `glob = ["Plugins/*.zip"]`：按模式从频道目录展开，**展开结果会写回 `version.toml`**，
  旧客户端仍能读到具体文件清单。新增 / 删除插件包只需改目录，不用手工维护清单。

频道声明了 `version_file = "version.json"` 时，脚本先生成该版本标记再并入清单：
内容是 `{"channel": "<频道>", "version": "<频道数据日期>"}`，只由目录 mtime 决定，
重跑不会制造假更新。新客户端读它显示「已装版本」，也参与逐文件 sha256 比对。

```bash
python make_version.py --no-upload   # 只生成，不上传
python make_version.py               # 生成 → 上传 → 刷新 CDN
python make_version.py --no-purge    # 上传但不刷新 CDN（排障用）
```

`ura_plugins/Plugins/*.zip` 由 `.gitignore` 例外放行（`!ura_plugins/Plugins/*.zip`），
其余 `*.zip` / `*.7z` 仍不入库。

## 缓存刷新（上传后自动）

频道里是**同名换内容**的文件（`Plugins/EventLoggerPlugin.zip` 换新包仍叫这个名），
CDN 对 `.zip` 用了长 TTL，源站的 `Cache-Control: max-age=0` 被缓存规则覆盖 →
边缘会继续吐旧包，客户端 sha256 校验不通过，报「文件校验不一致」并回滚。

所以脚本在 `coscmd upload` 成功后自动刷新：

| 动作 | 范围 |
|---|---|
| `PurgePathCache`（目录刷新） | 本次重写过清单的每个频道目录 `https://cdn2.viktorlab.cn/uma/<频道>/` |
| `PurgeUrlsCache`（URL 刷新） | `https://cdn2.viktorlab.cn/uma/version.toml`（旧客户端读的根清单） |

- 密钥：优先 `CDN_SECRET_ID` / `CDN_SECRET_KEY`，其次 `TENCENTCLOUD_SECRET_ID` / `TENCENTCLOUD_SECRET_KEY`、
  `COS_SECRET_ID` / `COS_SECRET_KEY`，**最后回落到 coscmd 的 `~/.cos.conf`**——上传本来就用 `coscmd`，
  那套密钥就在这个文件里，通常不用另配环境变量（可用 `COS_CONFIG_FILE` 指定别的配置文件）
- 该密钥需要有 CDN 刷新权限（`QcloudCDNFullAccess`，或含 `PurgePathCache` / `PurgeUrlsCache` 的自定义策略）；
  只绑了 COS 的子账号会报 `AuthFailure.UnauthorizedOperation`，换密钥或补策略即可
- 域名可用 `UMA_CDN_BASE` 覆盖（默认 `https://cdn2.viktorlab.cn/uma`）
- 上传命令返回非 0 时不刷新（先排查上传，避免把「没更新成功的源站」刷成最新）
- 刷新失败只警告不中断（上传已完成），并打印可直接粘到控制台的目录 / URL 清单
- 目录刷新有每日额度（百条量级）；每次发布用「频道数条目录 + 1 条 URL」，正常够用
- 生效通常 1–5 分钟；未生效前客户端仍会报校验不一致

实现走两条路，入参出参一致：

1. **官方 SDK**（推荐）：`pip install tencentcloud-sdk-python-cdn`，脚本自动使用；
2. **内置签名兜底**：未装 SDK 时用标准库实现 TC3-HMAC-SHA256，保证缺包也能刷。
   该实现已按官方样例对拍（`payload` / `canonicalRequest` 哈希与文档逐字符一致），
   并且用假密钥实测能拿到 `AuthFailure.SecretIdNotFound`（说明请求格式被网关接受，
   只差真实密钥）。

