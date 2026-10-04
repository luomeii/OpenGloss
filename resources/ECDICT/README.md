# ECDICT 词典资源

本目录存放 PAE 引擎的词典数据。`lemma.en.txt`（约 2.2MB）已随仓库提供。

## dict.sqlite 为什么不在仓库里

`dict.sqlite` 约 65MB，超过 GitHub 单文件合理体积，且为 `ecdict.csv` 的
派生物（可随时重建），因此不入库，由 `.gitignore` 排除。

## 获取 dict.sqlite 的两种方式

**方式一：从 ECDICT csv 自行构建（推荐）**

1. 取 [skywind3000/ECDICT](https://github.com/skywind3000/ECDICT) 仓库根目录的 `ecdict.csv`
   （约 63MB / **77 万词条**，即 ECDICT 官方所说的「基础版本」——本仓库文档中的基准数字
   `dict_words = 770,611` 就是它构建出来的，**请优先用这个**）。最省事的方式是浅克隆：

   ```bash
   git clone --depth 1 https://github.com/skywind3000/ECDICT /tmp/ecdict
   cp /tmp/ecdict/ecdict.csv resources/ECDICT/
   ```

   或在浏览器里打开该仓库 → 点 `ecdict.csv` → 右上角 Download raw file。
2. 放到本目录下，然后在**仓库根目录**执行（用相对路径，避免中文路径问题）：

   ```bash
   python scripts/build_dict.py --csv resources/ECDICT/ecdict.csv --out resources/ECDICT/dict.sqlite
   ```

   构建完成后脚本会打印两表行数，基准值：`dict_words` = 770,611、
   `exchange_map` = 58,158。流式读取，普通笔记本约 1~2 分钟。

   > 也可以用 ECDICT Release 页的完整版（`stardict.7z`，200 万+ 词条）——须先解压转成
   > csv，体积和构建时间都大得多，产出词条数会远超基准值，同样可用但非本仓库默认配置。

**方式二：从 GitHub Releases 下载成品（若本仓库已发布）**

若本仓库的 Releases 页提供了 `dict.sqlite` 附件，下载后直接放到本目录即可——
这是最省事的方式（无需下载 csv、无需构建）。仓库维护者可把自己构建好的
`dict.sqlite` 作为 Release 附件发布，方便其他人一步到位。

## 许可与署名

词典数据来自 [skywind3000/ECDICT](https://github.com/skywind3000/ECDICT)，
遵循其 **MIT License**（见 [ECDICT LICENSE](https://github.com/skywind3000/ECDICT/blob/master/LICENSE)）。
使用或再分发时请保留对 ECDICT 项目的署名。

> 注：随仓库提供的 `lemma.en.txt` 文件自带抬头写的是「free to use for any research
> and/or educational purposes」（比 MIT 更窄的表述）。两者不冲突——以文件自带抬头为准即可，
> 本仓库按原样分发、未作修改。
