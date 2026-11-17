# 主列表选择采集

现有 README.md、运行清单和全部原测试描述完整段 V1，均保留。本文件只增加主列表选择这一条链路。正常运行仍为单进程顺序调用，不增加网络协议、并发、掉电恢复、自适应播放或跨版本时间对齐。V1 的直接媒体来源、精确时间、逐段失败重试、固定发布与导出保全规则继续成立。

## 新入口及选择规则

Collector.refresh_selected(source_id, max_bandwidth) 使用已登记来源，返回 (selection, revision_id, revision)。revision 保持媒体 PlaylistRevision 的 body、evidence、declarations 访问能力；revision_id 是可持久读取的该媒体修订身份。max_bandwidth 必须为正整数（bool 不算），否则抛 InputError。原 refresh 继续只接受直接媒体列表，不能悄悄替调用方选择版本；直接媒体来源继续使用原 collect、freeze、read、export。

新入口的登记地址必须返回一层主列表。按 V1 方式严格解码 UTF-8、去掉行首尾空白、忽略空行；首个非空行须为 #EXTM3U。其后仅允许普通注释以及 #EXT-X-STREAM-INF:BANDWIDTH=N 和对应 URI，N 为仅含 ASCII 数字的正十进制整数；不允许其他属性或 EXT 声明。每个 STREAM-INF 后下一个非空、非普通注释行须为 URI；前一项缺 URI 时再遇 STREAM-INF、悬空声明、孤立 URI、媒体声明混入及空主列表均拒绝。将 URI 相对主列表响应的最终地址解析为合法绝对 HTTP/HTTPS URL；按解析所得字符串逐字比较，重复 URI 拒绝，不额外做别名归并。查询串属于 URI。选取 BANDWIDTH 不超过上限的最高值，同值按绝对 URI 的 Python 字符串字典序取首项；没有候选抛 PlaylistError。只请求选中媒体列表；它必须满足 V1 媒体列表语法，嵌套主列表拒绝。媒体段相对媒体响应的最终地址解析。

主列表与媒体列表的响应均须为无范围的完整 200，原字节长度和 SHA-256 与响应证据一致。传输失败抛 FetchError；语法、选择和列表证据非法抛 PlaylistError；同版本历史段声明冲突保留 DeclarationConflict。刷新须共同接纳主列表原字节及响应、策略、选择结果和媒体修订；任一步失败保留原选择及候选。不得在媒体列表尚未通过全部检查时启用新选择；失败不得新增历史声明。无需保留被拒刷新用于用户查询。

版本身份由登记来源及解析后的媒体请求 URI 确定，重定向不合并版本；同版本跨刷新复用段身份，异版本相同序号互不冲突。版本可以采用内部子来源或扩展身份表示，不规定内部表结构；对用户的 Capture.source_id、source_url 仍为原登记来源。collect 仅处理当前选择所固定媒体修订的缺失项；freeze 仅固定该版本累计就绪段，切回原版本可复用旧段，不拼接其他版本。退出当前媒体列表而尚未就绪的旧段仍不自动补采。新入口首次成功前，原未采集来源的空成果行为保留。新入口只用于登记地址是主列表的来源，不要求同一登记地址在直接媒体与主列表模式间切换。

## 可观察证据

selection 为具有以下九个字段的 dict：
- selection_id：非空字符串，完整选择回执的确定性内容身份。相同证据、策略、选择及媒体修订应幂等；任一内容改变应产生不同身份。
- source_id、max_bandwidth：本次登记来源与调用上限。
- bandwidth、media_url：获选带宽及解析后的媒体请求 URI。
- revision_id：本次接纳的媒体修订身份，与返回的 revision_id 相同。
- master_body：原始主列表 bytes。
- master_evidence：现有 ResponseEvidence，保留请求地址及最终地址。
- source_url：登记地址。

固定成果同时保存本次选择回执及各段首次就绪时的选择链；read 和 export 交付这些证据，Verifier 重读原证据、重算选择并核对媒体声明归属，不访问网络。选择链为上述回执及它绑定的媒体修订原字节和响应，后两项沿 V1 的修订留存、ReplayItem.playlist_body 和 playlist_evidence 交付。不是仅存一个可重新查询的 URL。同一份媒体字节及响应可能在不同主列表、策略下被重复接纳；修订表示可自行选择，但不得把后来选择覆写为旧段的首次来源。第一次媒体发布失败后重试成功的段，绑定重试所用当前选择。

Archive.freeze 返回的 Capture 及 ReplayView.capture 增加 selection；ReplayItem 增加 selection，分别是固定时选择和该段首次就绪时选择。直接媒体 V1 成果这两处均为 None，包括升级前已有成果。selection 中的媒体修订必须可以离线加载；固定时即使没有就绪段，也须保存并复核本次选择。Capture.to_dict、固定输入指纹和重投判定须覆盖选择证据；同 capture_id 在选择改变后应抛 CaptureConflict，即使媒体列表和就绪段相同。旧成果的原编码和读写行为继续可读，不要求改写历史数据。

export 保留全部 V1 文件，对选择式成果增加 selection.json 和每项 000000.selection.json（序号沿用 V1 六位前缀）。内容为对应 selection dict，master_body 转为 UTF-8 字符串，master_evidence 转为 to_dict()，其余字段照原值；空选择式成果仍导出 selection.json。直接媒体成果不增加这些文件。两类选择证据及媒体证据必须共同受离线核账保护：保存的主列表、上限、选择结果或首次链发生不一致时，Verifier 报不合格，Replay.read/export 拒绝交付。核账不能用当前选择代替固定选择，也不能信任先前的成功报告。沿用 V1 的失败诊断和目标已存在时不覆盖规则。

## 已提供材料与手算情境

selected_fixture.py 仅提供有限本地 HTTP 来源，不含主列表解析、选择、版本映射或归档实现。serving() 上下文返回具有 url、base、master、routes、counts、fail_once 的来源；counts 的键为 (请求路径, Range)，routes 的值为 (状态, bytes, 响应头元组)，master 可替换。固定响应日期使相同媒体响应证据可以重复出现。--check-data 核对静态材料；--http-check 用真实 HttpClient 检查重定向、相对媒体地址及受控 503。这两个检查不验证新功能。

默认主列表从 /entry 重定向到 /catalog/master.m3u8，带宽 100 的 A.m3u8 与 200 的 B.m3u8 分别再重定向到 /media/A/list.m3u8、/media/B/list.m3u8。二者都是 (discontinuity=0, media=40)，时长 1/2 秒，正文分别为 A 与 B。上限 150 选 A，上限 250 选 B；分别采集和固定后只能得到各自一个段。切回 A 不再次请求其就绪正文，旧 A 成果及首次链不变。将两个请求 URI 重定向到同一最终媒体列表，也仍是两个版本，各自具有段身份。

若第一次选 A 后未采集，将主列表 A 的带宽改成 90，再以 150 选择后采集：段的首次选择须为第二次回执。再次仅改变主列表普通注释并选择 A 后固定：当前选择为第三次回执，首次选择仍为第二次，不能取第一次或第三次冒充。媒体原字节及响应可以完全相同，不能靠正文变化区分这三次选择。

同带宽时 A 的绝对 URI 在 B 前，因此选 A。主列表加入解析后重复的 ./A.m3u8、带未知属性的 STREAM-INF、嵌套主列表，或上限低于全部带宽时均拒绝；选中媒体的 503、截断或非法列表也须保持此前当前选择和候选。单段短读仍按 V1 只影响该段，不回退已经成功接纳的选择。固定成果重开后，停止来源服务仍须读取、导出和重新复核；固定发布失败可用 V1 checkpoint 注入并重试，不得占住新 capture_id 或损坏旧成果。

## 交付与运行

实现这条选择、采集、固定交付和独立复核链，保留全部原测试字节及旧接口。补充选择链状态归属的简短说明，以及覆盖上述公开边界的业务回归。selected_check.py 是已提供的联合场景入口，须在实现后通过；它在 V1 缺少 refresh_selected 时失败属于待开发能力，不是原有缺陷。该场景检查 A→B→A、同媒体多次选择、失败选择保全、固定失败重试、停服重开及真实导出，不替代其余公开边界的回归。

沿现有 .pointwise-runtime.json 接通全部原回归和新联合核验，demo 展示真实版本切换、复用及固定证据，不只打印预设成功。清单仍采用现有 version=1、kind=cli、setup 为 argv 数组列表、verify/demo 各为 argv 数组的格式。原回归入口为 python3.11 -B verify.py；新增联合场景入口为 python3.11 -B selected_check.py，材料检查分别为 python3.11 -B selected_fixture.py --check-data 和 --http-check。

## 选择链状态归属

- 版本归属：登记来源不变；（登记来源，解析后的媒体请求 URI）唯一确定内部版本子来源，段身份、声明历史、就绪目录与当前修订都挂在子来源上。重定向不合并版本；异版本相同序号各自独立；同版本跨刷新复用段身份。
- 当前选择：current_selection 是登记来源唯一的选择指针。refresh_selected 只有在主列表证据、策略、选择结果与媒体修订全部通过检查后，才原子写入回执并移动指针；任一步失败，原选择、各版本候选修订与历史声明保持不变。
- 首次链：段首次就绪成功时在 ready_selection 登记当时选择，只插不改；freeze 把固定时选择身份写入 captures、把各段首次就绪选择身份钉入 capture_items。固定成果自包含，后续选择变化不回写旧段的首次来源。
- 核账归属：Capture.source_id、source_url 始终为登记来源；Verifier 只依据已保存的回执、主列表原字节与媒体修订重算选择并核对段归属，不访问网络，也不用当前选择替代固定选择。直接媒体 V1 成果两处选择均为 None。
