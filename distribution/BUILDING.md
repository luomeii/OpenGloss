# 构建与发布验收

构建者需要 Python 3.12+；最终使用者不需要。本目录的 inputs.lock.json 固定官方 Python 嵌入运行时、PyPI wheels 和公开词典的来源及 SHA256。不要使用开发者电脑的 site-packages、个人 config/persona、pae.db 或已安装目录来组装发行包。

在干净源码根目录准备锁定输入（下面是 PowerShell；curl.exe 从声明的公开地址下载，不安装到系统）：

```powershell
$releaseInputs = Get-Content distribution/inputs.lock.json -Raw | ConvertFrom-Json
New-Item -ItemType Directory -Force .release-inputs/wheels | Out-Null
curl.exe -L --fail $releaseInputs.runtime.url -o .release-inputs/python.zip
curl.exe -L --fail $releaseInputs.dictionary.url -o .release-inputs/dict.sqlite
foreach ($releaseWheel in $releaseInputs.wheels) {
  curl.exe -L --fail $releaseWheel.url -o (Join-Path .release-inputs/wheels $releaseWheel.filename)
  if ($LASTEXITCODE -ne 0) { throw 'wheel download failed' }
}
python -m distribution.build_release --runtime .release-inputs/python.zip --wheels .release-inputs/wheels --dictionary .release-inputs/dict.sqlite --output dist/OpenGloss-windows-x64 --version 0.4.0-rc.3
```

构建器会核对锁定输入，使用明确文件清单，拒绝额外运行数据/密钥和校验不一致，生成逐文件 SHA256/来源清单；ZIP 不含 Git 元数据、研究报告、日志、用户库或本机依赖缓存。包内公开 dict.sqlite 是词典资源，不是用户数据库。重复构建请选择新输出目录；不要覆盖仍在试用的程序。

安全的本阶段合同测试：

```powershell
python -m unittest discover -s distribution/tests -p "test_*.py" -v
node distribution/tests/test_popup.cjs
```

这些测试使用临时合成数据，不接管日常引擎端口。原仓库 pae/tests 中的历史端到端测试可能停止 4815 进程，不能在日常使用实例运行时执行。

发布前应在没有 Python/pip/Git 的全新 Windows 10/11 x64 环境核验：解压→Install.cmd→引擎健康→手动加载扩展→页面标注/悬停/认识了→模型与记忆→重启持久化→真实 Release 更新→错误校验/启动失败恢复→回退。预发布包、合成 Release 试验和本机隔离测试均不能替代这些门槛。

正式发布时把经审核的 ZIP 命名为 **OpenGloss-windows-x64.zip**，随正确 SemVer 的 GitHub Release 上传，核对 GitHub 资产 digest 与本地 SHA256。管理器只接受 luomeii/OpenGloss 的更新更高正式版本、固定平台资产及有效 SHA256。不要上传安装后目录，也不要把研究目录当源码附件。扩展仍需用户手动切换到新版目录并重新加载。

