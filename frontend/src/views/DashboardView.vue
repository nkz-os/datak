<script setup lang="ts">
import { onMounted, onUnmounted, ref, computed } from 'vue'
import { useSensorStore } from '../stores/sensors'
import { createWebSocket } from '../api'

const sensorStore = useSensorStore()

const ws = ref<WebSocket | null>(null)
const bufferStatus = ref({ unsynced_count: 0, cloud_available: true })

const stats = computed(() => sensorStore.sensorCount)

onMounted(async () => {
  await sensorStore.fetchSensors()
  connectWebSocket()
})

onUnmounted(() => {
  if (ws.value) {
    ws.value.close()
  }
})

function connectWebSocket() {
  ws.value = createWebSocket()
  
  if (!ws.value) return

  ws.value.onmessage = (event) => {
    const message = JSON.parse(event.data)
    
    if (message.type === 'sensor_update') {
      sensorStore.updateSensorValue(
        message.data.sensor_id,
        message.data.value,
        message.data.status,
        message.data.register_id,
        message.data.register_name
      )
    }
  }

  ws.value.onclose = () => {
    setTimeout(connectWebSocket, 5000)
  }
}

function getStatusClass(status: string): string {
  switch (status) {
    case 'ONLINE': return 'online'
    case 'OFFLINE': return 'offline'
    case 'ERROR': return 'warning'
    default: return 'unknown'
  }
}

function formatValue(value: number | undefined, decimals = 2): string {
  if (value === undefined || value === null) return '--'
  return value.toFixed(decimals)
}
</script>

<template>
  <div>
    <div class="page-header">
      <h1 class="page-title">Dashboard</h1>
      <div style="display: flex; gap: 0.5rem; align-items: center;">
        <span 
          :class="['status-indicator', bufferStatus.cloud_available ? 'online' : 'offline']"
        ></span>
        <span style="font-size: 0.875rem; color: var(--text-muted);">
          {{ bufferStatus.cloud_available ? 'Cloud Connected' : 'Buffering' }}
        </span>
      </div>
    </div>

    <!-- Stats Grid -->
    <div class="stats-grid">
      <div class="stat-card">
        <div class="stat-icon info">
          <i class="pi pi-wifi"></i>
        </div>
        <div class="stat-content">
          <div class="stat-value">{{ stats.total }}</div>
          <div class="stat-label">Total Sensors</div>
        </div>
      </div>

      <div class="stat-card">
        <div class="stat-icon success">
          <i class="pi pi-check-circle"></i>
        </div>
        <div class="stat-content">
          <div class="stat-value">{{ stats.online }}</div>
          <div class="stat-label">Online</div>
        </div>
      </div>

      <div class="stat-card">
        <div class="stat-icon error">
          <i class="pi pi-times-circle"></i>
        </div>
        <div class="stat-content">
          <div class="stat-value">{{ stats.offline }}</div>
          <div class="stat-label">Offline</div>
        </div>
      </div>

      <div class="stat-card">
        <div class="stat-icon warning">
          <i class="pi pi-exclamation-triangle"></i>
        </div>
        <div class="stat-content">
          <div class="stat-value">{{ stats.error }}</div>
          <div class="stat-label">Errors</div>
        </div>
      </div>
    </div>

    <!-- Sensors List -->
    <div class="card">
      <div class="card-header">
        <h2 class="card-title">Sensor Status</h2>
        <RouterLink to="/sensors" class="btn btn-secondary">
          <i class="pi pi-arrow-right"></i>
          View All
        </RouterLink>
      </div>

      <div v-if="sensorStore.loading" style="text-align: center; padding: 2rem;">
        <i class="pi pi-spin pi-spinner" style="font-size: 2rem;"></i>
      </div>

      <div v-else-if="sensorStore.sensors.length === 0" style="text-align: center; padding: 2rem; color: var(--text-muted);">
        <i class="pi pi-inbox" style="font-size: 3rem; margin-bottom: 1rem;"></i>
        <p>No sensors configured</p>
        <RouterLink to="/sensors" class="btn btn-primary" style="margin-top: 1rem;">
          Add Sensor
        </RouterLink>
      </div>

      <div v-else class="sensor-list">
        <div 
          v-for="sensor in sensorStore.sensors.slice(0, 10)" 
          :key="sensor.id" 
        >
          <div class="sensor-item">
            <div :class="['sensor-status', getStatusClass(sensor.status)]"></div>
            <div class="sensor-info">
              <div class="sensor-name">{{ sensor.name }}</div>
              <div class="sensor-protocol">{{ sensor.protocol }}</div>
            </div>
            <div class="sensor-value">
              <div class="sensor-reading">{{ formatValue(sensor.last_value) }}</div>
              <div class="sensor-unit">{{ sensor.unit || '' }}</div>
            </div>
          </div>
          <!-- Register values (if any) -->
          <div v-if="sensor.registers && sensor.registers.length > 0" class="register-values">
            <div v-for="reg in sensor.registers" :key="reg.id || reg.name" class="register-value-item">
              <span class="register-name">{{ reg.name }}</span>
              <span class="register-val">{{ formatValue(reg.last_value, reg.decimal_places) }}</span>
              <span class="register-unit">{{ reg.unit || '' }}</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.status-indicator {
  width: 10px;
  height: 10px;
  border-radius: 50%;
}
.status-indicator.online { background: var(--success); box-shadow: 0 0 8px var(--success); }
.status-indicator.offline { background: var(--error); }

.register-values {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
  padding: 0.5rem 0.75rem 0.5rem 2rem;
  background: var(--surface-hover);
  border-bottom: 1px solid var(--border);
}

.register-value-item {
  display: flex;
  align-items: center;
  gap: 0.3rem;
  font-size: 0.8rem;
  padding: 0.25rem 0.5rem;
  background: var(--surface);
  border-radius: 6px;
  border: 1px solid var(--border);
}

.register-name {
  color: var(--text-muted);
}

.register-val {
  font-weight: 600;
  color: var(--text);
  font-variant-numeric: tabular-nums;
}

.register-unit {
  color: var(--text-muted);
  font-size: 0.75rem;
}
</style>
