# 人物与宠物识别：独立本机原型

不接入生产服务，不修改图库或生产数据库，不运行 OCR。模型准备脚本只下载公开权重；分析脚本本地运行，关闭 ONNX 遥测并显式使用 CPU。真实预览、特征、路径、核对结果均存放在被 Git 忽略的 `tmp/vision-lab/`，不要提交或发布。

## 当前结果

从项目根目录打开 `tmp/vision-lab/combined/review.html`。这是约 39 MB 的独立离线页面，包含真实照片预览，应仅在本机打开。2025 年 2 月重点样本优先排列，后接跨目录抽样；两批合并去重并重新计算跨批匹配。

动物请从“猫狗鸟档案”的候选分组开始，先检查组内照片，再命名一组并勾选其他组批量合并。单独的“宠物匹配”页面已移除，历史配对标注仍保留。点图片查看完整预览和检测框。“抽样照片”还能检查漏检，但当前没有漏检标注功能。

核对内容尝试保存到当前浏览器。请使用“导出核对结果”保留 JSON；换浏览器或清理浏览数据后可以导入。页面没有上传或联网分析接口。浏览器打开本地文件时的保存行为可能不同，因此导出文件是可靠备份。

### 人物组合并（2026-11-01）

“人物候选”现在包含人物列表、相似组推荐和手动组合并。选择一个人物，勾选其他属于同一人的候选组，然后点击“合并到当前人物并确认人脸”。也可以在下拉列表中选择没有被推荐的组。给当前人物填写别名，合并后统一显示在这个人物下。

合并会将未标记及已确认的人脸作为人工确认样本；标为“不是这个人”“忽略”“不确定”的人脸不会作为参考。请先排除错误人脸再确认整组。原始机器分组与分数保持不变，人工合并记录覆盖其展示。每次人物操作均可撤销，最多保留最近 30 步，并随导出文件保存。

推荐使用人工确认的参考脸，最多选取 6 张不同特征的代表；其他人物卡片最多显示 3 张候选代表。推荐比较这些参考与其他组的可用人脸，按最高及次高匹配加权排序，最多展示 8 组。当前展示门槛收紧至 0.45，只是核对便利参数，不能作为自动身份阈值。没有通过平均脸或放宽原始聚类门槛自动合并。

“隐藏这条推荐”只调整核对列表，不作为模型训练的负例；可撤销或恢复隐藏。合并、别名、确认参考均在本机保存并能导出/导入。升级后数据集签名保持一致，旧版猫狗与人物核对记录可以继续使用。此次没有重读原图或重新跑检测，仅使用已有特征重建页面。

当前已实现现有样本间的多参考推荐。未来新照片的自动归入、跨样本集人物档案迁移尚未接入；不能将这次 UI 升级理解为完成了全库自动身份识别。

### 只整理关心的人

默认进入“关注的人”：已有名字的人自动出现在这里，也可以在“其他人物”中暂不命名就关注。其余候选留在“其他人物”，无需逐一确认。关注人物没有人工确认参考脸时，会提示“去确认”；命名本身仍然不构成人脸真值确认。

“保存名字并确认未排除的人脸为参考”提供直接的确认入口。确认时跳过“不是这个人”“忽略”“不确定”的脸。匹配推荐只针对已关注且有已确认参考脸的人，候选来自其他人物；已经命名的人仍可手动合并。

“不关注此人”从日常列表和匹配推荐中隐藏此人，保留姓名、合并、标注与原照片。在“不关注”列表中可恢复关注，也支持撤销。关注状态随 JSON 导出/导入；原先的“忽略”仍只表示无效人脸，与不关注是不同的动作。

可在单批输出目录放置匹配该数据集的 `labels.seed.private.json`，重建时作为本机离线备用标注嵌入页面。没有浏览器记录时使用备用标注；同一浏览器已有记录优先，避免覆盖更新的人工编辑。导入 JSON 可显式恢复指定版本。备用文件与生成页面都含私人姓名，不要发布。

### 关注人物的增量发现

```powershell
& tmp/vision-lab/venv/Scripts/python.exe prototypes/vision-lab/discover.py --labels 'PATH_TO_LATEST_REVIEW.json' --count 800 --out tmp/vision-lab/discovery-001
```

从已关注且有人为确认参考脸的人建立本机档案；未命名/不关注的人不会成为检索目标。只分析原样本里尚未做人脸分析的照片，跨目录分层抽取，原图保持只读。图库目录和实际选片清单仍只在忽略提交的本机目录内。

逐张缓存人脸特征、预览与版本（原文件大小、修改时间、模型哈希、预处理版本）。相同参数重跑复用缓存，原照片不再重复解码；文件变化时重算，缓存损坏时重新生成。分析后重新检查所有源文件的大小与修改时间，变化则不发布结果。

单参考人物需要最高余弦分数至少 0.62，且领先其他关注人物至少 0.12；多参考人物按最高与次高分数 0.8/0.2 加权，至少 0.55、次高至少 0.45，领先至少 0.08。最小脸 40 像素。同一照片最多为一个人物推荐一张脸，每人最多展示 10 张，粗略近重复会被抑制。**这些是未校准的候选筛选规则，不是身份判定或准确率保证**；参考数量、同场景偏差、年龄和相似亲属仍会影响结果。

完成后自动更新原页面的“关注人物的新照片”页签；每人默认仅显示 3 张，其余可选展开。没有新增必须完成的核对任务。可选“确认是这个人/不是这个人/不确定”单独记录并随原核对 JSON 导出，不自动改动原分组、原参考脸或图库索引。

`profiles.private.json` 保存目标人物和已确认特征，`discovery.private.json` 保存候选及统计。原 `results.private.json`、原分组 ID 和核对数据集签名保持不变。本轮用户导出记录作为 `labels.seed.private.json` 保留，浏览器中更新的本地记录仍优先。

后续继续下一批时可排除已经扫描且版本未变的照片：

```powershell
& tmp/vision-lab/venv/Scripts/python.exe prototypes/vision-lab/discover.py --labels 'PATH_TO_LATEST_REVIEW.json' --count 800 --out tmp/vision-lab/discovery-002 --exclude-run tmp/vision-lab/discovery-001
```

可重复指定 `--exclude-run`。之前失败或文件版本变化的照片不会被当成已完成而跳过。修改参考后，先重跑已有批次即可复用图像特征重新计算匹配；当前页面是扫描时刻的结果，并不会随浏览器里的参考修改自动重新检索全库。

### 合并多批发现

先用同一份最新人工档案重新匹配旧批次（复用缓存），再分析新批次，最后汇总：

```powershell
& tmp/vision-lab/venv/Scripts/python.exe prototypes/vision-lab/aggregate_discoveries.py tmp/vision-lab/discovery-001 tmp/vision-lab/discovery-002 --out tmp/vision-lab/discovery-expanded
```

只接受参考签名、数据集和模型一致的批次；重复选片或源文件发生变化时拒绝发布。汇总每人最多 12 张，从最新批次开始交替取不同批次候选，粗略折叠跨批近重复。默认仍只展示 3 张，不要求追加核对。源批次的完整候选清单保留。

已反馈的旧批次需在重算前保留 `user-reviewed-round.private.json` 及对应 `feedback.private.json`，避免把旧反馈套到新筛选结果上。整体反馈与逐条标注分别保存，不据此推算全库准确率。后续在人物页合并改名时，发现页沿用最新人物归属和姓名；核对键仍使用原观测及原参考版本，防止旧判断误用。

### 将明确确认的精选归入原人物

仅在用户明确确认整批精选均属于对应人物后执行：

```powershell
& tmp/vision-lab/venv/Scripts/python.exe prototypes/vision-lab/integrate_confirmed.py --discovery tmp/vision-lab/discovery-expanded --labels 'PATH_TO_LATEST_REVIEW.json'
```

保存至 `confirmed_discoveries.private.json`，记录原人物、观测、源版本、特征与用户确认批次。原机器分组和数据集 ID 不变。人物页直接显示新增人脸，原导出缺少这些键时依据确认记录补全；后续手动排除或不关注仍会生效。重复执行同批不会重复添加，归属或源版本冲突时拒绝覆盖。

后续扫描自动使用补充的参考脸，已归入的人脸不再成为待匹配候选。旧批次可加 `--no-publish` 复用缓存重算，待多批汇总时统一发布，避免中途反复替换页面。原批次及确认来源继续留档。

该整合仍是原型的附加档案，正式数据层尚未接入。需要整体撤回时，可备份并移走附加档案、重建原页面，原始分组及照片不受影响；不能把移除附加档案理解为删除原照片。

## SQLite 与猫狗鸟档案

本机自动保存入口：`http://127.0.0.1:8767/`。页面显示“已保存到本机 SQLite”及版本号才代表数据库保存完成；JSON 导出继续保留为备份。原 `file:///.../review.html` 仍只写浏览器/导出文件，不直接连接数据库。

```powershell
& tmp/vision-lab/venv/Scripts/python.exe prototypes/vision-lab/scan_animals.py
& tmp/vision-lab/venv/Scripts/python.exe prototypes/vision-lab/import_vision.py
& tmp/vision-lab/venv/Scripts/python.exe prototypes/vision-lab/review_server.py
```

数据库是 `data/vision.db`，使用标准库 SQLite，无新增数据库服务依赖。首次导入原分组、已确认扩展人脸、动物观测及特征；重复导入保留数据库里的最新命名、动物归属与关注状态。需要导入后续 JSON 时使用管理页的导入按钮；旧导出缺少动物字段时会保留现有动物档案。

“猫狗鸟档案”里先纠正类别，再勾选同一只并命名。确认后直接归入该动物，可追加照片、合并同类档案、不关注与恢复、移出、撤销。多种动物可分别不关注，但不能直接归成同一个体。框错或不确定的检测在独立列表中保留恢复入口。猫狗可按已确认参考的相似度排序，鸟不使用猫狗个体模型。

检测复用已有 YOLOX 权重；COCO 类别中的 bird/cat/dog 分别是 14/15/16，依据[官方类别表](https://raw.githubusercontent.com/Megvii-BaseDetection/YOLOX/main/yolox/data/datasets/coco_classes.py)。此次仅补扫既有 521 张预览，新增 3 个鸟检测候选，未重读原图，未推断同一只鸟的身份。原先错误的猫狗分类也可以手动改为鸟。

本机服务仅监听回环地址，写入需同源、会话令牌与正确版本；多页面竞争时返回冲突，不自动覆盖。未保存/冲突修改仍可导出；浏览器会提示尚未保存。文件模式保持禁止网络连接，服务器模式仅允许同源连接，不上传照片到外部。

`vision_db.py` 提供 SQLite 存储及持久任务队列基础，主图库调度和接口尚未挂接。任务优先领取、租约回收和旧工作单元拒绝完成已测试，前台看图自动让位、完整扫描工作单元和主图库搜索属于下一批工作。

检查：`tests/test_vision_db.py`、`tests/test_vision_review_http.py`、原型单元检查和 `test_review.mjs`。HTTP 检查只连接本机，需要运行环境允许回环连接。真实照片不用于模型可见的截图验收。

### 按候选组批量整理动物

动物页接入已有 base 模型的保守候选组（缺少时采用 small）；剩余检测、鸟类、手动拆出的照片和类别被纠正的检测独立成组。已归档的观测不会再次进入候选。当前 97 条未归档检测组成 82 组，其中 7 组包含多张照片。

默认没有关注档案时直接显示候选分组。每组显示独立卡片和最多 3 张预览，支持展开整组、拆出错误项、命名本组；也可勾选多个组一次命名归档。候选列表先展示 24 组，可展开更多并保留勾选。命名后的档案内按同类推荐最多 8 组，也可手动将未推荐组和已有档案加入勾选，一次确认合并。

独立“宠物匹配”入口和逐对打分页面已移除；相似度仅用于档案页推荐排序。鸟类手动选组，不套用猫狗特征。候选组编号只是展示标签，实际组 ID 稳定，防止归档后编号变化影响选择。

整批归档、批量合并、忽略、拆分、类别纠正均可撤销并通过原 SQLite 快照保存。忽略机器候选组时以 `confirmed:false` 保存，其成员不会被写为已确认个体；恢复后返回候选分组。原有已命名动物档案及人物记录保留。

## 重现

在项目根目录使用 PowerShell。当前机器已创建隔离环境并下载校验所有权重。

```powershell
python -m venv --system-site-packages tmp/vision-lab/venv
& tmp/vision-lab/venv/Scripts/python.exe -m pip install -r prototypes/vision-lab/requirements.txt
& tmp/vision-lab/venv/Scripts/python.exe prototypes/vision-lab/prepare.py
& tmp/vision-lab/venv/Scripts/python.exe prototypes/vision-lab/run.py --screen 480 --faces 180 --out tmp/vision-lab/new-run
```

环境依赖现有的 Pillow、pillow-heif、numpy；本次 numpy 2.1.2，OpenCV 5.0.0，ONNX Runtime 1.31.1。完整冻结权重来源、版本和 SHA256 见 `models.json`。下载模型约 458 MiB。运行时再次校验权重。

`--sample-list` 可读取指定照片的私有清单；格式为 `samples.private.json`。仅接受配置图库内的路径。此次月份清单来自生产目录的只读查询；分析不依赖生产服务。

```powershell
& tmp/vision-lab/venv/Scripts/python.exe prototypes/vision-lab/combine.py tmp/vision-lab/feb2025 tmp/vision-lab/run-001 --out tmp/vision-lab/combined
& tmp/vision-lab/venv/Scripts/python.exe prototypes/vision-lab/evaluate.py --run tmp/vision-lab/combined --labels PATH_TO_EXPORTED_JSON
& tmp/vision-lab/venv/Scripts/python.exe -m unittest discover -s prototypes/vision-lab -p test_prototype.py
node prototypes/vision-lab/test_review.mjs
```

合并时优先保留第一批的重复照片，保留特征与观测 ID 对应关系，重新生成跨批匹配。原始分批耗时相加展示，不表示合并程序耗时。后续重新跑完任一批时，应重新合并并检查是否还能使用旧标注；核对文件的样本签名可防止错配。

## 算法与边界

- YuNet 检脸，SFace 提取特征；完整连接式保守分组，阈值 0.55。同图人脸保持分开，避免多人照误合并。
- YOLOX Nano 检测猫/狗，分数门槛 0.40，类别内 NMS 0.45；最小框 36 像素。它会漏检或框错，检测分数不是准确率。
- 宠物完整身体裁剪缩为 224，分别跑专门宠物 small/base 特征模型。两者均使用 RGB/ImageNet 归一化，余弦相似度比较。当前最多每批 60 个候选进入特征比较。
- 匹配只比较同物种、不同原图；近重复候选降序排在非近重复之后，并单独提示。dHash 只提供粗略提醒，不是严格去重。
- 宠物聚类阈值 0.80 是实验参数，尚未校准。Top-3 推荐也不是身份结论。固定衣着、背景、连拍、同毛色等偏差都需要人工核对。
- `evaluate.py` 只统计已核对候选，不给未核对数据推断准确率，也不把候选正确比例当作整体召回率。比较模型需要共同、跨日期且包含难负例的真值集合。
- 每个模型会话使用 2 个 CPU 线程，顺序加载宠物模型，原图读取后限制分析分辨率。尚未接入任务队列、前台优先级、增量缓存或 GPU；独立原型不证明正式服务的调度表现。
- 原图只读；运行前后比较大小和修改时间。本次检查不等同于逐文件内容哈希审计。核对页内的图像、特征和人工别名仍是私人数据。

尚未进入正式图库的人物命名、宠物命名或自动检索。先完成真实样本核对，再确定模型和阈值。


## 正式后台接入

主服务已接通 `vision_db.py`、`vision_service.py`、`vision_worker.py` 和主图库索引。数据库版本 2；模型在 `data/vision/models`，正式识别环境 `.venv-vision`，依赖固定于 `requirements-vision.txt`。主服务自动监督工作进程；可设 `SEEKLIVE_VISION_PYTHON` 指定解释器或 `SEEKLIVE_VISION=0` 禁用。

初次从本机原型迁移可运行 `tools/vision_bootstrap.py`：它校验模型、迁移已算好的人脸缓存并补齐源版本，保留数据库中的最新人工记录。初始化工作环境应复用主图库的 Pillow/pillow-heif，安装可选识别依赖。

管理页已接通正式全库统计、人物/动物按档案分页、候选批量确认/排除与撤销。新增候选与人工已归档独立计数；原始 521 张试验样本保留在折叠说明和初始样本区。主图库相册入口仍属下一批。

正式后台每分钟同步当前图库索引；手动核对新增会触发主服务原有增量索引。暂停与任务状态持久化，文件/模型/参考变化分开失效；人工确认不被覆盖。详情见项目根目录《正式接入_数据与后台任务_2026-11-01.md》。


正式展示接口：`GET /api/vision/summary`、`GET /api/vision/photos?entity=...&mode=pending|confirmed&offset=0&limit=24`、`PUT /api/vision/decision`。写入沿用本机令牌、Origin、乐观版本检查；确认和撤销在同一 SQLite 事务内完成。图片接口 `GET /api/vision/image?id=...&view=crop|photo` 接受本机令牌或仅用于图片的 HttpOnly 会话；无路径参数。离线 HTML 保持原型快照，不访问数据库。
