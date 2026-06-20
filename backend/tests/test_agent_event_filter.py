import pytest

from agent_event_filter import is_agent_generated_event


@pytest.mark.unit
class TestAgentEventFilter:
    def test_filters_scheduled_task_registration_for_agent_task(self):
        record = {
            "EventID": 4698,
            "EventData": {
                "TaskName": "\\GirlMeetsCode-LogUploader",
                "SubjectUserName": "DESKTOP\\admin",
            },
        }
        parsed = {
            "identity": {
                "TaskName": "\\GirlMeetsCode-LogUploader",
                "SubjectUserName": "DESKTOP\\admin",
            }
        }

        assert is_agent_generated_event(0, 4698, record, parsed)

    def test_filters_security_process_creation_for_upload_script(self):
        record = {
            "EventID": 4688,
            "EventData": {
                "NewProcessName": "C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe",
                "CommandLine": "powershell.exe -File C:\\agent\\extract_and_upload_security_log.ps1 -Mode Run",
            },
        }
        parsed = {
            "identity": {
                "NewProcessName": "C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe",
                "CommandLine": record["EventData"]["CommandLine"],
            }
        }

        assert is_agent_generated_event(0, 4688, record, parsed)

    def test_keeps_unrelated_process_creation(self):
        record = {
            "EventID": 4688,
            "EventData": {
                "NewProcessName": "C:\\Windows\\System32\\notepad.exe",
                "CommandLine": "notepad.exe C:\\temp\\notes.txt",
            },
        }
        parsed = {"identity": dict(record["EventData"])}

        assert not is_agent_generated_event(0, 4688, record, parsed)
