# ASockPEView · 中证全指市盈率

使用 AKShare 获取中证全指（000985）的历史滚动市盈率，在本机网页中查看。

**仅手动更新。** 启动程序、打开页面、切换图表范围均只读取本地缓存，不请求上游数据。没有定时任务、定时刷新或实时行情订阅。

![中证全指历史滚动市盈率](docs/pe-history.png)

上图是仓库保存的静态预览。成功手动更新数据时会重新生成；提交并推送后，GitHub 仓库首页才会展示新版。GitHub README 不运行 Python，也不提供直接抓取数据的交互按钮。

## 本机打开

推荐 Python 3.13。Windows 首次使用：

1. 克隆或下载本仓库。
2. 双击 `setup.cmd` 安装依赖（需联网，只安装依赖，不抓取行情）。
3. 双击 `start.cmd`，打开 **http://127.0.0.1:8765**。

仓库附带已验证的历史缓存，首次打开即可看图。之后启动只需 `start.cmd`。

也可以使用命令行：

```powershell
git clone https://github.com/duoshaga/ASockPEView.git
cd ASockPEView
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe launch.py
```

macOS / Linux：创建虚拟环境后，使用 `.venv/bin/python` 执行相同命令。

## 手动更新

在本机页面点击 **“手动更新数据”**，或者执行：

```powershell
.\.venv\Scripts\python.exe update.py
```

程序在这时访问中证指数官网。初次没有缓存时读取完整历史；有缓存时读取最近 35 天的重叠区间并合并。更新失败保留上次成功数据，页面显示错误。只在一次手动任务进行中查询其完成状态，不会自行发起下一次抓取。

更新后同步到 GitHub：首页图片与数据文件需一并提交、推送。网页按钮本身不会自动提交或推送。

```powershell
git add data/history.json web/data.json web/history.csv docs/pe-history.png
git commit -m "Update CSI All Share PE snapshot"
git push
```

仅重新生成首页图片、不联网抓取：

```powershell
.\.venv\Scripts\python.exe preview.py
```

## 图表与数据口径

- 默认近 10 年，可切换 1/3/5/10 年及全部；悬停看数值、拖动缩放、双击复原，支持完整 CSV 下载。
- 来源：[中证指数官网](https://www.csindex.com.cn/zh-CN/indices/index-detail/000985)。使用 [AKShare](https://github.com/akfamily/akshare) 的 `stock_zh_index_hist_csindex`，代码 `000985`，字段 `滚动市盈率`。
- 随仓库提交的首份有效历史从 2011-06-28 开始，早期存在缺口；缺失值保留为空，图表断线，不插值、不补零。最新日期以页面和预览图为准。
- 分位线按当前区间的有效日度 PE 计算，采用线性插值；百分位表示其中不高于最新可用 PE 的观测值比例。README 图片采用全部有效历史，网页默认近 10 年，两者分位值可能不同。
- 中证全指用于观察广泛 A 股市场，不等于全部上市股票的市盈率；直接采用中证官网口径，不混用其他指数、不重新推断权重。
- 数据是源站发布的日度记录，不是盘中实时数据。周末、休市或源站未发布新记录时，手动抓取也不会产生新的数据日期。

## 结构与核验

| 文件 | 用途 |
| --- | --- |
| `server.py` / `launch.py` | 只监听本机的服务及启动入口 |
| `web/` | 交互图表、本地 Plotly 库、JSON 和 CSV |
| `data_pipeline.py` / `update.py` | 数据校验、缓存合并、手动更新 |
| `data/history.json` | 最后成功的完整缓存 |
| `preview.py` / `docs/pe-history.png` | 生成及展示仓库首页预览 |
| `tests/` | 数据质量、缓存保护、服务和前端行为验证 |

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
node tests/test_frontend.cjs
```

只有最后一项前端测试需要 Node.js，运行图表不需要 Node。绘图库保存在本地，不依赖 CDN。上游公开接口可能变更，出现错误时查看页面状态或 `server.log`。

第三方绘图库许可见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
