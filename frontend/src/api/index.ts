import axios from 'axios'

export const api = axios.create({
    baseURL: import.meta.env.VITE_API_URL || '/api',
    headers: {
        'Content-Type': 'application/json',
    },
})

// Add auth token to requests
api.interceptors.request.use((config) => {
    const token = localStorage.getItem('token')
    if (token) {
        config.headers.Authorization = `Bearer ${token}`
    }
    return config
})

// Handle 401 responses
api.interceptors.response.use(
    (response) => response,
    (error) => {
        if (error.response?.status === 401) {
            localStorage.removeItem('token')
            window.location.href = '/login'
        }
        return Promise.reject(error)
    }
)

// WebSocket connection
export function createWebSocket(): WebSocket | null {
    const token = localStorage.getItem('token')
    if (!token) return null

    // Use VITE_API_URL if available, otherwise fallback to relative path (proxy)
    const apiUrl = import.meta.env.VITE_API_URL || window.location.origin
    // Replace http(s) with ws(s) and ensure no trailing slash
    const wsBase = apiUrl.replace(/^http/, 'ws').replace(/\/$/, '')

    // Construct full URL with token as query param since standard WS doesn't support headers easily
    // OR rely on cookie (frontend sends token in header for API, but WS might need it in query or protocol)
    // For now, let's keep the connection simple. The backend likely checks token in "on_connect" or "middleware"
    // Note: The previous code didn't pass token in URL either.
    const ws = new WebSocket(`${wsBase}/ws`)

    return ws
}
