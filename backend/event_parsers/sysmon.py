from event_details import compact_fields, field, make_event_details
from event_parsers.common import event_data_map, normalize_payload, payload_value


def parse_sysmon_process_create(record: dict) -> dict:
    payload = normalize_payload(record)
    data = event_data_map(payload)
    image = payload_value(payload, "Image") or data.get("Image")
    command_line = payload_value(payload, "CommandLine") or data.get("CommandLine")
    parent_image = payload_value(payload, "ParentImage") or data.get("ParentImage")
    user = payload_value(payload, "User") or data.get("User")
    hashes = payload_value(payload, "Hashes") or data.get("Hashes")

    fields = compact_fields(
        field("image", "Image", image, emphasis=True),
        field("commandLine", "Command Line", command_line),
        field("parentImage", "Parent Image", parent_image),
        field("user", "User", user),
        field("hashes", "Hashes", hashes),
    )
    identity = {
        key: value
        for key, value in {
            "image": image,
            "commandLine": command_line,
            "parentImage": parent_image,
            "user": user,
        }.items()
        if value
    }
    return make_event_details("Process Create", fields, identity)


def parse_sysmon_network_connection(record: dict) -> dict:
    payload = normalize_payload(record)
    data = event_data_map(payload)
    source_ip = payload_value(payload, "SourceIp") or data.get("SourceIp")
    destination_ip = payload_value(payload, "DestinationIp") or data.get("DestinationIp")
    destination_port = payload_value(payload, "DestinationPort") or data.get("DestinationPort")
    destination_hostname = payload_value(payload, "DestinationHostname") or data.get("DestinationHostname")
    source_port = payload_value(payload, "SourcePort") or data.get("SourcePort")
    protocol = payload_value(payload, "Protocol") or data.get("Protocol")
    image = payload_value(payload, "Image") or data.get("Image")
    user = payload_value(payload, "User") or data.get("User")

    fields = compact_fields(
        field("sourceIp", "Source IP", source_ip, emphasis=True),
        field("destinationIp", "Destination IP", destination_ip, emphasis=True),
        field("destinationPort", "Destination Port", destination_port),
        field("destinationHostname", "Destination Hostname", destination_hostname),
        field("sourcePort", "Source Port", source_port),
        field("protocol", "Protocol", protocol),
        field("image", "Process", image),
        field("user", "User", user),
    )
    identity = {
        key: value
        for key, value in {
            "sourceIp": source_ip,
            "destinationIp": destination_ip,
            "destinationPort": destination_port,
            "destinationHostname": destination_hostname,
            "sourcePort": source_port,
            "protocol": protocol,
            "image": image,
        }.items()
        if value
    }
    return make_event_details("Network Connection", fields, identity)


def parse_sysmon_process_terminated(record: dict) -> dict:
    payload = normalize_payload(record)
    data = event_data_map(payload)
    image = payload_value(payload, "Image") or data.get("Image")
    user = payload_value(payload, "User") or data.get("User")
    fields = compact_fields(
        field("image", "Image", image, emphasis=True),
        field("user", "User", user),
    )
    identity = {"image": image} if image else {}
    return make_event_details("Process Terminated", fields, identity)


def parse_sysmon_process_access(record: dict) -> dict:
    payload = normalize_payload(record)
    data = event_data_map(payload)
    source_image = payload_value(payload, "SourceImage") or data.get("SourceImage")
    target_image = payload_value(payload, "TargetImage") or data.get("TargetImage")
    granted_access = payload_value(payload, "GrantedAccess") or data.get("GrantedAccess")
    call_trace = payload_value(payload, "CallTrace") or data.get("CallTrace")

    fields = compact_fields(
        field("sourceImage", "Source Process", source_image, emphasis=True),
        field("targetImage", "Target Process", target_image, emphasis=True),
        field("grantedAccess", "Granted Access", granted_access),
        field("callTrace", "Call Trace", call_trace),
    )
    identity = {
        key: value
        for key, value in {
            "sourceImage": source_image,
            "targetImage": target_image,
            "grantedAccess": granted_access,
        }.items()
        if value
    }
    return make_event_details("Process Access", fields, identity)


def parse_sysmon_image_loaded(record: dict) -> dict:
    payload = normalize_payload(record)
    data = event_data_map(payload)
    image_loaded = payload_value(payload, "ImageLoaded") or data.get("ImageLoaded")
    image = payload_value(payload, "Image") or data.get("Image")
    signed = payload_value(payload, "Signed") or data.get("Signed")
    fields = compact_fields(
        field("imageLoaded", "Image Loaded", image_loaded, emphasis=True),
        field("image", "Process", image),
        field("signed", "Signed", signed),
    )
    identity = {
        key: value
        for key, value in {"imageLoaded": image_loaded, "image": image}.items()
        if value
    }
    return make_event_details("Image Loaded", fields, identity)


def parse_sysmon_file_create(record: dict) -> dict:
    payload = normalize_payload(record)
    data = event_data_map(payload)
    target = payload_value(payload, "TargetFilename") or data.get("TargetFilename")
    image = payload_value(payload, "Image") or data.get("Image")
    user = payload_value(payload, "User") or data.get("User")
    fields = compact_fields(
        field("targetFilename", "Target File", target, emphasis=True),
        field("image", "Process", image),
        field("user", "User", user),
    )
    identity = {key: value for key, value in {"targetFilename": target, "image": image}.items() if value}
    return make_event_details("File Create", fields, identity)


def parse_sysmon_file_delete(record: dict) -> dict:
    payload = normalize_payload(record)
    data = event_data_map(payload)
    target = payload_value(payload, "TargetFilename") or data.get("TargetFilename")
    image = payload_value(payload, "Image") or data.get("Image")
    user = payload_value(payload, "User") or data.get("User")
    fields = compact_fields(
        field("targetFilename", "Deleted File", target, emphasis=True),
        field("image", "Process", image),
        field("user", "User", user),
    )
    identity = {key: value for key, value in {"targetFilename": target, "image": image}.items() if value}
    return make_event_details("File Delete", fields, identity)


def parse_sysmon_registry_set_value(record: dict) -> dict:
    payload = normalize_payload(record)
    data = event_data_map(payload)
    target_object = payload_value(payload, "TargetObject") or data.get("TargetObject")
    details = payload_value(payload, "Details") or data.get("Details")
    image = payload_value(payload, "Image") or data.get("Image")
    fields = compact_fields(
        field("targetObject", "Target Object", target_object, emphasis=True),
        field("details", "Details", details),
        field("image", "Process", image),
    )
    identity = {
        key: value
        for key, value in {"targetObject": target_object, "details": details, "image": image}.items()
        if value
    }
    return make_event_details("Registry SetValue", fields, identity)


SYSMON_EVENT_PARSERS = {
    1: parse_sysmon_process_create,
    3: parse_sysmon_network_connection,
    5: parse_sysmon_process_terminated,
    7: parse_sysmon_image_loaded,
    10: parse_sysmon_process_access,
    11: parse_sysmon_file_create,
    13: parse_sysmon_registry_set_value,
    26: parse_sysmon_file_delete,
}
