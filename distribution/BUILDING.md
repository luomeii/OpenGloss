# OpenGloss Windows 构建与发布验收

构建者需要 Python 3.12+。`inputs.lock.json` 固定官方 Python 嵌入运行时、15 个 PyPI wheels（含 certifi 2026.10.18）和公开词典的来源与 SHA256。内置 CA 仅允许 `runtime/site-packages/certifi/cacert.pem` 的固定内容；私钥和其他 PEM 禁止入包。不要从开发者 site-packages、个人配置、数据库或已安装目录组装发行包。

在干净源码根目录准备锁定的公开输入。可使用已核验的 `work/public-inputs`，或按锁文件 URL 下载并独立核对 SHA256：

```powershell
$releaseInputs = Get-Content distribution/inputs.lock.json -Raw | ConvertFrom-Json
New-Item -ItemType Directory -Force .release-inputs/wheels | Out-Null
curl.exe -L --fail $releaseInputs.runtime.url -o .release-inputs/python.zip
curl.exe -L --fail $releaseInputs.dictionary.url -o .release-inputs/dict.sqlite
foreach ($releaseWheel in $releaseInputs.wheels) {
  curl.exe -L --fail $releaseWheel.url -o (Join-Path .release-inputs/wheels $releaseWheel.filename)
  if ($LASTEXITCODE -ne 0) { throw 'wheel download failed' }
}
python -m distribution.build_release --runtime .release-inputs/python.zip --wheels .release-inputs/wheels --dictionary .release-inputs/dict.sqlite --output dist/OpenGloss-windows-x64 --version 0.5.0
```

构建器校验所有锁输入，自动识别唯一 `python3*._pth` 及同版本 `python3*.zip`，拒绝缺失或歧义；按明确白名单打包，包括卸载助手。版本为 0.5.0，源基线 `fdd2acccb0b46969055a4867f0be7f5136998c76`；状态仍写为候选、待 Windows/浏览器验收。输出逐文件 SHA256 清单、ZIP 和 SHA256 文件。包不含 Git 元数据、用户数据库/密钥、日志或本机依赖缓存。公开 `dict.sqlite` 是词典资源，不是用户库。

可在隔离环境运行 `python -m unittest discover -s distribution/tests -p "test_*.py" -v`，配合单独的扩展 Node 合同。测试应使用临时目录、合成 SQLite、mock 注册表/打开目录和独立端口；不要在日用实例运行会停 4815 进程的旧端到端脚本。

发布前仍须在全新 Windows 10/11 x64 环境核验解压、安装、管理页/扩展配对、页面标注、模型、隐私设置、自启开关、导出一致性、卸载/保留数据、真实正式 GitHub Release 更新和回退。此轮合成测试不等于新机保证。正式发布只上传经审核的 `OpenGloss-windows-x64.zip`，核对 GitHub 资产 digest；预发布不会自动提示。旧 0.4 管理器与 0.5 Bearer/匿名健康协议不兼容，旧试用者需新包独立安装并手动处理旧数据；不要宣称直接自动升级。扩展仍需用户手动更新并重新配对。
