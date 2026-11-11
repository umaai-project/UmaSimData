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
python make_version.py               # 生成并上传到 COS
```

`ura_plugins/Plugins/*.zip` 由 `.gitignore` 例外放行（`!ura_plugins/Plugins/*.zip`），
其余 `*.zip` / `*.7z` 仍不入库。
