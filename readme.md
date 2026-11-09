UmaSimData 
----
马娘育成计算器和小黑板数据存档

## 发布

`make_version.py` 刷新根目录 `version.toml`，并为每个频道生成 `update.json`
（逐文件 sha256、文件大小与协议版本），随后随 `coscmd upload -rs . /uma/` 上传到 COS。
旧客户端继续读 `version.toml`；新客户端读 `<频道>/update.json`，两者可并存。