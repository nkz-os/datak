<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useSensorStore, type Sensor } from '../stores/sensors'
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
    { key: 'address', label: 'Register Address', type: 'number', default: 40001, tooltip: 'Starting register address' },
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
    { key: 'address', label: 'Register Address', type: 'number', default: 40001 },
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
        class="sensor-item"
        style="cursor: pointer;"
        @click="openEditModal(sensor)"
      >
        <div :class="['sensor-status', getStatusClass(sensor.status)]"></div>
        <div class="sensor-info" style="flex: 2;">
          <div class="sensor-name">{{ sensor.name }}</div>
          <div class="sensor-protocol">{{ sensor.protocol }} • {{ sensor.description || 'No description' }}</div>
        </div>
        <div style="flex: 1; text-align: center;">
          <div style="font-size: 0.75rem; color: var(--text-muted);">Formula</div>
          <code style="font-size: 0.875rem;">{{ sensor.data_formula }}</code>
        </div>
        <div class="sensor-value">
          <div class="sensor-reading">{{ sensor.last_value?.toFixed(2) || '--' }}</div>
          <div class="sensor-unit">{{ sensor.unit || '' }}</div>
        </div>
        <button 
          v-if="sensor.connection_params?.is_actuator"
          class="btn btn-primary" 
          style="margin-right: 0.5rem; padding: 0.5rem 0.75rem;"
          title="Control Device"
          @click.stop="openWriteModal(sensor)"
        >
          <i class="pi pi-bolt"></i>
        </button>
        <button 
          class="btn btn-secondary delete-btn" 
          title="Delete Sensor"
          @click.stop="handleDelete(sensor)"
        >
          <i class="pi pi-trash"></i>
        </button>
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
</style>
