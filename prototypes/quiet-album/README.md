# 家庭影像馆 · UI 原型

打开 `dist/compare.html` 查看桌面／手机新旧对比，打开 `dist/index.html` 体验新原型。相对路径已本地化，不需要安装依赖或连接照片服务。

视觉方向：暖白、松绿、细腻的时间层级与照片优先的布局。桌面采用侧栏，手机采用底部导航。封面卡片与非等宽照片排布用于探索回忆浏览的视觉节奏，后续落地需结合真实照片比例、密度控制、滚动锚点和窗口化渲染验证。

可交互：照片灯箱与前后切换、收藏、搜索、批量选择、年／月／日视图、相册入口、设置抽屉。分享、最近删除等管理动作仅展示提示，不执行真实操作；LIVE 是视觉标记，没有连接实况视频。原型状态保存在页面内存，刷新即恢复。所有照片／日期／相册分组均为示例。

`dist/before.html` 是使用相同示例照片运行当前正式前端得到的冻结页面，仅作布局快照；交互请以新原型为准。`screenshots/` 及 `dist/previews/` 保存浏览器实际截图，没有用生成图片替代界面。

验证：桌面与 390px 手机浏览；灯箱切换、搜索“森林”得到 1 条、批量选择计数、设置抽屉与手机管理入口。手机页面内容宽 375px，视口 390px（预留滚动条），未发生横向溢出。正式 web/ 文件未修改。

素材为公开 Unsplash 风景照片，未读取真实家庭照片。来源照片标识：

- https://images.unsplash.com/photo-1650095598175-f4d731fd5330
- https://images.unsplash.com/photo-1628748971583-72abdc44d708
- https://images.unsplash.com/photo-1740144359786-4d0375af41ba
- https://images.unsplash.com/photo-1765912436895-e2251c445f77
- https://images.unsplash.com/photo-1476514525535-07fb3b4ae5f1
- https://images.unsplash.com/photo-1441974231531-c6227db76b6e
- https://images.unsplash.com/photo-1500530855697-b586d89ba3ee
- https://images.unsplash.com/photo-1493246507139-91e8fad9978e
