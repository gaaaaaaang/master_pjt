from pathlib import Path
import textwrap

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas


ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "output" / "pdf"
OUT_PATH = OUT_DIR / "semiconductor_fab_incident_response_playbook_kr.pdf"

PAGE_W, PAGE_H = A4
MARGIN_X = 17 * mm
TOP_Y = PAGE_H - 22 * mm
BOTTOM_Y = 18 * mm

FONT_REG = "AppleGothic"
FONT_BOLD = "AppleGothic"
FONT_PATH = "/System/Library/Fonts/Supplemental/AppleGothic.ttf"


def register_fonts():
    pdfmetrics.registerFont(TTFont(FONT_REG, FONT_PATH))


def string_width(text, size, font=FONT_REG):
    return pdfmetrics.stringWidth(text, font, size)


def wrap_text(text, max_width, size=9.5, font=FONT_REG):
    lines = []
    for raw in str(text).split("\n"):
        if raw == "":
            lines.append("")
            continue
        current = ""
        for ch in raw:
            trial = current + ch
            if string_width(trial, size, font) <= max_width:
                current = trial
            else:
                if current:
                    lines.append(current)
                current = ch
        if current:
            lines.append(current)
    return lines


def draw_wrapped(c, text, x, y, max_width, size=9.5, leading=13, color=colors.HexColor("#1f2933")):
    c.setFillColor(color)
    c.setFont(FONT_REG, size)
    for line in wrap_text(text, max_width, size):
        c.drawString(x, y, line)
        y -= leading
    return y


def draw_header_footer(c, page_no, title):
    c.setFillColor(colors.HexColor("#0f172a"))
    c.rect(0, PAGE_H - 14 * mm, PAGE_W, 14 * mm, fill=1, stroke=0)
    c.setFillColor(colors.white)
    c.setFont(FONT_BOLD, 9)
    c.drawString(MARGIN_X, PAGE_H - 9 * mm, "Semiconductor FAB Incident Response Playbook")
    c.setFont(FONT_REG, 8)
    c.drawRightString(PAGE_W - MARGIN_X, PAGE_H - 9 * mm, "RAG Reference / Simulation Use")

    c.setStrokeColor(colors.HexColor("#cbd5e1"))
    c.line(MARGIN_X, BOTTOM_Y + 8, PAGE_W - MARGIN_X, BOTTOM_Y + 8)
    c.setFillColor(colors.HexColor("#64748b"))
    c.setFont(FONT_REG, 8)
    c.drawString(MARGIN_X, BOTTOM_Y, title[:72])
    c.drawRightString(PAGE_W - MARGIN_X, BOTTOM_Y, f"{page_no} / 25")


def draw_title(c, page_no, title, subtitle=None):
    draw_header_footer(c, page_no, title)
    y = TOP_Y
    c.setFillColor(colors.HexColor("#111827"))
    c.setFont(FONT_BOLD, 18)
    c.drawString(MARGIN_X, y, title)
    y -= 18
    if subtitle:
        c.setFillColor(colors.HexColor("#475569"))
        c.setFont(FONT_REG, 9)
        c.drawString(MARGIN_X, y, subtitle)
        y -= 18
    c.setStrokeColor(colors.HexColor("#94a3b8"))
    c.setLineWidth(0.8)
    c.line(MARGIN_X, y, PAGE_W - MARGIN_X, y)
    return y - 18


def draw_tag_box(c, x, y, label, value, width=160, height=28):
    c.setFillColor(colors.HexColor("#f8fafc"))
    c.setStrokeColor(colors.HexColor("#cbd5e1"))
    c.roundRect(x, y - height, width, height, 3, fill=1, stroke=1)
    c.setFillColor(colors.HexColor("#334155"))
    c.setFont(FONT_BOLD, 7.5)
    c.drawString(x + 7, y - 10, label)
    c.setFillColor(colors.HexColor("#0f172a"))
    c.setFont(FONT_REG, 8.5)
    c.drawString(x + 7, y - 22, value[:30])


def draw_metadata(c, y, meta):
    x = MARGIN_X
    width = (PAGE_W - 2 * MARGIN_X - 12) / 3
    for idx, (label, value) in enumerate(meta[:6]):
        row = idx // 3
        col = idx % 3
        draw_tag_box(c, x + col * (width + 6), y - row * 35, label, value, width)
    return y - 76


def draw_section(c, y, heading, bullets):
    c.setFillColor(colors.HexColor("#0f766e"))
    c.setFont(FONT_BOLD, 11)
    c.drawString(MARGIN_X, y, heading)
    y -= 15
    for bullet in bullets:
        c.setFillColor(colors.HexColor("#0f172a"))
        c.setFont(FONT_BOLD, 8)
        c.drawString(MARGIN_X + 2, y, "-")
        y = draw_wrapped(c, bullet, MARGIN_X + 13, y, PAGE_W - 2 * MARGIN_X - 13, 8.7, 12)
        y -= 2
    return y - 6


def draw_table(c, y, headers, rows, col_widths=None, row_h=24):
    x = MARGIN_X
    total_w = PAGE_W - 2 * MARGIN_X
    if col_widths is None:
        col_widths = [total_w / len(headers)] * len(headers)
    c.setFillColor(colors.HexColor("#e2e8f0"))
    c.setStrokeColor(colors.HexColor("#94a3b8"))
    c.rect(x, y - row_h, total_w, row_h, fill=1, stroke=1)
    cx = x
    c.setFont(FONT_BOLD, 7.8)
    c.setFillColor(colors.HexColor("#0f172a"))
    for h, w in zip(headers, col_widths):
        c.drawString(cx + 5, y - 15, h)
        c.line(cx, y - row_h, cx, y - row_h * (len(rows) + 1))
        cx += w
    c.line(x + total_w, y - row_h, x + total_w, y - row_h * (len(rows) + 1))
    y -= row_h
    c.setFont(FONT_REG, 7.2)
    for ridx, row in enumerate(rows):
        fill = colors.HexColor("#ffffff") if ridx % 2 == 0 else colors.HexColor("#f8fafc")
        c.setFillColor(fill)
        c.rect(x, y - row_h, total_w, row_h, fill=1, stroke=1)
        cx = x
        c.setFillColor(colors.HexColor("#1f2937"))
        for cell, w in zip(row, col_widths):
            lines = wrap_text(cell, w - 8, 7.2)
            for i, line in enumerate(lines[:2]):
                c.drawString(cx + 5, y - 10 - i * 9, line)
            c.line(cx, y, cx, y - row_h)
            cx += w
        c.line(x + total_w, y, x + total_w, y - row_h)
        y -= row_h
    return y - 12


def draw_flow(c, x, y, steps, width=86, height=30):
    colors_fill = ["#dbeafe", "#dcfce7", "#fef3c7", "#fee2e2", "#ede9fe", "#e0f2fe"]
    for i, step in enumerate(steps):
        bx = x + i * (width + 8)
        c.setFillColor(colors.HexColor(colors_fill[i % len(colors_fill)]))
        c.setStrokeColor(colors.HexColor("#64748b"))
        c.roundRect(bx, y - height, width, height, 4, fill=1, stroke=1)
        c.setFillColor(colors.HexColor("#111827"))
        c.setFont(FONT_BOLD, 7.4)
        for j, line in enumerate(wrap_text(step, width - 10, 7.4)[:2]):
            c.drawCentredString(bx + width / 2, y - 12 - j * 9, line)
        if i < len(steps) - 1:
            c.setStrokeColor(colors.HexColor("#334155"))
            c.line(bx + width, y - height / 2, bx + width + 8, y - height / 2)
            c.line(bx + width + 5, y - height / 2 + 3, bx + width + 8, y - height / 2)
            c.line(bx + width + 5, y - height / 2 - 3, bx + width + 8, y - height / 2)


def draw_bottleneck_diagram(c, y):
    x = MARGIN_X + 4
    centers = [x + 62, x + 196, x + 330, x + 464]
    labels = ["Upstream WIP 증가", "Bottleneck Tool", "Downstream Starvation", "Recovery Control"]
    fills = ["#fef3c7", "#fecaca", "#dbeafe", "#dcfce7"]
    for center, label, fill in zip(centers, labels, fills):
        c.setFillColor(colors.HexColor(fill))
        c.setStrokeColor(colors.HexColor("#475569"))
        c.roundRect(center - 58, y - 23, 116, 46, 18, fill=1, stroke=1)
        c.setFillColor(colors.HexColor("#111827"))
        c.setFont(FONT_BOLD, 7.2)
        for i, line in enumerate(wrap_text(label, 96, 7.2)[:2]):
            c.drawCentredString(center, y + 5 - i * 10, line)
    c.setStrokeColor(colors.HexColor("#334155"))
    for a, b in zip(centers, centers[1:]):
        c.line(a + 58, y, b - 58, y)
        c.line(b - 63, y + 4, b - 58, y)
        c.line(b - 63, y - 4, b - 58, y)


def draw_decision_tree(c, y):
    x = MARGIN_X + 35
    nodes = [
        (x + 180, y, "이슈 감지"),
        (x + 60, y - 55, "품질 영향?"),
        (x + 300, y - 55, "납기 영향?"),
        (x + 10, y - 110, "LOT HOLD"),
        (x + 120, y - 110, "조건부 진행"),
        (x + 250, y - 110, "재스케줄"),
        (x + 370, y - 110, "모니터링"),
    ]
    for px, py, label in nodes:
        c.setFillColor(colors.HexColor("#f8fafc"))
        c.setStrokeColor(colors.HexColor("#64748b"))
        c.roundRect(px, py - 24, 95, 28, 4, fill=1, stroke=1)
        c.setFillColor(colors.HexColor("#111827"))
        c.setFont(FONT_BOLD, 7.8)
        c.drawCentredString(px + 47.5, py - 13, label)
    c.setStrokeColor(colors.HexColor("#334155"))
    lines = [
        ((x + 227, y - 24), (x + 107, y - 51)),
        ((x + 227, y - 24), (x + 347, y - 51)),
        ((x + 107, y - 79), (x + 57, y - 106)),
        ((x + 107, y - 79), (x + 167, y - 106)),
        ((x + 347, y - 79), (x + 297, y - 106)),
        ((x + 347, y - 79), (x + 417, y - 106)),
    ]
    for (sx, sy), (ex, ey) in lines:
        c.line(sx, sy, ex, ey)


def draw_raci(c, y):
    headers = ["Task", "Operator", "Scheduler", "Engineer", "Manager"]
    rows = [
        ["Detect / log", "R", "C", "C", "I"],
        ["Hold decision", "C", "C", "A/R", "I"],
        ["Schedule recovery", "I", "A/R", "C", "I"],
        ["RCA approval", "C", "I", "R", "A"],
    ]
    return draw_table(c, y, headers, rows, [120, 86, 86, 86, 86], 23)


pages = [
    dict(title="01. 문서 목적과 사용 범위", subtitle="시뮬레이션 및 RAG 참조용 FAB 돌발상황 대응 플레이북", meta=[("playbook_id", "PB-GEN-000"), ("scope", "fab10-fab13"), ("source", "SMT2020 + public studies"), ("use", "RAG reference"), ("status", "draft baseline"), ("language", "Korean")], sections=[
        ("목적", ["이 문서는 실제 기업 내부 SOP가 아니라 공개 자료와 일반 FAB 운영 원칙을 바탕으로 만든 시뮬레이션용 대응 플레이북이다.", "RAG 검색을 위해 각 페이지가 독립 청크로 쓰일 수 있도록 이슈 유형, 트리거, 역할, 조치, 복구 조건을 반복 기재한다."]),
        ("적용 범위", ["SMT2020 dataset 1, 2, 3, 4를 각각 fab10, fab11, fab12, fab13으로 매핑한다.", "운영 이슈는 장비 고장, 병목, 수율 저하, Lot Hold, 자재 부족, 긴급 오더, PM 지연을 대상으로 한다."])
    ], diagram=("flow", ["Detect", "Classify", "Contain", "Recover", "RCA", "Prevent"])),
    dict(title="02. 데이터 소스와 가정", subtitle="공개 자료 기반으로 생성한 운영 지식의 근거", meta=[("playbook_id", "PB-GEN-001"), ("source_type", "public"), ("risk", "assumption required"), ("fab_base", "SMT2020"), ("quality_ref", "SECOM"), ("manual_ref", "case studies")], sections=[
        ("주요 출처", ["SMT2020은 설비군, route, PM, breakdown, lot release를 제공하므로 시나리오의 fab 구조로 사용한다.", "SECOM은 센서 feature와 pass/fail 라벨을 제공하므로 공정 이상 또는 수율 저하의 감지 예시로 사용한다.", "Hold lot 및 exception handling 사례는 운영 판단과 PDCA 흐름을 구성하는 참고 자료로 사용한다."]),
        ("가정", ["수치 기준값은 실제 회사 기준이 아니라 시뮬레이션을 위한 예시값이다.", "모든 조치는 안전, 품질, 고객 납기 영향도를 우선순위로 평가한 뒤 수행한다."])
    ], table=(["Source", "Use in Playbook", "RAG Role"], [["SMT2020", "fab route / tool / PM / breakdown", "context"], ["SECOM", "sensor anomaly / yield label", "trigger example"], ["Hold Lot Case", "hold / release process", "procedure"], ["PDCA Exception", "MRB / action tracking", "workflow"]])),
    dict(title="03. RAG 문서 스키마", subtitle="검색과 답변 생성을 위한 최소 필드", meta=[("playbook_id", "PB-GEN-002"), ("chunk_type", "schema"), ("format", "JSON-ready"), ("granularity", "one playbook per issue"), ("required", "yes"), ("owner", "data team")], sections=[
        ("필수 필드", ["playbook_id, issue_type, fab_id, area, trigger_condition, severity, owner_role, actions, recovery_condition, evidence_required, tags를 필수로 둔다.", "RAG 답변에서는 원문 청크의 playbook_id와 recovery_condition을 함께 반환하도록 설계한다."]),
        ("권장 필드", ["affected_lots, downstream_impact, upstream_impact, schedule_action, hold_policy, RCA_template, prevention_rule을 추가하면 운영 질문에 대한 답변 품질이 좋아진다."])
    ], table=(["Field", "Example", "Purpose"], [["issue_type", "equipment_down", "상황 분류"], ["trigger_condition", "tool_status=DOWN", "검색 키"], ["owner_role", "Operator / Engineer", "역할별 응답"], ["recovery_condition", "qualification pass", "종료 판단"]])),
    dict(title="04. 역할 정의와 RACI", subtitle="Operator, Scheduler, Engineer, Manager의 판단 경계", meta=[("playbook_id", "PB-GEN-003"), ("issue_type", "all"), ("role_model", "RACI"), ("decision", "shared"), ("escalation", "yes"), ("tags", "role,raci")], sections=[
        ("역할 원칙", ["Operator는 감지, 현장 확인, 초기 기록, 설비 상태 전환을 담당한다.", "Scheduler는 lot 우선순위, 대체 설비 배정, release 조정, hot lot 영향 평가를 담당한다.", "Engineer는 원인 판단, hold/release 승인, qualification 조건, RCA를 담당한다.", "Manager는 중대 이슈의 납기/품질 리스크 승인과 고객 영향 커뮤니케이션을 담당한다."])
    ], diagram=("raci", [])),
    dict(title="05. 공통 대응 프로세스", subtitle="Detect - Classify - Contain - Recover - Learn", meta=[("playbook_id", "PB-GEN-004"), ("issue_type", "all"), ("severity", "S1-S4"), ("first_action", "log event"), ("owner", "Operator"), ("tags", "workflow")], sections=[
        ("기본 절차", ["모든 돌발상황은 발생 시각, fab, area, toolgroup, lot, product, 현재 step을 먼저 기록한다.", "품질 영향 가능성이 있으면 lot을 즉시 격리하거나 hold 후보로 전환한다.", "납기 영향 가능성이 있으면 Scheduler가 대체 toolgroup과 dispatching rule 변경 가능성을 검토한다.", "복구 후에는 RCA와 prevention rule 업데이트를 완료해야 한다."])
    ], diagram=("flow", ["Event Log", "Severity", "Containment", "Schedule Fix", "Recovery Check", "RCA Update"])),
    dict(title="06. Severity 분류 기준", subtitle="대응 속도와 승인 단계를 결정하는 기준", meta=[("playbook_id", "PB-GEN-005"), ("issue_type", "all"), ("decision", "severity"), ("owner", "Engineer"), ("tags", "severity,escalation"), ("version", "v0.1")], sections=[
        ("분류 기준", ["S1은 안전 또는 광범위한 품질 영향이 의심되는 경우로 즉시 hold와 manager escalation이 필요하다.", "S2는 특정 area 또는 product group의 납기, cycle time, WIP가 크게 흔들리는 경우다.", "S3는 단일 toolgroup 또는 일부 lot에 제한된 영향이다.", "S4는 감시 대상 이벤트이며 생산 지속이 가능하다."])
    ], table=(["Level", "Condition", "Required Response"], [["S1", "safety / broad quality risk", "stop, hold, MRB"], ["S2", "major WIP or due-date risk", "escalate, reschedule"], ["S3", "local equipment or lot issue", "contain, monitor"], ["S4", "warning only", "record, observe"]])),
    dict(title="07. 장비 고장 대응", subtitle="Equipment Down / Unexpected Shutdown", meta=[("playbook_id", "PB-EQ-001"), ("issue_type", "equipment_down"), ("trigger", "tool status DOWN"), ("owner", "Operator"), ("decision", "hold or reroute"), ("tags", "equipment,breakdown")], sections=[
        ("감지 조건", ["tool status가 DOWN으로 전환되거나 breakdown event가 발생한다.", "queue length와 WIP age가 급증하고 해당 toolgroup의 start rate가 0에 가까워진다."]),
        ("역할별 대응", ["Operator는 설비 상태, 마지막 처리 lot, chamber, recipe, alarm code를 기록한다.", "Engineer는 품질 영향 lot을 식별하고 hold 필요 여부를 결정한다.", "Scheduler는 대체 toolgroup 가능 여부와 hot lot 우선순위를 재평가한다."]),
        ("복구 조건", ["수리 완료, qualification pass, 첫 생산 lot 모니터링 통과, 영향 lot disposition 완료."])
    ], diagram=("flow", ["Alarm", "Stop Tool", "Find Affected Lots", "Hold/Reroute", "Qual Pass", "Release"])),
    dict(title="08. PM 지연과 설비 가용성 저하", subtitle="Preventive Maintenance Delay", meta=[("playbook_id", "PB-EQ-002"), ("issue_type", "pm_delay"), ("trigger", "PM overdue"), ("owner", "Engineer"), ("decision", "run vs stop"), ("tags", "pm,availability")], sections=[
        ("감지 조건", ["PM due time이 초과되었거나 PM 예정 시간과 hot lot 처리 계획이 충돌한다.", "동일 area 내 병렬 tool 수가 부족해져 대기 시간이 증가한다."]),
        ("즉시 조치", ["PM 연기는 Engineer 승인 없이는 허용하지 않는다.", "PM 수행 전후로 영향 product와 lot priority를 Scheduler가 재계산한다.", "PM 이후 qualification 또는 dummy run 결과를 기록한다."]),
        ("복구 조건", ["PM 작업 완료, 설비 상태 AVAILABLE, 관련 alarm clear, PM 지연으로 발생한 WIP backlog 정상화."])
    ], table=(["Decision", "Allowed When", "Evidence"], [["PM execute now", "quality or safety risk", "PM due / alarm"], ["PM short delay", "low risk and manager approval", "risk memo"], ["Tool stop", "qualification needed", "engineer sign-off"]])),
    dict(title="09. 병목 발생 대응", subtitle="Bottleneck Formation", meta=[("playbook_id", "PB-BN-001"), ("issue_type", "bottleneck"), ("trigger", "queue / utilization high"), ("owner", "Scheduler"), ("decision", "rebalance"), ("tags", "bottleneck,wip")], sections=[
        ("감지 조건", ["특정 toolgroup의 utilization이 지속적으로 높고 queue가 증가한다.", "upstream WIP가 쌓이고 downstream toolgroup은 starvation 상태가 된다."]),
        ("대응 절차", ["Scheduler는 release rate와 dispatching priority를 조정한다.", "Engineer는 대체 recipe 또는 qualified alternate tool 사용 가능성을 확인한다.", "Operator는 병목 tool의 setup loss, loading delay, batch 구성 실패 여부를 확인한다."]),
        ("복구 조건", ["queue age 감소, downstream starvation 해소, bottleneck degree가 경고 기준 아래로 회복."])
    ], diagram=("bottleneck", [])),
    dict(title="10. 병목의 전후 공정 영향", subtitle="Upstream Accumulation and Downstream Starvation", meta=[("playbook_id", "PB-BN-002"), ("issue_type", "bottleneck_impact"), ("trigger", "flow imbalance"), ("owner", "Scheduler"), ("impact", "cross-area"), ("tags", "upstream,downstream")], sections=[
        ("영향 모델", ["병목 앞단은 queue와 WIP age가 증가하고, lot priority 충돌이 심해진다.", "병목 뒷단은 투입 부족으로 idle 시간이 증가하고 throughput 변동성이 커진다.", "hot lot이 병목에 집중되면 일반 lot의 cycle time tail이 길어진다."]),
        ("운영 판단", ["병목 완화 조치가 다른 area의 starvation을 악화시키지 않는지 확인한다.", "release를 줄이는 조치는 단기 납기와 장기 WIP 안정성 사이의 trade-off를 명시해야 한다."])
    ], table=(["Symptom", "Likely Cause", "Response"], [["Upstream WIP high", "capacity loss", "release throttle"], ["Downstream idle", "feed shortage", "bottleneck feeding"], ["Hot lot delay", "priority conflict", "priority review"]])),
    dict(title="11. 수율 저하 대응", subtitle="Yield Drop / Fail Rate Increase", meta=[("playbook_id", "PB-YD-001"), ("issue_type", "yield_drop"), ("trigger", "fail rate high"), ("owner", "Engineer"), ("decision", "hold / RCA"), ("tags", "yield,secom")], sections=[
        ("감지 조건", ["검사 결과 fail rate가 평소 기준보다 높거나 SECOM류 sensor feature가 이상 패턴을 보인다.", "동일 product, recipe, tool, chamber, material batch에서 fail이 집중된다."]),
        ("즉시 조치", ["영향 lot을 hold candidate로 묶고 commonality analysis를 수행한다.", "최근 recipe 변경, PM 이력, material lot, tool alarm, operator intervention을 확인한다.", "품질 영향이 확정되기 전까지 무리한 release를 금지한다."]),
        ("복구 조건", ["원인 범위가 특정되고 재검 또는 engineering disposition이 완료됨."])
    ], diagram=("flow", ["Fail Signal", "Commonality", "Hold Scope", "RCA", "Disposition", "Prevent"])),
    dict(title="12. SPC Alarm 대응", subtitle="Out-of-Control / Spec Violation", meta=[("playbook_id", "PB-QA-001"), ("issue_type", "spc_alarm"), ("trigger", "OOC/OOS"), ("owner", "Engineer"), ("decision", "contain"), ("tags", "spc,quality")], sections=[
        ("감지 조건", ["SPC chart에서 관리 한계 초과, trend rule 위반, spec out이 발생한다.", "측정 장비 이상 가능성과 실제 공정 이상 가능성을 분리해 판단한다."]),
        ("대응 절차", ["Operator는 측정값, wafer position, metrology tool, sampling rule을 기록한다.", "Engineer는 affected lot 범위를 정하고 re-measure, rework, scrap, release 중 하나를 결정한다.", "Scheduler는 hold로 인한 downstream plan 변경을 반영한다."])
    ], table=(["Check", "Question", "Action"], [["Measurement", "metrology issue?", "remeasure"], ["Process", "recipe drift?", "hold/RCA"], ["Scope", "same tool/product?", "commonality"], ["Plan", "due-date impact?", "reschedule"]])),
    dict(title="13. Lot Hold 판단", subtitle="When to Hold a Lot", meta=[("playbook_id", "PB-HL-001"), ("issue_type", "lot_hold"), ("trigger", "quality or process risk"), ("owner", "Engineer"), ("decision", "hold"), ("tags", "hold,containment")], sections=[
        ("Hold 조건", ["품질 영향 가능성이 있고 원인 범위가 확정되지 않은 경우 hold한다.", "공정 spec 위반, 장비 alarm 후 처리 lot, material 의심 lot, recipe mismatch lot은 우선 hold 후보가 된다.", "납기 압박은 품질 hold 해제의 단독 사유가 될 수 없다."]),
        ("필수 기록", ["hold reason, affected operation, suspect tool, suspect time window, owner, expected next review time을 기록한다."])
    ], diagram=("tree", [])),
    dict(title="14. Lot Release 판단", subtitle="Release from Hold", meta=[("playbook_id", "PB-HL-002"), ("issue_type", "lot_release"), ("trigger", "review complete"), ("owner", "Engineer"), ("decision", "release"), ("tags", "release,disposition")], sections=[
        ("Release 조건", ["원인 분석이 완료되고 lot disposition이 승인된다.", "필요 시 re-measure, rework, engineering evaluation, qualification 결과가 pass여야 한다.", "release 후 downstream 공정의 queue time과 timelink 위반 여부를 Scheduler가 확인한다."]),
        ("Release 유형", ["normal release는 추가 제약 없이 진행한다.", "conditional release는 특정 operation 또는 toolgroup 사용 조건을 붙인다.", "engineering release는 추적 대상 lot으로 지정해 모니터링한다."])
    ], table=(["Release Type", "Condition", "Follow-up"], [["Normal", "risk cleared", "standard dispatch"], ["Conditional", "limited risk", "restricted route"], ["Engineering", "needs observation", "enhanced tracking"], ["Reject", "risk remains", "continue hold"]])),
    dict(title="15. 자재 부족 대응", subtitle="Material Shortage / Consumable Constraint", meta=[("playbook_id", "PB-MT-001"), ("issue_type", "material_shortage"), ("trigger", "stockout risk"), ("owner", "Scheduler"), ("decision", "prioritize"), ("tags", "material,priority")], sections=[
        ("감지 조건", ["photoresist, gas, chemical, reticle, spare part 등의 available quantity가 계획 소요량보다 부족하다.", "자재 부족으로 특정 operation의 release 가능 lot 수가 제한된다."]),
        ("대응 절차", ["Scheduler는 customer due date, hot lot, engineering priority 기준으로 소비 우선순위를 정한다.", "Engineer는 대체 자재 또는 대체 recipe 사용 가능성을 평가한다.", "Manager는 납기 영향이 큰 경우 고객 영향 범위를 승인한다."]),
        ("복구 조건", ["자재 입고 확인, 사용 가능성 승인, backlog 처리 계획 수립."])
    ], diagram=("flow", ["Shortage", "Lot Ranking", "Alt Material", "Plan Change", "Arrival", "Backlog Clear"])),
    dict(title="16. 긴급 오더와 Hot Lot 대응", subtitle="Rush Order / Hot Lot Insertion", meta=[("playbook_id", "PB-HOT-001"), ("issue_type", "hot_lot"), ("trigger", "urgent due date"), ("owner", "Scheduler"), ("decision", "priority override"), ("tags", "hotlot,dispatch")], sections=[
        ("감지 조건", ["고객 납기 변경, engineering sample 긴급 요청, 품질 확인용 split lot이 발생한다.", "hot lot 투입으로 일반 lot의 cycle time 증가가 예상된다."]),
        ("대응 절차", ["Scheduler는 hot lot의 전체 route상 병목 step과 timelink risk를 먼저 계산한다.", "Engineer는 hot lot이 우회 가능한 operation과 우회 불가 operation을 구분한다.", "Manager는 일반 lot 납기 영향과 고객 우선순위 trade-off를 승인한다."])
    ], table=(["Priority Rule", "Use When", "Risk"], [["Hot first", "critical due date", "normal lot delay"], ["Reservation", "bottleneck step", "capacity lock"], ["Batch merge", "batch tool available", "queue wait"], ["No override", "quality risk", "late hot lot"]])),
    dict(title="17. Queue Time / Timelink 위반 대응", subtitle="Time-sensitive Operation Control", meta=[("playbook_id", "PB-QT-001"), ("issue_type", "queue_time_risk"), ("trigger", "time window close"), ("owner", "Scheduler"), ("decision", "expedite or hold"), ("tags", "queue,time")], sections=[
        ("감지 조건", ["이전 operation 완료 후 다음 operation까지 허용 시간이 임박하거나 초과된다.", "병목, 장비 고장, hot lot insertion으로 time-sensitive lot의 대기가 길어진다."]),
        ("대응 절차", ["Scheduler는 time window가 가장 짧은 lot을 우선 배정한다.", "Engineer는 queue time 초과 lot의 품질 영향과 rework 가능성을 판단한다.", "Operator는 해당 lot의 physical location과 carrier 상태를 확인한다."]),
        ("복구 조건", ["time-sensitive lot 처리 완료 또는 disposition 결정 완료."])
    ], diagram=("flow", ["Time Alert", "Find Lot", "Reserve Tool", "Run / Hold", "Check Quality", "Close"])),
    dict(title="18. Dispatching Rule Override", subtitle="자동 규칙을 임시 변경하는 경우", meta=[("playbook_id", "PB-SC-001"), ("issue_type", "dispatch_override"), ("trigger", "rule conflict"), ("owner", "Scheduler"), ("decision", "manual override"), ("tags", "dispatch,schedule")], sections=[
        ("허용 조건", ["SuperHotLotFIRST, reservation, least setup time 같은 규칙이 품질 또는 납기 목표와 충돌할 때 임시 override를 검토한다.", "override는 시작 시각, 종료 조건, 영향 lot, 승인자를 반드시 남긴다."]),
        ("금지 조건", ["품질 hold lot을 단순 납기 사유로 override release하지 않는다.", "대체 tool qualification이 불명확한 상태에서 dispatch만 변경하지 않는다."])
    ], table=(["Override Case", "Approver", "End Condition"], [["Hot lot queue", "Scheduler", "hot lot passes bottleneck"], ["Quality containment", "Engineer", "disposition complete"], ["Tool recovery", "Engineer", "qual pass"], ["Material limit", "Manager", "shortage cleared"]])),
    dict(title="19. 대체 장비 배정", subtitle="Qualified Alternate Tool Use", meta=[("playbook_id", "PB-ALT-001"), ("issue_type", "alternate_tool"), ("trigger", "primary unavailable"), ("owner", "Engineer"), ("decision", "qualify"), ("tags", "tool,reroute")], sections=[
        ("판단 기준", ["대체 tool은 product, recipe, chamber capability, qualification status가 맞아야 한다.", "setup time 증가가 병목 완화 효과보다 큰 경우 대체 배정을 보류한다.", "대체 장비 사용 후 첫 lot은 engineering watch 대상으로 지정한다."]),
        ("복구 조건", ["primary tool 복구 또는 alternate tool 안정 처리 확인, affected lot 추적 완료."])
    ], diagram=("flow", ["Primary Down", "Check Qual", "Setup Cost", "Assign Alt", "Watch First Lot", "Normalize"])),
    dict(title="20. RCA 템플릿", subtitle="Root Cause Analysis and 5-Why", meta=[("playbook_id", "PB-RCA-001"), ("issue_type", "rca"), ("trigger", "S1/S2 or repeat issue"), ("owner", "Engineer"), ("decision", "cause closure"), ("tags", "rca,5why")], sections=[
        ("필수 질문", ["문제는 언제, 어디서, 어떤 lot/product/tool에서 시작되었는가?", "가장 먼저 관측된 신호는 무엇이고 그 이전 변경점은 무엇인가?", "동일 현상이 반복되었는가, 특정 recipe나 material batch에 집중되는가?", "임시 조치와 영구 조치를 분리했는가?"]),
        ("종료 조건", ["root cause, containment, corrective action, prevention rule, owner, due date가 모두 기록되어야 RCA를 종료한다."])
    ], table=(["RCA Field", "Example"], [["Problem", "Dry Etch queue spike after tool down"], ["Why 1", "capacity loss"], ["Why 2", "PM delayed"], ["Corrective", "PM window lock"], ["Preventive", "early PM alert rule"]])),
    dict(title="21. Recovery Checklist", subtitle="생산 재개 전 확인 항목", meta=[("playbook_id", "PB-RCV-001"), ("issue_type", "recovery"), ("trigger", "containment complete"), ("owner", "Operator/Engineer"), ("decision", "resume"), ("tags", "recovery,checklist")], sections=[
        ("재개 전 확인", ["설비 상태가 AVAILABLE이며 alarm이 clear 되었는가?", "영향 lot의 hold/release/disposition 상태가 확정되었는가?", "대체 장비 또는 변경 dispatch rule의 종료 조건이 명확한가?", "Scheduler가 backlog와 due date risk를 반영한 새 계획을 배포했는가?"]),
        ("재개 후 확인", ["첫 생산 lot 또는 monitor lot 결과를 확인한다.", "queue age, WIP, downstream idle이 정상 범위로 돌아오는지 추적한다."])
    ], table=(["Check Item", "Owner", "Evidence"], [["Tool available", "Operator", "status log"], ["Qual pass", "Engineer", "qual record"], ["Plan updated", "Scheduler", "new schedule"], ["RCA opened", "Engineer", "RCA id"]])),
    dict(title="22. 커뮤니케이션과 Escalation", subtitle="누가 언제 누구에게 알리는가", meta=[("playbook_id", "PB-COM-001"), ("issue_type", "communication"), ("trigger", "S1/S2/S3"), ("owner", "Manager"), ("decision", "escalate"), ("tags", "communication")], sections=[
        ("알림 기준", ["S1은 즉시 Manager와 품질 책임자에게 알리고 MRB 후보로 등록한다.", "S2는 affected product, due date, WIP impact를 포함해 shift 내 보고한다.", "S3는 local owner 중심으로 처리하되 반복 발생 시 S2로 승격한다."]),
        ("메시지 구조", ["event time, fab, area, toolgroup, affected lots, current action, decision needed, next update time을 포함한다."])
    ], table=(["Severity", "Notify", "Cadence"], [["S1", "Manager / Quality / Scheduler", "immediate"], ["S2", "Area lead / Scheduler", "within shift"], ["S3", "Local owner", "daily"], ["S4", "Log only", "review cycle"]])),
    dict(title="23. Knowledge Base 업데이트", subtitle="RAG 플레이북을 계속 개선하는 방법", meta=[("playbook_id", "PB-KB-001"), ("issue_type", "kb_update"), ("trigger", "case closure"), ("owner", "Data team"), ("decision", "add/update"), ("tags", "rag,kb")], sections=[
        ("업데이트 기준", ["새로운 원인 유형, 새로운 조치, 기존 playbook과 다른 판단이 발생하면 KB 업데이트 후보로 등록한다.", "RAG 문서에는 사례 요약과 함께 실제 수치보다 판단 기준과 증거 필드를 우선 저장한다."]),
        ("품질 기준", ["한 청크에는 하나의 이슈와 하나의 주요 의사결정만 담는다.", "태그는 issue_type, fab_area, role, action, recovery_condition 중심으로 붙인다."])
    ], table=(["KB Field", "Rule"], [["chunk_title", "specific and searchable"], ["tags", "5-8 keywords"], ["source", "public/simulated/internal"], ["validity", "draft/reviewed/approved"]])),
    dict(title="24. RAG용 예시 레코드", subtitle="검색 가능한 JSON 형태", meta=[("playbook_id", "PB-EX-001"), ("issue_type", "example"), ("format", "json"), ("owner", "Data team"), ("decision", "sample"), ("tags", "example,json")], sections=[
        ("예시", ['{"playbook_id":"PB-BN-001","fab_id":"fab10","issue_type":"bottleneck","trigger_condition":"DE_BE_11 queue age high","owner_role":"Scheduler","actions":["release throttle","alternate tool check","hot lot priority review"],"recovery_condition":"queue age normalized and downstream starvation cleared","tags":["bottleneck","wip","scheduler","recovery"]}', '{"playbook_id":"PB-HL-001","fab_id":"fab12","issue_type":"lot_hold","trigger_condition":"SPC out of control","owner_role":"Engineer","actions":["identify affected lots","hold suspect window","open RCA"],"recovery_condition":"disposition approved","tags":["hold","quality","spc"]}'])
    ], diagram=("flow", ["Query", "Retrieve Playbook", "Answer with ID", "Ask Missing Info", "Suggest Action", "Log Case"])),
    dict(title="25. 참고문헌과 사용상 주의", subtitle="공개 자료 기반 생성 문서", meta=[("playbook_id", "PB-REF-001"), ("issue_type", "references"), ("source", "public"), ("status", "generated"), ("owner", "project team"), ("tags", "references")], sections=[
        ("참고문헌", ["Kopp et al., SMT2020 - A Semiconductor Manufacturing Testbed. IEEE TSM, 2020.", "UCI Machine Learning Repository. SECOM dataset. DOI: 10.24432/C54305.", "Improvement of Hold Lot Management - A Case Study of A Company. NYCU repository.", "Constructing a production exception handling system for the semiconductor manufacturing process. Kybernetes, 2011.", "Fab-Wide Scheduling of Semiconductor Plants: Industrial Deployment Case Study. WSC, 2022.", "Chung and Jang. WIP balancing for throughput maximization in semiconductor fabrication. IEEE TSM, 2009."]),
        ("주의", ["이 문서는 실제 FAB 내부 절차서가 아니며 교육, 시뮬레이션, RAG 프로토타입을 위한 초안이다.", "실제 생산 적용 전에는 회사별 품질 시스템, EHS, MES, 승인 체계, 고객 계약 조건에 맞춰 검토해야 한다."])
    ], table=(["Document Status", "Meaning"], [["Generated", "public-source-based draft"], ["Reviewed", "domain expert checked"], ["Approved", "site-specific SOP mapped"], ["Retired", "not for retrieval"]])),
]


def draw_page(c, page_no, page):
    y = draw_title(c, page_no, page["title"], page.get("subtitle"))
    y = draw_metadata(c, y, page["meta"])

    if "diagram" in page:
        dtype, steps = page["diagram"]
        if dtype == "flow":
            draw_flow(c, MARGIN_X, y, steps, width=(PAGE_W - 2 * MARGIN_X - 40) / len(steps), height=33)
            y -= 56
        elif dtype == "bottleneck":
            draw_bottleneck_diagram(c, y - 12)
            y -= 76
        elif dtype == "tree":
            draw_decision_tree(c, y - 5)
            y -= 145
        elif dtype == "raci":
            y = draw_raci(c, y)

    for heading, bullets in page.get("sections", []):
        y = draw_section(c, y, heading, bullets)

    if "table" in page:
        headers, rows = page["table"]
        col_widths = None
        if len(headers) == 2:
            col_widths = [150, PAGE_W - 2 * MARGIN_X - 150]
        elif len(headers) == 3:
            col_widths = [110, 190, PAGE_W - 2 * MARGIN_X - 300]
        y = draw_table(c, y, headers, rows, col_widths)

    c.setFillColor(colors.HexColor("#f1f5f9"))
    c.setStrokeColor(colors.HexColor("#cbd5e1"))
    c.roundRect(MARGIN_X, BOTTOM_Y + 18, PAGE_W - 2 * MARGIN_X, 26, 3, fill=1, stroke=1)
    c.setFillColor(colors.HexColor("#475569"))
    c.setFont(FONT_REG, 7.5)
    c.drawString(MARGIN_X + 8, BOTTOM_Y + 33, "RAG note:")
    note = "검색 시 playbook_id, issue_type, trigger_condition, owner_role, recovery_condition을 우선 인덱싱한다."
    c.drawString(MARGIN_X + 55, BOTTOM_Y + 33, note)


def build_pdf():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    register_fonts()
    c = canvas.Canvas(str(OUT_PATH), pagesize=A4)
    c.setTitle("Semiconductor FAB Incident Response Playbook KR")
    c.setAuthor("OpenAI Codex generated draft")
    c.setSubject("RAG reference playbook for semiconductor FAB incident response")
    for idx, page in enumerate(pages, start=1):
        draw_page(c, idx, page)
        c.showPage()
    c.save()


if __name__ == "__main__":
    build_pdf()
    print(OUT_PATH)
