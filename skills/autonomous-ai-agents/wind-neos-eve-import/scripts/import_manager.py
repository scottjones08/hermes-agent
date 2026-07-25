#!/usr/bin/env python3
"""Private, idempotent control plane for the Wind Neos -> Eve import.

The browser/agent performs vendor UI operations. This script owns the durable
parts that must not depend on the browser:

* explicit exclusions
* open-case filtering
* duplicate prevention and reconciliation state
* notes change detection
* private run manifests
* production of the stable DOCX uploaded through SharePoint

No client data is written to the repository. The default private data home is
~/.hermes/private/wind-neos-eve-import and can be changed with
NEOS_EVE_IMPORT_HOME.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import sys
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from xml.sax.saxutils import escape


VERSION = 1
MUTATING_ACTIONS = {"create_and_sync", "update_summary"}
OPEN_WORDS = {"open", "active", "prelit", "pre-lit", "litigation"}
CLOSED_WORDS = {"closed", "inactive", "rejected", "declined", "archived"}

CASE_NUMBER_HEADERS = (
    "Case Number",
    "Case No",
    "Case No.",
    "Case #",
    "File Number",
    "Matter Number",
    "case_number",
)
CASE_NAME_HEADERS = (
    "Case Name",
    "Matter Name",
    "Name",
    "Client Name",
    "case_name",
)
CASE_ID_HEADERS = ("Case ID", "Neos Case ID", "Matter ID", "case_id", "id")
CASE_STATUS_HEADERS = ("Current Status", "Status", "Case Status", "status")

NOTE_CASE_HEADERS = CASE_NUMBER_HEADERS
NOTE_BODY_HEADERS = ("Note", "Body", "Note Body", "Text", "Description", "body")
NOTE_DATE_HEADERS = (
    "Entry Date",
    "Date",
    "Date Created",
    "Created",
    "note_date",
)
NOTE_TYPE_HEADERS = ("Note Type", "Type", "Channel", "note_type")
NOTE_SUBJECT_HEADERS = ("Subject", "Topic", "Title", "subject")
NOTE_DIRECTION_HEADERS = ("Direction", "Inbound/Outbound", "direction")
NOTE_STAFF_HEADERS = ("Staff", "Staff Created", "Created By", "Author", "staff")
NOTE_ID_HEADERS = ("Entry ID", "Note ID", "ID", "entry_id")


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def data_home() -> Path:
    raw = os.environ.get("NEOS_EVE_IMPORT_HOME")
    return Path(raw).expanduser() if raw else Path.home() / ".hermes" / "private" / "wind-neos-eve-import"


def default_exclusions() -> Path:
    return data_home() / "exclusions.json"


def default_state() -> Path:
    return data_home() / "state.json"


def normalize(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", "", (value or "").strip().lower())


def clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def pick(row: dict[str, Any], candidates: Iterable[str]) -> str:
    normalized = {normalize(k): clean(v) for k, v in row.items()}
    for candidate in candidates:
        value = normalized.get(normalize(candidate), "")
        if value:
            return value
    return ""


def case_key(case_number: str, neos_case_id: str = "") -> str:
    if clean(neos_case_id):
        return f"id:{normalize(neos_case_id)}"
    return f"number:{normalize(case_number)}"


def is_open_status(status: str) -> bool:
    """Treat a blank/unknown status as open because input is the Open Case report.

    Explicitly closed-like statuses are always rejected. This provides a second
    safety check without dropping rows when the saved Open Case List omits the
    status column.
    """
    value = clean(status).lower()
    if not value:
        return True
    if any(word in value for word in CLOSED_WORDS):
        return False
    if any(word in value for word in OPEN_WORDS):
        return True
    return True


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def parse_cases(path: Path) -> list[dict[str, str]]:
    cases: dict[str, dict[str, str]] = {}
    for row in read_csv(path):
        number = pick(row, CASE_NUMBER_HEADERS)
        if not number:
            continue
        neos_id = pick(row, CASE_ID_HEADERS)
        key = case_key(number, neos_id)
        cases[key] = {
            "key": key,
            "case_number": number,
            "case_name": pick(row, CASE_NAME_HEADERS),
            "neos_case_id": neos_id,
            "status": pick(row, CASE_STATUS_HEADERS),
        }
    return sorted(cases.values(), key=lambda item: normalize(item["case_number"]))


def parse_notes(path: Path) -> dict[str, list[dict[str, str]]]:
    grouped: dict[str, list[dict[str, str]]] = {}
    for row in read_csv(path):
        number = pick(row, NOTE_CASE_HEADERS)
        body = pick(row, NOTE_BODY_HEADERS)
        if not number or not body:
            continue
        note = {
            "case_number": number,
            "entry_id": pick(row, NOTE_ID_HEADERS),
            "entry_date": pick(row, NOTE_DATE_HEADERS),
            "note_type": pick(row, NOTE_TYPE_HEADERS),
            "subject": pick(row, NOTE_SUBJECT_HEADERS),
            "direction": pick(row, NOTE_DIRECTION_HEADERS),
            "staff": pick(row, NOTE_STAFF_HEADERS),
            "body": body,
        }
        grouped.setdefault(normalize(number), []).append(note)
    for notes in grouped.values():
        notes.sort(key=lambda item: (item["entry_date"], item["entry_id"], item["body"]))
    return grouped


def note_fingerprint(notes: list[dict[str, str]]) -> str:
    payload = json.dumps(notes, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def read_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return default
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Cannot read {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise SystemExit(f"Expected a JSON object in {path}")
    return value


def atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, indent=2, ensure_ascii=False) + "\n"
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def secure_text_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    os.chmod(path, 0o600)


def exclusions_data(path: Path) -> dict[str, Any]:
    value = read_json(path, {"version": VERSION, "exclusions": {}})
    value.setdefault("version", VERSION)
    value.setdefault("exclusions", {})
    if not isinstance(value["exclusions"], dict):
        raise SystemExit(f"Invalid exclusions collection in {path}")
    return value


def state_data(path: Path) -> dict[str, Any]:
    value = read_json(path, {"version": VERSION, "cases": {}})
    value.setdefault("version", VERSION)
    value.setdefault("cases", {})
    if not isinstance(value["cases"], dict):
        raise SystemExit(f"Invalid cases collection in {path}")
    return value


def find_exclusion(
    exclusions: dict[str, Any], case_number: str, neos_case_id: str
) -> dict[str, Any] | None:
    number_key = f"number:{normalize(case_number)}"
    id_key = f"id:{normalize(neos_case_id)}" if neos_case_id else ""
    for key in (id_key, number_key):
        if key and key in exclusions.get("exclusions", {}):
            item = exclusions["exclusions"][key]
            if item.get("active", True):
                return item
    wanted_number = normalize(case_number)
    for item in exclusions.get("exclusions", {}).values():
        if item.get("active", True) and normalize(item.get("case_number", "")) == wanted_number:
            return item
    return None


def find_state_record(
    state: dict[str, Any], case_number: str, neos_case_id: str = ""
) -> tuple[str, dict[str, Any] | None]:
    preferred = case_key(case_number, neos_case_id)
    cases = state.get("cases", {})
    if preferred in cases:
        return preferred, cases[preferred]
    wanted_number = normalize(case_number)
    for key, value in cases.items():
        if normalize(value.get("case_number", "")) == wanted_number:
            return key, value
    return preferred, None


def private_output_guard(path: Path) -> None:
    resolved = path.expanduser().resolve()
    for parent in (resolved, *resolved.parents):
        if (parent / ".git").exists():
            raise SystemExit(
                f"REFUSED: output {resolved} is inside the git repository {parent}. "
                "Use NEOS_EVE_IMPORT_HOME or --output outside the repo."
            )


def note_source_document(case: dict[str, str], notes: list[dict[str, str]]) -> str:
    lines = [
        "# Neos Notes Source",
        "",
        f"Case number: {case['case_number']}",
        f"Case name: {case['case_name']}",
        f"Exported notes: {len(notes)}",
        "",
        "This is a private source packet for generating the Eve summary. "
        "Neos remains the authoritative record.",
        "",
    ]
    for index, note in enumerate(notes, 1):
        metadata = " | ".join(
            value
            for value in (
                note["entry_date"],
                note["note_type"],
                note["direction"],
                note["staff"],
                note["subject"],
            )
            if value
        )
        lines.extend([f"## Note {index}", metadata or "No metadata", "", note["body"], ""])
    return "\n".join(lines)


def build_plan(args: argparse.Namespace) -> int:
    cases = parse_cases(Path(args.cases))
    notes = parse_notes(Path(args.notes))
    exclusions = exclusions_data(Path(args.exclusions))
    state = state_data(Path(args.state))
    output = (
        Path(args.output).expanduser()
        if args.output
        else data_home() / "runs" / datetime.now().strftime("%Y%m%dT%H%M%S")
    )
    private_output_guard(output)
    output.mkdir(parents=True, exist_ok=True)
    os.chmod(output, 0o700)

    actions: list[dict[str, Any]] = []
    mutation_count = 0
    for case in cases:
        number = case["case_number"]
        case_notes = notes.get(normalize(number), [])
        fingerprint = note_fingerprint(case_notes)
        exclusion = find_exclusion(exclusions, number, case["neos_case_id"])
        state_key, existing = find_state_record(state, number, case["neos_case_id"])
        action = ""
        reason = ""

        if exclusion:
            action = "excluded"
            reason = exclusion.get("reason", "Explicit exclusion")
        elif not is_open_status(case["status"]):
            action = "skipped_not_open"
            reason = f"Neos status is {case['status']!r}"
        elif not case["case_name"]:
            action = "blocked_missing_name"
            reason = "Case name is required before an Eve matter can be created"
        elif not existing or not existing.get("eve_matter_id"):
            action = "create_and_sync"
        elif existing.get("last_notes_fingerprint") != fingerprint:
            action = "update_summary"
        else:
            action = "noop"
            reason = "Eve matter exists and Neos notes have not changed"

        if action in MUTATING_ACTIONS:
            if mutation_count >= args.max_actions:
                action = "deferred"
                reason = f"Run cap of {args.max_actions} mutating actions reached"
            else:
                mutation_count += 1

        item: dict[str, Any] = {
            "action": action,
            "reason": reason,
            "state_key": state_key,
            "case_number": number,
            "case_name": case["case_name"],
            "neos_case_id": case["neos_case_id"],
            "neos_status": case["status"],
            "eve_matter_name": f"{number} - {case['case_name']}" if case["case_name"] else "",
            "sharepoint_folder_query": number,
            "note_count": len(case_notes),
            "notes_fingerprint": fingerprint,
            "existing_eve_matter_id": (existing or {}).get("eve_matter_id", ""),
            "existing_eve_matter_url": (existing or {}).get("eve_matter_url", ""),
        }
        if action in MUTATING_ACTIONS:
            case_dir = output / re.sub(r"[^A-Za-z0-9._-]+", "_", number)
            source_path = case_dir / "Neos Notes Source.md"
            source_json = case_dir / "notes.json"
            secure_text_write(source_path, note_source_document(case, case_notes))
            atomic_write_json(
                source_json,
                {
                    "version": VERSION,
                    "case": case,
                    "notes_fingerprint": fingerprint,
                    "notes": case_notes,
                },
            )
            item["notes_source_path"] = str(source_path)
            item["notes_json_path"] = str(source_json)
            item["summary_docx_path"] = str(case_dir / "Neos Notes Summary.docx")
        actions.append(item)

    manifest = {
        "version": VERSION,
        "created_at": now_iso(),
        "source": {
            "cases_csv": str(Path(args.cases).expanduser().resolve()),
            "notes_csv": str(Path(args.notes).expanduser().resolve()),
        },
        "controls": {
            "open_cases_only": True,
            "max_actions": args.max_actions,
            "exclusions_file": str(Path(args.exclusions).expanduser().resolve()),
            "state_file": str(Path(args.state).expanduser().resolve()),
        },
        "counts": {
            name: sum(1 for item in actions if item["action"] == name)
            for name in sorted({item["action"] for item in actions})
        },
        "actions": actions,
    }
    manifest_path = output / "manifest.json"
    atomic_write_json(manifest_path, manifest)
    print(str(manifest_path))
    print(json.dumps(manifest["counts"], sort_keys=True))
    return 0


def minimal_docx(path: Path, paragraphs: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = []
    for paragraph in paragraphs:
        if not paragraph:
            body.append("<w:p/>")
            continue
        style = ""
        text = paragraph
        if paragraph.startswith("# "):
            style = '<w:pPr><w:pStyle w:val="Title"/></w:pPr>'
            text = paragraph[2:]
        elif paragraph.startswith("## "):
            style = '<w:pPr><w:pStyle w:val="Heading1"/></w:pPr>'
            text = paragraph[3:]
        body.append(
            f'<w:p>{style}<w:r><w:t xml:space="preserve">{escape(text)}</w:t></w:r></w:p>'
        )
    document_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{''.join(body)}"
        '<w:sectPr><w:pgSz w:w="12240" w:h="15840"/>'
        '<w:pgMar w:top="1080" w:right="1080" w:bottom="1080" w:left="1080"/></w:sectPr>'
        "</w:body></w:document>"
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        '<Override PartName="/word/styles.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
        "</Types>"
    )
    relationships = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="word/document.xml"/></Relationships>'
    )
    document_relationships = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" '
        'Target="styles.xml"/></Relationships>'
    )
    styles = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        '<w:style w:type="paragraph" w:default="1" w:styleId="Normal">'
        '<w:name w:val="Normal"/></w:style>'
        '<w:style w:type="paragraph" w:styleId="Title">'
        '<w:name w:val="Title"/><w:basedOn w:val="Normal"/>'
        '<w:rPr><w:b/><w:sz w:val="32"/></w:rPr></w:style>'
        '<w:style w:type="paragraph" w:styleId="Heading1">'
        '<w:name w:val="heading 1"/><w:basedOn w:val="Normal"/>'
        '<w:rPr><w:b/><w:sz w:val="26"/></w:rPr></w:style>'
        "</w:styles>"
    )
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", relationships)
        archive.writestr("word/document.xml", document_xml)
        archive.writestr("word/_rels/document.xml.rels", document_relationships)
        archive.writestr("word/styles.xml", styles)
    os.chmod(path, 0o600)


def finalize_summary(args: argparse.Namespace) -> int:
    summary_path = Path(args.summary_file)
    summary = summary_path.read_text(encoding="utf-8").strip()
    required = (
        "Matter Snapshot",
        "Executive Summary",
        "Communications Summary",
        "Open Items",
        "Recent Timeline",
        "Source and Safety",
    )
    missing = [section for section in required if section.lower() not in summary.lower()]
    if missing and not args.allow_incomplete:
        raise SystemExit("Summary is missing required sections: " + ", ".join(missing))
    output = Path(args.output)
    private_output_guard(output)
    minimal_docx(output, summary.splitlines())
    print(str(output))
    return 0


def exclusion_command(args: argparse.Namespace) -> int:
    path = Path(args.exclusions)
    data = exclusions_data(path)
    if args.exclusion_action == "list":
        print(json.dumps(list(data["exclusions"].values()), indent=2, ensure_ascii=False))
        return 0
    key = case_key(args.case_number, args.neos_case_id)
    if args.exclusion_action == "add":
        data["exclusions"][key] = {
            "key": key,
            "case_number": clean(args.case_number),
            "neos_case_id": clean(args.neos_case_id),
            "reason": clean(args.reason) or "Do not import",
            "active": True,
            "added_at": now_iso(),
            "added_by": clean(args.added_by) or "operator",
        }
    elif args.exclusion_action == "remove":
        removed = data["exclusions"].pop(key, None)
        if removed is None and not args.neos_case_id:
            wanted = normalize(args.case_number)
            match = next(
                (
                    existing_key
                    for existing_key, item in data["exclusions"].items()
                    if normalize(item.get("case_number", "")) == wanted
                ),
                None,
            )
            if match:
                data["exclusions"].pop(match)
    atomic_write_json(path, data)
    print(str(path))
    return 0


def record_command(args: argparse.Namespace) -> int:
    path = Path(args.state)
    state = state_data(path)
    key, existing = find_state_record(state, args.case_number, args.neos_case_id)
    record = dict(existing or {})
    record.update(
        {
            "case_number": clean(args.case_number),
            "neos_case_id": clean(args.neos_case_id) or record.get("neos_case_id", ""),
            "status": args.status,
            "updated_at": now_iso(),
        }
    )
    for attr in ("eve_matter_id", "eve_matter_url", "notes_fingerprint", "error"):
        value = clean(getattr(args, attr))
        if value:
            target = "last_notes_fingerprint" if attr == "notes_fingerprint" else attr
            record[target] = value
    if args.status not in {"failed", "blocked"} and not clean(args.error):
        record.pop("error", None)
    state["cases"][key] = record
    atomic_write_json(path, state)
    print(str(path))
    return 0


def init_command(args: argparse.Namespace) -> int:
    home = data_home()
    home.mkdir(parents=True, exist_ok=True)
    os.chmod(home, 0o700)
    exclusion_path = Path(args.exclusions)
    state_path = Path(args.state)
    if not exclusion_path.exists():
        atomic_write_json(exclusion_path, {"version": VERSION, "exclusions": {}})
    if not state_path.exists():
        atomic_write_json(state_path, {"version": VERSION, "cases": {}})
    print(json.dumps({"home": str(home), "exclusions": str(exclusion_path), "state": str(state_path)}))
    return 0


def self_test() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        cases = root / "open_cases.csv"
        notes = root / "notes.csv"
        exclusions = root / "exclusions.json"
        state = root / "state.json"
        output = root / "run"
        cases.write_text(
            "Case Number,Case Name,Current Status,Case ID\n"
            "24-001,Alpha v. Beta,Open,c1\n"
            "24-002,Gamma v. Delta,Closed,c2\n"
            "24-003,Epsilon v. Zeta,Active,c3\n",
            encoding="utf-8",
        )
        notes.write_text(
            "Case No,Entry Date,Type,Note\n"
            "24-001,2026-07-01,Email,Client asked for a status update.\n"
            "24-001,2026-07-02,Text,Firm confirmed records were requested.\n"
            "24-003,2026-07-03,Call,Left voicemail.\n",
            encoding="utf-8",
        )
        atomic_write_json(
            exclusions,
            {
                "version": VERSION,
                "exclusions": {
                    "number:24003": {
                        "case_number": "24-003",
                        "active": True,
                        "reason": "test",
                    }
                },
            },
        )
        atomic_write_json(state, {"version": VERSION, "cases": {}})
        args = argparse.Namespace(
            cases=str(cases),
            notes=str(notes),
            exclusions=str(exclusions),
            state=str(state),
            output=str(output),
            max_actions=5,
        )
        build_plan(args)
        manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["counts"] == {
            "create_and_sync": 1,
            "excluded": 1,
            "skipped_not_open": 1,
        }
        assert manifest["actions"][0]["eve_matter_name"] == "24-001 - Alpha v. Beta"
        assert (output / "24-001" / "notes.json").exists()
        summary = root / "summary.md"
        summary.write_text(
            "# Neos Notes Summary\n\n"
            "## Matter Snapshot\nx\n## Executive Summary\nx\n"
            "## Communications Summary\nx\n## Open Items\nx\n"
            "## Recent Timeline\nx\n## Source and Safety\nx\n",
            encoding="utf-8",
        )
        docx = root / "Neos Notes Summary.docx"
        finalize_summary(
            argparse.Namespace(
                summary_file=str(summary), output=str(docx), allow_incomplete=False
            )
        )
        assert zipfile.is_zipfile(docx)
    print("self-test passed")
    return 0


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="create private state and exclusions files")
    init.add_argument("--exclusions", default=str(default_exclusions()))
    init.add_argument("--state", default=str(default_state()))
    init.set_defaults(func=init_command)

    exclude = sub.add_parser("exclude", help="manage the do-not-import list")
    exclude.add_argument("exclusion_action", choices=("add", "remove", "list"))
    exclude.add_argument("case_number", nargs="?", default="")
    exclude.add_argument("--neos-case-id", default="")
    exclude.add_argument("--reason", default="")
    exclude.add_argument("--added-by", default="operator")
    exclude.add_argument("--exclusions", default=str(default_exclusions()))
    exclude.set_defaults(func=exclusion_command)

    plan = sub.add_parser("plan", help="build an exclusion-aware browser action manifest")
    plan.add_argument("--cases", required=True, help="Neos Open Case List CSV")
    plan.add_argument("--notes", required=True, help="Neos Case Notes CSV")
    plan.add_argument("--exclusions", default=str(default_exclusions()))
    plan.add_argument("--state", default=str(default_state()))
    plan.add_argument("--output", help="private output directory outside the repo")
    plan.add_argument("--max-actions", type=int, default=5)
    plan.set_defaults(func=build_plan)

    finalize = sub.add_parser("finalize-summary", help="turn a reviewed Markdown summary into DOCX")
    finalize.add_argument("--summary-file", required=True)
    finalize.add_argument("--output", required=True)
    finalize.add_argument("--allow-incomplete", action="store_true")
    finalize.set_defaults(func=finalize_summary)

    record = sub.add_parser("record", help="record a verified Eve/SharePoint browser result")
    record.add_argument("--case-number", required=True)
    record.add_argument("--neos-case-id", default="")
    record.add_argument(
        "--status",
        required=True,
        choices=("created", "synced", "summary_uploaded", "failed", "blocked"),
    )
    record.add_argument("--eve-matter-id", default="")
    record.add_argument("--eve-matter-url", default="")
    record.add_argument("--notes-fingerprint", default="")
    record.add_argument("--error", default="")
    record.add_argument("--state", default=str(default_state()))
    record.set_defaults(func=record_command)

    test = sub.add_parser("self-test", help="run offline safety and idempotency checks")
    test.set_defaults(func=lambda _args: self_test())
    return ap


def main() -> int:
    args = parser().parse_args()
    if args.command == "exclude" and args.exclusion_action != "list" and not args.case_number:
        raise SystemExit("case_number is required for exclude add/remove")
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
