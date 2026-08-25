from __future__ import annotations

from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    Flowable,
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "output" / "pdf" / "creatoros-search-routing-guide.pdf"

INK = colors.HexColor("#101828")
MUTED = colors.HexColor("#667085")
LINE = colors.HexColor("#D9E0EA")
SOFT = colors.HexColor("#F5F7FA")
NAVY = colors.HexColor("#0B1220")
PURPLE = colors.HexColor("#7467E8")
PURPLE_SOFT = colors.HexColor("#EEEAFE")
CYAN = colors.HexColor("#2CA8B8")
CYAN_SOFT = colors.HexColor("#E6F7F8")
GREEN = colors.HexColor("#218B64")
GREEN_SOFT = colors.HexColor("#E8F6EF")
AMBER = colors.HexColor("#B87516")
AMBER_SOFT = colors.HexColor("#FFF4DD")
RED = colors.HexColor("#C54B53")
RED_SOFT = colors.HexColor("#FDECEE")
WHITE = colors.white


def register_fonts() -> None:
    pdfmetrics.registerFont(
        TTFont("YaHei", r"C:\Windows\Fonts\msyh.ttc", subfontIndex=0)
    )
    pdfmetrics.registerFont(
        TTFont("YaHei-Bold", r"C:\Windows\Fonts\msyhbd.ttc", subfontIndex=0)
    )
    pdfmetrics.registerFontFamily(
        "YaHei", normal="YaHei", bold="YaHei-Bold", italic="YaHei", boldItalic="YaHei-Bold"
    )


register_fonts()


def build_styles():
    styles = getSampleStyleSheet()
    styles.add(
        ParagraphStyle(
            "CNBody",
            fontName="YaHei",
            fontSize=9.2,
            leading=15,
            textColor=INK,
            spaceAfter=5,
            wordWrap="CJK",
        )
    )
    styles.add(
        ParagraphStyle(
            "CNBodySmall",
            parent=styles["CNBody"],
            fontSize=8,
            leading=12.5,
            textColor=MUTED,
        )
    )
    styles.add(
        ParagraphStyle(
            "H1CN",
            fontName="YaHei-Bold",
            fontSize=21,
            leading=28,
            textColor=NAVY,
            spaceAfter=8,
            wordWrap="CJK",
        )
    )
    styles.add(
        ParagraphStyle(
            "H2CN",
            fontName="YaHei-Bold",
            fontSize=12.5,
            leading=18,
            textColor=NAVY,
            spaceBefore=5,
            spaceAfter=6,
            wordWrap="CJK",
        )
    )
    styles.add(
        ParagraphStyle(
            "H3CN",
            fontName="YaHei-Bold",
            fontSize=9.5,
            leading=14,
            textColor=NAVY,
            spaceAfter=3,
            wordWrap="CJK",
        )
    )
    styles.add(
        ParagraphStyle(
            "BulletCN",
            parent=styles["CNBody"],
            leftIndent=11,
            firstLineIndent=-8,
            bulletIndent=1,
            bulletFontName="YaHei",
            bulletFontSize=8,
            spaceAfter=3,
        )
    )
    styles.add(
        ParagraphStyle(
            "CodeCN",
            fontName="YaHei",
            fontSize=7.7,
            leading=11.5,
            textColor=colors.HexColor("#24324A"),
            backColor=SOFT,
            borderColor=LINE,
            borderWidth=0.5,
            borderPadding=7,
            spaceBefore=4,
            spaceAfter=7,
            wordWrap="CJK",
        )
    )
    styles.add(
        ParagraphStyle(
            "QuoteCN",
            parent=styles["CNBody"],
            leftIndent=10,
            borderColor=PURPLE,
            borderWidth=2,
            borderPadding=(3, 0, 3, 8),
            textColor=colors.HexColor("#344054"),
            backColor=PURPLE_SOFT,
        )
    )
    styles.add(
        ParagraphStyle(
            "TableCN",
            parent=styles["CNBodySmall"],
            fontSize=7.4,
            leading=11.2,
            textColor=INK,
        )
    )
    styles.add(
        ParagraphStyle(
            "TableHeadCN",
            parent=styles["TableCN"],
            fontName="YaHei-Bold",
            textColor=WHITE,
            alignment=TA_LEFT,
        )
    )
    styles.add(
        ParagraphStyle(
            "QCN",
            fontName="YaHei-Bold",
            fontSize=9.3,
            leading=14,
            textColor=NAVY,
            spaceAfter=3,
            wordWrap="CJK",
        )
    )
    styles.add(
        ParagraphStyle(
            "ACN",
            parent=styles["CNBody"],
            fontSize=8.5,
            leading=13.5,
            textColor=colors.HexColor("#344054"),
            spaceAfter=8,
        )
    )
    return styles


S = build_styles()


def p(text: str, style: str = "CNBody") -> Paragraph:
    return Paragraph(text, S[style])


def bullet(text: str) -> Paragraph:
    return Paragraph(f"<bullet>•</bullet>{text}", S["BulletCN"])


def section(title: str, kicker: str | None = None):
    items = []
    if kicker:
        items.append(
            Paragraph(
                kicker.upper(),
                ParagraphStyle(
                    "Kicker",
                    fontName="YaHei-Bold",
                    fontSize=7.5,
                    leading=10,
                    textColor=PURPLE,
                    tracking=1.2,
                    spaceAfter=3,
                ),
            )
        )
    items.append(p(title, "H1CN"))
    items.append(Spacer(1, 1.5 * mm))
    return items


def callout(title: str, body: str, tone: str = "purple") -> Table:
    palette = {
        "purple": (PURPLE_SOFT, PURPLE),
        "green": (GREEN_SOFT, GREEN),
        "amber": (AMBER_SOFT, AMBER),
        "red": (RED_SOFT, RED),
        "cyan": (CYAN_SOFT, CYAN),
    }
    bg, accent = palette[tone]
    data = [[p(title, "H3CN")], [p(body, "CNBodySmall")]]
    table = Table(data, colWidths=[166 * mm], hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), bg),
                ("BOX", (0, 0), (-1, -1), 0.6, accent),
                ("LINEBEFORE", (0, 0), (0, -1), 3, accent),
                ("LEFTPADDING", (0, 0), (-1, -1), 9),
                ("RIGHTPADDING", (0, 0), (-1, -1), 9),
                ("TOPPADDING", (0, 0), (-1, 0), 7),
                ("BOTTOMPADDING", (0, -1), (-1, -1), 7),
            ]
        )
    )
    return table


def data_table(headers, rows, widths, font_size: float = 7.4) -> Table:
    head_style = S["TableHeadCN"]
    body_style = ParagraphStyle(
        f"TableBody{font_size}", parent=S["TableCN"], fontSize=font_size, leading=font_size * 1.5
    )
    data = [[Paragraph(str(cell), head_style) for cell in headers]]
    for row in rows:
        data.append([Paragraph(str(cell), body_style) for cell in row])
    table = Table(data, colWidths=widths, repeatRows=1, hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), NAVY),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [WHITE, SOFT]),
                ("GRID", (0, 0), (-1, -1), 0.35, LINE),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    return table


class Cover(Flowable):
    def __init__(self):
        super().__init__()
        self.width = 170 * mm
        self.height = 245 * mm

    def draw(self):
        c = self.canv
        c.saveState()
        c.setFillColor(NAVY)
        c.rect(-20 * mm, -20 * mm, 210 * mm, 297 * mm, fill=1, stroke=0)
        c.setFillColor(PURPLE)
        c.circle(160 * mm, 235 * mm, 39 * mm, fill=1, stroke=0)
        c.setFillColor(CYAN)
        c.circle(150 * mm, 225 * mm, 19 * mm, fill=1, stroke=0)
        c.setFillColor(colors.HexColor("#26324B"))
        c.circle(18 * mm, 14 * mm, 36 * mm, fill=1, stroke=0)

        c.setFont("YaHei-Bold", 10)
        c.setFillColor(CYAN_SOFT)
        c.drawString(0, 222 * mm, "CREATOROS  /  PROJECT FIELD GUIDE")
        c.setFont("YaHei-Bold", 28)
        c.setFillColor(WHITE)
        c.drawString(0, 185 * mm, "搜索与作者匹配")
        c.drawString(0, 168 * mm, "全链路学习手册")
        c.setFont("YaHei", 11)
        c.setFillColor(colors.HexColor("#C6CEDD"))
        c.drawString(0, 151 * mm, "从热榜候选、搜索增强到双通道路由与 LLM 重排")

        cards = [
            ("2", "知乎只读 Tool", PURPLE),
            ("7", "ready 作者画像", CYAN),
            ("83 / 37", "领域 / 视角原型", GREEN),
        ]
        x = 0
        for number, label, accent in cards:
            c.setFillColor(colors.HexColor("#151F33"))
            c.roundRect(x, 103 * mm, 50 * mm, 27 * mm, 3 * mm, fill=1, stroke=0)
            c.setFillColor(accent)
            c.setFont("YaHei-Bold", 16)
            c.drawString(x + 5 * mm, 117 * mm, number)
            c.setFillColor(colors.HexColor("#B7C0D1"))
            c.setFont("YaHei", 7.5)
            c.drawString(x + 5 * mm, 108 * mm, label)
            x += 56 * mm

        c.setFillColor(colors.HexColor("#D2D8E5"))
        c.setFont("YaHei", 9)
        c.drawString(0, 67 * mm, "用途：项目梳理  ·  方案评审  ·  面试复盘")
        c.setFillColor(colors.HexColor("#8E9AAF"))
        c.setFont("YaHei", 8)
        c.drawString(0, 24 * mm, "基于 CreatorOS main @ b4cab7a  |  2026-08-26")
        c.drawString(0, 15 * mm, "已实现与目标方案严格分层，不把路线图表述为完成项")
        c.restoreState()


class PipelineFlow(Flowable):
    def __init__(self):
        super().__init__()
        self.width = 166 * mm
        self.height = 112 * mm

    def draw(self):
        c = self.canv
        c.saveState()
        stages = [
            ("1", "定时触发", "计划", MUTED, SOFT),
            ("2", "热榜候选", "已实现", GREEN, GREEN_SOFT),
            ("3", "搜索增强", "已实现", GREEN, GREEN_SOFT),
            ("4", "热点拆解", "待实现", AMBER, AMBER_SOFT),
            ("5", "画像读取", "已实现", GREEN, GREEN_SOFT),
            ("6", "双通道召回", "下一步", PURPLE, PURPLE_SOFT),
            ("7", "LLM 重排", "待实现", AMBER, AMBER_SOFT),
            ("8", "分身生成", "接口已有", CYAN, CYAN_SOFT),
            ("9", "评审与发布", "计划", MUTED, SOFT),
        ]
        box_w, box_h = 50 * mm, 22 * mm
        coords = []
        for idx in range(9):
            row = idx // 3
            col = idx % 3 if row % 2 == 0 else 2 - idx % 3
            coords.append((col * 58 * mm, self.height - (row + 1) * 34 * mm))

        for idx, (num, title, status, accent, bg) in enumerate(stages):
            x, y = coords[idx]
            c.setFillColor(bg)
            c.setStrokeColor(accent)
            c.setLineWidth(0.8)
            c.roundRect(x, y, box_w, box_h, 3 * mm, fill=1, stroke=1)
            c.setFillColor(accent)
            c.setFont("YaHei-Bold", 8)
            c.drawString(x + 4 * mm, y + 14 * mm, f"{num}  {title}")
            c.setFont("YaHei", 6.7)
            c.drawString(x + 4 * mm, y + 6 * mm, status)
            if idx < len(stages) - 1:
                nx, ny = coords[idx + 1]
                if abs(ny - y) < 1:
                    start_x = x + box_w if nx > x else x
                    end_x = nx if nx > x else nx + box_w
                    line_y = y + box_h / 2
                    c.setStrokeColor(LINE)
                    c.line(start_x, line_y, end_x, line_y)
                else:
                    turn_x = x + box_w / 2
                    c.setStrokeColor(LINE)
                    c.line(turn_x, y, turn_x, ny + box_h)
        c.restoreState()


def on_page(canvas, doc):
    if doc.page == 1:
        return
    canvas.saveState()
    canvas.setStrokeColor(LINE)
    canvas.setLineWidth(0.5)
    canvas.line(22 * mm, 282 * mm, 188 * mm, 282 * mm)
    canvas.setFont("YaHei-Bold", 7.2)
    canvas.setFillColor(PURPLE)
    canvas.drawString(22 * mm, 286 * mm, "CREATOROS  /  SEARCH & ROUTING")
    canvas.setFont("YaHei", 7.2)
    canvas.setFillColor(MUTED)
    canvas.drawRightString(188 * mm, 286 * mm, "学习手册  ·  2026-08-26")
    canvas.line(22 * mm, 14 * mm, 188 * mm, 14 * mm)
    canvas.drawString(22 * mm, 8.5 * mm, "当前实现与目标设计分开标注")
    canvas.drawRightString(188 * mm, 8.5 * mm, f"{doc.page - 1:02d}")
    canvas.restoreState()


def add_qa(story, question: str, answer: str):
    story.append(KeepTogether([p(question, "QCN"), p(answer, "ACN")]))


def build_story():
    story = [Cover(), PageBreak()]

    story += section("一页看懂：现在已经走到哪里", "01  End-to-end map")
    story.append(
        p(
            "CreatorOS 的目标不是让一个 LLM 凭感觉包办全部工作，而是把稳定的数据获取、可解释的向量召回和需要语义判断的 LLM 重排组合成一条可恢复链路。当前已经完成两端连接器，中间的 Routing 正是下一阶段。"
        )
    )
    story.append(Spacer(1, 2 * mm))
    story.append(PipelineFlow())
    story.append(Spacer(1, 2 * mm))
    story.append(
        callout(
            "最关键的现状判断",
            "已经能拿到热榜、搜索结果和作者画像，不等于已经完成作者匹配。当前缺口是：把热点拆成领域与视角文本，建立 CreatorOS 自己的画像检索索引，计算双通道得分，再让 LLM 对少量候选做最终判断。",
            "amber",
        )
    )
    story.append(Spacer(1, 4 * mm))
    story.append(p("两条路径不要混在一起", "H2CN"))
    story.append(
        data_table(
            ["路径", "什么时候运行", "输入", "输出"],
            [
                ["离线画像同步", "作者新增或 corpus_version 变化", "PersonClone routing profile", "CreatorOS 路由原型索引"],
                ["在线热点路由", "每日计划或用户发起", "热榜 + 搜索增强 + 路由索引", "每位作者的 Top-N 选题"],
            ],
            [30 * mm, 40 * mm, 47 * mm, 49 * mm],
        )
    )
    story.append(PageBreak())

    story += section("热榜和搜索不是重复工作", "02  Discovery")
    story.append(
        data_table(
            ["能力", "回答的问题", "当前对象", "核心字段"],
            [
                ["热榜", "现在什么受到关注？", "HotListSnapshot / HotTopic", "rank, title, url, summary, thumbnail_url"],
                ["搜索", "这个热点到底发生了什么，已经有哪些内容和观点？", "ZhihuSearchSnapshot / ZhihuSearchItem", "content_text, author, votes, comments, authority, ranking_score"],
            ],
            [23 * mm, 43 * mm, 48 * mm, 52 * mm],
        )
    )
    story.append(Spacer(1, 5 * mm))
    story.append(p("为什么选中热榜后还要搜索？", "H2CN"))
    story.append(bullet("热榜只给候选和热度信号，标题可能过短、含糊或缺少事件背景。"))
    story.append(bullet("搜索补齐相关问题、回答、文章、作者、互动量与原文链接，用来判断话题是否真实、是否已经饱和、有哪些冲突角度。"))
    story.append(bullet("搜索发生在作者匹配之前。它服务于热点理解，不负责直接挑作者。"))
    story.append(Spacer(1, 4 * mm))
    story.append(
        p(
            "示例：热榜标题只有“某平台发布新规则”。搜索阶段会生成若干查询词，找出规则原文、受影响人群、支持与反对观点；随后热点拆解才形成“平台治理”领域文本和“效率与公平冲突”视角文本。",
            "QuoteCN",
        )
    )
    story.append(Spacer(1, 5 * mm))
    story.append(p("当前已实现", "H2CN"))
    story.append(bullet("get_zhihu_hot_list(limit)：官方只读 GET，limit 为 1-30。"))
    story.append(bullet("search_zhihu(query, count)：官方只读 GET，count 为 1-10。"))
    story.append(bullet("结果先映射为内部不可变 dataclass，再投影给 ToolResult；密钥不进入消息、日志或仓库。"))
    story.append(
        callout(
            "边界提醒",
            "ContentText 可能很长，且正文内链接不一定可靠。事实溯源优先使用搜索条目的顶层 url；长结果由现有 ToolResult 投影控制上下文大小，完整原文仍留在 Session。",
            "cyan",
        )
    )
    story.append(PageBreak())

    story += section("把热点拆成“写什么”和“怎么看”", "03  Hotspot brief")
    story.append(p("下一步需要一个结构化 HotspotBrief。它不是最终文章，也不是作者选择结果，而是搜索资料到路由算法之间的稳定合同。"))
    story.append(
        p(
            "hotspot_id: 稳定去重 ID<br/>"
            "title / source_url: 事件标题与主来源<br/>"
            "hotspot_domain_text: 主题领域、对象、行业与关键实体<br/>"
            "hotspot_perspective_text: 可切入的价值冲突、问题意识与观察角度<br/>"
            "evidence_refs: 支撑拆解的搜索条目 ID / URL<br/>"
            "freshness / risk_flags: 时效性与事实、平台、品牌风险",
            "CodeCN",
        )
    )
    story.append(p("为什么必须拆成两个文本？", "H2CN"))
    story.append(
        data_table(
            ["文本", "匹配对象", "解决的问题", "错误理解"],
            [
                ["domain_text", "domain prototypes", "作者历史上是否覆盖过这个主题", "写过某领域不等于拥有某人格"],
                ["perspective_text", "perspective prototypes", "作者惯常的价值关注与推理方式是否适合切入", "没写过该领域不等于不能跨域"],
            ],
            [29 * mm, 37 * mm, 54 * mm, 46 * mm],
        )
    )
    story.append(Spacer(1, 5 * mm))
    story.append(p("LLM 在这里做什么？", "H2CN"))
    story.append(bullet("读取热榜标题与有限的搜索证据，输出受 Pydantic 约束的 HotspotBrief。"))
    story.append(bullet("只做理解与归纳，不凭空补事实；每个关键结论必须能回指 evidence_refs。"))
    story.append(bullet("相同热点可缓存拆解结果，避免每位作者重复调用模型。"))
    story.append(
        callout(
            "面试表达",
            "我没有直接把热榜标题拿去和作者向量算相似度，因为短标题的信息密度太低。先用搜索证据构造结构化热点表示，再做路由，召回稳定性和可解释性都会更好。",
            "purple",
        )
    )
    story.append(PageBreak())

    story += section("PersonClone 提供画像，CreatorOS 负责路由", "04  Profile boundary")
    story.append(p("唯一正式入口是 GET /api/personas/{author}/routing-profile。CreatorOS 复用登录 Cookie，只消费 API 响应，不读取 PersonClone 本地 JSON、原始语料或 Qdrant。"))
    story.append(
        data_table(
            ["画像类型", "表示什么", "主要字段", "如何参与路由"],
            [
                ["domain_prototypes", "作者过去写过什么", "label, description, retrieval_text, representative evidence", "匹配 hotspot_domain_text"],
                ["perspective_prototypes", "作者通常如何看问题", "values, trigger_cues, reasoning_pattern, boundaries", "匹配 hotspot_perspective_text"],
            ],
            [33 * mm, 37 * mm, 55 * mm, 41 * mm],
        )
    )
    story.append(Spacer(1, 5 * mm))
    story.append(p("真实联调结果", "H2CN"))
    story.append(
        data_table(
            ["作者", "领域", "视角", "状态"],
            [
                ["22-85-32-51", "2", "6", "ready"],
                ["an-ling-91", "3", "3", "ready"],
                ["ban-ma-ban-ma-30-2", "17", "5", "ready"],
                ["lu-shi-han-89", "12", "6", "ready"],
                ["mr-dang-77", "5", "5", "ready"],
                ["superpeople-wudi", "34", "6", "ready"],
                ["wu-ren-jun-28", "10", "6", "ready"],
            ],
            [63 * mm, 30 * mm, 30 * mm, 43 * mm],
        )
    )
    story.append(Spacer(1, 4 * mm))
    story.append(bullet("合计 83 个领域原型、37 个视角原型；统一声明 BAAI/bge-m3、1024 维。"))
    story.append(bullet("corpus_version 是缓存失效键；版本变化后必须刷新该作者在 CreatorOS 的路由索引。"))
    story.append(bullet("ready 使用双通道；domain_ready 只用领域；perspective_pending 不得伪造视角；404 标记暂不可匹配。"))
    story.append(
        callout(
            "为什么 get_routing_profile 不是 LLM Tool？",
            "这份画像是路由服务的内部输入，体积大且结构稳定。由 Python 代码获取、校验、缓存和检索更可控；LLM 只看到 Top-K 候选的关键证据，而不是每轮吞下所有画像。",
            "green",
        )
    )
    story.append(PageBreak())

    story += section("向量从哪里来：CreatorOS 自建路由索引", "05  Routing index")
    story.append(p("当前画像 API 返回可用于检索的文本和不透明 vector_ref，但不返回原始 float 向量；CreatorOS 又被明确禁止直连 PersonClone 的 Qdrant。因此不能拿 point_id 偷读底层存储。"))
    story.append(
        data_table(
            ["方案", "优点", "代价", "判断"],
            [
                ["CreatorOS 对 API 返回的 retrieval_text 重新 embedding，并写入自己的 Qdrant", "边界清晰、可批量检索、CreatorOS 自主控制召回与缓存", "有一次重复向量计算，需要校验 embedding_model", "当前推荐"],
                ["PersonClone 新增正式 vector search API", "不重复 embedding，复用原向量库", "路由召回依赖远端服务，接口和过滤能力需要扩展", "未来可选"],
            ],
            [42 * mm, 50 * mm, 48 * mm, 26 * mm],
        )
    )
    story.append(Spacer(1, 5 * mm))
    story.append(p("离线同步算法", "H2CN"))
    story.append(bullet("GET 作者画像，Pydantic 校验状态、模型、维度与 corpus_version。"))
    story.append(bullet("把每个 domain / perspective prototype 转为 RoutePrototypeDoc；保留 author_id、prototype_id、type、confidence 和 evidence IDs。"))
    story.append(bullet("对 retrieval_text 批量调用 BGE-M3，写入 CreatorOS 自有 collection；同一作者版本变化时原子替换旧版本。"))
    story.append(bullet("索引里保存的是 PersonClone 正式 API 暴露的路由文本，不读取原始作者全文。"))
    story.append(Spacer(1, 4 * mm))
    story.append(
        p(
            "RoutingIndexKey = (author_id, prototype_id, prototype_type, corpus_version)<br/>"
            "Vector model = profile.embedding_model<br/>"
            "Payload = label + confidence + representative evidence IDs + source version",
            "CodeCN",
        )
    )
    story.append(
        callout(
            "模型一致性",
            "只有热点向量与画像向量使用相同模型、维度和归一化方式，cosine similarity 才可比较。发现模型或维度不一致应拒绝路由并重建索引，而不是静默计算。",
            "red",
        )
    )
    story.append(PageBreak())

    story += section("双通道打分：先召回，再按作者聚合", "06  Scoring")
    story.append(p("对每个热点分别生成 domain vector 与 perspective vector，在两个 prototype_type 子空间中检索。每个作者可能有很多原型，但只保留最匹配的领域原型和视角原型，避免平均向量稀释细分专长。"))
    story.append(
        p(
            "D(a,h) = max cosine(h_domain, domain_proto[a,i])<br/>"
            "P(a,h) = max cosine(h_perspective, perspective_proto[a,j]) × confidence[a,j]<br/>"
            "RouteScore(a,h) = w_domain × D(a,h) + w_perspective × P(a,h)",
            "CodeCN",
        )
    )
    story.append(p("为什么用 Max Similarity？", "H2CN"))
    story.append(bullet("作者可能覆盖金融、职场、关系等多个簇；把全部文章平均成一个向量会得到“什么都像一点、什么都不够像”的模糊中心。"))
    story.append(bullet("Max 会保留真正命中的细分原型，同时返回 winning prototype 作为可解释证据。"))
    story.append(bullet("若担心偶然命中，可增加最低 confidence、最低文档数或 Top-2 一致性，而不是退回单一平均向量。"))
    story.append(Spacer(1, 4 * mm))
    story.append(p("M × N 到底贵不贵？", "H2CN"))
    story.append(p("这里的 M × N 是向量相似度，不是 M × N 次 LLM 调用。120 个现有原型与几十个热点的计算量很小；规模扩大后用 Qdrant 做近似近邻检索即可。真正昂贵的语义判断只留给召回后的 Top-K。"))
    story.append(Spacer(1, 4 * mm))
    story.append(
        data_table(
            ["产品模式", "排序方向", "适用场景"],
            [
                ["按作者日更（当前推荐）", "每位作者对所有热点打分 -> 保留 Top-K -> LLM 选 Top-N", "保证矩阵中每位作者按配置产出"],
                ["按机会优先", "每个热点对所有作者打分 -> 选择最适合作者", "不要求每位作者每天发文"],
            ],
            [43 * mm, 73 * mm, 50 * mm],
        )
    )
    story.append(
        callout(
            "同一热点能否给多个作者？",
            "可以。CreatorOS 当前产品选择不设置“同一热点当天只能分配一次”的全局上限。不同作者可能从不同人格视角切入；只有业务出现内容同质化证据后，才增加组合多样性约束。",
            "cyan",
        )
    )
    story.append(PageBreak())

    story += section("为什么 Top-K 后还要 LLM 重排", "07  Reranking")
    story.append(
        data_table(
            ["阶段", "擅长什么", "不擅长什么"],
            [
                ["Embedding 召回", "便宜、稳定、批量处理语义相似度", "难判断事实边界、表达空间、价值冲突和重复内容"],
                ["LLM 重排", "结合证据比较切入角度、风险和作者独特性", "全量调用成本高、延迟高、结果有随机性"],
            ],
            [35 * mm, 65 * mm, 66 * mm],
        )
    )
    story.append(Spacer(1, 5 * mm))
    story.append(p("重排输入应保持窄而有证据", "H2CN"))
    story.append(bullet("一份 HotspotBrief，而不是所有搜索原文。"))
    story.append(bullet("作者 Top-K 候选的分数、winning domain / perspective prototype、confidence。"))
    story.append(bullet("每个 winning prototype 的少量 representative evidence，而不是完整历史语料。"))
    story.append(Spacer(1, 3 * mm))
    story.append(
        p(
            "RoutingDecision<br/>"
            "- author_id / hotspot_id / selected<br/>"
            "- final_rank / fit_reason / proposed_angle<br/>"
            "- domain_evidence_ids / perspective_evidence_ids<br/>"
            "- risk_flags / confidence / rejected_reasons",
            "CodeCN",
        )
    )
    story.append(p("执行策略", "H2CN"))
    story.append(bullet("每位作者先保留 embedding Top-K，再由 LLM 选出配置的 Top-N；Top-N 可以由 CLI 查询或作者配置改变。"))
    story.append(bullet("LLM 输出必须结构化，并引用候选证据 ID；无法说明依据的候选不进入生成阶段。"))
    story.append(bullet("同一 HotspotBrief 只生成一次；同一作者的候选可以批量重排，降低固定 prompt 和缓存成本。"))
    story.append(
        callout(
            "核心工程取舍",
            "召回追求不漏，重排追求选准。把全量语义比较交给向量库，把少量复杂判断交给 LLM，是成本、延迟、可解释性之间的平衡。",
            "purple",
        )
    )
    story.append(PageBreak())

    story += section("用一个具体例子串起数据流", "08  Worked example")
    story.append(p("下面是目标流程示例，不代表当前已经自动执行。假设每天取 10 个热点，当前有 7 位作者、120 个路由原型。"))
    story.append(
        data_table(
            ["步骤", "输入", "动作", "输出规模"],
            [
                ["1. 候选", "知乎热榜", "取前 10 条并去重", "10 HotTopic"],
                ["2. 增强", "每条标题", "搜索 5 条相关内容", "约 50 SearchItem"],
                ["3. 拆解", "热榜 + 搜索证据", "LLM 结构化 domain / perspective", "10 HotspotBrief"],
                ["4. 召回", "20 个热点向量 + 120 原型", "双通道 cosine 检索与作者聚合", "70 个 author-hotspot pair score"],
                ["5. 截断", "每位作者 10 个 pair", "保留 Top-K=5", "最多 35 候选"],
                ["6. 重排", "候选 + winning evidence", "LLM 为每位作者选 Top-N=1", "7 RoutingDecision"],
                ["7. 生成", "选题与角度", "调用 PersonClone ask_author", "7 份草稿"],
            ],
            [24 * mm, 38 * mm, 68 * mm, 36 * mm],
            7.1,
        )
    )
    story.append(Spacer(1, 5 * mm))
    story.append(p("一条候选为什么入选", "H2CN"))
    story.append(
        p(
            "热点：某平台推出新的内容治理规则<br/>"
            "作者 A winning domain：平台经济与内容生态，D=0.81<br/>"
            "作者 A winning perspective：先拆变量再判断，P=0.76 × confidence 0.88<br/>"
            "LLM 重排：该作者虽然没写过这条具体规则，但有平台治理专长，且擅长区分规则目标、执行成本与副作用，因此提出“效率与公平如何权衡”的角度。",
            "QuoteCN",
        )
    )
    story.append(Spacer(1, 4 * mm))
    story.append(p("随后发生什么", "H2CN"))
    story.append(bullet("RoutingDecision 交给内容生成编排，不把画像当成最终答案模板。"))
    story.append(bullet("PersonClone 根据选定作者、问题与角度生成草稿；CreatorOS 负责后续 Judge、审批、发布和反馈。"))
    story.append(bullet("生成失败不会推翻路由结果；两者是不同阶段，可分别重试与追踪。"))
    story.append(PageBreak())

    story += section("对象、所有权与失败处理", "09  Contracts & reliability")
    story.append(
        data_table(
            ["对象", "所有者", "当前状态", "失败策略"],
            [
                ["HotTopic / SearchItem", "CreatorOS Discovery", "已实现", "鉴权、超时、协议错误转稳定 ToolResult"],
                ["HotspotBrief", "CreatorOS Discovery", "待实现", "证据不足则拒绝拆解，不伪造事实"],
                ["AuthorRoutingProfile", "PersonClone 生成；CreatorOS 消费", "GET 已接通，Pydantic 待实现", "404 或非 ready 时暂不可匹配"],
                ["RoutePrototypeDoc / Index", "CreatorOS Routing", "待实现", "按 corpus_version 原子刷新"],
                ["RoutingDecision", "CreatorOS Routing", "待实现", "无证据或低置信候选不进入生成"],
                ["PersonaAnswer", "PersonClone 生成；CreatorOS 编排", "Client 已实现", "生成错误与路由错误分开记录"],
            ],
            [37 * mm, 43 * mm, 41 * mm, 45 * mm],
            7.1,
        )
    )
    story.append(Spacer(1, 5 * mm))
    story.append(p("哪些步骤适合 Tool，哪些适合普通代码？", "H2CN"))
    story.append(bullet("热榜和搜索当前是只读 Tool：Agent 可以按任务动态决定调用与查询词。"))
    story.append(bullet("画像同步、向量召回、分数聚合更适合确定性 Python 服务：可测试、可缓存、不会污染模型上下文。"))
    story.append(bullet("热点拆解和候选重排使用 LLM，但输入输出由 Pydantic 合同约束。"))
    story.append(bullet("发布属于高副作用能力，未来必须单独审批、幂等和审计，不能沿用普通只读 Tool 权限。"))
    story.append(
        callout(
            "缓存最小设计",
            "热榜按 snapshot 时间缓存；搜索按 query + 时间窗缓存；HotspotBrief 按 hotspot 内容哈希缓存；路由画像和向量索引按 author_id + corpus_version 缓存。缓存键跟业务版本走，而不是只设置一个盲目的 TTL。",
            "green",
        )
    )
    story.append(Spacer(1, 4 * mm))
    story.append(p("最小可观测指标", "H2CN"))
    story.append(bullet("Discovery：调用成功率、热点去重率、搜索证据覆盖率。"))
    story.append(bullet("Routing：人工标注集 Recall@K / NDCG、LLM 接受率、跨域命中率、拒绝原因分布。"))
    story.append(bullet("Generation：草稿 Judge 通过率、人工改写率、发布成功率；效果反馈后再评估点击与互动。"))
    story.append(PageBreak())

    story += section("面试高频追问：架构与算法", "10  Interview QA")
    add_qa(story, "Q1：为什么热榜之后还要搜索，不能直接匹配作者吗？", "热榜是趋势候选，不是充分语义表示。短标题缺少事件背景和冲突视角，直接 embedding 容易误召回。我用搜索补齐事实、相关内容和观点，再构造结构化 HotspotBrief，最后才进入作者路由。")
    add_qa(story, "Q2：domain prototype 和 perspective prototype 有什么区别？", "domain 回答作者写过什么，来自历史标题聚类；perspective 回答作者通常如何思考，来自 Narrative Schema 的稳定视角。两者不一一对应，分开匹配才能支持“没写过该领域但人格视角适合”的跨域选题。")
    add_qa(story, "Q3：为什么不用一个作者平均向量？", "多领域作者的平均向量会稀释细分专长。我保留多个领域和视角原型，对作者内部取 Max Similarity，并带回命中的 prototype 与 evidence，因此既保持召回能力又能解释为什么匹配。")
    add_qa(story, "Q4：M × N 计算不会很贵吗？", "需要区分向量计算和 LLM 调用。M × N 的 cosine 计算在当前 120 个原型规模下极小，规模大后由 Qdrant 近邻检索解决；我不会做 M × N 次 LLM 判断，只把每位作者的 Top-K 交给 LLM 重排。")
    add_qa(story, "Q5：为什么 embedding 之后还需要 LLM？", "Embedding 擅长语义相似，无法稳定判断事实边界、价值冲突、表达空间和品牌风险。LLM 只对少量召回候选结合证据重排，负责复杂但低吞吐的判断。")
    add_qa(story, "Q6：为什么不让 LLM 直接读取所有画像并选择？", "全量画像会让上下文膨胀、成本和延迟随作者数增长，而且难以缓存和复现。确定性检索先缩小空间，LLM 看到 winning prototypes 与证据即可完成更可靠的比较。")
    story.append(PageBreak())

    story += section("面试高频追问：边界、可靠性与落地", "11  Interview QA")
    add_qa(story, "Q7：PersonClone 已经有 Qdrant，为什么 CreatorOS 不直接连接？", "那会绕过服务边界，把 CreatorOS 绑定到 PersonClone 的内部 collection、payload 和迁移细节。当前合同明确 vector_ref 是不透明引用，因此我只消费正式 API；推荐在 CreatorOS 对公开的 routing text 建独立索引，或者未来要求 PersonClone 提供正式 search API。")
    add_qa(story, "Q8：画像更新后怎么避免使用旧向量？", "每份画像携带 corpus_version。CreatorOS 以 author_id + corpus_version 作为同步和缓存键，版本变化时原子替换该作者的全部 prototype vectors；vector_ref 也只在对应版本内有效。")
    add_qa(story, "Q9：profile 不是 ready 怎么办？", "404 表示暂不可匹配；domain_ready 只进入领域通道；perspective_pending 不生成或猜测人格视角。路由分数会根据可用通道重新归一化，并在决策中记录降级原因。")
    add_qa(story, "Q10：为什么画像读取不注册成 Agent Tool？", "Tool 适合让模型动态决定动作；画像读取、缓存和检索是确定性的内部数据路径。把整份画像暴露给模型会浪费上下文并削弱边界，所以由 Routing Service 消费，只把 Top-K 证据提供给 LLM。")
    add_qa(story, "Q11：同一热点匹配多个作者会不会内容重复？", "当前业务允许多个作者命中同一热点，因为他们可从不同视角切入。我不会先加没有需求证据的全局上限；后续若同质化指标变差，再在重排阶段增加角度去重或组合多样性约束。")
    add_qa(story, "Q12：如何证明路由真的有效？", "先构建热点-作者人工标注集，离线看 Recall@K 和 NDCG；上线观察 LLM 选择接受率、人工改写率和 Judge 通过率。发布数据稳定后，再用互动指标做延迟反馈，但不直接把流量等同于内容质量。")
    story.append(PageBreak())

    story += section("面试时怎样诚实又有亮点地讲", "12  Interview playbook")
    story.append(p("60 秒项目介绍", "H2CN"))
    story.append(
        p(
            "CreatorOS 是面向多创作者矩阵的自治运营 Agent。我把链路拆成趋势发现、热点理解、作者路由、数字分身生成、质量评审和发布反馈。当前已接通知乎官方热榜与搜索，也通过独立 FastAPI 服务读取 7 位作者的 120 个领域/视角原型。路由层采用领域与人格视角双通道：先用向量检索对作者内部多原型取 Max Similarity，再把带证据的 Top-K 候选交给 LLM 重排，从而避免全量 LLM 匹配，并支持跨领域但人格视角契合的选题。当前正在实现画像 Pydantic 合同和 CreatorOS 自有路由索引，后续再接生成评审与发布。",
            "QuoteCN",
        )
    )
    story.append(Spacer(1, 5 * mm))
    story.append(p("一定要区分完成项和设计项", "H2CN"))
    story.append(
        data_table(
            ["可以说“已完成”", "应说“已设计 / 正在实现”"],
            [
                ["知乎官方热榜与搜索 Tool；真实 API 验证", "HotspotBrief 自动拆解"],
                ["PersonClone 作者列表、SSE 生成接口、routing profile GET", "CreatorOS 路由向量索引与 Max 聚合"],
                ["7 位作者画像真实读取：83 domain + 37 perspective", "Top-K + LLM 重排、Judge、发布和反馈闭环"],
                ["Agent Runtime、Streaming、Session、Compaction、ToolResult", "定时自治运营与生产级任务恢复"],
            ],
            [83 * mm, 83 * mm],
        )
    )
    story.append(Spacer(1, 5 * mm))
    story.append(p("接下来三个最小切片", "H2CN"))
    story.append(bullet("1. 用真实 wire shape 建 AuthorRoutingProfile Pydantic 模型，并实现状态降级。"))
    story.append(bullet("2. 定义 RoutePrototypeDoc，同步 120 个原型到 CreatorOS 自有向量索引，按 corpus_version 刷新。"))
    story.append(bullet("3. 定义 HotspotBrief 与双通道 scorer，用一组真实热点完成 Top-K，再接 LLM 重排。"))
    story.append(Spacer(1, 5 * mm))
    story.append(
        callout(
            "一句话记忆",
            "热榜负责发现，搜索负责理解，画像负责描述作者，向量负责召回，LLM 负责少量复杂判断，PersonClone 负责按选定作者生成。",
            "purple",
        )
    )
    story.append(Spacer(1, 5 * mm))
    story.append(p("资料与校准基线", "H2CN"))
    story.append(p("CreatorOS repository: https://github.com/SlamWeb/CreatorOS<br/>基线 commit: b4cab7a<br/>实现来源: creatoros/discovery, creatoros/integrations/zhihu.py, creatoros/integrations/personclone.py, SPEC.md<br/>PersonClone API contract: 用户于 2026-08-26 提供；真实画像字段与 7 位作者状态已通过本地服务复验。", "CNBodySmall"))
    return story


def main() -> None:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(
        str(OUTPUT),
        pagesize=A4,
        rightMargin=22 * mm,
        leftMargin=22 * mm,
        topMargin=22 * mm,
        bottomMargin=20 * mm,
        title="CreatorOS 搜索与作者匹配全链路学习手册",
        author="CreatorOS",
        subject="搜索、热点拆解、Creator Routing 与面试问答",
    )
    doc.build(build_story(), onFirstPage=on_page, onLaterPages=on_page)
    print(OUTPUT)


if __name__ == "__main__":
    main()
