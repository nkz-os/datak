import { createRouter, createWebHistory } from 'vue-router'

const routes = [
    {
        path: '/',
        redirect: '/dashboard',
    },
    {
        path: '/login',
        name: 'Login',
        component: () => import('../views/LoginView.vue'),
        meta: { requiresAuth: false },
    },
    {
        path: '/dashboard',
        name: 'Dashboard',
        component: () => import('../views/DashboardView.vue'),
        meta: { requiresAuth: true },
    },
    {
        path: '/sensors',
        name: 'Sensors',
        component: () => import('../views/SensorsView.vue'),
        meta: { requiresAuth: true },
    },
    {
        path: '/sensors/:id',
        name: 'SensorDetail',
        component: () => import('../views/SensorDetailView.vue'),
        meta: { requiresAuth: true },
    },
    {
        path: '/reports',
        name: 'reports',
        component: () => import('../views/ReportsView.vue'),
        meta: { requiresAuth: true }
    },
    {
        path: '/report-jobs',
        name: 'report-jobs',
        component: () => import('../views/ReportJobsView.vue'),
        meta: { requiresAuth: true }
    },
    {
        path: '/automation',
        name: 'Automation',
        component: () => import('../views/AutomationView.vue'),
        meta: { requiresAuth: true }
    },
    {
        path: '/config',
        name: 'Configuration',
        component: () => import('../views/ConfigView.vue'),
        meta: { requiresAuth: true },
    },
]

const router = createRouter({
    history: createWebHistory(),
    routes,
})

// Navigation guard
router.beforeEach((to, from, next) => {
    const token = localStorage.getItem('token')

    if (to.meta.requiresAuth && !token) {
        next('/login')
    } else if (to.path === '/login' && token) {
        next('/dashboard')
    } else {
        next()
    }
})

export default router
