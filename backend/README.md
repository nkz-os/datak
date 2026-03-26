# DaTaK - IoT Edge Gateway & Data Aggregator

Industrial IoT gateway for multi-protocol data acquisition (Modbus TCP/RTU, CANbus, MQTT) with local processing, resilient storage, and Digital Twin forwarding.

## Features

- **Multi-Protocol Support**: Modbus TCP/RTU, CANbus (with DBC parsing), MQTT
- **Hot-Reload**: Add/remove sensors without service restart
- **Offline Resilience**: Store & Forward queue for network failures
- **Statistical Reports**: Automated CSV generation with Min/Max/Avg/StdDev
- **Secure Formula Engine**: Sandboxed Python expressions for data transformation
- **Real-time Dashboard**: Vue.js UI with WebSocket updates
- **Audit Trail**: Full logging of configuration changes

## Quick Start

```bash
# Clone repository
git clone https://github.com/k8-benetis/datak.git
cd datak

# Start infrastructure (InfluxDB, Mosquitto, Prometheus)
docker-compose -f docker/docker-compose.yml up -d

# Setup Python environment
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# Run migrations
alembic upgrade head

# Start gateway
python -m app.main
```

## Architecture

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│  Modbus Devices │     │  ESP32 (MQTT)   │     │  CANbus Network │
└────────┬────────┘     └────────┬────────┘     └────────┬────────┘
         │                       │                       │
         └───────────────────────┼───────────────────────┘
                                 │
                    ┌────────────▼────────────┐
                    │    DaTaK Gateway        │
                    │  ┌─────────────────┐    │
                    │  │  Async Drivers  │    │
                    │  └────────┬────────┘    │
                    │  ┌────────▼────────┐    │
                    │  │  Formula Engine │    │
                    │  └────────┬────────┘    │
                    │  ┌────────▼────────┐    │
                    │  │ Store & Forward │    │
                    │  └────────┬────────┘    │
                    └───────────┼─────────────┘
                                │
         ┌──────────────────────┼──────────────────────┐
         │                      │                      │
┌────────▼────────┐  ┌──────────▼──────────┐  ┌───────▼───────┐
│   InfluxDB      │  │   Digital Twin      │  │   CSV Reports │
│   (Time-series) │  │   (Cloud)           │  │   (Local)     │
└─────────────────┘  └─────────────────────┘  └───────────────┘
```

## Project Structure

```
datak/
├── backend/          # FastAPI + async drivers
├── frontend/         # Vue.js 3 dashboard
├── docker/           # Docker compose files
├── configs/          # Gateway configuration
├── systemd/          # Service files
└── docs/             # Documentation
```

## Configuration

Copy `configs/gateway.example.yaml` to `configs/gateway.yaml` and adjust:

```yaml
gateway:
  name: "Plant-A-Gateway"
  
influxdb:
  url: "http://localhost:8086"
  token: "your-token"
  org: "datak"
  bucket: "sensors"

digital_twin:
  enabled: true
  host: "nkz.artotxiki.com"
  port: 443
  topic: "/json/<api_key>/<device_id>/attrs"
  username: "<device_id>"
  password: "<api_key>"
```

## License

AGPL-3.0 - See [LICENSE](LICENSE) for details.
