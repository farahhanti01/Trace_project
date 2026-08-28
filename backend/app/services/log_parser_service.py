import re
from typing import Any


TRANSACTION_START_PATTERN = re.compile(
    r"\bStart\s+Dump(?:Visa|Cis|Iso|Postilion)\s*\(",
    flags=re.IGNORECASE,
)
MTI_PATTERN = re.compile(
    r"\b(?:M\.T\.I|Message\s+Type)\s*:\s*"
    r"\[?(?P<mti>[0-9]{3,4})\]?",
    flags=re.IGNORECASE,
)
FIELD_PATTERN = re.compile(
    r"\bFLD\s*\(\s*0*(?P<field>\d{1,3}(?:\.\d+)?)\s*\)"
    r"\s*:?\s*\(\s*(?P<length>\d{1,4})?[^)]*\)\s*:?\s*\[(?P<value>[^\]]*)\]",
    flags=re.IGNORECASE,
)
TLV_FIELD_PATTERN = re.compile(
    r"^\s*(?:\S+\s+)*\d+\|\d+\|\s*"
    r"0*(?P<field>\d{1,3}(?:\.\d+)?)\s*\{[^}]*\}\s*"
    r"\d+\s+(?P<value>.+?)\s*\.?\s*$",
    flags=re.IGNORECASE,
)
LOG_PREFIX_PATTERN = re.compile(
    r"^\s*(?:\S+\s+)*(?P<thread>\d+)\|\d+\|\s*(?P<content>.*)$",
    flags=re.IGNORECASE,
)
FUNCTION_PATTERN = re.compile(
    r"\b(?P<kind>Start|End)\s+"
    r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*\((?P<args>.*)\)",
    flags=re.IGNORECASE,
)
LOOSE_END_FUNCTION_PATTERN = re.compile(
    r"\bEnd\s+"
    r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s+"
    r"(?P<args>.+)$",
    flags=re.IGNORECASE,
)
ERROR_PATTERN = re.compile(
    r"\b(ERROR|ERR|FAIL|FAILED|NOK|EXCEPTION|ORA-\d+|KO)\b"
    r"|(?:^|[,(])\s*-\d+\b"
    r"|\bRESULT\s*\(\s*N\s*\)"
    r"|\bRESULT_FLAG\s*=\s*['\"]?[NE]['\"]?",
    flags=re.IGNORECASE,
)
WARNING_PATTERN = re.compile(
    r"\b(WARN|WARNING|TIMEOUT|RETRY)\b",
    flags=re.IGNORECASE,
)
SUCCESS_PATTERN = re.compile(
    r"\b(OK|SUCCESS|SUCCES)\b"
    r"|\bRESULT\s*\(\s*[YO]\s*\)"
    r"|\bRESULT_FLAG\s*=\s*['\"]?[RO]['\"]?",
    flags=re.IGNORECASE,
)
ERROR_TOKEN_PATTERN = re.compile(
    r"\b(?P<label>NOK|ERROR|ERR|FAIL|FAILED|KO|EXCEPTION)"
    r"(?:\s*\(\s*(?P<code>-?\d+)\s*\))?",
    flags=re.IGNORECASE,
)
HSM_COMMAND_PATTERN = re.compile(
    r"\bStart\s+command_(?P<command>[A-Za-z0-9]+)\s*\(",
    flags=re.IGNORECASE,
)
HSM_COMMAND_END_PATTERN = re.compile(
    r"\b(?:End|Fin)\s+command_(?P<command>[A-Za-z0-9]+)"
    r"\s*\((?P<status>[^)]*)\)",
    flags=re.IGNORECASE,
)
TO_HSM_PATTERN = re.compile(
    r"\bTO\s+HSM\s*:\s*"
    r"(?:(?:Len=\[(?P<bracket_length>\d+)\])|(?P<plain_length>\d+))?"
    r"\s*-->\s*(?:Data=)?\s*(?P<payload>.*)$",
    flags=re.IGNORECASE,
)
FROM_HSM_PATTERN = re.compile(
    r"\bFROM\s+HSM\s*:\s*<--\s*(?P<payload>.+)$",
    flags=re.IGNORECASE,
)
HSM_RESULT_PATTERN = re.compile(
    r"\bHsmResultCode\s*=\s*(?P<code>[A-Za-z0-9]+)",
    flags=re.IGNORECASE,
)
HSM_ACTIVITY_PATTERN = re.compile(
    r"\b("
    r"TO\s+HSM|FROM\s+HSM|HsmResultCode|"
    r"Start\s+command_[A-Za-z0-9]+|End\s+command_[A-Za-z0-9]+|"
    r"Fin\s+command_[A-Za-z0-9]+|"
    r"CheckHsmResource|WriteBalHsm|ReadBalHsm|HsmQuery|"
    r"PVV\s+Verification|CheckAuthSecurity"
    r")\b",
    flags=re.IGNORECASE,
)
HSM_FAILURE_STATUSES = {
    "FAILED",
    "TECHNICAL_ERROR",
    "FORMAT_ERROR",
    "COMMUNICATION_ERROR",
}
MTI_LABELS = {
    "0100": "Authorization Request",
    "0110": "Authorization Response",
    "0200": "Authorization Request",
    "0210": "Authorization Response",
    "1100": "Authorization Request",
    "1110": "Authorization Response",
    "0800": "Network Management Request",
    "0810": "Network Management Response",
}
AUTHORIZATION_REQUEST_MTIS = {
    "0100",
    "0200",
    "1100",
}
AUTHORIZATION_RESPONSE_MTIS = {
    "0110",
    "0210",
    "1110",
    "1210",
}
FINAL_AUTHORIZATION_RESPONSE_MTIS = {
    "0110",
    "0210",
}
NETWORK_MANAGEMENT_MTIS = {
    "0800",
    "0810",
}


def mask_field_value(
    field: str,
    value: str | None,
) -> str | None:
    if value is None:
        return None

    cleaned_value = value.strip()

    return cleaned_value


def normalize_mti_value(
    value: str,
) -> str:
    cleaned_value = value.strip()

    if len(cleaned_value) == 3 and cleaned_value.isdigit():
        return cleaned_value.zfill(4)

    return cleaned_value


def normalize_field_id(
    value: str,
) -> str:
    cleaned_value = value.strip()

    if "." in cleaned_value:
        base, suffix = cleaned_value.split(".", 1)
        return f"{base.zfill(3)}.{suffix}"

    return cleaned_value.zfill(3)


def extract_tlv_field_value(
    line: str,
) -> tuple[str, str] | None:
    match = TLV_FIELD_PATTERN.search(line)

    if not match:
        return None

    field = match.group("field")
    value = match.group("value").strip()

    if field == "002":
        # Les traces POS ajoutent souvent une empreinte technique apres le PAN
        # masque. On conserve uniquement le premier token affichable.
        value = value.split()[0] if value else value

    return field, value


def clean_log_line(
    line: str,
) -> str:
    return line.rstrip().rstrip(".").strip()


def extract_thread_and_content(
    line: str,
) -> tuple[str | None, str]:
    match = LOG_PREFIX_PATTERN.match(line)

    if not match:
        return None, clean_log_line(line)

    return match.group("thread"), clean_log_line(match.group("content"))


def split_transactions(
    text: str,
) -> list[dict[str, Any]]:
    lines = text.splitlines()
    transactions = []
    current_lines: list[tuple[int, str]] = []
    start_line = 1

    for line_number, line in enumerate(lines, start=1):
        if TRANSACTION_START_PATTERN.search(line):
            if current_lines:
                transactions.append({
                    "start_line": start_line,
                    "end_line": line_number - 1,
                    "lines": current_lines,
                })

            current_lines = []
            start_line = line_number

        if current_lines or TRANSACTION_START_PATTERN.search(line):
            current_lines.append((line_number, line))

    if current_lines:
        transactions.append({
            "start_line": start_line,
            "end_line": current_lines[-1][0],
            "lines": current_lines,
        })

    if transactions:
        return transactions

    return split_hsm_transactions(lines)


def split_hsm_transactions(
    lines: list[str],
) -> list[dict[str, Any]]:
    hsm_threads = set()
    fallback_blocks: list[dict[str, Any]] = []
    current_block: list[tuple[int, str]] = []
    current_start_line = 1

    for line_number, line in enumerate(lines, start=1):
        thread, content = extract_thread_and_content(line)
        has_hsm_activity = HSM_ACTIVITY_PATTERN.search(content) is not None

        if thread and has_hsm_activity:
            hsm_threads.add(thread)

        if thread:
            continue

        if has_hsm_activity and not current_block:
            current_start_line = line_number

        if current_block or has_hsm_activity:
            current_block.append((line_number, line))

    if current_block:
        fallback_blocks.append({
            "start_line": current_start_line,
            "end_line": current_block[-1][0],
            "lines": current_block,
        })

    if not hsm_threads:
        return fallback_blocks

    grouped: dict[str, list[tuple[int, str]]] = {
        thread: []
        for thread in sorted(hsm_threads)
    }

    for line_number, line in enumerate(lines, start=1):
        thread, _content = extract_thread_and_content(line)

        if thread in grouped:
            grouped[thread].append((line_number, line))

    transactions = []

    for thread, thread_lines in grouped.items():
        if not thread_lines:
            continue

        transactions.append({
            "start_line": thread_lines[0][0],
            "end_line": thread_lines[-1][0],
            "lines": thread_lines,
            "thread": thread,
        })

    return transactions


def parse_transaction_fields(
    lines: list[tuple[int, str]],
) -> tuple[
    str | None,
    dict[str, str | None],
    dict[str, int],
    list[dict[str, Any]],
]:
    mti = None
    fields: dict[str, str | None] = {
        "002": None,
        "003": None,
        "037": None,
        "039": None,
    }
    field_lengths: dict[str, int] = {}
    evidence = []

    for line_number, line in lines:
        mti_match = MTI_PATTERN.search(line)

        if mti_match and not mti:
            mti = normalize_mti_value(mti_match.group("mti"))
            evidence.append({
                "line": line_number,
                "text": clean_log_line(line),
                "type": "mti",
            })

        field_match = FIELD_PATTERN.search(line)

        is_tlv_field = False

        if field_match:
            field = normalize_field_id(field_match.group("field"))
            value = field_match.group("value")
            length = field_match.group("length")

            if length and length.isdigit():
                field_lengths[field] = int(length)
        else:
            tlv_field = extract_tlv_field_value(line)

            if not tlv_field:
                continue

            field, value = tlv_field
            field = normalize_field_id(field)
            is_tlv_field = True

        if is_tlv_field and fields.get(field):
            continue

        fields[field] = mask_field_value(
            field,
            value,
        )
        evidence.append({
            "line": line_number,
            "text": clean_log_line(mask_log_line(line)),
            "type": f"field_{field}",
        })

    return mti, fields, field_lengths, evidence


def status_from_end_args(
    args: str,
) -> str:
    normalized = args.upper()
    stripped = normalized.strip()

    if re.fullmatch(r"[+]?0+(?:\s*,\s*)*", stripped):
        return "OK"

    if ERROR_PATTERN.search(normalized):
        return "ERROR"

    if WARNING_PATTERN.search(normalized):
        return "WARNING"

    if SUCCESS_PATTERN.search(normalized):
        return "OK"

    if not stripped:
        return "OK"

    return "UNKNOWN"


def extract_error_identifier(
    text: str,
) -> str:
    match = ERROR_TOKEN_PATTERN.search(text)

    if not match:
        negative_match = re.search(
            r"(?:^|[\s,(]\s*)(?P<code>-\d+)\b",
            text,
        )

        if negative_match:
            return f"NOK ({negative_match.group('code')})"

        result_match = re.search(
            r"\bRESULT\s*\(\s*N\s*\)",
            text,
            flags=re.IGNORECASE,
        )

        if result_match:
            return "RESULT(N)"

        ora_match = re.search(r"\bORA-\d+\b", text, flags=re.IGNORECASE)
        return ora_match.group(0).upper() if ora_match else ""

    label = match.group("label").upper()
    code = match.group("code")

    return f"{label} ({code})" if code else label


def extract_function_event(
    line: str,
) -> dict[str, str] | None:
    function_match = FUNCTION_PATTERN.search(line)

    if function_match:
        return {
            "kind": function_match.group("kind").lower(),
            "name": function_match.group("name"),
            "args": function_match.group("args") or "",
        }

    loose_end_match = LOOSE_END_FUNCTION_PATTERN.search(line)

    if loose_end_match:
        return {
            "kind": "end",
            "name": loose_end_match.group("name"),
            "args": loose_end_match.group("args") or "",
        }

    return None


def function_status_from_line(
    line: str,
) -> str:
    if ERROR_PATTERN.search(line):
        return "ERROR"

    if WARNING_PATTERN.search(line):
        return "WARNING"

    return "UNKNOWN"


def merge_status(
    current_status: str,
    new_status: str,
) -> str:
    priority = {
        "ERROR": 4,
        "WARNING": 3,
        "OK": 2,
        "UNKNOWN": 1,
    }

    return (
        new_status
        if priority.get(new_status, 0) > priority.get(current_status, 0)
        else current_status
    )


def merge_hsm_status(
    current_status: str,
    new_status: str,
) -> str:
    priority = {
        "TECHNICAL_ERROR": 6,
        "COMMUNICATION_ERROR": 5,
        "FORMAT_ERROR": 4,
        "FAILED": 3,
        "SUCCESS": 2,
        "UNKNOWN": 1,
    }

    return (
        new_status
        if priority.get(new_status, 0) > priority.get(current_status, 0)
        else current_status
    )


def parse_log_story(
    lines: list[tuple[int, str]],
) -> list[dict[str, Any]]:
    story = []
    open_indexes_by_name: dict[str, list[int]] = {}

    for line_number, line in lines:
        function_event = extract_function_event(line)

        if function_event:
            kind = function_event["kind"]
            name = function_event["name"]
            args = function_event["args"]

            if kind == "start":
                story.append({
                    "order": len(story) + 1,
                    "function_name": name,
                    "status": "UNKNOWN",
                    "evidence": [
                        {
                            "line": line_number,
                            "text": clean_log_line(mask_log_line(line)),
                        }
                    ],
                })
                open_indexes_by_name.setdefault(name, []).append(
                    len(story) - 1
                )
            else:
                status = status_from_end_args(args)
                detected_error = (
                    extract_error_identifier(args)
                    if status == "ERROR"
                    else ""
                )
                open_indexes = open_indexes_by_name.get(name) or []
                story_index = (
                    open_indexes.pop()
                    if open_indexes
                    else None
                )

                if story_index is None:
                    story.append({
                        "order": len(story) + 1,
                        "function_name": name,
                        "status": status,
                        "evidence": [],
                    })
                    story_index = len(story) - 1

                story[story_index]["status"] = merge_status(
                    story[story_index].get("status", "UNKNOWN"),
                    status,
                )
                if detected_error:
                    story[story_index]["detected_error"] = detected_error
                story[story_index].setdefault("evidence", []).append({
                    "line": line_number,
                    "text": clean_log_line(mask_log_line(line)),
                })

            continue

        line_status = function_status_from_line(line)

        if line_status in {"ERROR", "WARNING"} and story:
            story[-1]["status"] = merge_status(
                story[-1].get("status", "UNKNOWN"),
                line_status,
            )
            if line_status == "ERROR":
                story[-1]["detected_error"] = extract_error_identifier(line)
            story[-1].setdefault("evidence", []).append({
                "line": line_number,
                "text": clean_log_line(mask_log_line(line)),
            })

    return story


def hsm_status_from_text(
    text: str,
) -> str:
    normalized = text.upper()

    if re.search(r"\b(TIMEOUT|NO\s+RESPONSE|CONNECTION|COMMUNICATION)\b", normalized):
        return "COMMUNICATION_ERROR"

    if "FORMAT" in normalized:
        return "FORMAT_ERROR"

    if re.search(r"\b(SYSTEM|TECHNICAL|MALFUNCTION)\b", normalized):
        return "TECHNICAL_ERROR"

    if ERROR_PATTERN.search(normalized):
        return "FAILED"

    if SUCCESS_PATTERN.search(normalized):
        return "SUCCESS"

    return "UNKNOWN"


def hsm_status_from_result_code(
    result_code: str,
) -> str:
    code = (result_code or "").upper()

    if not code:
        return "UNKNOWN"

    if code.endswith("00"):
        return "SUCCESS"

    return "FAILED"


def is_hsm_payload_continuation(
    content: str,
) -> bool:
    stripped = content.strip()

    if not stripped:
        return False

    if re.search(
        r"\b(Start|End|Fin|M\.T\.I|FLD|Partner|Source|Type|Command|Length|"
        r"FROM\s+HSM|TO\s+HSM|HsmResultCode)\b",
        stripped,
        flags=re.IGNORECASE,
    ):
        return False

    if stripped.startswith(("=", "-", ">")):
        return False

    return re.fullmatch(r"[A-Za-z0-9]+\|?", stripped) is not None


def clean_hsm_payload(
    payload: str | None,
) -> str:
    if payload is None:
        return ""

    return payload.strip().rstrip(".").strip().rstrip("|").strip()


def select_hsm_thread(
    lines: list[tuple[int, str]],
) -> str:
    counts: dict[str, int] = {}

    for _line_number, raw_line in lines:
        thread, content = extract_thread_and_content(raw_line)

        if thread and HSM_ACTIVITY_PATTERN.search(content):
            counts[thread] = counts.get(thread, 0) + 1

    if not counts:
        return ""

    return sorted(
        counts.items(),
        key=lambda item: (-item[1], item[0]),
    )[0][0]


def parse_hsm_analysis(
    lines: list[tuple[int, str]],
) -> dict[str, Any]:
    commands: list[dict[str, Any]] = []
    current_index: int | None = None
    capture_field: str | None = None
    threads: list[str] = []
    target_thread = select_hsm_thread(lines)

    def ensure_command(
        command_code: str = "",
        thread: str | None = None,
    ) -> dict[str, Any]:
        nonlocal current_index

        if current_index is not None:
            return commands[current_index]

        commands.append({
            "order": len(commands) + 1,
            "thread": thread or "",
            "command": command_code,
            "command_name": "",
            "command_description": "",
            "response_command": "",
            "response_name": "",
            "hsm_result_code": "",
            "return_code": "",
            "return_code_meaning": "",
            "functional_result": "",
            "technical_interpretation": "",
            "trace_description": "",
            "request_message": "",
            "response_message": "",
            "message_length": "",
            "status": "UNKNOWN",
            "detected_status": "",
            "evidence": [],
            "documentation_findings": [],
        })
        current_index = len(commands) - 1
        return commands[current_index]

    def command_for_start(
        command_code: str,
        thread: str | None = None,
    ) -> dict[str, Any]:
        nonlocal current_index

        if current_index is None:
            return ensure_command(
                command_code=command_code,
                thread=thread,
            )

        command = commands[current_index]

        if command_code in {
            command.get("command"),
            command.get("response_command"),
        }:
            return command

        if (
            (command.get("request_message") or command.get("message_length"))
            and not command.get("response_command")
        ):
            command["response_command"] = command_code
            return command

        if (
            command.get("response_command")
            or command.get("hsm_result_code")
            or command.get("detected_status")
            or command.get("status") in {
                "SUCCESS",
                "FAILED",
                "TECHNICAL_ERROR",
                "FORMAT_ERROR",
                "COMMUNICATION_ERROR",
            }
        ):
            current_index = None

        return ensure_command(
            command_code=command_code,
            thread=thread,
        )

    for line_number, raw_line in lines:
        thread, content = extract_thread_and_content(raw_line)

        if thread:
            threads.append(thread)

        if target_thread and thread and thread != target_thread:
            continue

        if capture_field and current_index is not None:
            if is_hsm_payload_continuation(content):
                commands[current_index][capture_field] += clean_hsm_payload(
                    content,
                )
                commands[current_index].setdefault("evidence", []).append({
                    "line": line_number,
                    "text": clean_log_line(mask_log_line(raw_line)),
                })
                continue

            capture_field = None

        command_match = HSM_COMMAND_PATTERN.search(content)

        if command_match:
            command_code = command_match.group("command").upper()
            command = command_for_start(
                command_code=command_code,
                thread=thread,
            )
            if not command.get("command"):
                command["command"] = command_code
            if not command.get("thread") and thread:
                command["thread"] = thread
            command.setdefault("evidence", []).append({
                "line": line_number,
                "text": clean_log_line(mask_log_line(raw_line)),
            })
            continue

        to_hsm_match = TO_HSM_PATTERN.search(content)

        if to_hsm_match:
            payload = clean_hsm_payload(to_hsm_match.group("payload"))
            if (
                current_index is not None
                and (
                    commands[current_index].get("request_message")
                    or commands[current_index].get("response_message")
                    or commands[current_index].get("hsm_result_code")
                )
            ):
                current_index = None
            command = ensure_command(
                command_code=payload[:2].upper(),
                thread=thread,
            )
            command["request_message"] = payload
            command["message_length"] = (
                to_hsm_match.group("bracket_length")
                or to_hsm_match.group("plain_length")
                or ""
            )
            if not command.get("command") and len(payload) >= 2:
                command["command"] = payload[:2].upper()
            if not command.get("thread") and thread:
                command["thread"] = thread
            command.setdefault("evidence", []).append({
                "line": line_number,
                "text": clean_log_line(mask_log_line(raw_line)),
            })
            capture_field = "request_message"
            continue

        from_hsm_match = FROM_HSM_PATTERN.search(content)

        if from_hsm_match:
            payload = clean_hsm_payload(from_hsm_match.group("payload"))
            command = ensure_command(
                command_code="",
                thread=thread,
            )
            command["response_message"] = payload
            if len(payload) >= 2:
                command["response_command"] = payload[:2].upper()
            result_match = re.match(r"(?P<code>[A-Za-z]{2}[A-Za-z0-9]{2})", payload)
            if result_match:
                command["hsm_result_code"] = result_match.group("code").upper()
                command["return_code"] = command["hsm_result_code"][2:]
                command["status"] = merge_hsm_status(
                    command.get("status", "UNKNOWN"),
                    hsm_status_from_result_code(command["hsm_result_code"]),
                )
            if not command.get("thread") and thread:
                command["thread"] = thread
            command.setdefault("evidence", []).append({
                "line": line_number,
                "text": clean_log_line(mask_log_line(raw_line)),
            })
            capture_field = "response_message"
            continue

        result_match = HSM_RESULT_PATTERN.search(content)

        if result_match:
            command = (
                commands[current_index]
                if current_index is not None
                else (
                    commands[-1]
                    if commands
                    else ensure_command(command_code="", thread=thread)
                )
            )
            command["hsm_result_code"] = result_match.group("code").upper()
            command["return_code"] = command["hsm_result_code"][2:]
            command["status"] = merge_hsm_status(
                command.get("status", "UNKNOWN"),
                hsm_status_from_result_code(command["hsm_result_code"]),
            )
            command.setdefault("evidence", []).append({
                "line": line_number,
                "text": clean_log_line(mask_log_line(raw_line)),
            })
            continue

        command_end_match = HSM_COMMAND_END_PATTERN.search(content)

        if command_end_match and commands:
            command_code = command_end_match.group("command").upper()
            status_text = command_end_match.group("status") or ""
            command = ensure_command(
                command_code=command_code,
                thread=thread,
            )
            if command_code != command.get("command"):
                command["response_command"] = command_code
            command["status"] = merge_hsm_status(
                command.get("status", "UNKNOWN"),
                hsm_status_from_text(status_text),
            )
            command["detected_status"] = status_text
            command.setdefault("evidence", []).append({
                "line": line_number,
                "text": clean_log_line(mask_log_line(raw_line)),
            })
            if command_code == command.get("response_command"):
                current_index = None
            continue

        if (
            current_index is not None
            and re.search(r"verification\s+failure|verification_failed", content, re.IGNORECASE)
        ):
            trace_description = re.sub(
                r"[^A-Za-z0-9_ ]+",
                " ",
                content,
            )
            trace_description = re.sub(r"\s+", " ", trace_description).strip()
            commands[current_index]["status"] = merge_hsm_status(
                commands[current_index].get("status", "UNKNOWN"),
                "FAILED",
            )
            commands[current_index]["detected_status"] = "VERIFICATION_FAILED"
            commands[current_index]["trace_description"] = (
                trace_description or "Verification Failure"
            )
            commands[current_index].setdefault("evidence", []).append({
                "line": line_number,
                "text": clean_log_line(mask_log_line(raw_line)),
            })

    if not commands:
        return {
            "thread": "",
            "commands": [],
            "result_codes": [],
        }

    commands = [
        command
        for command in commands
        if command.get("request_message") or command.get("hsm_result_code")
    ]

    if not commands:
        return {
            "thread": "",
            "commands": [],
            "result_codes": [],
        }

    result_codes = list(dict.fromkeys(
        command["hsm_result_code"]
        for command in commands
        if command.get("hsm_result_code")
    ))
    preferred_threads = [
        command.get("thread", "")
        for command in commands
        if command.get("thread")
    ]

    selected_thread = (
        target_thread
        or (preferred_threads[0] if preferred_threads else "")
        or (threads[0] if threads else "")
    )

    return {
        "thread": selected_thread,
        "commands": commands,
        "result_codes": result_codes,
    }


def mask_log_line(
    line: str,
) -> str:
    return line


def transaction_status(
    fields: dict[str, str | None],
    log_story: list[dict[str, Any]],
    hsm_analysis: dict[str, Any] | None = None,
) -> str:
    field_039 = fields.get("039")

    if any(item.get("status") == "ERROR" for item in log_story):
        return "FAILED"

    if hsm_analysis and any(
        command.get("status") in HSM_FAILURE_STATUSES
        for command in hsm_analysis.get("commands", [])
    ):
        return "FAILED"

    if field_039 and not is_success_response_code(field_039):
        return "FAILED"

    if any(item.get("status") == "WARNING" for item in log_story):
        return "WARNING"

    if (field_039 and is_success_response_code(field_039)) or all(
        item.get("status") in {"OK", "UNKNOWN"}
        for item in log_story
    ):
        return "SUCCESS"

    return "UNKNOWN"


def is_success_response_code(
    response_code: str,
) -> bool:
    normalized = response_code.strip()

    return bool(normalized) and normalized.isdigit() and int(normalized) == 0


def mti_label(
    mti: str | None,
) -> str:
    return MTI_LABELS.get(mti or "", "Transaction")


def transaction_display_name(
    index: int,
    mti: str | None,
    fields: dict[str, str | None],
    hsm_analysis: dict[str, Any] | None = None,
) -> str:
    if not mti and hsm_analysis and hsm_analysis.get("thread"):
        parts = [f"HSM Authorization #{index}"]
        parts.append(f"Thread {hsm_analysis['thread']}")
    else:
        parts = [f"{mti_label(mti)} #{index}"]

    if fields.get("002"):
        parts.append(f"Card {fields['002']}")

    if fields.get("039"):
        parts.append(f"Response {fields['039']}")

    return " - ".join(parts)


def observed_facts_for_transaction(
    transaction: dict[str, Any],
) -> list[str]:
    fields = transaction["fields"]
    facts = [
        f"MTI detecte: {transaction.get('mti') or 'UNKNOWN'}.",
        (
            "Field 037 detecte: "
            f"{fields.get('037') or 'non present'}."
        ),
    ]

    if fields.get("039"):
        facts.append(
            f"Field 039 detecte: {fields['039']}."
        )

    hsm_analysis = transaction.get("hsm_analysis") or {}
    hsm_thread = hsm_analysis.get("thread")
    hsm_codes = hsm_analysis.get("result_codes") or []

    if hsm_thread:
        facts.append(f"Thread HSM detecte: {hsm_thread}.")

    if hsm_codes:
        facts.append(
            "HsmResultCode detecte: "
            f"{', '.join(hsm_codes)}."
        )

    error_functions = [
        item["function_name"]
        for item in transaction["log_story"]
        if item.get("status") == "ERROR"
    ]

    if error_functions:
        facts.append(
            "Fonctions en erreur: "
            f"{', '.join(error_functions)}."
        )

    return facts


def merge_response_blocks_with_requests(
    transactions: list[dict[str, Any]],
) -> None:
    requests_by_rrn: dict[str, dict[str, Any]] = {}

    for transaction in transactions:
        mti = transaction.get("mti")
        fields = transaction.get("fields", {})
        rrn = fields.get("037")

        if mti in AUTHORIZATION_REQUEST_MTIS and rrn:
            requests_by_rrn[rrn] = transaction
            continue

        if mti not in AUTHORIZATION_RESPONSE_MTIS or not rrn:
            continue

        request_transaction = requests_by_rrn.get(rrn)

        if not request_transaction:
            continue

        request_fields = request_transaction.setdefault("fields", {})

        if fields.get("039"):
            request_fields["039"] = fields["039"]

        request_transaction["response_mti"] = mti
        request_transaction["response_fields"] = {
            field: value
            for field, value in fields.items()
            if value
        }
        request_transaction["response_log_index"] = transaction.get(
            "log_index"
        )
        request_transaction["response_transaction_id"] = transaction.get(
            "transaction_id"
        )
        transaction["merged_into_transaction_id"] = request_transaction.get(
            "transaction_id"
        )

        request_transaction.setdefault("evidence", []).extend(
            transaction.get("evidence", [])
        )
        request_transaction["status"] = transaction_status(
            request_fields,
            request_transaction.get("log_story", []),
            request_transaction.get("hsm_analysis"),
        )
        request_transaction["display_name"] = transaction_display_name(
            index=request_transaction.get("log_index") or 0,
            mti=request_transaction.get("mti"),
            fields=request_fields,
            hsm_analysis=request_transaction.get("hsm_analysis"),
        )
        request_transaction["observed_facts"] = observed_facts_for_transaction(
            request_transaction
        )


def backfill_related_response_codes_by_rrn(
    transactions: list[dict[str, Any]],
) -> None:
    responses_by_rrn: dict[str, list[dict[str, Any]]] = {}

    for transaction in transactions:
        mti = transaction.get("mti")
        fields = transaction.get("fields", {})
        rrn = fields.get("037")

        if (
            mti in AUTHORIZATION_RESPONSE_MTIS
            and rrn
            and fields.get("039")
        ):
            responses_by_rrn.setdefault(rrn, []).append(transaction)

    for responses in responses_by_rrn.values():
        responses.sort(
            key=lambda item: (
                item.get("start_line") or 0,
                item.get("log_index") or 0,
            )
        )

    for transaction in transactions:
        mti = transaction.get("mti")
        fields = transaction.get("fields", {})
        rrn = fields.get("037")

        if (
            mti not in AUTHORIZATION_REQUEST_MTIS
            or not rrn
            or fields.get("039")
        ):
            continue

        request_start = transaction.get("start_line") or 0
        candidate_responses = [
            response
            for response in responses_by_rrn.get(rrn, [])
            if (response.get("start_line") or 0) > request_start
        ]

        if not candidate_responses:
            continue

        related_response = max(
            candidate_responses,
            key=lambda response: (
                1
                if response.get("mti") in FINAL_AUTHORIZATION_RESPONSE_MTIS
                else 0,
                response.get("start_line") or 0,
                response.get("log_index") or 0,
            ),
        )

        if not related_response:
            continue

        response_fields = {
            field: value
            for field, value in (related_response.get("fields") or {}).items()
            if value
        }
        response_code = response_fields.get("039")

        if not response_code:
            continue

        fields["039"] = response_code
        transaction.setdefault("response_fields", response_fields)
        transaction.setdefault(
            "response_mti",
            related_response.get("mti"),
        )
        transaction.setdefault(
            "response_log_index",
            related_response.get("log_index"),
        )
        transaction.setdefault(
            "response_transaction_id",
            related_response.get("transaction_id"),
        )
        transaction["related_response_backfilled"] = True
        transaction["status"] = transaction_status(
            fields,
            transaction.get("log_story", []),
            transaction.get("hsm_analysis"),
        )
        transaction["display_name"] = transaction_display_name(
            index=transaction.get("log_index") or 0,
            mti=transaction.get("mti"),
            fields=fields,
            hsm_analysis=transaction.get("hsm_analysis"),
        )
        transaction["observed_facts"] = observed_facts_for_transaction(
            transaction
        )


def parse_log_transactions(
    text: str,
    source: str,
) -> list[dict[str, Any]]:
    chunks = split_transactions(text)
    parsed_transactions = []

    for index, chunk in enumerate(chunks, start=1):
        mti, fields, field_lengths, field_evidence = parse_transaction_fields(
            chunk["lines"]
        )
        log_story = parse_log_story(chunk["lines"])
        hsm_analysis = parse_hsm_analysis(chunk["lines"])

        if mti in NETWORK_MANAGEMENT_MTIS:
            continue

        transaction = {
            "transaction_id": f"transaction_{index}",
            "log_index": index,
            "message_type": mti_label(mti),
            "display_name": transaction_display_name(
                index=index,
                mti=mti,
                fields=fields,
                hsm_analysis=hsm_analysis,
            ),
            "source": source,
            "start_line": chunk["start_line"],
            "end_line": chunk["end_line"],
            "mti": mti,
            "fields": fields,
            "field_lengths": field_lengths,
            "status": transaction_status(fields, log_story),
            "log_story": log_story,
            "hsm_analysis": hsm_analysis,
            "observed_facts": [],
            "documentation_findings": [],
            "probable_cause": "",
            "hypotheses": [],
            "recommendations": [],
            "sources": [],
            "evidence": field_evidence,
        }
        transaction["status"] = transaction_status(
            fields,
            log_story,
            hsm_analysis,
        )
        transaction["observed_facts"] = observed_facts_for_transaction(
            transaction
        )
        parsed_transactions.append(transaction)

    merge_response_blocks_with_requests(parsed_transactions)
    backfill_related_response_codes_by_rrn(parsed_transactions)

    return parsed_transactions


def build_statistics(
    transactions: list[dict[str, Any]],
) -> dict[str, int]:
    return {
        "total_transactions": len(transactions),
        "successful_transactions": sum(
            1
            for transaction in transactions
            if transaction.get("status") == "SUCCESS"
        ),
        "failed_transactions": sum(
            1
            for transaction in transactions
            if transaction.get("status") == "FAILED"
        ),
        "warning_transactions": sum(
            1
            for transaction in transactions
            if transaction.get("status") == "WARNING"
        ),
        "no_response_transactions": sum(
            1
            for transaction in transactions
            if (
                transaction.get("mti") in AUTHORIZATION_REQUEST_MTIS
                and not transaction.get("response_mti")
                and not (transaction.get("fields") or {}).get("039")
            )
        ),
        "alert_transactions": sum(
            1
            for transaction in transactions
            if transaction.get("status") == "WARNING"
        ),
    }
