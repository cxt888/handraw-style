#!/usr/bin/env python3
"""Check publication copy against the adjacent writing-rules.md word list.

Exit 0: no hard violations (warnings may remain); 1: hard violations; 2: input
or rules error. This is a rule checker, never an AI probability detector.
"""

import argparse
from bisect import bisect_right
import json
from pathlib import Path
import re
import sys


SENTENCE_END = re.compile(r"[。！？!?；;]|\r?\n[ \t\u3000]*\r?\n")
RULES_PATH = Path(__file__).resolve().parent.parent / "references" / "writing-rules.md"


def load_rules():
    source = RULES_PATH.read_text(encoding="utf-8-sig")
    blocks = re.findall(r"```json\s*\n(.*?)\n```", source, re.DOTALL)
    if len(blocks) != 1:
        raise ValueError("writing-rules.md must contain exactly one JSON fence")
    rules = json.loads(blocks[0])
    for key in ("forbidden_groups", "caution_phrases", "patterns"):
        if not isinstance(rules.get(key), list):
            raise ValueError("Missing or invalid rule list: " + key)
    return rules


def sentence_spans(source):
    """Yield sentence/paragraph spans; a single wrapped line stays in its sentence."""
    start = 0
    for boundary in SENTENCE_END.finditer(source):
        if start < boundary.start():
            yield start, boundary.start()
        start = boundary.end()
    if start < len(source):
        yield start, len(source)


def scan(source, rules):
    hits = []

    def add(start, end, severity, rule_id, label):
        hits.append({"start": start, "end": end, "severity": severity,
                     "rules": [{"id": rule_id, "label": label}]})

    for group_index, group in enumerate(rules["forbidden_groups"], 1):
        for phrase in group["phrases"]:
            if not phrase:
                raise ValueError("Empty forbidden phrase")
            for match in re.finditer(re.escape(phrase), source):
                add(match.start(), match.end(), "error", "literal:" + phrase,
                    group["name"] + "：" + phrase)
    for phrase in rules["caution_phrases"]:
        if not phrase:
            raise ValueError("Empty caution phrase")
        for match in re.finditer(re.escape(phrase), source):
            add(match.start(), match.end(), "warning", "caution:" + phrase,
                "谨慎使用词：" + phrase)

    sentences = list(sentence_spans(source))
    for rule in rules["patterns"]:
        kind = rule["kind"]
        if kind == "pair":
            matcher = re.compile(re.escape(rule["first"]) + r"[\s\S]*?" +
                                 re.escape(rule["second"]))
        elif kind == "regex":
            matcher = re.compile(rule["regex"])
        elif kind == "sentence_start":
            # Newlines also begin display-copy sentences. Permit ordinary Markdown
            # list/heading prefixes and opening quotation marks before the phrase.
            matcher = re.compile(
                r"(?:^|[。！？!?；;\r\n])[ \t\u3000]*"
                r"(?:#{1,6}\s*|>\s*|[-*+]\s*|\d+[.、）)]\s*)?"
                r"[“‘\"'（(]*[ \t\u3000]*(?P<phrase>" +
                re.escape(rule["phrase"]) + r")")
            for match in matcher.finditer(source):
                add(match.start("phrase"), match.end("phrase"), "error",
                    "pattern:" + rule["id"], rule["label"])
            continue
        else:
            raise ValueError("Unknown pattern kind: " + str(kind))
        for start, end in sentences:
            for match in matcher.finditer(source[start:end]):
                add(start + match.start(), start + match.end(), "error",
                    "pattern:" + rule["id"], rule["label"])

    # Overlapping phrases and patterns form one finding, with every associated
    # rule retained. Adjacent, non-overlapping words remain separate findings.
    merged = []
    for hit in sorted(hits, key=lambda item: (item["start"], item["end"])):
        if merged and hit["start"] < merged[-1]["end"]:
            previous = merged[-1]
            previous["end"] = max(previous["end"], hit["end"])
            if hit["severity"] == "error":
                previous["severity"] = "error"
            for rule in hit["rules"]:
                if rule not in previous["rules"]:
                    previous["rules"].append(rule)
        else:
            merged.append(hit)

    line_starts = [0] + [match.end() for match in re.finditer(r"\n", source)]
    findings = []
    for hit in merged:
        start, end = hit["start"], hit["end"]
        line = bisect_right(line_starts, start)
        end_line = bisect_right(line_starts, max(start, end - 1))
        findings.append({
            "severity": hit["severity"],
            "line": line,
            "end_line": end_line,
            "column": start - line_starts[line - 1] + 1,
            "match": source[start:end],
            "snippet": source[max(0, start - 24):min(len(source), end + 24)],
            "rules": hit["rules"],
        })
    return findings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("file", type=Path, help="one UTF-8 text file to check")
    parser.add_argument("--json", action="store_true", help="emit structured JSON")
    args = parser.parse_args()
    try:
        source = args.file.read_text(encoding="utf-8-sig")
        findings = scan(source, load_rules())
    except (OSError, UnicodeError, ValueError, KeyError, TypeError, re.error) as exc:
        if args.json:
            print(json.dumps({"file": str(args.file), "status": "input_error",
                              "message": str(exc)}, ensure_ascii=False))
        else:
            print("检查失败：" + str(exc), file=sys.stderr)
        return 2

    errors = sum(item["severity"] == "error" for item in findings)
    warnings = sum(item["severity"] == "warning" for item in findings)
    note = "仅检查已列规则；未命中不代表没有套话，也不表示 AI 概率为 0。"
    report = {
        "file": str(args.file),
        "status": "fail" if errors else ("warnings" if warnings else "no_rule_hits"),
        "error_count": errors,
        "warning_count": warnings,
        "findings": findings,
        "note": note,
    }
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        for item in findings:
            position = str(item["line"])
            if item["end_line"] != item["line"]:
                position += "-" + str(item["end_line"])
            labels = "；".join(rule["label"] for rule in item["rules"])
            snippet = item["snippet"].replace("\r", "").replace("\n", " ↵ ")
            print("{severity} L{line}:{column} {labels}\n  {snippet}".format(
                severity=item["severity"].upper(), line=position,
                column=item["column"], labels=labels, snippet=snippet))
        print("硬性命中 {} 处，谨慎词提示 {} 处。{}".format(errors, warnings, note))
    return 1 if errors else 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
    raise SystemExit(main())
