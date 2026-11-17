# 河口直播资料留存 V1

Python 3.11 标准库工程，为河口观测研究组保留完整媒体段、固定回放和原始证据。

资料员登记来源，刷新完整列表并补采；研究员固定当前成功媒体，重新核账后读取或导出。列表解释使用响应最终地址，支持相邻范围继承及 discontinuity 身份。范围必须由正确的 206 和 Content-Range 证明，Digest sha-256 存在时必须匹配。失败段留待下次显式 collect，成功段不重复请求。当前清单按 discontinuity、media 序号排序并保留此前就绪项，旧列表中未采集的段不会自动补回。

网络列表支持 EXTM3U、MEDIA-SEQUENCE、DISCONTINUITY-SEQUENCE、EXTINF、BYTERANGE、DISCONTINUITY、VERSION、TARGETDURATION、ENDLIST 和普通注释。EXTINF 是正有限十进制秒；不要求 ENDLIST 或末尾换行。空列表合法。PART、SKIP、MAP、加密、主列表及其他未支持声明会明确拒绝。系统不解码媒体，不执行自动重试，运行边界是单进程顺序调用。

## 运行

从项目根目录执行：

```sh
python3.11 -B verify.py
python3.11 -B demo.py
```

verify 运行全部 unittest 并传播失败退出码。demo 启动有限本地 HTTP 来源，在临时目录实际执行登记、刷新、解释、规划、范围下载、发布、固定、重开、核账和导出；结束清理临时资料。HTTP 测试端口动态分配，测试及 demo 不访问外部服务。

样例 fixtures/day.json 的 ABCDEFGHI 分成 ABCD、EFGHI，另有 JKLMNO，时长为 1/8、3/8、1/2 秒。第二范围首次短读时先交付第一、第三段，总长 5/8；重试后新成果总长 1，开始位置为 0、1/8、1/2，原成果不变。断言完成后输出 POINTWISE_SCENARIO JSON。另有明确标注的领域夹具测试 1/3、2/7 秒；这不是网络 HLS 语法。

## 公开接口

```python
from liverecord.archive.database import Database
from liverecord.source.registry import Sources
from liverecord.acquisition.collector import Collector
from liverecord.replay.snapshots import Archive
from liverecord.replay.reader import Replay
from liverecord.verification.service import Verifier

db = Database('observation-data')
try:
    Sources(db).register('station', 'https://example.org/live.m3u8')
    revision_id, revision = Collector(db).refresh('station')
    receipt = Collector(db).collect('station')
    capture = Archive(db).freeze('station', 'research-day-1')
    report = Verifier(db).check('research-day-1')
    view = Replay(db).read('research-day-1')
    # Replay(db).export('research-day-1', 'new-delivery-directory')
finally:
    db.close()
```

例中 URL 由调用方替换为自己的来源；verify/demo 只使用本地夹具。Database 根目录保存 archive.sqlite 和 objects。初始化可重复，关闭重开保留来源、修订、就绪媒体、固定成果及核账记录；不支持的模式版本拒绝，用户资料不会由样例覆盖或自动重置。

来源标识非空且地址为绝对 HTTP/HTTPS URL；同标识同地址幂等，换地址抛 SourceConflict。完整段身份为来源、discontinuity 序号、media 序号。刷新先整次校验，再接纳修订；非法刷新抛 PlaylistError 或 DeclarationConflict，不改变当前候选。传输错误为 FetchError。

collect 返回 CollectionReceipt.items，各 ItemReceipt 含 identity、success、detail。每次计划固定一个修订，只补缺失项；单段失败不阻塞其他完整段。Publisher.publish_ready 是真实归档入口，接受 validate_media 产生的回执，并独立重验。对象可按摘要共享，来源绑定各自独立。同身份同声明同字节幂等并保留首次证据，异内容抛 ArchiveConflict。

freeze 复制完整段选择、来源绑定及精确 Fraction 时间。同 capture_id、同来源、同输入幂等；输入变化抛 CaptureConflict。空来源可固定；未登记来源拒绝。对象准备、就绪发布、固定发布失败不暴露半成果，固定身份可重试。可选 checkpoint 回调用于模拟外部 I/O 失败，不改变正常业务逻辑。

ReplayView.items 按固定顺序返回 ReplayItem：body 是原媒体字节，start/end 为 Fraction 秒，evidence 是固定 SourceBinding，playlist_body 和 playlist_evidence 为原列表证据。total 是精确总长，缺号不补虚构时长。后续采集不改变固定成果。

Verifier 每次重读正文并核对长度、SHA-256、声明、原列表、响应、身份顺序和精确时间，保存本次诊断。摘要缺省时仅证明持久内容与采集时计算摘要一致，不宣称上游真实性。缺失成果报告不合格；Replay 缺失抛 CaptureNotFound，损坏抛 IntegrityError。导出产生 manifest.json、逐项 .media、.playlist、.evidence.json；目标文件、目录或符号链接已存在均抛 ExportConflict，失败清理本次暂存。

## 代码职责与回归

source 管登记、网络和证据；timeline 管完整段声明及原子修订；acquisition 管缺失规划与校验；archive 管对象和就绪发布；replay 管固定时间及交付；verification 独立重验媒体、来源和时间。24 个模块全部经公开流程或交付核账调用，采用 namespace 包。

tests/test_sources_timeline.py 覆盖 N1—N3：登记持久化、相对地址、范围继承、跨 discontinuity 身份、精确十进制、冲突与整次拒绝。
tests/test_collection_archive.py 覆盖 N4—N5：范围和摘要失败重试、发布回滚、判重、内容冲突、共享对象来源隔离及滑动列表。
tests/test_replay_verification.py 覆盖 N6—N8：固定回滚与重投、精确分数、空成果、重开历史、正文/来源/时间损坏、导出保全。
