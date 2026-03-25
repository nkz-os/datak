import pexpect
import sys

def run_remote_command(host, user, password, command):
    ssh_command = f"ssh -o StrictHostKeyChecking=no {user}@{host} '{command}'"
    child = pexpect.spawn(ssh_command, encoding='utf-8')
    child.logfile = sys.stdout
    try:
        i = child.expect(['password:', pexpect.EOF, pexpect.TIMEOUT], timeout=30)
        if i == 0:
            child.sendline(password)
            child.expect(pexpect.EOF)
            return child.before
        elif i == 1:
            return child.before
        else:
            return "Timeout"
    except Exception as e:
        return str(e)

if __name__ == "__main__":
    host = "192.168.1.132"
    user = "agrivolt"
    password = "agrivolt"
    
    print("--- Docker PS ---")
    print(run_remote_command(host, user, password, "docker ps"))
    
    print("\n--- Backend Logs (last 20 lines) ---")
    print(run_remote_command(host, user, password, "docker logs --tail 20 datak-backend"))
    
    print("\n--- Mosquitto Logs (last 20 lines) ---")
    print(run_remote_command(host, user, password, "docker logs --tail 20 datak-mosquitto"))
