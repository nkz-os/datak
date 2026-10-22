<script setup lang="ts">
import { ref, onMounted, computed } from 'vue'
import { useReportJobStore } from '../stores/reportJobs'
import { useSensorStore } from '../stores/sensors'

const jobStore = useReportJobStore()
const sensorStore = useSensorStore()

const showModal = ref(false)
const formData = ref({
  name: '',
  description: '',
  interval_minutes: 60,
  sensor_ids: [] as number[],
  stat_types: ['mean', 'min', 'max'] as string[],
  is_active: true
})

const statOptions = ['mean', 'min', 'max', 'stddev', 'count']

onMounted(async () => {
  await Promise.all([
    jobStore.fetchJobs(),
    sensorStore.fetchSensors()
  ])
})

function openModal() {
  formData.value = {
    name: '',
    description: '',
    interval_minutes: 60,
    sensor_ids: [],
    stat_types: ['mean', 'min', 'max'],
    is_active: true
  }
  showModal.value = true
}

async function handleSubmit() {
  try {
    await jobStore.createJob(formData.value)
    showModal.value = false
  } catch (e) {
    alert('Failed to create job')
  }
}

async function handleDelete(id: number) {
  if (confirm('Are you sure you want to delete this job?')) {
    await jobStore.deleteJob(id)
  }
}

function formatDate(iso: string | undefined) {
  if (!iso) return 'Never'
  return new Date(iso).toLocaleString()
}
</script>

<template>
  <div>
    <div class="page-header">
      <h1 class="page-title">Statistical Jobs</h1>
      <button class="btn btn-primary" @click="openModal">
        <i class="pi pi-plus"></i> New Job
      </button>
    </div>

    <!-- Job List -->
    <div class="job-grid">
      <div v-for="job in jobStore.jobs" :key="job.id" class="job-card">
        <div class="job-header">
          <h3>{{ job.name }}</h3>
          <span :class="['badge', job.is_active ? 'active' : 'inactive']">
            {{ job.is_active ? 'Active' : 'Paused' }}
          </span>
        </div>
        
        <p class="job-desc">{{ job.description || 'No description' }}</p>
        
        <div class="job-details">
          <div class="detail-item">
            <label>Interval</label>
            <span>Every {{ job.interval_minutes }} min</span>
          </div>
          <div class="detail-item">
            <label>Sensors</label>
            <span>{{ job.sensor_ids.length }} selected</span>
          </div>
          <div class="detail-item">
            <label>Stats</label>
            <span class="stats-tags">
              <span v-for="s in job.stat_types" :key="s" class="tag">{{ s }}</span>
            </span>
          </div>
          <div class="detail-item">
            <label>Next Run</label>
            <span style="color: var(--primary);">{{ formatDate(job.next_run_at) }}</span>
          </div>
           <div class="detail-item">
            <label>Last Run</label>
            <span>{{ formatDate(job.last_run_at) }}</span>
          </div>
        </div>

        <div v-if="job.last_error" class="error-msg">
          Error: {{ job.last_error }}
        </div>

        <div class="job-actions">
           <button class="btn btn-sm btn-danger" @click="handleDelete(job.id)">
             <i class="pi pi-trash"></i> Delete
           </button>
        </div>
      </div>
    </div>

    <!-- Create Modal -->
    <div v-if="showModal" class="modal-overlay" @click.self="showModal = false">
      <div class="modal-content">
        <div class="modal-header">
            <h2>Create New Job</h2>
            <button class="btn-close" @click="showModal = false">✕</button>
        </div>
        <form @submit.prevent="handleSubmit" class="modal-body">
            <div class="form-group">
                <label>Job Name</label>
                <input v-model="formData.name" type="text" class="form-input" required placeholder="e.g. Hourly Temperature Report" />
            </div>
            <div class="form-group">
                <label>Description</label>
                <input v-model="formData.description" type="text" class="form-input" />
            </div>
             <div class="form-row">
                <div class="form-group">
                    <label>Interval (minutes)</label>
                    <input v-model.number="formData.interval_minutes" type="number" class="form-input" min="1" required />
                </div>
            </div>

            <div class="form-group">
                <label>Select Sensors ({{ formData.sensor_ids.length }})</label>
                <div class="sensor-select-list">
                    <label v-for="sensor in sensorStore.sensors" :key="sensor.id" class="check-item">
                        <input type="checkbox" :value="sensor.id" v-model="formData.sensor_ids">
                        {{ sensor.name }}
                    </label>
                </div>
            </div>

            <div class="form-group">
                <label>Statistics</label>
                <div class="stats-select">
                    <label v-for="stat in statOptions" :key="stat" class="check-item">
                        <input type="checkbox" :value="stat" v-model="formData.stat_types">
                        {{ stat }}
                    </label>
                </div>
            </div>

            <div class="modal-footer">
                <button type="button" class="btn btn-secondary" @click="showModal = false">Cancel</button>
                <button type="submit" class="btn btn-primary" :disabled="formData.sensor_ids.length === 0">Create Job</button>
            </div>
        </form>
      </div>
    </div>

  </div>
</template>

<style scoped>
.job-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
    gap: 1.5rem;
    margin-top: 1.5rem;
}

.job-card {
    background: var(--surface-card);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 1.5rem;
    display: flex;
    flex-direction: column;
    gap: 1rem;
}

.job-header {
    display: flex;
    justify-content: space-between;
    align-items: start;
}

.job-header h3 {
    margin: 0;
    font-size: 1.1rem;
    font-weight: 600;
}

.badge {
    padding: 0.25rem 0.5rem;
    border-radius: 4px;
    font-size: 0.75rem;
    font-weight: 600;
}

.badge.active { background: rgba(16, 185, 129, 0.2); color: #10b981; }
.badge.inactive { background: var(--surface-b); color: var(--text-muted); }

.job-details {
    display: flex;
    flex-direction: column;
    gap: 0.5rem;
    font-size: 0.9rem;
}

.detail-item {
    display: flex;
    justify-content: space-between;
}

.detail-item label {
    color: var(--text-muted);
}

.stats-tags {
    display: flex;
    gap: 0.25rem;
}

.tag {
    background: var(--surface-ground);
    padding: 1px 6px;
    border-radius: 4px;
    font-size: 0.75rem;
}

.error-msg {
    color: #ef4444;
    font-size: 0.8rem;
    background: rgba(239, 68, 68, 0.1);
    padding: 0.5rem;
    border-radius: 4px;
}

.job-actions {
    margin-top: auto;
    padding-top: 1rem;
    border-top: 1px solid var(--border);
    display: flex;
    justify-content: flex-end;
}

/* Modal Styles Reuse */
.modal-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.75);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1000;
}
.modal-content {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 12px;
  width: 90%;
  max-width: 500px;
  max-height: 90vh;
  overflow-y: auto;
}
.modal-header {
    padding: 1rem 1.5rem;
    border-bottom: 1px solid var(--border);
    display: flex;
    justify-content: space-between;
    align-items: center;
}
.modal-body {
    padding: 1.5rem;
    display: flex;
    flex-direction: column;
    gap: 1rem;
}
.sensor-select-list, .stats-select {
    max-height: 150px;
    overflow-y: auto;
    border: 1px solid var(--border); /* Use var for consistency */
    border-radius: 6px;
    padding: 0.5rem;
    display: flex;
    flex-direction: column;
    gap: 0.5rem;
    background: var(--input-bg);
}
.check-item {
    display: flex;
    gap: 0.5rem;
    align-items: center;
    cursor: pointer;
    font-size: 0.9rem;
}
.modal-footer {
    padding-top: 1rem;
    display: flex;
    justify-content: flex-end;
    gap: 0.5rem;
}
.btn-close { background:none; border:none; color: var(--text-muted); cursor: pointer; font-size: 1.2rem; }
.form-input { 
    width: 100%; 
    padding: 0.6rem; 
    background: var(--input-bg); 
    border: 1px solid var(--border); 
    border-radius: 6px; 
    color: var(--text);
}
.form-row { display: flex; gap: 1rem; }
</style>
