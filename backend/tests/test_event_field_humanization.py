import pytest

from event_details import field
from event_parsers.sysmon import parse_sysmon_image_loaded, parse_sysmon_network_connection, parse_sysmon_process_access
from event_parsers.windows_security import parse_security_failed_logon, parse_security_successful_logon


@pytest.mark.unit
class TestEventFieldHumanization:
    def test_logon_type_is_rendered_with_label(self):
        rendered = field("LogonType", "Logon Type", "0x7")

        assert rendered is not None
        assert rendered["value"] == "Network Cleartext (0x7)"

    def test_status_is_rendered_with_label(self):
        rendered = field("Status", "Status", "0xC000006A")

        assert rendered is not None
        assert rendered["value"] == "Wrong password (0xC000006A)"

    def test_protocol_is_normalized(self):
        rendered = field("Protocol", "Protocol", "tcp")

        assert rendered is not None
        assert rendered["value"] == "TCP"

    def test_boolean_fields_are_humanized(self):
        rendered = field("Signed", "Signed", "false")

        assert rendered is not None
        assert rendered["value"] == "No"

    def test_granted_access_is_decoded(self):
        rendered = field("GrantedAccess", "Granted Access", "0x1410")

        assert rendered is not None
        assert rendered["value"].endswith("(0x1410)")
        assert "Query information" in rendered["value"]
        assert "Query limited information" in rendered["value"]

    def test_action_is_event_specific(self):
        generic = field("Action", "Action", "allow")
        firewall = field("Action", "Action", "allow", event_type="Windows Firewall Rule Changed")

        assert generic is not None
        assert generic["value"] == "allow"
        assert firewall is not None
        assert firewall["value"] == "Allow"

    def test_security_logon_parser_uses_human_readable_logon_type(self):
        details = parse_security_successful_logon(
            {
                "result": {
                    "LogonType": "0x7",
                    "TargetUserName": "alice",
                    "TargetDomainName": "CORP",
                    "IpAddress": "10.0.0.5",
                }
            }
        )

        logon_type = next(item for item in details["fields"] if item["key"] == "LogonType")
        assert logon_type["value"] == "Network Cleartext (0x7)"

    def test_failed_logon_parser_uses_human_readable_status(self):
        details = parse_security_failed_logon(
            {
                "result": {
                    "Status": "0xC0000234",
                    "TargetUserName": "alice",
                    "IpAddress": "10.0.0.5",
                }
            }
        )

        status = next(item for item in details["fields"] if item["key"] == "Status")
        assert status["value"] == "Account locked out (0xC0000234)"

    def test_sysmon_network_connection_parser_uses_normalized_protocol(self):
        details = parse_sysmon_network_connection(
            {
                "result": {
                    "SourceIp": "10.0.0.1",
                    "DestinationIp": "10.0.0.2",
                    "Protocol": "udp",
                    "Image": "C:\\Windows\\System32\\svchost.exe",
                }
            }
        )

        protocol = next(item for item in details["fields"] if item["key"] == "protocol")
        assert protocol["value"] == "UDP"

    def test_sysmon_process_access_parser_decodes_granted_access(self):
        details = parse_sysmon_process_access(
            {
                "result": {
                    "SourceImage": "C:\\Windows\\System32\\cmd.exe",
                    "TargetImage": "C:\\Windows\\System32\\lsass.exe",
                    "GrantedAccess": "0x1410",
                    "CallTrace": "",
                }
            }
        )

        granted_access = next(item for item in details["fields"] if item["key"] == "grantedAccess")
        assert granted_access["value"].endswith("(0x1410)")
        assert "Query information" in granted_access["value"]

    def test_sysmon_image_loaded_parser_humanizes_signed_flag(self):
        details = parse_sysmon_image_loaded(
            {
                "result": {
                    "ImageLoaded": "C:\\Windows\\System32\\ntdll.dll",
                    "Image": "C:\\Windows\\System32\\svchost.exe",
                    "Signed": "true",
                }
            }
        )

        signed = next(item for item in details["fields"] if item["key"] == "signed")
        assert signed["value"] == "Yes"

    def test_firewall_rule_parser_humanizes_action_and_direction_in_context(self):
        from event_parsers.windows_security import parse_security_firewall_rule

        details = parse_security_firewall_rule(
            {
                "result": {
                    "RuleName": "Test Rule",
                    "Action": "allow",
                    "Direction": "inbound",
                    "Application": "C:\\Windows\\System32\\svchost.exe",
                }
            }
        )

        action = next(item for item in details["fields"] if item["key"] == "Action")
        direction = next(item for item in details["fields"] if item["key"] == "Direction")
        assert action["value"] == "Allow"
        assert direction["value"] == "Inbound"