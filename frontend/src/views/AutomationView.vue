<script setup lang="ts">
import { ref, onMounted, computed } from 'vue'
import AppLayout from '../components/AppLayout.vue'
import { api } from '../api'
import { useSensorStore } from '../stores/sensors'

interface AutomationRule {
    id: string
    name: string
    condition: string
    target_sensor_id: number
    target_value: number
    target_formula?: string
    cooldown_s: number
    last_triggered: number
}

const sensorStore = useSensorStore()
const rules = ref<AutomationRule[]>([])
const loading = ref(false)
const showGuide = ref(false)

// Form State
const showModal = ref(false)
const editingRule = ref<AutomationRule | null>(null)
const formData = ref({
    name: '',
    condition: '',
    target_sensor_id: 0,
    target_value: 0,
    target_formula: '',
    value_type: 'FIXED', // 'FIXED' | 'FORMULA'
    cooldown_s: 5
})

onMounted(async () => {
    await Promise.all([
        fetchRules(),
        sensorStore.fetchSensors()
    ])
})

async function fetchRules() {
    loading.value = true
    try {
        const res = await api.get('/api/automation/rules')
        rules.value = res.data
    } catch (e) {
        console.error(e)
    } finally {
        loading.value = false
    }
}

// Filter sensors that are Actuators (currently check for writable or standard modbus/mqtt/system)
// System sensors are READ ONLY.
// We need to find sensors that allow writing. 
// For now, let's allow all non-SYSTEM sensors or check connection_params if we had that flag consistently.
// The user previously added "is_actuator" flag in UI. Let's try to use that if persisted?
// It's in connection_params.
const actuators = computed(() => 
    sensorStore.sensors.filter(s => 
        (s.connection_params as any)?.is_actuator === true || 
        // Fallback: Modbus can be actuator
        s.protocol.startsWith('MODBUS') || s.protocol === 'MQTT'
    )
)

const inputSensors = computed(() => sensorStore.sensors)

function openCreateModal() {
    editingRule.value = null
    formData.value = {
        name: '',
        condition: '', // Example: sensor_Temp1 > 50
        target_sensor_id: actuators.value.length > 0 ? actuators.value[0].id : 0,
        target_value: 0,
        target_formula: '',
        value_type: 'FIXED', 
        cooldown_s: 5
    }
    // Set example condition
    if (inputSensors.value.length > 0) {
        formData.value.condition = `${inputSensors.value[0].name} > 20`
    }
    showModal.value = true
}

async function handleSubmit() {
    try {
        const payload = { ...formData.value }
        if (payload.value_type === 'FIXED') {
            payload.target_formula = undefined
        } else {
            // Formula mode
            payload.target_value = 0 // Dummy value for API validation
        }
        
        await api.post('/api/automation/rules', payload)
        await fetchRules()
        showModal.value = false
    } catch (e: any) {
        alert("Failed to save rule: " + (e.response?.data?.detail || e.message))
    }
}

async function deleteRule(id: string) {
    if(!confirm("Delete this rule?")) return
    try {
        await api.delete(`/api/automation/rules/${id}`)
        await fetchRules()
    } catch (e) {
        alert("Failed to delete")
    }
}

function insertVariable(varName: string) {
    formData.value.condition += ` ${varName} `
}
</script>

<template>
    <div>
        <div class="header-actions">
            <h1>Automation Studio</h1>
            <button class="btn btn-primary" @click="openCreateModal">
                <i class="pi pi-plus"></i> New Rule
            </button>
        </div>

        <!-- Automation Guide -->
        <div class="card" style="margin-bottom: 2rem; background: var(--surface); border: 1px solid var(--border); border-radius: 12px; padding: 1rem;">
            <div style="display: flex; justify-content: space-between; align-items: center; cursor: pointer;" @click="showGuide = !showGuide">
                <h2 style="font-size: 1.1rem; margin: 0; display: flex; align-items: center; gap: 0.5rem; color: var(--text);">
                    <i class="pi pi-book" style="color: var(--primary);"></i> Automation Guide
                </h2>
                <i :class="['pi', showGuide ? 'pi-chevron-up' : 'pi-chevron-down']" style="color: var(--text-muted);"></i>
            </div>
            
            <div v-if="showGuide" style="margin-top: 1rem; border-top: 1px solid var(--border); padding-top: 1rem;">
                <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 2rem;">
                    <div>
                        <h3 style="font-size: 1rem; margin-bottom: 0.5rem;"><i class="pi pi-chart-line"></i> Smart Variables</h3>
                        <p style="font-size: 0.9rem; color: var(--text-muted);">Use statistical functions on sensor data (cached from history).</p>
                        <ul style="list-style: none; padding: 0; display: flex; flex-direction: column; gap: 0.5rem; font-size: 0.9rem;">
                            <li><code>stat_{sensor}_{func}_{window}</code></li>
                            <li>
                                <strong>Functions:</strong> <code>mean</code>, <code>min</code>, <code>max</code>, <code>std</code>, <code>median</code>
                            </li>
                            <li>
                                <strong>Windows:</strong> <code>5m</code>, <code>10m</code>, <code>1h</code>, <code>24h</code>
                            </li>
                            <li>
                                <strong>Examples:</strong>
                                <div style="margin-top: 0.25rem;">
                                    <code style="background: var(--surface-hover); padding: 0.2rem 0.4rem; border-radius: 4px;">stat_temp1_mean_10m</code>
                                </div>
                            </li>
                        </ul>
                    </div>
                    
                    <div>
                        <h3 style="font-size: 1rem; margin-bottom: 0.5rem;"><i class="pi pi-sliders-h"></i> Logic & Actions</h3>
                        <p style="font-size: 0.9rem; color: var(--text-muted);">Define conditions using Python syntax.</p>
                        <ul style="list-style: none; padding: 0; display: flex; flex-direction: column; gap: 0.5rem; font-size: 0.9rem;">
                            <li>
                                <strong>Condition:</strong>
                                <code style="display: block; margin-top: 0.25rem; background: var(--surface-hover); padding: 0.2rem 0.4rem; border-radius: 4px;">stat_temp1_mean_5m > 30 and humidity < 50</code>
                            </li>
                            <li>
                                <strong>System Sensors:</strong> Use system metrics like CPU load.
                                <div style="margin-top: 0.25rem;">
                                    <code style="background: var(--surface-hover); padding: 0.2rem 0.4rem; border-radius: 4px;">cpu_percent > 80</code>
                                </div>
                            </li>
                        </ul>
                    </div>
                </div>
            </div>
        </div>

        <div v-if="loading" class="loading">Loading rules...</div>

        <div v-else class="rules-grid">
            <div v-for="rule in rules" :key="rule.id" class="rule-card">
                <div class="rule-header">
                    <h3>{{ rule.name }}</h3>
                    <button class="btn-icon danger" @click="deleteRule(rule.id)"><i class="pi pi-trash"></i></button>
                </div>
                <div class="rule-body">
                    <div class="code-block">IF {{ rule.condition }}</div>
                    <div class="arrow"><i class="pi pi-arrow-down"></i></div>
                    <div class="action-block">
                        THEN Set <strong>Sensor #{{ rule.target_sensor_id }}</strong> to 
                        <strong v-if="rule.target_formula" class="code-font" style="color: var(--primary);">{{ rule.target_formula }}</strong>
                        <strong v-else>{{ rule.target_value }}</strong>
                    </div>
                </div>
                <div class="rule-footer">
                    <small>Cooldown: {{ rule.cooldown_s }}s</small>
                    <small v-if="rule.last_triggered > 0">Last run: {{ new Date(rule.last_triggered * 1000).toLocaleTimeString() }}</small>
                </div>
            </div>
        </div>

        <!-- Create Rule Modal -->
        <div v-if="showModal" class="modal-overlay" @click.self="showModal = false">
            <div class="modal-content large-modal">
                <div class="modal-header">
                    <h2>Create Logic Rule</h2>
                    <button class="btn-close" @click="showModal = false"><i class="pi pi-times"></i></button>
                </div>
                
                <div class="modal-multi-pane">
                    <!-- Left: Cheat Sheet -->
                    <div class="pane sidebar-pane">
                        <h4>Available Variables</h4>
                        <p class="hint">Click to insert</p>
                        
                        <div class="var-group">
                            <h5>Real-time Sensors</h5>
                            <div class="tag-cloud">
                                <span v-for="s in inputSensors" :key="s.id" class="tag" @click="insertVariable(s.name)">
                                    {{ s.name }}
                                </span>
                            </div>
                        </div>

                        <div class="var-group">
                            <h5>Statistical (Jobs/History)</h5>
                            <p class="tiny-text">Syntax: <code>stat_SENSOR_FUNC_WINDOW</code></p>
                            <div class="shortcuts">
                                <div class="shortcut-item" v-for="s in inputSensors.slice(0,5)" :key="s.id">
                                    <span class="tag stat" @click="insertVariable(`stat_${s.name}_mean_1h`)">Mean 1h</span>
                                    <span class="tag stat" @click="insertVariable(`stat_${s.name}_max_24h`)">Max 24h</span>
                                     <span class="tag stat" @click="insertVariable(`stat_${s.name}_min_5m`)">Min 5m</span>
                                </div>
                            </div>
                        </div>
                    </div>

                    <!-- Right: Editor -->
                    <div class="pane main-pane">
                        <form @submit.prevent="handleSubmit">
                            <div class="form-group">
                                <label>Rule Name</label>
                                <input v-model="formData.name" placeholder="e.g. High Temp Alarm" required class="form-input">
                            </div>

                            <div class="form-group">
                                <label>Condition Logic (Python Syntax)</label>
                                <textarea v-model="formData.condition" rows="4" class="form-input code-font" placeholder="Temp1 > 50 and stat_Temp1_mean_1h < 40"></textarea>
                                <small>Supported: max(), min(), round(), abs(), and sensor names.</small>
                            </div>

                            <div class="form-row">
                                <div class="form-group">
                                    <label>Action: Target Actuator</label>
                                    <select v-model="formData.target_sensor_id" class="form-input" required>
                                        <option v-for="s in actuators" :value="s.id" :key="s.id">
                                            {{ s.name }} ({{ s.protocol }})
                                        </option>
                                    </select>
                                </div>
                                <div class="form-group">
                                    <label>Set Value To</label>
                                    <div class="input-group">
                                        <div class="toggle-group">
                                            <button type="button" :class="{active: formData.value_type === 'FIXED'}" @click="formData.value_type = 'FIXED'">Fixed</button>
                                            <button type="button" :class="{active: formData.value_type === 'FORMULA'}" @click="formData.value_type = 'FORMULA'">Formula</button>
                                        </div>
                                        
                                        <input 
                                            v-if="formData.value_type === 'FIXED'"
                                            v-model.number="formData.target_value" 
                                            type="number" 
                                            step="any" 
                                            class="form-input" 
                                            required
                                        >
                                        
                                        <input 
                                            v-if="formData.value_type === 'FORMULA'"
                                            v-model="formData.target_formula" 
                                            type="text" 
                                            class="form-input code-font" 
                                            placeholder="(Temp1 * 2) / 100"
                                            required
                                        >
                                    </div>
                                </div>
                            </div>

                            <div class="form-group">
                                <label>Cooldown (seconds)</label>
                                <input v-model.number="formData.cooldown_s" type="number" class="form-input" required>
                            </div>

                            <div class="form-actions">
                                <button type="submit" class="btn btn-primary block">Create Rule</button>
                            </div>
                        </form>
                    </div>
                </div>
            </div>
        </div>
    </div>
</template>

<style scoped>
.header-actions {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 2rem;
}

.rules-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
    gap: 1.5rem;
}

.rule-card {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 1.5rem;
    display: flex;
    flex-direction: column;
}

.rule-header {
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
    margin-bottom: 1rem;
}

.rule-body {
    background: var(--background);
    padding: 1rem;
    border-radius: 8px;
    margin-bottom: 1rem;
    flex: 1;
}

.code-block {
    font-family: monospace;
    font-weight: bold;
    color: var(--primary);
}

.arrow {
    text-align: center;
    color: var(--text-muted);
    margin: 0.5rem 0;
}

.rule-footer {
    display: flex;
    justify-content: space-between;
    color: var(--text-muted);
    font-size: 0.875rem;
}

/* Modal Layout */
.large-modal {
    max-width: 900px !important;
    width: 95% !important;
    max-height: 85vh;
    display: flex;
    flex-direction: column;
}

.modal-multi-pane {
    display: flex;
    flex: 1;
    overflow: hidden;
    min-height: 400px;
}

.sidebar-pane {
    width: 250px;
    background: var(--surface-hover);
    padding: 1.5rem;
    border-right: 1px solid var(--border);
    overflow-y: auto;
}

.main-pane {
    flex: 1;
    padding: 1.5rem;
    overflow-y: auto;
}

.tag-cloud {
    display: flex;
    flex-wrap: wrap;
    gap: 0.5rem;
    margin-bottom: 1.5rem;
}

.tag {
    background: var(--surface);
    border: 1px solid var(--border);
    padding: 0.25rem 0.5rem;
    border-radius: 4px;
    font-size: 0.85rem;
    cursor: pointer;
    transition: all 0.2s;
}

.tag:hover {
    border-color: var(--primary);
    color: var(--primary);
}

.tag.stat {
    border-style: dashed;
    font-size: 0.75rem;
}

.shortcuts {
    display: flex;
    flex-direction: column;
    gap: 0.5rem;
}

.shortcut-item {
    display: flex;
    gap: 0.5rem;
    align-items: center;
    margin-bottom: 0.25rem;
}

.code-font {
    font-family: 'Fira Code', monospace;
}

.tiny-text {
    font-size: 0.75rem;
    color: var(--text-muted);
}

.toggle-group {
    display: flex;
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 2px;
    margin-bottom: 0.5rem;
    width: fit-content;
}

.toggle-group button {
    background: transparent;
    border: none;
    color: var(--text-muted);
    padding: 0.25rem 0.75rem;
    font-size: 0.85rem;
    border-radius: 4px;
    cursor: pointer;
}

.toggle-group button.active {
    background: var(--primary);
    color: white;
    font-weight: bold;
}
</style>
