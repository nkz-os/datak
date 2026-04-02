import paramiko
import time

HOST = "100.95.129.22"
USER = "g"
PASS = "agrivolt"
REPO_URL = "https://github.com/k8-benetis/datak.git"
repo_name = "datak"
ts = int(time.time())
TARGET_DIR = f"/home/g/{repo_name}_{ts}"
DOCKER_DIR = f"{TARGET_DIR}/docker"

def create_client():
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(HOST, username=USER, password=PASS)
    return client

def exec_sudo(client, command, password):
    print(f"DEBUG: Executing sudo: {command}")
    stdin, stdout, stderr = client.exec_command(f"sudo -S -p '' {command}")
    stdin.write(f"{password}\n")
    stdin.flush()
    
    exit_status = stdout.channel.recv_exit_status()
    out = stdout.read().decode().strip()
    err = stderr.read().decode().strip()
    
    if exit_status != 0:
        print(f"Error executing '{command}': {err}")
    return out

def exec_cmd(client, command):
    print(f"DEBUG: Executing: {command}")
    stdin, stdout, stderr = client.exec_command(command)
    exit_status = stdout.channel.recv_exit_status()
    out = stdout.read().decode().strip()
    err = stderr.read().decode().strip()
    
    if exit_status != 0:
        print(f"Error executing '{command}': {err}")
    return out

def main():
    print(f"Deploying to {USER}@{HOST}...")
    try:
        client = create_client()
        print("Connected.")

        # 1. Cleanup old stack
        print("\n--- Cleaning up old deployment ---")
        # Check for existing docker containers
        containers = exec_sudo(client, "docker ps -aq", PASS)
        if containers:
            print(f"Stopping and removing containers: {containers.replace('\n', ' ')}")
            exec_sudo(client, "docker stop $(docker ps -aq)", PASS)
            exec_sudo(client, "docker rm $(docker ps -aq)", PASS)
            print("Pruning volumes...")
            exec_sudo(client, "docker volume prune -f", PASS)
        
        # Prune networks/volumes to be clean (optional, maybe too aggressive? user said "can remove both")
        # "Eliminar ambos" refers to software. I'll stick to cleaning docker and the folder.
        
        # Remove directory
        print(f"Removing {TARGET_DIR}...")
        exec_sudo(client, f"rm -rf {TARGET_DIR}", PASS)

        # 2. Setup Prerequisites (Check Docker)
        print("\n--- Checking Prerequisites ---")
        docker_ver = exec_cmd(client, "docker --version")
        if "Docker version" not in docker_ver:
            print("Docker not found! Installing...")
            # Simple convenience script install
            exec_cmd(client, "curl -fsSL https://get.docker.com -o get-docker.sh")
            exec_sudo(client, "sh get-docker.sh", PASS)
            exec_sudo(client, f"usermod -aG docker {USER}", PASS)
            print("Docker installed. Note: You might need to relogin for group changes to take effect, but we use sudo for now.")
        else:
            print(f"Found {docker_ver}")

        # Install docker-compose if missing (newer docker has compose plugin, check that)
        compose_ver = exec_cmd(client, "docker compose version")
        if "Docker Compose" not in compose_ver:
             print("Installing Docker Compose plugin...")
             exec_sudo(client, "apt-get update && apt-get install -y docker-compose-plugin", PASS)

        # Install git
        exec_sudo(client, "apt-get update && apt-get install -y git", PASS)

        # 3. Clone Repo
        print("\n--- Cloning Repository ---")
        exec_cmd(client, f"git clone {REPO_URL} {TARGET_DIR}")

        # 4. Configure
        print("\n--- Configuring ---")
        # Verify files
        print("DEBUG: Checking backend files:")
        exec_cmd(client, f"ls -la {TARGET_DIR}/backend")
        
        # Copy example config to active config
        exec_cmd(client, f"cp {TARGET_DIR}/configs/gateway.example.yaml {TARGET_DIR}/configs/gateway.yaml")
        # Adjust config if needed? User didn't specify    
        print("DEBUG: Executing: cp " + TARGET_DIR + "/configs/gateway.example.yaml " + TARGET_DIR + "/configs/gateway.yaml")
        # Patch gateway.yaml for Docker environment (localhost -> service names)
        stdin, stdout, stderr = client.exec_command(f"sed -i 's/localhost:8086/influxdb:8086/g' {TARGET_DIR}/configs/gateway.yaml")
        stdin, stdout, stderr = client.exec_command(f"sed -i 's/broker: \"localhost\"/broker: \"mosquitto\"/g' {TARGET_DIR}/configs/gateway.yaml")
    
        # 5. Start Services
        print("\n--- Starting Services with Docker Compose ---")
        # Build and start
        # explicit path to docker-compose file
        cmd = f"cd {TARGET_DIR}/docker && sudo docker compose up -d --build"
        print(f"Running: {cmd}")
        # using sudo explicitly for docker commands just in case user isn't in group or session not updated
        out = exec_sudo(client, f"docker compose -f {TARGET_DIR}/docker/docker-compose.yml up -d --build", PASS)
        print(out)
        
        print("\n=== Deployment Complete ===")
        print(f"Check status at http://{HOST}:5173 (Frontend) and http://{HOST}:8000/docs (API)")

    except Exception as e:
        print(f"Deployment failed: {e}")
        import traceback
        traceback.print_exc()
    finally:
        if 'client' in locals():
            client.close()

if __name__ == "__main__":
    main()
