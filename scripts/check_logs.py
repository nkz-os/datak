import paramiko

HOST = "100.95.129.22"
USER = "g"
PASS = "agrivolt"

def check_logs():
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(HOST, username=USER, password=PASS)
    
    # Get latest deployment dir
    stdin, stdout, stderr = client.exec_command("ls -td /home/g/datak_* | head -1")
    latest_dir = stdout.read().decode().strip()
    print(f"DEBUG: Latest deployment: {latest_dir}")
    
    if latest_dir:
        print("\n--- REMOTE: backend/app/config.py (Snippet) ---")
        stdin, stdout, stderr = client.exec_command(f"grep -A 5 'class Settings' {latest_dir}/backend/app/config.py && grep 'api_cors_origins' {latest_dir}/backend/app/config.py")
        print(stdout.read().decode())

        print("\n--- REMOTE: configs/gateway.yaml (Snippet) ---")
        stdin, stdout, stderr = client.exec_command(f"grep -A 10 'api:' {latest_dir}/configs/gateway.yaml")
        print(stdout.read().decode())

    print("\n--- Docker Logs (Backend Startup) ---")
    stdin, stdout, stderr = client.exec_command("sudo -S docker logs datak-backend 2>&1 --tail 50")
    stdin.write(f"{PASS}\n")
    stdin.flush()
    print(stdout.read().decode())
    
    client.close()

if __name__ == "__main__":
    check_logs()
