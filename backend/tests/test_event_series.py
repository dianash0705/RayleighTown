import pytest

from event_series import (
    compute_series_key,
    enrich_parsed_details,
    extract_series_identity,
    should_skip_periodic_analysis,
)


@pytest.mark.unit
class TestEventSeriesKeys:
    def test_sysmon_network_client_series_groups_by_port_not_ip(self):
        identity = {
            "sourceIp": "10.0.0.5",
            "sourcePort": "54321",
            "destinationIp": "203.0.113.10",
            "destinationPort": "443",
            "protocol": "tcp",
            "image": "C:\\Windows\\System32\\svchost.exe",
        }
        tcp_key = compute_series_key(1, 3, identity)
        identity_other_ip = dict(identity)
        identity_other_ip["destinationIp"] = "198.51.100.20"
        identity_udp = dict(identity)
        identity_udp["protocol"] = "udp"

        assert tcp_key == compute_series_key(1, 3, identity_other_ip)
        assert tcp_key != compute_series_key(1, 3, identity_udp)
        assert "protocol=tcp" in tcp_key
        assert "destinationPort=443" in tcp_key
        assert "destinationIp" not in tcp_key

    def test_sysmon_network_client_series_uses_hostname_when_present(self):
        base = {
            "sourcePort": "54321",
            "destinationPort": "443",
            "protocol": "tcp",
            "image": "C:\\Windows\\System32\\svchost.exe",
        }
        with_hostname_a = {
            **base,
            "destinationIp": "203.0.113.10",
            "destinationHostname": "c2.example.com",
        }
        with_hostname_b = {
            **base,
            "destinationIp": "198.51.100.99",
            "destinationHostname": "c2.example.com",
        }
        without_hostname = {**base, "destinationIp": "203.0.113.10"}

        hostname_key = compute_series_key(1, 3, with_hostname_a)
        assert hostname_key == compute_series_key(1, 3, with_hostname_b)
        assert "destinationHostname=c2.example.com" in hostname_key
        assert compute_series_key(1, 3, without_hostname) != hostname_key

    def test_sysmon_network_server_series_includes_remote_client(self):
        identity = {
            "sourceIp": "10.0.0.99",
            "sourcePort": "53",
            "destinationIp": "10.0.0.5",
            "protocol": "udp",
            "image": "C:\\Windows\\System32\\dns.exe",
        }
        key = compute_series_key(1, 3, identity)

        assert "sourceIp=10.0.0.99" in key
        assert "sourcePort=53" in key
        assert "destinationIp" not in key

    def test_server_side_network_series_skips_periodic_analysis(self):
        identity = {
            "sourcePort": "53",
            "protocol": "udp",
            "image": "C:\\Windows\\System32\\dns.exe",
        }
        series_key = compute_series_key(1, 3, identity)

        assert should_skip_periodic_analysis(1, 3, identity)
        assert should_skip_periodic_analysis(1, 3, series_key=series_key)
        assert not should_skip_periodic_analysis(
            1,
            3,
            {"sourcePort": "54321", "destinationIp": "8.8.8.8", "protocol": "udp", "image": "dns.exe"},
        )

    def test_security_logon_ignores_ip_and_workstation(self):
        first = {
            "IpAddress": "10.0.0.1",
            "WorkstationName": "HOST-A",
            "TargetUserName": "alice",
            "LogonType": "3",
            "AuthenticationPackageName": "Negotiate",
        }
        second = dict(first)
        second["IpAddress"] = "10.0.0.99"
        second["WorkstationName"] = "HOST-B"

        assert compute_series_key(0, 4624, first) == compute_series_key(0, 4624, second)

    def test_enrich_parsed_details_adds_series_identity(self):
        details = enrich_parsed_details(
            0,
            4720,
            {
                "title": "User Account Created",
                "fields": [],
                "identity": {
                    "TargetUserName": "bob",
                    "SamAccountName": "bob",
                    "TargetDomainName": "CORP",
                },
            },
        )

        assert details["seriesIdentity"]["TargetUserName"] == "bob"
        assert "TargetDomainName" not in details["seriesIdentity"]
        assert details["seriesKey"] == "SamAccountName=bob|TargetUserName=bob"

    def test_events_without_series_fields_share_default_key(self):
        assert extract_series_identity(0, 9999, {"foo": "bar"}) == {}
        assert compute_series_key(0, 9999, {"foo": "bar"}) == ""
