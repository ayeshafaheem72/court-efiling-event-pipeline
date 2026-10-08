"""Builds the swimlane process maps from one model, in two formats:

  docs/process/*.svg   readable on GitHub
  docs/process/*.bpmn  BPMN 2.0 XML with diagram layout; opens in bpmn.io, Camunda Modeler or Signavio

    python tools/build_diagrams.py
"""
from __future__ import annotations

from pathlib import Path
from xml.sax.saxutils import escape

OUT = Path(__file__).resolve().parent.parent / "docs" / "process"

HEADER_W, LANE_H, COL_W, LEFT_PAD = 120, 120, 150, 30
TASK_W, TASK_H, EVENT_R, GW = 124, 64, 18, 26

AS_IS = {
    "id": "as_is",
    "title": "As-is: paper filing at the court counter",
    "lanes": ["Filer (citizen or lawyer)", "Filing counter clerk", "Bank branch", "Registrar"],
    "nodes": [
        ("s", "start", 0, 0, "Needs to file a case"),
        ("t1", "task", 0, 1, "Prepare paper file"),
        ("t2", "task", 0, 2, "Queue at filing counter", "pain"),
        ("t3", "task", 1, 3, "Check documents by hand"),
        ("g1", "gateway", 1, 4, "Complete?"),
        ("t4", "task", 1, 5, "Work out fee, issue challan"),
        ("t5", "task", 2, 6, "Pay fee in person", "pain"),
        ("t6", "task", 1, 7, "Register case in ledger"),
        ("t7", "task", 3, 8, "Read file, decide category"),
        ("t8", "task", 3, 9, "Move physical file to bench", "pain"),
        ("t9", "task", 0, 10, "Visit court to check status", "pain"),
        ("e", "end", 0, 11, "Learns next step"),
    ],
    "flows": [
        ("s", "t1", ""), ("t1", "t2", ""), ("t2", "t3", ""), ("t3", "g1", ""),
        ("g1", "t1", "No: come back another day"), ("g1", "t4", "Yes"), ("t4", "t5", ""),
        ("t5", "t6", "receipt"), ("t6", "t7", ""), ("t7", "t8", ""), ("t8", "t9", ""), ("t9", "e", ""),
    ],
}

TO_BE = {
    "id": "to_be",
    "title": "To-be: online filing with event-driven follow-up",
    "lanes": ["Filer (citizen or lawyer)", "E-filing API (synchronous)", "Message queue (Redis Streams)",
              "Workers (asynchronous)", "Registrar"],
    "nodes": [
        ("s", "start", 0, 0, "Needs to file a case"),
        ("t1", "task", 0, 1, "Submit filing online"),
        ("t2", "task", 1, 2, "Validate fields and documents"),
        ("g1", "gateway", 1, 3, "Valid?"),
        ("t3", "task", 1, 4, "Save case in MongoDB, return case number"),
        ("m1", "message", 2, 5, "case.filed"),
        ("t4", "task", 3, 6, "Triage: category and priority"),
        ("m2", "message", 2, 7, "case.triaged"),
        ("t5", "task", 3, 8, "Assess filing fee"),
        ("m3", "message", 2, 9, "case.fee_assessed"),
        ("t6", "task", 3, 10, "Assign registrar queue"),
        ("m4", "message", 2, 11, "case.assigned"),
        ("t7", "task", 4, 12, "Work queue, urgent cases first"),
        ("e2", "end", 4, 13, "Case with registry"),
        ("t8", "task", 3, 12, "Notify filer"),
        ("e", "end", 0, 13, "Filer has number, fee and status"),
    ],
    "flows": [
        ("s", "t1", ""), ("t1", "t2", "POST /v1/cases"), ("t2", "g1", ""),
        ("g1", "t1", "No: 422 with field errors"), ("g1", "t3", "Yes"), ("t3", "m1", "publish"),
        ("m1", "t4", ""), ("t4", "m2", ""), ("m2", "t5", ""), ("t5", "m3", ""), ("m3", "t6", ""),
        ("t6", "m4", ""), ("m4", "t7", ""), ("t7", "e2", ""), ("m4", "t8", ""), ("t8", "e", "email"),
    ],
}


def centre(node):
    _id, _kind, lane, col, *_ = node
    return HEADER_W + LEFT_PAD + col * COL_W + COL_W / 2 - LEFT_PAD, lane * LANE_H + LANE_H / 2


def half_width(kind):
    return TASK_W / 2 if kind == "task" else GW if kind == "gateway" else EVENT_R


def bounds(node):
    x, y = centre(node)
    kind = node[1]
    if kind == "task":
        return x - TASK_W / 2, y - TASK_H / 2, TASK_W, TASK_H
    if kind == "gateway":
        return x - GW, y - GW, 2 * GW, 2 * GW
    return x - EVENT_R, y - EVENT_R, 2 * EVENT_R, 2 * EVENT_R


def route(a, b, n_lanes):
    """Orthogonal waypoints from node a to node b."""
    (ax, ay), (bx, by) = centre(a), centre(b)
    if b[3] > a[3]:  # forward
        sx, tx = ax + half_width(a[1]), bx - half_width(b[1])
        if ay == by:
            return [(sx, ay), (tx, by)]
        mx = (sx + tx) / 2
        return [(sx, ay), (mx, ay), (mx, by), (tx, by)]
    # backward loop: leave from the bottom, run along the lane edge, enter the target from below
    lane_edge = max(a[2], b[2]) * LANE_H + LANE_H - 10 if a[2] != b[2] else ay + LANE_H / 2 - 10
    sy = ay + (GW if a[1] == "gateway" else TASK_H / 2)
    ty = by + (TASK_H / 2 if b[1] == "task" else EVENT_R)
    if a[2] > b[2]:  # going up to an earlier lane: enter target from its bottom edge
        return [(ax, sy), (ax, lane_edge), (bx, lane_edge), (bx, ty)]
    return [(ax, sy), (ax, lane_edge), (bx, lane_edge), (bx, ty)]


def wrap(text, width=18):
    words, lines, line = text.split(), [], ""
    for w in words:
        if len(line) + len(w) + 1 > width and line:
            lines.append(line)
            line = w
        else:
            line = f"{line} {w}".strip()
    lines.append(line)
    return lines


def svg(model) -> str:
    nodes = {n[0]: n for n in model["nodes"]}
    cols = max(n[3] for n in model["nodes"]) + 1
    W, H = HEADER_W + cols * COL_W + 20, len(model["lanes"]) * LANE_H
    top = 44
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H + top + 30}" '
           f'viewBox="0 0 {W} {H + top + 30}" font-family="Segoe UI, Arial, sans-serif">',
           '<defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" '
           'orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="#3b4a5a"/></marker></defs>',
           f'<rect width="{W}" height="{H + top + 30}" fill="#ffffff"/>',
           f'<text x="12" y="28" font-size="17" font-weight="600" fill="#1f3864">{escape(model["title"])}</text>',
           f'<g transform="translate(0,{top})">']
    for i, lane in enumerate(model["lanes"]):
        fill = "#f5f8fc" if i % 2 == 0 else "#ffffff"
        out.append(f'<rect x="0" y="{i * LANE_H}" width="{W}" height="{LANE_H}" fill="{fill}" stroke="#c9d3df"/>')
        out.append(f'<rect x="0" y="{i * LANE_H}" width="{HEADER_W}" height="{LANE_H}" fill="#1f3864"/>')
        for j, line in enumerate(wrap(lane, 16)):
            out.append(f'<text x="{HEADER_W / 2}" y="{i * LANE_H + LANE_H / 2 - 8 + j * 15}" text-anchor="middle" '
                       f'font-size="12" fill="#ffffff" font-weight="600">{escape(line)}</text>')
    for a, b, label in model["flows"]:
        pts = route(nodes[a], nodes[b], len(model["lanes"]))
        d = " ".join(f"{'M' if k == 0 else 'L'}{x:.0f},{y:.0f}" for k, (x, y) in enumerate(pts))
        dashed = ' stroke-dasharray="5,4"' if nodes[a][1] == "message" or nodes[b][1] == "message" else ""
        out.append(f'<path d="{d}" fill="none" stroke="#3b4a5a" stroke-width="1.4"{dashed} marker-end="url(#arrow)"/>')
        if label:
            lx, ly = pts[1] if len(pts) > 2 else ((pts[0][0] + pts[1][0]) / 2, pts[0][1])
            out.append(f'<text x="{lx + 4:.0f}" y="{ly - 5:.0f}" font-size="10.5" fill="#5a6b7d">{escape(label)}</text>')
    for n in model["nodes"]:
        nid, kind, lane, col, label, *flags = n
        x, y = centre(n)
        pain = "pain" in flags
        if kind == "task":
            stroke = "#c0392b" if pain else "#1f3864"
            out.append(f'<rect x="{x - TASK_W / 2}" y="{y - TASK_H / 2}" width="{TASK_W}" height="{TASK_H}" rx="9" '
                       f'fill="#ffffff" stroke="{stroke}" stroke-width="{2.2 if pain else 1.6}"/>')
            lines = wrap(label)
            for j, line in enumerate(lines):
                out.append(f'<text x="{x}" y="{y + 4 + (j - (len(lines) - 1) / 2) * 14:.0f}" text-anchor="middle" '
                           f'font-size="11.5" fill="#1b2733">{escape(line)}</text>')
            if pain:
                out.append(f'<circle cx="{x + TASK_W / 2 - 4}" cy="{y - TASK_H / 2 + 4}" r="8" fill="#c0392b"/>'
                           f'<text x="{x + TASK_W / 2 - 4}" y="{y - TASK_H / 2 + 8}" text-anchor="middle" '
                           f'font-size="11" font-weight="700" fill="#fff">!</text>')
        elif kind == "gateway":
            out.append(f'<polygon points="{x},{y - GW} {x + GW},{y} {x},{y + GW} {x - GW},{y}" fill="#fff8e1" '
                       f'stroke="#b7791f" stroke-width="1.6"/>'
                       f'<text x="{x}" y="{y + 5}" text-anchor="middle" font-size="16" fill="#b7791f">×</text>'
                       f'<text x="{x}" y="{y - GW - 6}" text-anchor="middle" font-size="11" fill="#1b2733">{escape(label)}</text>')
        else:
            styles = {"start": ("#e8f5e9", "#2e7d32", 1.6), "end": ("#fdecea", "#c0392b", 3.2),
                      "message": ("#eef2ff", "#4054b2", 1.6)}
            fill, stroke, sw = styles[kind]
            out.append(f'<circle cx="{x}" cy="{y}" r="{EVENT_R}" fill="{fill}" stroke="{stroke}" stroke-width="{sw}"/>')
            if kind == "message":
                out.append(f'<rect x="{x - 9}" y="{y - 6}" width="18" height="12" fill="none" stroke="{stroke}"/>'
                           f'<path d="M{x - 9},{y - 6} L{x},{y + 1} L{x + 9},{y - 6}" fill="none" stroke="{stroke}"/>')
            for j, line in enumerate(wrap(label, 20)):
                out.append(f'<text x="{x}" y="{y + EVENT_R + 14 + j * 13}" text-anchor="middle" font-size="10.5" '
                           f'fill="#1b2733">{escape(line)}</text>')
    out.append("</g>")
    if any("pain" in n[5:] for n in model["nodes"]):
        out.append(f'<circle cx="18" cy="{H + top + 16}" r="7" fill="#c0392b"/><text x="32" y="{H + top + 20}" '
                   f'font-size="11.5" fill="#1b2733">Pain point: waiting, travel or a manual hand-off</text>')
    else:
        out.append(f'<text x="12" y="{H + top + 20}" font-size="11.5" fill="#1b2733">Solid arrows: synchronous '
                   f'request/response. Dashed arrows: asynchronous events on the queue.</text>')
    out.append("</svg>")
    return "\n".join(out)


def bpmn(model) -> str:
    """BPMN 2.0 XML with lanes and diagram interchange, so tools draw it exactly like the SVG."""
    p = model["id"]
    nodes = {n[0]: n for n in model["nodes"]}
    tag = {"start": "startEvent", "end": "endEvent", "task": "task", "gateway": "exclusiveGateway",
           "message": "intermediateThrowEvent"}
    incoming = {n: [] for n in nodes}
    outgoing = {n: [] for n in nodes}
    for i, (a, b, _) in enumerate(model["flows"]):
        outgoing[a].append(f"{p}_f{i}")
        incoming[b].append(f"{p}_f{i}")
    cols = max(n[3] for n in model["nodes"]) + 1
    W, H = HEADER_W + cols * COL_W + 20, len(model["lanes"]) * LANE_H

    x = ['<?xml version="1.0" encoding="UTF-8"?>',
         '<bpmn:definitions xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL" '
         'xmlns:bpmndi="http://www.omg.org/spec/BPMN/20100524/DI" xmlns:dc="http://www.omg.org/spec/DD/20100524/DC" '
         'xmlns:di="http://www.omg.org/spec/DD/20100524/DI" '
         f'id="{p}_defs" targetNamespace="https://github.com/ayeshafaheem72/court-efiling-event-pipeline">',
         f'  <bpmn:collaboration id="{p}_collab">',
         f'    <bpmn:participant id="{p}_pool" name="{escape(model["title"])}" processRef="{p}_process"/>',
         '  </bpmn:collaboration>',
         f'  <bpmn:process id="{p}_process" isExecutable="false">',
         f'    <bpmn:laneSet id="{p}_lanes">']
    for i, lane in enumerate(model["lanes"]):
        x.append(f'      <bpmn:lane id="{p}_lane{i}" name="{escape(lane)}">')
        x += [f'        <bpmn:flowNodeRef>{p}_{n[0]}</bpmn:flowNodeRef>' for n in model["nodes"] if n[2] == i]
        x.append('      </bpmn:lane>')
    x.append('    </bpmn:laneSet>')
    for n in model["nodes"]:
        nid, kind, *_rest = n
        x.append(f'    <bpmn:{tag[kind]} id="{p}_{nid}" name="{escape(n[4])}">')
        x += [f'      <bpmn:incoming>{f}</bpmn:incoming>' for f in incoming[nid]]
        x += [f'      <bpmn:outgoing>{f}</bpmn:outgoing>' for f in outgoing[nid]]
        if kind == "message":
            x.append(f'      <bpmn:messageEventDefinition id="{p}_{nid}_msg"/>')
        x.append(f'    </bpmn:{tag[kind]}>')
    for i, (a, b, label) in enumerate(model["flows"]):
        name = f' name="{escape(label)}"' if label else ""
        x.append(f'    <bpmn:sequenceFlow id="{p}_f{i}"{name} sourceRef="{p}_{a}" targetRef="{p}_{b}"/>')
    x += ['  </bpmn:process>',
          f'  <bpmndi:BPMNDiagram id="{p}_diagram">',
          f'    <bpmndi:BPMNPlane id="{p}_plane" bpmnElement="{p}_collab">',
          f'      <bpmndi:BPMNShape id="{p}_pool_di" bpmnElement="{p}_pool" isHorizontal="true">'
          f'<dc:Bounds x="0" y="0" width="{W}" height="{H}"/></bpmndi:BPMNShape>']
    for i, _ in enumerate(model["lanes"]):
        x.append(f'      <bpmndi:BPMNShape id="{p}_lane{i}_di" bpmnElement="{p}_lane{i}" isHorizontal="true">'
                 f'<dc:Bounds x="30" y="{i * LANE_H}" width="{W - 30}" height="{LANE_H}"/></bpmndi:BPMNShape>')
    for n in model["nodes"]:
        bx, by, bw, bh = bounds(n)
        marker = ' isMarkerVisible="true"' if n[1] == "gateway" else ""
        x.append(f'      <bpmndi:BPMNShape id="{p}_{n[0]}_di" bpmnElement="{p}_{n[0]}"{marker}>'
                 f'<dc:Bounds x="{bx:.0f}" y="{by:.0f}" width="{bw:.0f}" height="{bh:.0f}"/></bpmndi:BPMNShape>')
    for i, (a, b, _) in enumerate(model["flows"]):
        pts = "".join(f'<di:waypoint x="{px:.0f}" y="{py:.0f}"/>' for px, py in route(nodes[a], nodes[b], 0))
        x.append(f'      <bpmndi:BPMNEdge id="{p}_f{i}_di" bpmnElement="{p}_f{i}">{pts}</bpmndi:BPMNEdge>')
    x += ['    </bpmndi:BPMNPlane>', '  </bpmndi:BPMNDiagram>', '</bpmn:definitions>']
    return "\n".join(x)


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    for model in (AS_IS, TO_BE):
        (OUT / f"{model['id']}.svg").write_text(svg(model), encoding="utf-8")
        (OUT / f"{model['id']}.bpmn").write_text(bpmn(model), encoding="utf-8")
        print("wrote", model["id"])
