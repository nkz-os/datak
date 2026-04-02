# DaTaK - IoT Edge Gateway & Data Aggregator

<p align="center">
  <img src="frontend/public/logo.png" alt="DaTaK Logo" width="120" />
</p>

<p align="center">
  Industrial IoT gateway for multi-protocol data acquisition with local processing, resilient storage, and Digital Twin forwarding.
  <br />
  <strong>Developed by <a href="https://robotika.cloud">Robotika</a> - IoT Solutions</strong>
</p>

---

## Features

- **Multi-Protocol Support**: Modbus TCP/RTU, CANbus (with DBC parsing), MQTT
- **Hot-Reload**: Add/remove sensors without service restart
- **Offline Resilience**: Store & Forward queue for network failures
- **Statistical Reports**: Automated CSV generation with Min/Max/Avg/StdDev
- **Secure Formula Engine**: Sandboxed Python expressions for data transformation
- **Real-time Dashboard**: Vue.js UI with WebSocket updates
- **Digital Twin Integration**: MQTT-based forwarding to cloud platforms
- **Automation Rules**: Local control loops with condition-based triggers

---

## Requirements

### System Requirements
- **OS**: Linux (Ubuntu 20.04+, Debian 11+) or Windows with WSL2
- **Docker**: v24.0+ with Docker Compose v2.20+
- **RAM**: Minimum 2GB, recommended 4GB+
- **Storage**: Minimum 10GB free space

### Software Dependencies
- Docker & Docker Compose
- Git
- (Optional) Python 3.12+ for local development

---

## Quick Start (Docker - Recommended)

The fastest way to get DaTaK running is with Docker:

```bash
# 1. Clone repository
git clone https://github.com/nkz-os/datak.git
cd datak

# 2. Copy configuration template
cp configs/gateway.example.yaml configs/gateway.yaml

# 3. Start all services
cd docker
docker compose up -d --build

# 4. Access the application
# Frontend: http://localhost:5173
# API Docs: http://localhost:8000/docs
# Default login: admin / admin
```

---

## Development Setup

For local development without Docker:

```bash
# Backend
cd backend
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
alembic upgrade head
python -m app.main

# Frontend (separate terminal)
cd frontend
npm install
npm run dev
```

> **Note**: You still need InfluxDB and Mosquitto running. Use `docker compose up influxdb mosquitto -d` from the `docker/` folder.

---

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
│   (Time-series) │  │   (MQTT Cloud)      │  │   (Local)     │
└─────────────────┘  └─────────────────────┘  └───────────────┘
```

---

## Project Structure

```
datak/
├── backend/          # FastAPI + async drivers
├── frontend/         # Vue.js 3 dashboard
├── docker/           # Docker compose files
├── configs/          # Gateway configuration
├── images/           # Logo assets
└── scripts/          # Deployment utilities
```

---

## Configuration

Copy `configs/gateway.example.yaml` to `configs/gateway.yaml` and adjust:

```yaml
gateway:
  name: "My-Gateway"
  
influxdb:
  url: "http://influxdb:8086"
  token: "datak-dev-token"
  org: "datak"
  bucket: "sensors"
  retention_days: 30

digital_twin:
  enabled: true
  host: "nkz.artotxiki.com"
  port: 443
  topic: "/json/<api_key>/<device_id>/attrs"
  username: "<device_id>"
  password: "<api_key>"
```

---


---

## Nekazari Platform Integration

DaTaK is the official edge gateway for the [Nekazari Platform](https://github.com/nkz-os/nkz). It bridges local sensors with the cloud digital twin via FIWARE NGSI-LD standards.

### End-to-End Setup Flow

```text
1. Register sensors in DaTaK (UI or Modbus/MQTT config)
2. Generate a DeviceProfile JSON from DaTaK
3. Create a sensor in the Nekazari Entity Wizard, importing the profile
4. Copy the MQTT credentials shown by the wizard
5. Configure DaTaK gateway.yaml with those credentials
6. DaTaK publishes SDM-compliant data → Nekazari digital twin
```

### Step 1: Generate a DeviceProfile

DaTaK auto-generates a FIWARE-compatible DeviceProfile from your registered sensors:

```bash
# From DaTaK API (default port 8000)
curl http://localhost:8000/api/config/device-profile | python -m json.tool
```

This returns a JSON profile ready to import into Nekazari:

```json
{
  "name": "DaTaK Gateway",
  "sdm_entity_type": "AgriSensor",
  "mappings": [
    { "incoming_key": "airTemperature", "target_attribute": "airTemperature", "type": "Number" },
    { "incoming_key": "solarRadiation", "target_attribute": "solarRadiation", "type": "Number" }
  ]
}
```

### Step 2: Create Sensor in Nekazari

1. Open the **Entity Wizard** in the Nekazari platform
2. Select **AgriSensor** as entity type
3. Click **Import Profile** and upload the JSON from Step 1
4. Complete the wizard — **copy the MQTT credentials** (shown once, not recoverable)

### Step 3: Configure DaTaK

**Option A — Auto-configuration** (if Nekazari provides a `device-config.json`):

```bash
python scripts/setup_nekazari.py path/to/device-config.json
```

**Option B — Manual edit** of `configs/gateway.yaml`:

```yaml
digital_twin:
  enabled: true
  host: "nkz.robotika.cloud"      # Nekazari MQTT host
  port: 31883                       # NodePort (or 8883 for TLS)
  topic: "/<apikey>/<device_id>/attrs"
  username: "<mqtt_username>"       # From wizard credentials
  password: "<mqtt_password>"       # From wizard credentials
  entity_type: "AgriSensor"
```

Restart DaTaK after changing the config:

```bash
cd docker && docker compose restart backend
```

### Smart Auto-SDM Mapping

DaTaK automatically maps sensor names to FIWARE Smart Data Model (SDM) attributes:

| Sensor name keywords | SDM attribute |
|----------------------|---------------|
| temp, termomet | `airTemperature` |
| hum, rh, humedad | `relativeHumidity` |
| soil, tierra, moist, suelo | `soilMoisture` |
| pres, baro, atm | `atmosphericPressure` |
| wind, viento, anemo, speed | `windSpeed` |
| solar, rad, sun, pira, insol | `solarRadiation` |
| bat, volt, bater | `batteryLevel` |

You can also set an explicit SDM attribute per sensor via the `twin_attribute` field in the DaTaK UI.

Unmatched names are sent as slugified keys (e.g. "Custom Sensor" → `custom_sensor`).

---

## Testing with Simulated Data

DaTaK includes a test data injector that simulates solar, temperature, and tilt sensors:

```bash
# From the project root (requires paho-mqtt)
# Docker: exec into the backend container
docker compose exec backend python /app/scripts/test_data_injector.py

# Local venv
.venv/bin/python scripts/test_data_injector.py

# With custom MQTT broker
.venv/bin/python scripts/test_data_injector.py --host localhost --port 1883
```

The injector publishes realistic solar radiation curves and temperature patterns to the local Mosquitto broker. DaTaK picks them up and forwards them to the cloud if `digital_twin` is enabled.

---

## Docker Deployment (Production)

```bash
# Build and start all services (backend, frontend, mosquitto, influxdb)
cd docker
docker compose up -d --build

# View logs
docker compose logs -f backend

# Rebuild only backend after code changes
docker compose build backend --no-cache && docker compose up -d backend

# Stop
docker compose down
```

Services exposed:
- **Frontend**: http://localhost:5173 (Vue.js dashboard)
- **Backend API**: http://localhost:8000 (FastAPI + Swagger at `/docs`)
- **Local MQTT**: localhost:1883 (Mosquitto)

Default credentials: `admin` / `admin`

---

## Contact & Support

**Robotika** - IoT Solutions  
- Website: [https://robotika.cloud](https://robotika.cloud)  
- Email: [kate@robotika.cloud](mailto:kate@robotika.cloud)

---

## License

AGPL-3.0 - See [LICENSE](LICENSE) for details.
