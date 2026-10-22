import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { api } from '../api'

interface User {
    id: number
    username: string
    role: string
    email?: string
}

export const useAuthStore = defineStore('auth', () => {
    const token = ref<string | null>(localStorage.getItem('token'))
    const user = ref<User | null>(null)

    const isAuthenticated = computed(() => !!token.value)
    const isAdmin = computed(() => user.value?.role === 'ADMIN')

    async function login(username: string, password: string): Promise<boolean> {
        try {
            const response = await api.post('/api/auth/login', { username, password })
            const data = response.data

            token.value = data.access_token
            localStorage.setItem('token', data.access_token)

            await fetchUser()
            return true
        } catch (error) {
            console.error('Login failed:', error)
            return false
        }
    }

    async function fetchUser(): Promise<void> {
        if (!token.value) return

        try {
            const response = await api.get('/api/auth/me')
            user.value = response.data
        } catch (error) {
            console.error('Failed to fetch user:', error)
            logout()
        }
    }

    function logout(): void {
        token.value = null
        user.value = null
        localStorage.removeItem('token')
    }

    async function changePassword(current: string, newPass: string): Promise<boolean> {
        try {
            await api.post('/api/auth/change-password', {
                current_password: current,
                new_password: newPass
            })
            return true
        } catch (error) {
            console.error('Change password failed:', error)
            throw error
        }
    }

    return {
        token,
        user,
        isAuthenticated,
        isAdmin,
        login,
        logout,
        fetchUser,
        changePassword,
    }
})
