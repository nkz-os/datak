<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { api } from '../api'

const deviceProfile = ref<object | null>(null)
const bufferStatus = ref({ unsynced_count: 0, synced_count: 0, cloud_available: true })
const loading = ref(false)
const systemConfig = ref({
  influxdb_retention_days: 30,
  digital_twin_enabled: false,
  digital_twin_host: '',
  digital_twin_port: 443,
  digital_twin_topic: '',
  digital_twin_username: '',
  digital_twin_password: '',
  digital_twin_entity_type: 'AgriSensor',
  gateway_name: ''
})
const sarefTypes = [
  'AgriculturalRobot',
  'AgriSensor',
  'AgriParcel',
  'AgriOperation',
  'AgriculturalTractor',
  'AgriculturalImplement',
  'WeatherObserved',
  'SatelliteImageObservation',
  'VegetationIndex',
  'LivestockAnimal',
  'LivestockGroup',
  'LivestockFarm',
  'LivestockProduction',
  'PhotovoltaicInstallation',
  'EnergyStorageSystem',
]
const savingSystem = ref(false)

const entityTypeSelect = ref('AgriSensor')

function handleTypeChange() {
  if (entityTypeSelect.value !== 'Custom') {
    systemConfig.value.digital_twin_entity_type = entityTypeSelect.value
  } else {
    // If switching to custom, keep current value if it's not in the list, or clear if it is
    if (sarefTypes.includes(systemConfig.value.digital_twin_entity_type)) {
      systemConfig.value.digital_twin_entity_type = ''
    }
  }
}

onMounted(async () => {
  await Promise.all([
    fetchDeviceProfile(),
    fetchBufferStatus(),
    fetchSystemConfig(),
  ])
})

async function fetchSystemConfig() {
  try {
    const response = await api.get('/api/config/system')
    systemConfig.value = response.data
    
    // Init dropdown
    if (systemConfig.value.digital_twin_entity_type) {
        if (sarefTypes.includes(systemConfig.value.digital_twin_entity_type)) {
            entityTypeSelect.value = systemConfig.value.digital_twin_entity_type
        } else {
            entityTypeSelect.value = 'Custom'
        }
    }
  } catch (e) {
    console.error('Failed to fetch system config:', e)
  }
}

async function saveSystemConfig() {
  savingSystem.value = true
  try {
    await api.put('/api/config/system', systemConfig.value)
    alert('System configuration saved successfully')
  } catch (e: any) {
    alert('Failed to save configuration: ' + (e.response?.data?.detail || e.message))
  } finally {
    savingSystem.value = false
  }
}

async function fetchDeviceProfile() {
  try {
    const response = await api.get('/api/config/device-profile')
    deviceProfile.value = response.data
  } catch (e) {
    console.error('Failed to fetch device profile:', e)
  }
}

async function fetchBufferStatus() {
  try {
    const response = await api.get('/api/config/buffer/status')
    bufferStatus.value = response.data
  } catch (e) {
    console.error('Failed to fetch buffer status:', e)
  }
}

async function flushBuffer() {
  loading.value = true
  try {
    const response = await api.post('/api/config/buffer/flush')
    alert(`Flushed ${response.data.synced} readings`)
    await fetchBufferStatus()
  } catch (e) {
    alert('Flush failed')
  } finally {
    loading.value = false
  }
}

async function downloadProfile() {
  try {
    const response = await api.get('/api/config/device-profile/download')
    // Correctly handle Blob response if configured, or if the API returns JSON, we wrap it.
    // However, the API returns JSONResponse which axios parses. 
    // We can just stringify the already fetched profile or fetching the download endpoint.
    // Actually the download endpoint returns content-disposition but axios might parse JSON body.
    // Safer to stringify deviceProfile.value directly if we trust it matches.
    // Or request blob.
    
    // Let's rely on stringifying deviceProfile.value which we have.
    // It is up to date since we fetch it on mount.
    
    const blob = new Blob([JSON.stringify(deviceProfile.value, null, 2)], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    // Generate filename
    const date = new Date().toISOString().split('T')[0].replace(/-/g, '')
    const name = systemConfig.value.gateway_name || 'datak-gateway'
    a.download = `device-profile-${name}-${date}.json`
    a.click()
    URL.revokeObjectURL(url)
  } catch (e) {
    alert('Download failed')
  }
}

async function exportConfig() {
  try {
    const response = await api.get('/api/config/export')
    const blob = new Blob([JSON.stringify(response.data, null, 2)], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = 'gateway-config.json'
    a.click()
    URL.revokeObjectURL(url)
  } catch (e) {
    alert('Export failed')
  }
}
</script>

<template>
  <div>
    <div class="page-header">
      <h1 class="page-title">Configuration</h1>
    </div>

    <div class="config-grid">
      <!-- System Settings -->
      <div class="card">
        <div class="card-header">
          <h2 class="card-title">System Settings</h2>
          <button 
            class="btn btn-primary" 
            @click="saveSystemConfig" 
            :disabled="savingSystem"
          >
            <i :class="['pi', savingSystem ? 'pi-spin pi-spinner' : 'pi-save']"></i>
            Save Changes
          </button>
        </div>
        
        <div class="form-grid">
          <div class="form-group">
            <label>Gateway Name</label>
            <input v-model="systemConfig.gateway_name" class="form-input" type="text" placeholder="DaTaK Gateway" />
          </div>
          
          <div class="form-group">
             <label>Data Retention (Days)</label>
             <input v-model.number="systemConfig.influxdb_retention_days" class="form-input" type="number" min="1" />
             <small class="help-text">Data older than this will be automatically deleted from InfluxDB.</small>
          </div>

          <div class="form-group" style="grid-column: 1 / -1; margin-top: 1rem; border-top: 1px solid var(--border); padding-top: 1rem;">
             <div class="checkbox-wrapper">
                <input type="checkbox" id="dt-enabled" v-model="systemConfig.digital_twin_enabled">
                <label for="dt-enabled" style="font-weight: 600;">Enable Digital Twin Integration (MQTT)</label>
             </div>
          </div>
          
          <template v-if="systemConfig.digital_twin_enabled">
            <div class="form-group full-width">
              <label>SAREF4Agri Entity Type</label>
              <div style="display: flex; gap: 0.5rem;">
                <select 
                  v-model="entityTypeSelect" 
                  class="form-input" 
                  @change="handleTypeChange"
                >
                  <option v-for="type in sarefTypes" :key="type" :value="type">{{ type }}</option>
                  <option value="Custom">Other / Custom...</option>
                </select>
                <input 
                  v-if="entityTypeSelect === 'Custom'"
                  v-model="systemConfig.digital_twin_entity_type" 
                  type="text" 
                  class="form-input" 
                  placeholder="e.g. MultiSensorStation"
                />
              </div>
              <small class="help-text">Select a standard type or define a custom one for this gateway.</small>
            </div>

            <div class="form-group">
              <label>MQTT Broker Host</label>
              <input v-model="systemConfig.digital_twin_host" class="form-input" type="text" placeholder="mqtt.example.com" />
            </div>
            
            <div class="form-group">
              <label>MQTT Port</label>
              <input v-model.number="systemConfig.digital_twin_port" class="form-input" type="number" placeholder="8883" />
            </div>

            <div class="form-group full-width">
              <label>Topic for Attributes</label>
              <input v-model="systemConfig.digital_twin_topic" class="form-input" type="text" placeholder="/org/device/attrs" />
            </div>
            
            <div class="form-group">
              <label>Username (Optional)</label>
              <input v-model="systemConfig.digital_twin_username" class="form-input" type="text" />
            </div>

            <div class="form-group">
              <label>Password / Token</label>
              <input v-model="systemConfig.digital_twin_password" class="form-input" type="password" />
            </div>
          </template>
        </div>
      </div>

      <!-- Device Profile -->
      <div class="card">
        <div class="card-header">
          <h2 class="card-title">Device Profile</h2>
          <button class="btn btn-primary" @click="downloadProfile">
            <i class="pi pi-download"></i>
            Download JSON
          </button>
        </div>
        <p style="color: var(--text-muted); margin-bottom: 1rem;">
          Export the device profile to configure your Digital Twin platform.
        </p>
        <div v-if="deviceProfile" class="code-block">
          <pre>{{ JSON.stringify(deviceProfile, null, 2).slice(0, 500) }}...</pre>
        </div>
      </div>

      <!-- Buffer Status -->
      <div class="card">
        <div class="card-header">
          <h2 class="card-title">Store & Forward Buffer</h2>
          <button 
            class="btn btn-secondary" 
            @click="flushBuffer"
            :disabled="loading || bufferStatus.unsynced_count === 0"
          >
            <i :class="['pi', loading ? 'pi-spin pi-spinner' : 'pi-sync']"></i>
            Flush Now
          </button>
        </div>
        
        <div class="buffer-stats">
          <div class="buffer-stat">
            <div class="stat-value">{{ bufferStatus.unsynced_count }}</div>
            <div class="stat-label">Pending Sync</div>
          </div>
          <div class="buffer-stat">
            <div class="stat-value">{{ bufferStatus.synced_count }}</div>
            <div class="stat-label">Synced</div>
          </div>
          <div class="buffer-stat">
            <div :class="['status-badge', bufferStatus.cloud_available ? 'success' : 'warning']">
              {{ bufferStatus.cloud_available ? 'Connected' : 'Buffering' }}
            </div>
            <div class="stat-label">Cloud Status</div>
          </div>
        </div>
      </div>

      <!-- Export/Import -->
      <div class="card">
        <div class="card-header">
          <h2 class="card-title">Configuration Backup</h2>
        </div>
        <p style="color: var(--text-muted); margin-bottom: 1rem;">
          Export your current configuration for backup or migration.
        </p>
        <div style="display: flex; gap: 0.75rem;">
          <button class="btn btn-primary" @click="exportConfig">
            <i class="pi pi-download"></i>
            Export Config
          </button>
          <button class="btn btn-secondary" disabled>
            <i class="pi pi-upload"></i>
            Import Config
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.config-grid {
  display: grid;
  gap: 1.5rem;
}

.code-block {
  background: var(--background);
  border-radius: 8px;
  padding: 1rem;
  overflow-x: auto;
}

.code-block pre {
  font-size: 0.75rem;
  color: var(--text-muted);
  margin: 0;
}

.buffer-stats {
  display: flex;
  gap: 2rem;
}

.buffer-stat {
  text-align: center;
}

.buffer-stat .stat-value {
  font-size: 1.5rem;
  font-weight: 700;
}

.buffer-stat .stat-label {
  font-size: 0.75rem;
  color: var(--text-muted);
}

.status-badge {
  display: inline-block;
  padding: 0.375rem 0.75rem;
  border-radius: 20px;
  font-size: 0.75rem;
  font-weight: 600;
}

.status-badge.success {
  background: rgba(34, 197, 94, 0.15);
  color: var(--success);
}

.status-badge.warning {
  background: rgba(234, 179, 8, 0.15);
  color: var(--warning);
}
</style>
