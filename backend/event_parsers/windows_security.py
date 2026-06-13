from event_details import compact_fields, field, make_event_details
from event_parsers.common import event_data_map, normalize_payload, payload_value


def _security_fields(payload: dict, mapping: list[tuple[str, str, bool]]) -> tuple[list, dict]:
    data = event_data_map(payload)
    fields = []
    identity: dict[str, str] = {}
    for key, label, emphasis in mapping:
        value = payload_value(payload, key) or data.get(key)
        parsed = field(key, label, value, emphasis=emphasis)
        if parsed:
            fields.append(parsed)
            identity[key] = str(value).strip()
    return fields, identity


def parse_security_process_creation(record: dict) -> dict:
    payload = normalize_payload(record)
    fields, identity = _security_fields(
        payload,
        [
            ("NewProcessName", "New Process", True),
            ("CommandLine", "Command Line", False),
            ("ParentProcessName", "Parent Process", False),
            ("SubjectUserName", "Subject User", False),
            ("SubjectDomainName", "Subject Domain", False),
        ],
    )
    return make_event_details("Process Creation", fields, identity)


def parse_security_successful_logon(record: dict) -> dict:
    payload = normalize_payload(record)
    fields, identity = _security_fields(
        payload,
        [
            ("IpAddress", "IP Address", True),
            ("WorkstationName", "Workstation", True),
            ("TargetUserName", "Target User", False),
            ("TargetDomainName", "Target Domain", False),
            ("LogonType", "Logon Type", False),
            ("ProcessName", "Logon Process", False),
            ("AuthenticationPackageName", "Auth Package", False),
        ],
    )
    return make_event_details("Successful Logon", fields, identity)


def parse_security_failed_logon(record: dict) -> dict:
    payload = normalize_payload(record)
    fields, identity = _security_fields(
        payload,
        [
            ("IpAddress", "IP Address", True),
            ("WorkstationName", "Workstation", True),
            ("TargetUserName", "Target User", False),
            ("FailureReason", "Failure Reason", True),
            ("Status", "Status", False),
            ("LogonType", "Logon Type", False),
        ],
    )
    return make_event_details("Failed Logon", fields, identity)


def parse_security_user_account_created(record: dict) -> dict:
    payload = normalize_payload(record)
    fields, identity = _security_fields(
        payload,
        [
            ("TargetUserName", "Target User", True),
            ("SamAccountName", "SAM Account", True),
            ("TargetDomainName", "Target Domain", False),
            ("SubjectUserName", "Subject User", False),
        ],
    )
    return make_event_details("User Account Created", fields, identity)


def parse_security_user_account_deleted(record: dict) -> dict:
    payload = normalize_payload(record)
    fields, identity = _security_fields(
        payload,
        [
            ("TargetUserName", "Target User", True),
            ("SamAccountName", "SAM Account", True),
            ("TargetDomainName", "Target Domain", False),
            ("SubjectUserName", "Subject User", False),
        ],
    )
    return make_event_details("User Account Deleted", fields, identity)


def parse_security_scheduled_task(record: dict) -> dict:
    payload = normalize_payload(record)
    fields, identity = _security_fields(
        payload,
        [
            ("TaskName", "Task Name", True),
            ("SubjectUserName", "Subject User", False),
            ("Operation", "Operation", False),
        ],
    )
    return make_event_details("Scheduled Task Created/Updated", fields, identity)


def parse_security_token_right_adjusted(record: dict) -> dict:
    payload = normalize_payload(record)
    fields, identity = _security_fields(
        payload,
        [
            ("SubjectUserName", "Subject User", True),
            ("TargetUserName", "Target User", False),
            ("PrivilegeList", "Privilege List", True),
        ],
    )
    return make_event_details("Token Right Adjusted", fields, identity)


def parse_security_firewall_setting(record: dict) -> dict:
    payload = normalize_payload(record)
    fields, identity = _security_fields(
        payload,
        [
            ("SettingType", "Setting Type", True),
            ("SettingValue", "Setting Value", True),
            ("SubjectUserName", "Subject User", False),
        ],
    )
    return make_event_details("Windows Firewall Setting Changed", fields, identity)


def parse_security_firewall_rule(record: dict) -> dict:
    payload = normalize_payload(record)
    fields, identity = _security_fields(
        payload,
        [
            ("RuleName", "Rule Name", True),
            ("Action", "Action", False),
            ("Direction", "Direction", False),
            ("Application", "Application", False),
        ],
    )
    return make_event_details("Windows Firewall Rule Changed", fields, identity)


WINDOWS_SECURITY_EVENT_PARSERS = {
    4688: parse_security_process_creation,
    4624: parse_security_successful_logon,
    4625: parse_security_failed_logon,
    4720: parse_security_user_account_created,
    4726: parse_security_user_account_deleted,
    4698: parse_security_scheduled_task,
    4702: parse_security_scheduled_task,
    4703: parse_security_token_right_adjusted,
    4946: parse_security_firewall_setting,
    4947: parse_security_firewall_rule,
}
