# 开发须知（CONTRIBUTING）

> 这份文件**只讲 README 与提交记录里看不到的东西**：本机环境的坑、以及几处「改了会静默坏掉」的唯一入口。
>
> 项目是什么、怎么用、为什么这样设计 —— 看 [`README.md`](README.md)（十二章，含「七、技术架构」与「八、关键设计决策」）和 `git log`（每条提交都写了问题 / 根因 / 改动 / 实测数据），**不在这里重复**。

---

## 一、跑起来

| 事项 | 说明 |
| --- | --- |
| **解释器** | 若 `python` 报错找不到 `Pillow`，改用 `py -3`（Windows）或 `python3`。本机上 PATH 里的 `python` 是**另一个没装 Pillow 的版本**，表现为「缩略图全部生成失败」。 |
| **依赖** | 只有两个可选包：`pillow-heif`（读 HEIC 的拍摄时间）、`Pillow`（转码）。**缺了不会崩**，但 HEIC 的 EXIF 读不到，时间轴会退化成从 mtime 兜底。 |
| **ffmpeg** | **本机依赖**，把 `ffmpeg.exe` 放项目根目录即可（92MB，在 `.gitignore` 里）。缺了它的表现：独立视频全是黑封面、桌面端点 LIVE 只得到一个黑窗，而 iOS 因为原生解 HEVC 一切正常。服务运行中放进去会**自动接上，不用重启**。 |
| **启停** | 双击 `一键启动.bat` / `一键结束.bat`；等价于 `python server.py --port 9000 --lan` 与 `python server.py --stop`。 |
| **端口占用** | `check_already_running()` 会拦住第二个实例并打印「服务已在运行，本次启动已取消」。 |

## 二、四个「改了会静默坏掉」的地方

1. **`server.py` 首行不要加 `#!` shebang。**
   Windows 的 `py` 启动器会按 shebang 去找 `python3`，而本机 `python3` 命中 Microsoft Store 的占位程序 —— 它会**静默返回 9009 且不打印任何信息**，表现为「双击 bat 什么都没发生」。

2. **`一键启动.bat` / `一键结束.bat` 是 GBK 编码 + CRLF。**
   改这两个文件必须保持 GBK，用 UTF-8 写回会让中文提示全部乱码。

3. **缩略图只有一档 640（`THUMB_DEFAULT`）。**
   前三档调整日视图照片大小；月份采用连续整月网格，年份采用月份封面卡片。三个层级共用 **640 单档缩略图**和有上限的分页缓存，不为界面增加缓存规格。要动规格时：`THUMB_DEFAULT` / `THUMB_QUALITY` 和**服务端对旧参数（`s=160/240/320/480/960`）的吸附**必须同步改，`tests/test_thumb_spec.py` 会守住这条。**不要**再引入多规格缓存。

4. **图片生成的闸门是两条，别合回一条。**
   `_fg_sem`（前台：用户正在等的大图 / 预览）与 `_gen_sem`（后台：缩略图批量）。合成一条 FIFO 会让几百个缩略图把大图堵到队尾 —— 实测一张 0.8s 就能转完的图变成 **17.9s**。并发用 `SEEKLIVE_GEN_WORKERS` / `SEEKLIVE_FG_WORKERS` 调。

## 三、跑测试

```bash
py -3 tests/<name>.py      # Python 专项
node tests/<name>.mjs      # 前端调用链静态守卫（不需要浏览器/Playwright）
```

完整清单、各项断言数与运行前置条件见 README「九、仓库说明」。

**硬约束：测试进程绝不能碰全局 `server.pid` / `server.port`。**
这两个文件是**全局唯一**的，`server.py --stop` 按它们杀进程。测试一旦读到真实服务的 pid，就会**把正在跑的服务杀掉**（本项目真的发生过）。起子进程时带上 `SEEKLIVE_STATE_PREFIX=test_` 做隔离 —— `tests/test_admin_auth.py` 是现成范例，另有「组 8 · 实例隔离」断言守着这条。

浏览器端到端用「**系统 Edge + 隔离安装的 `playwright-core`**」，Playwright 不进生产依赖。

## 四、提交前必查：本仓库的 `.md` 会被外部编辑器写回

已发生 **4 次**。现象是：文件 mtime 是**很新的时间**，但内容是**旧版本**（已删的章节回来、新加的章节消失），或者内容不变却被重新格式化（表格列宽重排、`**` 被转义、行尾变成 CRLF）。

根因基本确定：**编辑器里长期开着这些 `.md`，旧缓冲区一保存就回写。** 代码文件（`server.py` / `web/*`）目前没出过这个问题，只出在 `.md` 上。

**防护习惯：**

- `git add` 之前先看 `git --no-pager diff --numstat`。某个文档突然出现**上百行删除** = 被覆盖了。
- 发现被覆盖：**先备份**当前副本到 `tmp/`，再 `git checkout HEAD -- <file>` 取回正确版本，然后用脚本（`str.replace` + 断言匹配次数）把本轮改动重新套一遍 —— **不要手改**。
- 改完**尽快 commit**，缩小暴露窗口。
- **不要把 `README.md` 长时间开在编辑器的预览面板里。**

还有一条反直觉的：**别用带「自动格式化 Markdown」的编辑器保存 `README.md`。** 它大量使用**行尾两个空格**做 Markdown 硬换行，格式化器会把这些空格吃掉，渲染结果就变了。

## 五、其它约定

- **运行期目录不要提交。** `data/` `cache/` `logs/` `tmp/`、`libraries.json`、`admin.json` 都在 `.gitignore` 里 —— 它们含本机绝对路径、私有目录名和**管理口令**。需要示例时用 `libraries.example.json` / `admin.example.json`。
- **本机有 HTTP 代理（`127.0.0.1:7890`），两个方向都要注意：**
  - 用 `urllib` 自测 `127.0.0.1` 必须 `build_opener(ProxyHandler({}))` 绕过，否则拿到 502（裸 socket 不受影响）。
  - 反过来，访问 `github.com` **必须**经这个代理（已在 repo-local 配好 `http.proxy`），直连会得到 `Empty reply from server`。
- **访问控制 / 分享有几处「唯一入口」**（撤销必须生效于已在用的连接、`do_HEAD` 复用 `do_GET` 以受同一套约束等），改动时不要绕开。相关回归在 `tests/test_access.py`（含 keep-alive 撤销）。
- **别随手停掉别人正在跑的服务。** 改前端（`web/*`）**不用重启**（静态文件 no-cache），只有改 `server.py` 才需要重启。
