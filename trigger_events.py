import argparse
import time
import subprocess
import ctypes

def trigger_failed_logon():
    """
    Type 1: Generates Event ID 4625 (Failed Logon).
    Uses the Windows API to attempt an interactive login with a fake user.
    """
    print("Triggering failed logon (Event ID 4625)...")
    advapi32 = ctypes.windll.advapi32
    token = ctypes.c_void_p()
    # 2 = LOGON32_LOGON_INTERACTIVE, 0 = LOGON32_PROVIDER_DEFAULT
    # This will fail and generate a 4625 event in the Security Log.
    advapi32.LogonUserW("RayleighFakeUser", ".", "FakePassword123!", 2, 0, ctypes.byref(token))
    time.sleep(1)

def trigger_account_management():
    """
    Type 2: Generates Event IDs 4720 (User Created) and 4726 (User Deleted).
    Creates and immediately removes a temporary user. Requires Admin rights.
    """
    print("Triggering account creation/deletion (Event IDs 4720, 4726)...")
    username = "RayleighTestUser"
    password = "TempPassword!"
    
    # Create the user
    subprocess.run(f'net user {username} {password} /add', shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1) # Brief pause to ensure LSA registers the creation before deletion

    # Delete the user
    subprocess.run(f'net user {username} /delete', shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def trigger_firewall_rule():
    """
    Type 3: Generates Event ID 4946 (Firewall Rule Added) and 4947 (Rule Changed/Deleted).
    Creates a benign, disabled firewall rule and deletes it. Requires Admin rights.
    """
    print("Triggering firewall rule modification (Event IDs 4946, 4947)...")
    rule_name = "RayleighTest_FirewallRule"
    
    # Add a disabled rule
    add_cmd = f'netsh advfirewall firewall add rule name="{rule_name}" dir=in action=block enable=no'
    subprocess.run(add_cmd, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1)
    
    # Delete the rule
    del_cmd = f'netsh advfirewall firewall delete rule name="{rule_name}"'
    subprocess.run(del_cmd, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def main():
    parser = argparse.ArgumentParser(description="Active Windows Security Event Trigger")
    parser.add_argument("--type", required=True, choices=["type1", "type2", "type3"], help="Which action to trigger")
    parser.add_argument("--period", type=int, required=True, help="Frequency of triggers in seconds")

    args = parser.parse_args()

    print(f"Starting real event triggers for {args.type} every {args.period} seconds.")
    print("Make sure you are running this as Administrator!")
    print("Press Ctrl+C to stop.\n")

    try:
        while True:
            if args.type == "type1":
                trigger_failed_logon()
            elif args.type == "type2":
                trigger_account_management()
            elif args.type == "type3":
                trigger_firewall_rule()
            
            time.sleep(args.period - 1)
    except KeyboardInterrupt:
        print("\nTrigger script stopped by user.")

if __name__ == "__main__":
    main()