<script setup lang="ts">
import { onMounted, ref, computed } from 'vue'
import { useSensorStore, type Sensor, type Register } from '../stores/sensors'
import { api } from '../api' // Kept for other potential uses
import FormulaEditor from '../components/FormulaEditor.vue'

const sensorStore = useSensorStore()

const showModal = ref(false)
const editingSensor = ref<Sensor | null>(null)
const formData = ref({
  name: '',
  description: '',
  protocol: 'MODBUS_TCP',
  connection_params: {} as Record<string, unknown>,
  data_formula: 'val',
  unit: '',
  poll_interval_ms: 1000,
  registers: [] as Register[],
})

const protocols = [
  { value: 'MODBUS_TCP', label: 'Modbus TCP' },
  { value: 'MODBUS_RTU', label: 'Modbus RTU' },
  { value: 'MQTT', label: 'MQTT' },
  { value: 'CAN', label: 'CANbus' },
  { value: 'SYSTEM', label: 'System Mon (PC)' },
  { value: 'VIRTUAL_OUTPUT', label: 'Virtual Output (Automation)' },
]

// Connection params based on protocol
const connectionFields = {
  VIRTUAL_OUTPUT: [
    { key: 'initial_value', label: 'Initial Value', type: 'number', default: 0, tooltip: 'Starting value before automation writes' },
  ],
  SYSTEM: [
    { 
      key: 'metric', 
      label: 'Metric Type', 
      type: 'select', 
      options: [
          { v: 'cpu_percent', l: 'CPU Usage (%)' },
          { v: 'memory_percent', l: 'Memory Usage (%)' },
          { v: 'disk_usage', l: 'Disk Usage (%)' },
          { v: 'temperature', l: 'Temperature (°C)' }
      ],
      default: 'cpu_percent'
    },
    { key: 'path', label: 'Path (Disk only)', type: 'text', default: '/', tooltip: 'Mount point for disk usage' },
    { key: 'sensor_label', label: 'Sensor Label (Temp only)', type: 'text', default: '', tooltip: 'Specific hardware sensor name (optional)' },
  ],
  MODBUS_TCP: [
    { key: 'host', label: 'Host IP', type: 'text', default: '192.168.1.10', tooltip: 'IP address of the Modbus server' },
    { key: 'port', label: 'Port', type: 'number', default: 502, tooltip: 'TCP port (usually 502)' },
    { key: 'slave_id', label: 'Slave ID', type: 'number', default: 1, tooltip: 'Unit ID (1-247)' },
  ],
  MODBUS_RTU: [
    { key: 'port', label: 'Serial Port', type: 'text', default: '/dev/ttyUSB0', tooltip: 'Device path (e.g. /dev/ttyUSB0)' },
    { 
      key: 'baudrate', 
      label: 'Baudrate', 
      type: 'select', 
      options: [1200, 2400, 4800, 9600, 19200, 38400, 57600, 115200],
      default: 9600,
      tooltip: 'Communication speed'
    },
    { 
      key: 'parity', 
      label: 'Parity', 
      type: 'select', 
      options: [{v:'N', l:'None'}, {v:'E', l:'Even'}, {v:'O', l:'Odd'}],
      default: 'N',
      tooltip: 'Error check bit' 
    },
    { 
      key: 'stopbits', 
      label: 'Stop Bits', 
      type: 'select', 
      options: [1, 2],
      default: 1
    },
    { 
      key: 'bytesize', 
      label: 'Byte Size', 
      type: 'select', 
      options: [7, 8],
      default: 8
    },
    { key: 'slave_id', label: 'Slave ID', type: 'number', default: 1 },
  ],
  MQTT: [
    { key: 'broker', label: 'Broker Host', type: 'text', default: 'localhost' },
    { key: 'port', label: 'Broker Port', type: 'number', default: 1883 },
    { key: 'topic', label: 'Topic', type: 'text', default: 'sensors/temp1', tooltip: 'MQTT Topic to subscribe to' },
    { key: 'json_path', label: 'JSON Path', type: 'text', default: '', tooltip: 'JSONPath to extract value (e.g. $.data.temp)' },
    { key: 'username', label: 'Username', type: 'text', default: '', tooltip: 'Optional' },
    { key: 'password', label: 'Password', type: 'password', default: '', tooltip: 'Optional' },
  ],
  CAN: [
    { key: 'interface', label: 'Interface', type: 'text', default: 'socketcan' },
    { key: 'channel', label: 'Channel', type: 'text', default: 'can0' },
    { key: 'arbitration_id', label: 'Arbitration ID (Hex)', type: 'text', default: '0x123', tooltip: 'Message ID in Hex' },
    { key: 'signal_name', label: 'Signal Name', type: 'text', default: '', tooltip: 'Signal name from DBC file' },
  ],
}

onMounted(() => {
  sensorStore.fetchSensors()
})

function openAddModal() {
  editingSensor.value = null
  formData.value = {
    name: '',
    description: '',
    protocol: 'MODBUS_TCP',
    connection_params: {},
    data_formula: 'val',
    unit: '',
    poll_interval_ms: 1000,
    registers: [],
  }
  initConnectionParams('MODBUS_TCP')
  showModal.value = true
}

function openEditModal(sensor: Sensor) {
  editingSensor.value = sensor
  formData.value = {
    name: sensor.name,
    description: sensor.description || '',
    protocol: sensor.protocol,
    connection_params: { ...sensor.connection_params },
    data_formula: sensor.data_formula,
    unit: sensor.unit || '',
    poll_interval_ms: sensor.poll_interval_ms,
    registers: sensor.registers ? sensor.registers.map(r => ({ ...r })) : [],
  }
  showModal.value = true
}

function initConnectionParams(protocol: string) {
  const fields = connectionFields[protocol as keyof typeof connectionFields] || []
  formData.value.connection_params = {}
  fields.forEach(f => {
    formData.value.connection_params[f.key] = f.default
  })
}

function handleProtocolChange() {
  initConnectionParams(formData.value.protocol)
}

async function handleSubmit() {
  if (editingSensor.value) {
    await sensorStore.updateSensor(editingSensor.value.id, formData.value)
  } else {
    try {
      console.log('Creating sensor with payload:', JSON.parse(JSON.stringify(formData.value)))
      await sensorStore.createSensor(formData.value)
    } catch (e: any) {
      console.error("Sensor creation failed:", e)
      const detail = e.response?.data?.detail
      let msg = ''
      if (typeof detail === 'object') {
        msg = JSON.stringify(detail, null, 2)
      } else {
        msg = detail || e.message
      }
      alert('Failed to create sensor:\n' + msg)
    }
  }
  showModal.value = false
}

async function handleDelete(sensor: Sensor) {
  if (confirm(`Delete sensor "${sensor.name}"?`)) {
    await sensorStore.deleteSensor(sensor.id)
  }
}

// Write / Control Logic
const showWriteModal = ref(false)
const writeTarget = ref<Sensor | null>(null)
const writeValue = ref<number>(0)
const isWriting = ref(false)

function openWriteModal(sensor: Sensor) {
  writeTarget.value = sensor
  writeValue.value = 0
  showWriteModal.value = true
}

async function handleWrite() {
  if (!writeTarget.value) return
  isWriting.value = true
  try {
    await api.post(`/api/sensors/${writeTarget.value.id}/write`, { value: writeValue.value })
    alert('Command sent successfully')
    showWriteModal.value = false
  } catch (e: any) {
    alert('Write failed: ' + (e.response?.data?.detail || e.message))
  } finally {
    isWriting.value = false
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

// Register management
const showRegisters = computed(() => {
  return ['MODBUS_TCP', 'MODBUS_RTU'].includes(formData.value.protocol)
})

function addRegister() {
  formData.value.registers.push({
    name: '',
    address: 0,
    count: 1,
    register_type: 'holding',
    data_formula: 'val',
    unit: null,
    decimal_places: 2,
  })
}

function removeRegister(index: number) {
  formData.value.registers.splice(index, 1)
}

// Collapsible row state
const expandedSensors = ref<Set<number>>(new Set())

function toggleExpand(sensorId: number) {
  if (expandedSensors.value.has(sensorId)) {
    expandedSensors.value.delete(sensorId)
  } else {
    expandedSensors.value.add(sensorId)
  }
}

function isExpanded(sensorId: number): boolean {
  return expandedSensors.value.has(sensorId)
}

function formatValue(value: number | undefined | null, decimals = 2): string {
  if (value === undefined || value === null) return '--'
  return value.toFixed(decimals)
}

// WebSocket for live updates on SensorsView
import { onUnmounted } from 'vue'
import { createWebSocket } from '../api'

const ws = ref<WebSocket | null>(null)

function connectWebSocket() {
  ws.value = createWebSocket()
  if (!ws.value) return

  ws.value.onmessage = (event) => {
    try {
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
    } catch (e) {
      console.error('WS parse error:', e)
    }
  }

  ws.value.onclose = () => {
    setTimeout(connectWebSocket, 5000)
  }
}

onMounted(() => {
  connectWebSocket()
})

onUnmounted(() => {
  if (ws.value) {
    ws.value.close()
  }
})
</script>

<template>
  <div>
    <div class="page-header">
      <h1 class="page-title">Sensors</h1>
      <button class="btn btn-primary" @click="openAddModal">
        <i class="pi pi-plus"></i>
        Add Sensor
      </button>
    </div>

    <div class="sensor-list">
      <div 
        v-for="sensor in sensorStore.sensors" 
        :key="sensor.id" 
        class="sensor-card-wrapper"
      >
        <div 
          class="sensor-item"
          style="cursor: pointer;"
          @click="toggleExpand(sensor.id)"
        >
          <!-- Expand/Collapse Chevron -->
          <div class="expand-icon" :class="{ 'expanded': isExpanded(sensor.id) }">
            <i class="pi pi-chevron-right"></i>
          </div>

          <div :class="['sensor-status', getStatusClass(sensor.status)]"></div>
          
          <div class="sensor-info" style="flex: 2;">
            <div class="sensor-name">{{ sensor.name }}</div>
            <div class="sensor-protocol">
              {{ sensor.protocol }}
              <span v-if="sensor.registers && sensor.registers.length > 0">
                • {{ sensor.registers.length }} channels
              </span>
              <span v-if="sensor.description"> • {{ sensor.description }}</span>
            </div>
          </div>

          <!-- Multi-register summary badges OR single formula -->
          <div class="sensor-summary" style="flex: 2; display: flex; flex-wrap: wrap; gap: 0.5rem; align-items: center;">
            <template v-if="sensor.registers && sensor.registers.length > 0">
              <span 
                v-for="reg in sensor.registers" 
                :key="reg.id || reg.name"
                class="channel-pill"
              >
                <span class="pill-name">{{ reg.name }}:</span>
                <span class="pill-val">{{ formatValue(reg.last_value, reg.decimal_places) }}</span>
                <span class="pill-unit">{{ reg.unit || '' }}</span>
              </span>
            </template>
            <template v-else>
              <div style="font-size: 0.75rem; color: var(--text-muted);">
                Formula: <code style="font-size: 0.85rem;">{{ sensor.data_formula }}</code>
              </div>
            </template>
          </div>

          <!-- Main Value (if single register) -->
          <div v-if="!sensor.registers || sensor.registers.length === 0" class="sensor-value">
            <div class="sensor-reading">{{ formatValue(sensor.last_value) }}</div>
            <div class="sensor-unit">{{ sensor.unit || '' }}</div>
          </div>

          <!-- Actions -->
          <div class="sensor-actions" style="display: flex; align-items: center; gap: 0.5rem;" @click.stop>
            <button 
              v-if="sensor.connection_params?.is_actuator"
              class="btn btn-primary" 
              style="padding: 0.5rem 0.75rem;"
              title="Control Device"
              @click="openWriteModal(sensor)"
            >
              <i class="pi pi-bolt"></i>
            </button>
            <button 
              class="btn btn-secondary" 
              style="padding: 0.5rem 0.75rem;"
              title="Edit Sensor Configuration"
              @click="openEditModal(sensor)"
            >
              <i class="pi pi-pencil"></i>
            </button>
            <button 
              class="btn btn-secondary delete-btn" 
              title="Delete Sensor"
              @click="handleDelete(sensor)"
            >
              <i class="pi pi-trash"></i>
            </button>
          </div>
        </div>

        <!-- Collapsible Register Breakdown -->
        <div v-if="isExpanded(sensor.id)" class="sensor-details-panel">
          <div v-if="sensor.registers && sensor.registers.length > 0" class="registers-table">
            <div class="table-header-row">
              <div class="col-name">Channel / Variable</div>
              <div class="col-addr">Modbus Register</div>
              <div class="col-formula">Formula</div>
              <div class="col-val">Live Value</div>
              <div class="col-raw">Raw Value</div>
            </div>
            <div 
              v-for="reg in sensor.registers" 
              :key="reg.id || reg.name" 
              class="table-data-row"
            >
              <div class="col-name">
                <strong>{{ reg.name }}</strong>
                <span v-if="reg.twin_attribute" class="sdm-badge">{{ reg.twin_attribute }}</span>
              </div>
              <div class="col-addr">
                <code>{{ reg.register_type }} @ {{ reg.address }}</code>
              </div>
              <div class="col-formula">
                <code>{{ reg.data_formula }}</code>
              </div>
              <div class="col-val">
                <span class="live-val">{{ formatValue(reg.last_value, reg.decimal_places) }}</span>
                <span class="live-unit">{{ reg.unit || '' }}</span>
              </div>
              <div class="col-raw text-muted">
                {{ reg.last_raw_value !== undefined && reg.last_raw_value !== null ? reg.last_raw_value : '--' }}
              </div>
            </div>
          </div>
          <div v-else class="single-sensor-details">
            <div class="detail-item">
              <span class="detail-label">Protocol:</span>
              <span class="detail-value">{{ sensor.protocol }}</span>
            </div>
            <div class="detail-item">
              <span class="detail-label">Poll Interval:</span>
              <span class="detail-value">{{ sensor.poll_interval_ms }} ms</span>
            </div>
            <div class="detail-item">
              <span class="detail-label">Formula:</span>
              <span class="detail-value"><code>{{ sensor.data_formula }}</code></span>
            </div>
            <div class="detail-item">
              <span class="detail-label">Raw Value:</span>
              <span class="detail-value">{{ sensor.last_raw_value ?? '--' }}</span>
            </div>
            <div class="detail-item">
              <span class="detail-label">Transformed Value:</span>
              <span class="detail-value"><strong>{{ formatValue(sensor.last_value) }} {{ sensor.unit || '' }}</strong></span>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- Write Modal -->
    <div v-if="showWriteModal" class="modal-overlay" @click.self="showWriteModal = false">
      <div class="modal-content" style="max-width: 400px;">
        <div class="modal-header">
          <h2>Control Device</h2>
          <button class="btn-close" @click="showWriteModal = false"><i class="pi pi-times"></i></button>
        </div>
        <form @submit.prevent="handleWrite" class="modal-body">
          <p class="text-muted" style="margin-bottom: 1rem;">
            Sending command to <strong>{{ writeTarget?.name }}</strong> via {{ writeTarget?.protocol }}
          </p>
          <div class="form-group">
            <label class="form-label">Value to Write</label>
            <input v-model.number="writeValue" type="number" step="any" class="form-input" required autofocus />
          </div>
          <div class="modal-footer">
            <button type="button" class="btn btn-secondary" @click="showWriteModal = false">Cancel</button>
            <button type="submit" class="btn btn-primary" :disabled="isWriting">
              <i v-if="isWriting" class="pi pi-spin pi-spinner"></i>
              {{ isWriting ? 'Sending...' : 'Send Command' }}
            </button>
          </div>
        </form>
      </div>
    </div>

    <!-- Modal -->
    <div v-if="showModal" class="modal-overlay" @click.self="showModal = false">
      <div class="modal-content">
        <div class="modal-header">
          <h2>{{ editingSensor ? 'Edit Sensor' : 'Add Sensor' }}</h2>
          <button class="btn-close" @click="showModal = false">
            <i class="pi pi-times"></i>
          </button>
        </div>

        <form @submit.prevent="handleSubmit" class="modal-body">
          <div class="form-row">
            <div class="form-group" style="flex: 2;">
              <label class="form-label">Name *</label>
              <input v-model="formData.name" type="text" class="form-input" required />
            </div>
            <div class="form-group" style="flex: 1;">
              <label class="form-label">Protocol *</label>
              <select 
                v-model="formData.protocol" 
                class="form-input"
                @change="handleProtocolChange"
                :disabled="!!editingSensor"
              >
                <option v-for="p in protocols" :key="p.value" :value="p.value">
                  {{ p.label }}
                </option>
              </select>
            </div>
          </div>

          <div class="form-group">
            <label class="form-label">Description</label>
            <input v-model="formData.description" type="text" class="form-input" />
          </div>

          <div class="form-group" style="margin-bottom: 1rem;">
             <label class="check-item" style="display: flex; align-items: center; gap: 0.5rem; cursor: pointer;">
                <input type="checkbox" v-model="formData.connection_params['is_actuator']">
                <span>Is Actuator / Controllable?</span>
                <i class="pi pi-bolt" style="color: var(--primary);"></i>
             </label>
             <small class="text-muted">Enables manual control button and automation targeting.</small>
          </div>

          <h3 style="margin: 1rem 0 0.75rem; font-size: 0.875rem; color: var(--text-muted); display: flex; align-items: center; gap: 0.5rem;">
            Connection Parameters (Source of 'val')
            <i class="pi pi-info-circle" style="font-size: 0.8rem;" title="Configure protocol-specific settings. The value read here is passed as 'val' to the formula below."></i>
          </h3>

          <div class="form-row" style="flex-wrap: wrap;">
            <div 
              v-for="field in connectionFields[formData.protocol as keyof typeof connectionFields]"
              :key="field.key"
              class="form-group"
              style="flex: 1; min-width: 150px;"
            >
              <label class="form-label" :title="field.tooltip">
                {{ field.label }}
                <i v-if="field.tooltip" class="pi pi-question-circle" style="font-size: 0.7rem; margin-left: 4px; color: var(--text-muted);"></i>
              </label>
              
              <!-- Select Input -->
              <select 
                v-if="field.type === 'select'"
                v-model="formData.connection_params[field.key]"
                class="form-input"
              >
                <option 
                  v-for="opt in field.options" 
                  :key="typeof opt === 'object' ? opt.v : opt" 
                  :value="typeof opt === 'object' ? opt.v : opt"
                >
                  {{ typeof opt === 'object' ? opt.l : opt }}
                </option>
              </select>

              <!-- Number Input -->
              <input 
                v-else-if="field.type === 'number'"
                v-model.number="formData.connection_params[field.key]"
                type="number"
                class="form-input"
              />

              <!-- Text/Password Input -->
              <input 
                v-else
                v-model="formData.connection_params[field.key]"
                :type="field.type"
                class="form-input"
              />
            </div>
          </div>

          <div class="form-row">
            <div class="form-group" style="flex: 1;">
              <label class="form-label">
                  Data Formula 
                  <span class="text-muted" style="font-size: 0.75rem; font-weight: normal;">(Use 'val' for input)</span>
              </label>
              <FormulaEditor v-model="formData.data_formula" />
            </div>
          </div>
          
          <!-- Registers section (Modbus only) -->
          <div v-if="showRegisters" style="margin-top: 1rem;">
            <h3 style="margin: 0.5rem 0 0.75rem; font-size: 0.875rem; color: var(--text-muted); display: flex; align-items: center; gap: 0.5rem;">
              📊 Registers
              <i class="pi pi-info-circle" style="font-size: 0.8rem;" title="Define each register to read from this device."></i>
            </h3>
            
            <div v-if="formData.registers.length === 0" class="text-muted" style="padding: 1rem; border: 1px dashed var(--border); border-radius: 8px; text-align: center; font-size: 0.8rem;">
              No registers defined yet. Click "Add Register" to define readings.
            </div>

            <div v-for="(reg, idx) in formData.registers" :key="idx" class="register-row">
              <div class="form-row">
                <div class="form-group" style="flex: 0.8;">
                  <label class="form-label">Name</label>
                  <input v-model="reg.name" type="text" class="form-input" placeholder="e.g. Temperature" />
                </div>
                <div class="form-group" style="flex: 0.8;">
                  <label class="form-label">Address</label>
                  <input v-model.number="reg.address" type="number" class="form-input" min="0" />
                </div>
                <div class="form-group" style="flex: 0.5;">
                  <label class="form-label">Count</label>
                  <input v-model.number="reg.count" type="number" class="form-input" min="1" max="4" />
                </div>
                <div class="form-group" style="flex: 0.8;">
                  <label class="form-label">Type</label>
                  <select v-model="reg.register_type" class="form-input">
                    <option value="holding">Holding</option>
                    <option value="input">Input</option>
                    <option value="coil">Coil</option>
                    <option value="discrete">Discrete</option>
                  </select>
                </div>
              </div>
              <div class="form-row">
                <div class="form-group" style="flex: 1;">
                  <label class="form-label">Formula</label>
                  <input v-model="reg.data_formula" type="text" class="form-input" placeholder="val / 10.0" />
                </div>
                <div class="form-group" style="flex: 0.6;">
                  <label class="form-label">Unit</label>
                  <input v-model="reg.unit" type="text" class="form-input" placeholder="°C" />
                </div>
                <div class="form-group" style="flex: 0.3;">
                  <label class="form-label">&nbsp;</label>
                  <button type="button" class="btn btn-secondary" @click="removeRegister(idx)" style="padding: 0.5rem 0.75rem; color: #ef4444; border-color: #ef4444;">
                    <i class="pi pi-trash"></i>
                  </button>
                </div>
              </div>
            </div>

            <button type="button" class="btn btn-secondary" @click="addRegister" style="margin-top: 0.5rem; width: 100%;">
              <i class="pi pi-plus"></i> Add Register
            </button>
          </div>
          
          <div class="form-group">
              <label class="form-label">Unit</label>
              <input v-model="formData.unit" type="text" class="form-input" placeholder="°C, bar, %" />
          </div>

          <div class="form-group">
            <label class="form-label">Poll Interval (ms)</label>
            <input v-model.number="formData.poll_interval_ms" type="number" class="form-input" min="100" max="60000" />
          </div>

          <div class="modal-footer">
            <button type="button" class="btn btn-secondary" @click="showModal = false">Cancel</button>
            <button type="submit" class="btn btn-primary">
              {{ editingSensor ? 'Save Changes' : 'Create Sensor' }}
            </button>
          </div>
        </form>
      </div>
    </div>
  </div>
</template>

<style scoped>
.modal-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.7);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1000;
}

.modal-content {
  background: var(--surface);
  border-radius: 16px;
  border: 1px solid var(--border);
  width: 90%;
  max-width: 600px;
  max-height: 90vh;
  overflow-y: auto;
}

.modal-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 1.25rem 1.5rem;
  border-bottom: 1px solid var(--border);
}

.modal-header h2 {
  font-size: 1.25rem;
  font-weight: 600;
}

.btn-close {
  background: none;
  border: none;
  color: var(--text-muted);
  cursor: pointer;
  padding: 0.5rem;
}

.modal-body {
  padding: 1.5rem;
}

.modal-footer {
  display: flex;
  justify-content: flex-end;
  gap: 0.75rem;
  margin-top: 1.5rem;
  padding-top: 1rem;
  border-top: 1px solid var(--border);
}

.form-row {
  display: flex;
  gap: 1rem;
}

.delete-btn {
  padding: 0.5rem 0.75rem;
  transition: all 0.2s;
}

.delete-btn:hover {
  background: rgba(239, 68, 68, 0.2);
  color: #ef4444;
  border-color: #ef4444;
}

@media (max-width: 600px) {
  .form-row {
    flex-direction: column;
  }
}

.register-row {
  background: var(--surface-hover);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 0.75rem;
  margin-bottom: 0.5rem;
}

.sensor-card-wrapper {
  margin-bottom: 0.75rem;
  border: 1px solid var(--border);
  border-radius: 12px;
  background: var(--surface);
  overflow: hidden;
  transition: border-color 0.2s;
}

.sensor-card-wrapper:hover {
  border-color: var(--primary);
}

.expand-icon {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  color: var(--text-muted);
  transition: transform 0.2s ease;
  margin-right: 0.5rem;
}

.expand-icon.expanded {
  transform: rotate(90deg);
  color: var(--primary);
}

.channel-pill {
  display: inline-flex;
  align-items: center;
  gap: 0.25rem;
  background: var(--surface-hover);
  border: 1px solid var(--border);
  padding: 0.2rem 0.5rem;
  border-radius: 6px;
  font-size: 0.8rem;
}

.pill-name {
  color: var(--text-muted);
  font-weight: 500;
}

.pill-val {
  font-weight: 600;
  color: var(--text-primary);
}

.pill-unit {
  color: var(--text-muted);
  font-size: 0.75rem;
}

.sensor-details-panel {
  background: var(--surface-hover);
  border-top: 1px solid var(--border);
  padding: 1rem 1.5rem;
}

.registers-table {
  width: 100%;
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}

.table-header-row {
  display: flex;
  font-size: 0.75rem;
  font-weight: 600;
  color: var(--text-muted);
  text-transform: uppercase;
  letter-spacing: 0.5px;
  padding-bottom: 0.5rem;
  border-bottom: 1px solid var(--border);
}

.table-data-row {
  display: flex;
  align-items: center;
  font-size: 0.875rem;
  padding: 0.5rem 0;
  border-bottom: 1px dashed var(--border);
}

.table-data-row:last-child {
  border-bottom: none;
}

.col-name { flex: 2; display: flex; align-items: center; gap: 0.5rem; }
.col-addr { flex: 1.5; }
.col-formula { flex: 1; }
.col-val { flex: 1; font-weight: 600; }
.col-raw { flex: 1; }

.live-val { color: var(--primary); font-size: 1rem; }
.live-unit { color: var(--text-muted); margin-left: 0.25rem; }

.sdm-badge {
  background: rgba(34, 197, 94, 0.15);
  color: #22c55e;
  border: 1px solid rgba(34, 197, 94, 0.3);
  padding: 0.1rem 0.4rem;
  border-radius: 4px;
  font-size: 0.7rem;
  font-family: monospace;
}

.single-sensor-details {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  gap: 0.75rem;
  font-size: 0.875rem;
}

.detail-item {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
}

.detail-label {
  font-size: 0.75rem;
  color: var(--text-muted);
}
</style>
