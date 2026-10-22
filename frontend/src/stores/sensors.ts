import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { api } from '../api'

export interface Register {
    id?: number
    name: string
    address: number
    count: number
    register_type: string
    data_formula: string
    unit: string | null
    decimal_places: number
    last_value?: number | null
    last_raw_value?: number | null
    twin_attribute?: string | null
}

export interface Sensor {
    id: number
    name: string
    description?: string
    protocol: string
    connection_params: Record<string, unknown>
    data_formula: string
    unit?: string
    poll_interval_ms: number
    is_active: boolean
    status: string
    last_seen?: string
    last_value?: number
    error_count: number
    registers?: Register[]
}

export const useSensorStore = defineStore('sensors', () => {
    const sensors = ref<Sensor[]>([])
    const loading = ref(false)
    const error = ref<string | null>(null)

    const onlineSensors = computed(() =>
        sensors.value.filter(s => s.status === 'ONLINE')
    )

    const offlineSensors = computed(() =>
        sensors.value.filter(s => s.status === 'OFFLINE')
    )

    const sensorCount = computed(() => ({
        total: sensors.value.length,
        online: onlineSensors.value.length,
        offline: offlineSensors.value.length,
        error: sensors.value.filter(s => s.status === 'ERROR').length,
    }))

    async function fetchSensors(): Promise<void> {
        loading.value = true
        error.value = null

        try {
            const response = await api.get('/api/sensors')
            sensors.value = response.data
        } catch (e: unknown) {
            error.value = e instanceof Error ? e.message : 'Failed to fetch sensors'
        } finally {
            loading.value = false
        }
    }

    async function createSensor(data: Partial<Sensor>): Promise<Sensor | null> {
        try {
            const response = await api.post('/api/sensors', data)
            const newSensor = response.data
            sensors.value.push(newSensor)
            return newSensor
        } catch (e) {
            console.error('Failed to create sensor:', e)
            return null
        }
    }

    async function updateSensor(id: number, data: Partial<Sensor>): Promise<boolean> {
        try {
            const response = await api.patch(`/api/sensors/${id}`, data)
            const index = sensors.value.findIndex(s => s.id === id)
            if (index !== -1) {
                sensors.value[index] = response.data
            }
            return true
        } catch (e) {
            console.error('Failed to update sensor:', e)
            return false
        }
    }

    async function deleteSensor(id: number): Promise<boolean> {
        try {
            await api.delete(`/api/sensors/${id}`)
            sensors.value = sensors.value.filter(s => s.id !== id)
            return true
        } catch (e) {
            console.error('Failed to delete sensor:', e)
            return false
        }
    }

    function updateSensorValue(
        sensorId: number,
        value: number,
        status: string,
        registerId?: number,
        registerName?: string
    ): void {
        const sensor = sensors.value.find(s => s.id === sensorId)
        if (sensor) {
            sensor.last_value = value
            sensor.status = status
            sensor.last_seen = new Date().toISOString()

            if (sensor.registers && sensor.registers.length > 0) {
                const reg = sensor.registers.find(
                    r => (registerId !== undefined && r.id === registerId) || (registerName && r.name === registerName)
                )
                if (reg) {
                    reg.last_value = value
                }
            }
        }
    }

    return {
        sensors,
        loading,
        error,
        onlineSensors,
        offlineSensors,
        sensorCount,
        fetchSensors,
        createSensor,
        updateSensor,
        deleteSensor,
        updateSensorValue,
    }
})
