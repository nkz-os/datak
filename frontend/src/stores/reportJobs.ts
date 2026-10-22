import { defineStore } from 'pinia'
import { ref } from 'vue'
import { api } from '../api'

export interface ReportJob {
    id: number
    name: string
    description?: string
    interval_minutes: number
    sensor_ids: number[]
    stat_types: string[]
    is_active: boolean
    next_run_at: string
    last_run_at?: string
    last_error?: string
}

export const useReportJobStore = defineStore('reportJobs', () => {
    const jobs = ref<ReportJob[]>([])
    const loading = ref(false)
    const error = ref<string | null>(null)

    async function fetchJobs(): Promise<void> {
        loading.value = true
        error.value = null
        try {
            const response = await api.get('/api/report-jobs')
            jobs.value = response.data
        } catch (e: any) {
            error.value = e.response?.data?.detail || e.message
            console.error(e)
        } finally {
            loading.value = false
        }
    }

    async function createJob(data: Partial<ReportJob>): Promise<ReportJob | null> {
        try {
            const response = await api.post('/api/report-jobs', data)
            const newJob = response.data
            jobs.value.push(newJob)
            return newJob
        } catch (e: any) {
            console.error('Failed to create job:', e)
            error.value = e.response?.data?.detail || e.message
            throw e
        }
    }

    async function deleteJob(id: number): Promise<boolean> {
        try {
            await api.delete(`/api/report-jobs/${id}`)
            jobs.value = jobs.value.filter(j => j.id !== id)
            return true
        } catch (e: any) {
            console.error('Failed to delete job:', e)
            error.value = e.response?.data?.detail || e.message
            return false
        }
    }

    return {
        jobs,
        loading,
        error,
        fetchJobs,
        createJob,
        deleteJob
    }
})
