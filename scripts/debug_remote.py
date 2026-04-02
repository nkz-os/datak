import paramiko

HOST = "100.95.129.22"
USER = "g"
PASS = "agrivolt"

def main():
    print(f"Connecting to {HOST}...")
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(HOST, username=USER, password=PASS)
    
    # Get backend container ID
    stdin, stdout, stderr = client.exec_command("sudo -S -p '' docker ps -qf name=backend")
    stdin.write(f"{PASS}\n")
    stdin.flush()
    
    container_id = stdout.read().decode().strip()
    
    if not container_id:
        print("Backend container not found!")
        return
        
    print(f"Fetching logs for container {container_id}...")
    stdin, stdout, stderr = client.exec_command(f"sudo -S -p '' docker logs --tail 50 {container_id}")
    stdin.write(f"{PASS}\n")
    stdin.flush()
    
    print(stdout.read().decode())
    print(stderr.read().decode())
    
    client.close()

if __name__ == "__main__":
    main()
